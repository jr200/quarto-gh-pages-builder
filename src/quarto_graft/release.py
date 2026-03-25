"""Trunk release logic: version bumping, graft-aware release notes, two-tag rollback."""

from __future__ import annotations

import logging
import os
import re

import pygit2

from .branches import branch_to_key, read_branches_list
from .constants import TRUNK_BRANCHES
from .git_utils import push_to_origin

logger = logging.getLogger(__name__)


def _get_trunk_branch(repo: pygit2.Repository | None = None) -> str:
    """Detect the trunk branch name from the repo's origin/HEAD or by probing known trunk branches."""
    if repo is None:
        repo = _get_repo()

    # Try origin/HEAD (set by 'git clone' or 'git remote set-head')
    try:
        origin_head = repo.references.get("refs/remotes/origin/HEAD")
        if origin_head is not None:
            target = origin_head.target
            if isinstance(target, str) and target.startswith("refs/remotes/origin/"):
                return target.removeprefix("refs/remotes/origin/")
    except Exception:
        pass

    # Fall back: check which trunk branches exist on the remote
    for name in ("master", "main"):
        if name in TRUNK_BRANCHES:
            ref = f"refs/remotes/origin/{name}"
            if ref in repo.references:
                return name

    # Last resort: check local branches
    for name in ("master", "main"):
        if name in TRUNK_BRANCHES:
            ref = f"refs/heads/{name}"
            if ref in repo.references:
                return name

    return "main"


RELEASED_TAG_PREFIX = "released/"
RELEASING_TAG_PREFIX = "releasing/"


def _get_repo() -> pygit2.Repository:
    git_dir = pygit2.discover_repository(".")
    if git_dir is None:
        raise RuntimeError("No git repository found")
    return pygit2.Repository(git_dir)


def _get_gh_api():
    """Return a ghapi GhApi instance configured for the current repo's origin."""
    from ghapi.all import GhApi

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN or GH_TOKEN environment variable required for releases")

    repo = _get_repo()
    origin = repo.remotes["origin"]
    url = origin.url

    # Extract owner/repo from SSH or HTTPS URL
    m = re.search(r"[:/]([^/]+)/([^/.]+?)(?:\.git)?$", url)
    if not m:
        raise RuntimeError(f"Cannot parse GitHub owner/repo from origin URL: {url}")

    return GhApi(owner=m.group(1), repo=m.group(2), token=token)


def get_latest_release_tag() -> str | None:
    """Return the tag name of the latest GitHub release, or None."""
    api = _get_gh_api()
    try:
        release = api.repos.get_latest_release()
        return release.tag_name
    except Exception:
        return None


def compute_next_version(current: str | None) -> str:
    """Auto-increment patch version from current tag, or start at v0.0.1."""
    if not current:
        return "v0.0.1"

    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", current)
    if not m:
        raise ValueError(f"Cannot parse version from tag: {current}")

    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"v{major}.{minor}.{patch + 1}"


def generate_main_notes(current: str | None, next_tag: str) -> str:
    """Generate release notes for the main branch using GitHub's API."""
    if not current:
        return ""

    api = _get_gh_api()
    try:
        result = api.repos.generate_release_notes(
            tag_name=next_tag,
            target_commitish=_get_trunk_branch(),
            previous_tag_name=current,
        )
        return result.body
    except Exception as e:
        logger.warning(f"Failed to generate main branch notes: {e}")
        return ""


def generate_graft_notes() -> str:
    """Generate per-graft commit logs since the last release."""
    repo = _get_repo()
    branch_specs = read_branches_list()

    sections: list[str] = []
    for spec in branch_specs:
        branch = spec["branch"]
        key = branch_to_key(branch)

        # Resolve the graft branch HEAD (prefer origin/)
        remote_ref = f"origin/{branch}"
        try:
            graft_head = repo.revparse_single(remote_ref)
        except KeyError:
            logger.debug(f"Remote ref {remote_ref} not found, skipping")
            continue

        # Determine the anchor (previous release point for this graft)
        anchor_oid = None
        tag_ref = f"refs/tags/{RELEASED_TAG_PREFIX}{key}"
        if tag_ref in repo.references:
            anchor_oid = repo.references[tag_ref].peel().id

        # Collect commits
        commits: list[str] = []
        for commit in repo.walk(graft_head.peel(pygit2.Commit).id, pygit2.GIT_SORT_TOPOLOGICAL):  # type: ignore[arg-type]
            if anchor_oid and commit.id == anchor_oid:
                break
            short_sha = str(commit.id)[:7]
            first_line = commit.message.split("\n", 1)[0]
            commits.append(f"{short_sha} {first_line}")

        if commits:
            lines = "\n".join(commits)
            sections.append(f"## {branch}\n```\n{lines}\n```")

    return "\n\n".join(sections)


