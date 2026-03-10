from __future__ import annotations

import logging
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

import pygit2

from . import constants
from .constants import TRUNK_BRANCHES

logger = logging.getLogger(__name__)


class GitError(RuntimeError):
    """Base error for git operations."""


class GitRefNotFoundError(GitError):
    """Raised when a git reference cannot be resolved."""


class GitRemoteError(GitError):
    """Raised when a remote operation (push/fetch) fails."""


_thread_local = threading.local()


def _get_repo(cwd: Path | None = None) -> pygit2.Repository:
    """Open the git repository at cwd (or ROOT), caching per thread."""
    base = cwd or constants.ROOT
    key = str(base)
    repos = getattr(_thread_local, "repos", None)
    if repos is None:
        _thread_local.repos = repos = {}
    if key not in repos:
        git_dir = pygit2.discover_repository(key)
        if git_dir is None:
            raise RuntimeError(f"No git repository found at {base}")
        repos[key] = pygit2.Repository(git_dir)
    return repos[key]


def _list_worktree_objects(repo: pygit2.Repository):
    """Return list of (name, path, head_shorthand) for worktrees."""
    worktrees = []
    if hasattr(repo, "list_worktrees"):
        for name in repo.list_worktrees():
            try:
                wt = repo.lookup_worktree(name)
                wt_repo = pygit2.Repository(str(Path(wt.path)))
                head = wt_repo.head
                shorthand = head.shorthand if head else None
                worktrees.append((name, Path(wt.path).resolve(), shorthand))
            except Exception:
                continue
    return worktrees


def _get_auth_callbacks() -> pygit2.RemoteCallbacks:
    """Create RemoteCallbacks with SSH agent and GITHUB_TOKEN authentication support."""

    class AuthCallbacks(pygit2.RemoteCallbacks):
        def credentials(self, url, username_from_url, allowed_types):
            # Try GITHUB_TOKEN for HTTPS authentication (works for private/enterprise repos)
            if allowed_types & pygit2.credentials.CredentialType.USERPASS_PLAINTEXT:
                token = os.environ.get("GITHUB_TOKEN")
                if token:
                    # For GitHub, username can be anything when using a token
                    return pygit2.UserPass("x-access-token", token)

            # Try SSH agent (most common for GitHub/GitLab)
            if allowed_types & pygit2.credentials.CredentialType.SSH_KEY:
                # Use git as username if connecting to GitHub/GitLab
                username = username_from_url or "git"
                return pygit2.KeypairFromAgent(username)

            # Fallback to default credential types
            return None

    return AuthCallbacks()


def rev_parse(ref: str, cwd: Path | None = None) -> str:
    """Resolve a git ref to its full SHA hex string.

    Raises:
        GitRefNotFoundError: If the ref cannot be resolved.
    """
    repo = _get_repo(cwd)
    try:
        obj = repo.revparse_single(ref)
        return str(obj.id)
    except KeyError as e:
        raise GitRefNotFoundError(f"Reference not found: {ref}") from e


def ref_exists(ref: str, cwd: Path | None = None) -> bool:
    """Return True if *ref* can be resolved in the repository."""
    try:
        rev_parse(ref, cwd)
        return True
    except GitRefNotFoundError:
        return False


def list_local_branches(cwd: Path | None = None) -> list[str]:
    """Return sorted list of local branch names."""
    repo = _get_repo(cwd)
    return sorted(repo.branches.local)


def push_to_origin(refspec: str, cwd: Path | None = None) -> None:
    """Push *refspec* to the 'origin' remote.

    Deletion refspecs (e.g. ':refs/heads/branch') are best-effort and
    will not raise if the remote branch does not exist.

    Raises:
        GitRemoteError: If the remote is missing or the push fails.
    """
    repo = _get_repo(cwd)
    try:
        origin = repo.remotes["origin"]
    except KeyError as e:
        raise GitRemoteError("remote 'origin' not found") from e

    if refspec.startswith(":"):
        # Deletion push — best effort
        try:
            origin.push([refspec], callbacks=_get_auth_callbacks())
        except Exception as e:
            logger.debug(f"Push delete failed: {e}")
        return

    try:
        origin.push([refspec], callbacks=_get_auth_callbacks())
    except Exception as e:
        raise GitRemoteError(f"push failed: {e}") from e


def prune_worktrees() -> None:
    """Remove orphaned worktree directories (equivalent to ``git worktree prune``)."""
    cleanup_orphan_worktrees()


