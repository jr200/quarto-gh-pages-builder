"""Tests for git_utils module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pygit2
import pytest

from quarto_graft.git_utils import (
    GitError,
    GitRefNotFoundError,
    GitRemoteError,
    _get_repo,
    _list_worktree_objects,
    _resolve_ref,
    cleanup_orphan_worktrees,
    create_worktree,
    delete_branch,
    has_commits,
    is_worktree,
    list_local_branches,
    list_worktree_paths,
    managed_worktree,
    prune_worktrees,
    ref_exists,
    remove_worktree,
    rev_parse,
    worktrees_for_branch,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal pygit2 repository with one commit on 'main'."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = pygit2.init_repository(str(repo_path), bare=False)

    # Create initial commit
    sig = pygit2.Signature("test", "test@test.local")
    (repo_path / "README.md").write_text("# Test\n", encoding="utf-8")
    repo.index.add("README.md")
    repo.index.write()
    tree_id = repo.index.write_tree()
    repo.create_commit("refs/heads/main", sig, sig, "Initial commit", tree_id, [])
    repo.set_head("refs/heads/main")
    return repo


@pytest.fixture
def git_repo_with_branch(git_repo):
    """Repository with a 'feature' branch pointing at HEAD."""
    commit = git_repo.revparse_single("HEAD")
    git_repo.branches.local.create("feature", commit)
    return git_repo


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_git_error_extends_runtime_error(self):
        assert issubclass(GitError, RuntimeError)

    def test_ref_not_found_extends_git_error(self):
        assert issubclass(GitRefNotFoundError, GitError)

    def test_remote_error_extends_git_error(self):
        assert issubclass(GitRemoteError, GitError)

    def test_git_error_catchable_as_runtime_error(self):
        with pytest.raises(RuntimeError):
            raise GitRefNotFoundError("test")


# ---------------------------------------------------------------------------
# _get_repo
# ---------------------------------------------------------------------------


class TestGetRepo:
    def test_opens_existing_repo(self, git_repo):
        repo = _get_repo(cwd=Path(git_repo.workdir))
        assert repo.workdir == git_repo.workdir

    def test_raises_when_not_repo(self, tmp_path):
        with pytest.raises(RuntimeError, match="No git repository"):
            _get_repo(cwd=tmp_path)


# ---------------------------------------------------------------------------
# _list_worktree_objects
# ---------------------------------------------------------------------------


class TestListWorktreeObjects:
    def test_empty_when_no_worktrees(self, git_repo):
        result = _list_worktree_objects(git_repo)
        assert result == []


# ---------------------------------------------------------------------------
# rev_parse
# ---------------------------------------------------------------------------


class TestRevParse:
    def test_resolves_head(self, git_repo):
        expected = str(git_repo.head.target)
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            result = rev_parse("HEAD")
        assert result == expected

    def test_resolves_branch_name(self, git_repo_with_branch):
        expected = str(git_repo_with_branch.branches["feature"].target)
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
            result = rev_parse("feature")
        assert result == expected

    def test_resolves_full_sha(self, git_repo):
        sha = str(git_repo.head.target)
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            result = rev_parse(sha)
        assert result == sha

    def test_nonexistent_raises_git_ref_not_found(self, git_repo):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            with pytest.raises(GitRefNotFoundError, match="nonexistent"):
                rev_parse("nonexistent")

    def test_with_cwd(self, git_repo):
        """rev_parse passes cwd through to _get_repo."""
        expected = str(git_repo.head.target)
        result = rev_parse("HEAD", cwd=Path(git_repo.workdir))
        assert result == expected


# ---------------------------------------------------------------------------
# ref_exists
# ---------------------------------------------------------------------------


class TestRefExists:
    def test_returns_true_for_existing_ref(self, git_repo):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            assert ref_exists("HEAD") is True

    def test_returns_true_for_branch(self, git_repo_with_branch):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
            assert ref_exists("feature") is True

    def test_returns_false_for_nonexistent(self, git_repo):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            assert ref_exists("nonexistent") is False


# ---------------------------------------------------------------------------
# list_local_branches
# ---------------------------------------------------------------------------


class TestListLocalBranches:
    def test_lists_branches(self, git_repo_with_branch):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
            branches = list_local_branches()
        assert "main" in branches
        assert "feature" in branches

    def test_sorted_output(self, git_repo_with_branch):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
            branches = list_local_branches()
        assert branches == sorted(branches)

    def test_returns_list(self, git_repo):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            result = list_local_branches()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# delete_branch
# ---------------------------------------------------------------------------


class TestDeleteBranch:
    def test_deletes_existing_branch(self, git_repo_with_branch):
        assert "feature" in git_repo_with_branch.branches.local
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
            delete_branch("feature")
        assert "feature" not in git_repo_with_branch.branches.local

    def test_nonexistent_branch_no_error(self, git_repo):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            delete_branch("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# prune_worktrees
# ---------------------------------------------------------------------------


class TestPruneWorktrees:
    def test_delegates_to_cleanup(self, git_repo):
        import quarto_graft.constants as constants

        try:
            constants._root_override = Path(git_repo.workdir)
            with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
                prune_worktrees()  # should not raise
        finally:
            constants._root_override = None


# ---------------------------------------------------------------------------
# _resolve_ref
# ---------------------------------------------------------------------------


class TestResolveRef:
    def test_resolve_local_branch(self, git_repo_with_branch):
        obj = _resolve_ref(git_repo_with_branch, "feature")
        assert obj.id == git_repo_with_branch.branches["feature"].target

    def test_resolve_full_ref(self, git_repo):
        obj = _resolve_ref(git_repo, "refs/heads/main")
        assert obj.id == git_repo.branches["main"].target

    def test_resolve_branch_returns_correct_commit(self, git_repo):
        """Resolving 'main' returns the commit pointed to by main."""
        obj = _resolve_ref(git_repo, "main")
        assert obj.id == git_repo.branches["main"].target


# ---------------------------------------------------------------------------
# has_commits
# ---------------------------------------------------------------------------


class TestHasCommits:
    def test_true_with_commits(self, git_repo):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            assert has_commits() is True

    def test_false_with_no_commits(self, tmp_path):
        repo_path = tmp_path / "empty_repo"
        repo_path.mkdir()
        repo = pygit2.init_repository(str(repo_path), bare=False)
        with patch("quarto_graft.git_utils._get_repo", return_value=repo):
            assert has_commits() is False


# ---------------------------------------------------------------------------
# create_worktree / remove_worktree / managed_worktree
# ---------------------------------------------------------------------------


class TestCreateAndRemoveWorktree:
    def test_create_worktree(self, git_repo_with_branch):
        """Use 'feature' branch since 'main' is the HEAD of the main repo."""
        import quarto_graft.constants as constants

        try:
            constants._root_override = Path(git_repo_with_branch.workdir)
            with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
                wt_dir = create_worktree("feature", "test-wt")
            assert wt_dir.exists()
            assert (wt_dir / "README.md").exists()
        finally:
            constants._root_override = None

    def test_remove_worktree(self, git_repo_with_branch):
        import quarto_graft.constants as constants

        try:
            constants._root_override = Path(git_repo_with_branch.workdir)
            with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
                wt_dir = create_worktree("feature", "test-rm-wt")
                assert wt_dir.exists()
                remove_worktree("test-rm-wt")
            assert not wt_dir.exists()
        finally:
            constants._root_override = None

    def test_managed_worktree_context_manager(self, git_repo_with_branch):
        import quarto_graft.constants as constants

        try:
            constants._root_override = Path(git_repo_with_branch.workdir)
            with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
                with managed_worktree("feature", "test-managed") as wt_dir:
                    assert wt_dir.exists()
                    assert (wt_dir / "README.md").exists()
            # After context exit, worktree should be cleaned up
            assert not wt_dir.exists()
        finally:
            constants._root_override = None

    def test_managed_worktree_cleanup_on_error(self, git_repo_with_branch):
        import quarto_graft.constants as constants

        try:
            constants._root_override = Path(git_repo_with_branch.workdir)
            with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
                with pytest.raises(ValueError):
                    with managed_worktree("feature", "test-err") as wt_dir:
                        assert wt_dir.exists()
                        raise ValueError("test error")
            assert not wt_dir.exists()
        finally:
            constants._root_override = None

    def test_remove_nonexistent_worktree_noop(self, git_repo):
        import quarto_graft.constants as constants

        try:
            constants._root_override = Path(git_repo.workdir)
            with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
                remove_worktree("nonexistent-wt")  # should not raise
        finally:
            constants._root_override = None


# ---------------------------------------------------------------------------
# list_worktree_paths / is_worktree / worktrees_for_branch
# ---------------------------------------------------------------------------


class TestWorktreeQueries:
    def test_list_worktree_paths_empty(self, git_repo):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            assert list_worktree_paths() == []

    def test_is_worktree_false_for_random_path(self, git_repo, tmp_path):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            assert is_worktree(tmp_path / "random") is False

    def test_worktrees_for_branch_empty(self, git_repo):
        with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
            assert worktrees_for_branch("nonexistent") == []


# ---------------------------------------------------------------------------
# cleanup_orphan_worktrees
# ---------------------------------------------------------------------------


class TestCleanupOrphanWorktrees:
    def test_removes_orphan_directories(self, git_repo, tmp_path):
        import quarto_graft.constants as constants

        try:
            constants._root_override = Path(git_repo.workdir)
            cache_dir = constants.WORKTREES_CACHE
            cache_dir.mkdir(parents=True, exist_ok=True)

            # Create an orphan directory
            orphan = cache_dir / "orphan-dir"
            orphan.mkdir()
            (orphan / "file.txt").write_text("data")

            with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
                removed = cleanup_orphan_worktrees()

            assert len(removed) == 1
            assert not orphan.exists()
        finally:
            constants._root_override = None

    def test_keeps_registered_worktrees(self, git_repo_with_branch):
        import quarto_graft.constants as constants

        try:
            constants._root_override = Path(git_repo_with_branch.workdir)
            cache_dir = constants.WORKTREES_CACHE
            cache_dir.mkdir(parents=True, exist_ok=True)

            # Create a real worktree using 'feature' branch (not HEAD)
            with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
                wt_dir = create_worktree("feature", "real-wt")
                removed = cleanup_orphan_worktrees()

            # Real worktree should not be removed
            assert removed == []
            assert wt_dir.exists()

            # Cleanup
            with patch("quarto_graft.git_utils._get_repo", return_value=git_repo_with_branch):
                remove_worktree("real-wt")
        finally:
            constants._root_override = None

    def test_empty_cache_dir(self, git_repo):
        import quarto_graft.constants as constants

        try:
            constants._root_override = Path(git_repo.workdir)
            with patch("quarto_graft.git_utils._get_repo", return_value=git_repo):
                removed = cleanup_orphan_worktrees()
            assert removed == []
        finally:
            constants._root_override = None