def build_release_notes(current: str | None, next_tag: str) -> str:
    """Combine main branch and graft branch notes."""
    parts: list[str] = []

    main_notes = generate_main_notes(current, next_tag)
    if main_notes:
        parts.append(f"# Main branch changes\n\n{main_notes}")

    graft_notes = generate_graft_notes()
    if graft_notes:
        parts.append(f"# Graft branch changes\n\n{graft_notes}")

    return "\n\n".join(parts)


def stage_graft_tags() -> list[str]:
    """Create releasing/<key> tags on each graft branch HEAD. Returns list of keys staged."""
    repo = _get_repo()
    branch_specs = read_branches_list()
    staged_keys: list[str] = []

    for spec in branch_specs:
        branch = spec["branch"]
        key = branch_to_key(branch)
        remote_ref = f"origin/{branch}"

        try:
            target = repo.revparse_single(remote_ref)
        except KeyError:
            logger.debug(f"Remote ref {remote_ref} not found, skipping tag")
            continue

        tag_name = f"{RELEASING_TAG_PREFIX}{key}"
        tag_ref = f"refs/tags/{tag_name}"

        # Delete existing tag if present
        if tag_ref in repo.references:
            repo.references.delete(tag_ref)

        repo.references.create(tag_ref, target.peel(pygit2.Commit).id, force=True)
        staged_keys.append(key)
        logger.debug(f"Staged tag {tag_name} -> {target.peel(pygit2.Commit).id}")

    return staged_keys


def rollback_staging_tags(staged_keys: list[str]) -> None:
    """Remove releasing/<key> tags (local only)."""
    repo = _get_repo()
    for key in staged_keys:
        tag_ref = f"refs/tags/{RELEASING_TAG_PREFIX}{key}"
        if tag_ref in repo.references:
            repo.references.delete(tag_ref)
            logger.debug(f"Rolled back {tag_ref}")


def create_release(next_tag: str, notes: str) -> str:
    """Create a GitHub release targeting main. Returns the release URL."""
    api = _get_gh_api()
    release = api.repos.create_release(
        tag_name=next_tag,
        name=next_tag,
        body=notes,
        target_commitish=_get_trunk_branch(),
    )
    return release.html_url


def promote_tags(staged_keys: list[str]) -> None:
    """Promote releasing/<key> -> released/<key> and push to origin."""
    repo = _get_repo()

    for key in staged_keys:
        releasing_ref = f"refs/tags/{RELEASING_TAG_PREFIX}{key}"
        released_ref = f"refs/tags/{RELEASED_TAG_PREFIX}{key}"

        if releasing_ref not in repo.references:
            continue

        target_oid = repo.references[releasing_ref].peel().id

        # Create/update released tag
        if released_ref in repo.references:
            repo.references.delete(released_ref)
        repo.references.create(released_ref, target_oid, force=True)

        # Delete releasing tag locally
        repo.references.delete(releasing_ref)

    # Push all released tags and delete releasing tags on remote
    for key in staged_keys:
        push_to_origin(f"refs/tags/{RELEASED_TAG_PREFIX}{key}:refs/tags/{RELEASED_TAG_PREFIX}{key}")
        push_to_origin(f":refs/tags/{RELEASING_TAG_PREFIX}{key}")


def trigger_workflow(workflow_file: str = "quarto-graft-build-publish.yaml") -> None:
    """Trigger a GitHub Actions workflow on main."""
    api = _get_gh_api()
    api.actions.create_workflow_dispatch(
        workflow_id=workflow_file,
        ref=_get_trunk_branch(),
    )