def delete_branch(name: str, cwd: Path | None = None) -> None:
    """Force-delete a local branch. No-op if the branch does not exist."""
    repo = _get_repo(cwd)
    try:
        repo.branches.delete(name)
    except KeyError:
        pass


def _force_remove_worktree_ref(path: Path) -> None:
    """Prune a worktree registration by path (best-effort, internal helper)."""
    repo = _get_repo()
    name = path.name
    try:
        wt = repo.lookup_worktree(name)
        wt.prune(force=True)
    except Exception:
        pass



def list_worktree_paths() -> list[Path]:
    """Return a list of worktree paths registered with git."""
    repo = _get_repo()
    return [path for _, path, _ in _list_worktree_objects(repo)]


def is_worktree(path: Path) -> bool:
    """Check whether the given path is a registered git worktree."""
    path_resolved = path.resolve()
    return path_resolved in list_worktree_paths()


def worktrees_for_branch(branch: str) -> list[Path]:
    """Return paths of worktrees checked out at a given branch."""
    repo = _get_repo()
    paths: list[Path] = []
    for _, path, shorthand in _list_worktree_objects(repo):
        if shorthand == branch:
            paths.append(path)
    return paths


def has_commits() -> bool:
    """Return True if the repository has at least one commit."""
    repo = _get_repo()
    return not repo.head_is_unborn


def fetch_origin() -> None:
    """Fetch and prune origin to ensure refs are up to date before building."""
    logger.info("[fetch] git fetch --prune origin")
    repo = _get_repo()
    try:
        origin = repo.remotes["origin"]
    except KeyError:
        logger.info("[fetch] No origin remote found; skipping fetch")
        return
    origin.fetch(prune=True, callbacks=_get_auth_callbacks())


def _resolve_ref(repo: pygit2.Repository, ref: str) -> pygit2.Object:
    """Resolve a ref/branch/oid to a git object."""
    # Local branch
    if ref in repo.branches.local:
        br = repo.branches[ref]
        return repo.get(br.target)

    # Remote branch (e.g., origin/feature)
    if ref in getattr(repo.branches, "remote", []):
        br = repo.branches.remote[ref]
        return repo.get(br.target)

    # Full ref name
    if ref in repo.references:
        return repo.get(repo.references[ref].target)

    # Try revparse on any other ref/oid
    try:
        return repo.revparse_single(ref)
    except Exception as e:
        raise RuntimeError(f"Reference not found: {ref} ({e})") from e


def create_worktree(ref: str, name: str) -> Path:
    """
    Create (or reuse) a git worktree for the given reference.
    """
    constants.WORKTREES_CACHE.mkdir(exist_ok=True)
    wt_dir = constants.WORKTREES_CACHE / name

    # Always recreate to ensure clean state
    if wt_dir.exists():
        remove_worktree(name, force=True)

    repo = _get_repo()
    target = _resolve_ref(repo, ref)

    # Add worktree (detached initially)
    repo.add_worktree(name, str(wt_dir))

    # Open the worktree repo and reset to target
    wt_repo = pygit2.Repository(str(wt_dir))
    wt_repo.reset(target.id, pygit2.GIT_RESET_HARD)

    # Try to set HEAD to branch if ref is a local branch
    branch_ref = None
    existing_heads = {sh for _, _, sh in _list_worktree_objects(repo) if sh}
    branch_name = None
    if ref in repo.branches.local:
        branch_name = ref
        branch_ref = f"refs/heads/{ref}"
    elif ref.startswith("refs/heads/"):
        branch_name = ref.split("/", 2)[-1]
        branch_ref = ref

    # If branch is already checked out in another worktree, stay detached
    if branch_name and branch_name in existing_heads:
        branch_ref = None

    if branch_ref:
        if branch_ref not in wt_repo.references:
            wt_repo.create_reference(branch_ref, target.id, force=True)
        wt_repo.set_head(branch_ref)
    else:
        wt_repo.set_head(target.id)

    wt_repo.checkout_head(strategy=pygit2.GIT_CHECKOUT_FORCE)
    wt_repo.state_cleanup()
    return wt_dir


def remove_worktree(worktree_name: str | Path, force: bool = False) -> None:
    """
    Remove a git worktree by name or absolute path.

    This function removes:
    - The worktree directory itself
    - The git admin directory (.git/worktrees/<name>)
    - Any branch created by pygit2.add_worktree() with the same name

    This ensures that temporary worktrees created during builds don't leave
    behind orphaned branches like "head-marimo-75a809".
    """
    wt_dir = Path(worktree_name)
    if not wt_dir.is_absolute():
        wt_dir = constants.WORKTREES_CACHE / wt_dir

    repo = _get_repo()
    name = wt_dir.name

    # Early exit if nothing to clean up
    worktree_exists = wt_dir.exists()
    branch_exists = name in repo.branches.local
    if not worktree_exists and not branch_exists:
        return

    try:
        # Prune the worktree registration (best-effort)
        _force_remove_worktree_ref(wt_dir)

        # Ensure admin dir under .git/worktrees/<name> is gone
        admin_dir = Path(repo.path) / "worktrees" / name
        if admin_dir.exists():
            shutil.rmtree(admin_dir, ignore_errors=True)

        # Remove working directory itself
        if wt_dir.exists():
            shutil.rmtree(wt_dir)

        # Delete the branch created by pygit2.add_worktree() if it exists
        # This cleans up temporary branches like "head-marimo-75a809" created during builds
        if name in repo.branches.local:
            try:
                branch = repo.branches.local[name]
                branch.delete()
                logger.debug(f"Deleted branch: {name}")
            except Exception as e:
                logger.debug(f"Failed to delete branch {name}: {e}")

        logger.debug(f"Removed worktree: {wt_dir}")
    except Exception:
        logger.warning(f"Failed to remove worktree via pygit2/git, removing manually: {wt_dir}")
        shutil.rmtree(wt_dir, ignore_errors=True)


@contextmanager
def managed_worktree(ref: str, name: str):
    """Context manager for managing git worktrees with automatic cleanup."""
    wt_dir = None
    try:
        wt_dir = create_worktree(ref, name)
        yield wt_dir
    finally:
        if wt_dir is not None:
            try:
                remove_worktree(name)
            except Exception as e:
                logger.warning(f"Failed to cleanup worktree {name}: {e}")


def ensure_worktree(branch: str) -> Path:
    """
    Ensure there is a git worktree for the given branch under .grafts-cache/<branch>.
    """

    if branch in TRUNK_BRANCHES:
        raise ValueError(f"{branch} is not a graft git-branch")

    wt_dir = constants.WORKTREES_CACHE / branch

    if wt_dir.exists():
        logger.info(f"[get-worktree] Worktree directory already exists: {wt_dir}")
        return wt_dir

    logger.info(f"[get-worktree] Creating worktree for branch '{branch}' at {wt_dir} ...")

    repo = _get_repo()
    ref = None
    if branch in repo.branches.local:
        ref = f"refs/heads/{branch}"
        logger.info(f"[get-worktree] Using local branch '{branch}'")
    elif f"refs/remotes/origin/{branch}" in repo.references:
        ref = f"refs/remotes/origin/{branch}"
        logger.info(f"[get-worktree] Using remote branch 'origin/{branch}'")
    else:
        raise RuntimeError(f"Branch '{branch}' does not exist locally or on origin")

    constants.WORKTREES_CACHE.mkdir(exist_ok=True)
    create_worktree(ref, branch)

    logger.info(f"[get-worktree] Worktree created: {wt_dir}")
    return wt_dir


def delete_worktree(branch: str) -> None:
    """Delete the git worktree under .grafts-cache/<branch>."""
    logger.info(f"[delete-worktree] Removing worktree for branch '{branch}'")
    remove_worktree(branch)


def cleanup_orphan_worktrees() -> list[Path]:
    """
    Remove directories under .grafts-cache/ that are no longer registered with git.

    Returns:
        List of successfully removed worktree paths.
        Failed removals are logged but don't stop the cleanup process.
    """
    constants.WORKTREES_CACHE.mkdir(exist_ok=True)
    registered = set(list_worktree_paths())
    removed: list[Path] = []
    failures: list[tuple[Path, Exception]] = []

    for path in constants.WORKTREES_CACHE.iterdir():
        if not path.is_dir():
            continue
        if path.resolve() in registered:
            continue

        logger.info(f"[cleanup-worktrees] Removing orphaned worktree dir {path}")
        try:
            shutil.rmtree(path)
            removed.append(path)
        except PermissionError as e:
            logger.warning(f"[cleanup-worktrees] Permission denied removing {path}: {e}")
            failures.append((path, e))
        except OSError as e:
            logger.warning(f"[cleanup-worktrees] Failed to remove {path}: {e}")
            failures.append((path, e))
        except Exception as e:
            logger.error(f"[cleanup-worktrees] Unexpected error removing {path}: {e}")
            failures.append((path, e))

    if failures:
        logger.warning(
            f"[cleanup-worktrees] Failed to remove {len(failures)} orphaned worktrees. "
            "You may need to manually delete them or check permissions."
        )

    return removed
