"""Tests for cache module (per-page render cache)."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import pygit2
import pytest

from quarto_graft.cache import (
    CACHE_BRANCH,
    CACHE_MANIFEST_NAME,
    _create_empty_cache_branch,
    _extract_sidebar,
    _iter_tree_blobs,
    _replace_sidebar,
    _write_rootless_commit,
    cache_branch_exists,
    cache_status,
    clear_cache,
    content_hash,
    content_hash_bytes,
    ensure_local_cache_branch,
    fix_navigation,
    load_cache_manifest,
    lookup_cached_page,
    restore_cached_files,
    update_cache_after_render,
)
from quarto_graft.constants import GRAFTS_BUILD_RELPATH

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    """Create a bare-minimum pygit2 repository."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = pygit2.init_repository(str(repo_path), bare=False)
    sig = pygit2.Signature("test", "test@test.local")
    tree_id = repo.index.write_tree()
    repo.create_commit("refs/heads/main", sig, sig, "init", tree_id, [])
    return repo


@pytest.fixture
def repo_with_cache(git_repo):
    """Repository with an initialised (empty) ``_cache`` branch."""
    _create_empty_cache_branch(git_repo)
    return git_repo


def _populate_cache(repo, branch_key, pages):
    """Write *pages* into the ``_cache`` branch, accumulating onto existing content.

    *pages* maps ``source_relpath`` → ``(content_hash, {output_relpath: bytes, …})``.
    """
    existing = {}
    manifest = {"version": 1, "pages": {}}
    try:
        commit = repo.revparse_single(CACHE_BRANCH)
        tree = commit.peel(pygit2.Tree)
        for path, oid, mode in _iter_tree_blobs(repo, tree):
            existing[path] = (oid, mode)
        try:
            me = tree[CACHE_MANIFEST_NAME]
            manifest = json.loads(repo.get(me.id).data)
        except (KeyError, json.JSONDecodeError):
            pass
    except (KeyError, pygit2.GitError):
        pass

    for src_rel, (h, output_map) in pages.items():
        page_key = f"{branch_key}/{src_rel}"
        out_files = []
        for out_rel, data in output_map.items():
            blob_id = repo.create_blob(data)
            existing[f"{branch_key}/{out_rel}"] = (blob_id, pygit2.GIT_FILEMODE_BLOB)
            out_files.append(out_rel)
        manifest["pages"][page_key] = {
            "content_hash": h,
            "cached_at": "2026-01-01T00:00:00Z",
            "output_files": out_files,
        }

    mj = json.dumps(manifest, indent=2, sort_keys=True).encode()
    existing[CACHE_MANIFEST_NAME] = (repo.create_blob(mj), pygit2.GIT_FILEMODE_BLOB)

    idx = pygit2.Index()
    for path, (oid, mode) in sorted(existing.items()):
        idx.add(pygit2.IndexEntry(path, oid, mode))
    _write_rootless_commit(repo, idx)


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_hash_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        assert content_hash(f) == hashlib.sha256(b"hello world").hexdigest()

    def test_hash_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert content_hash(f) == hashlib.sha256(b"").hexdigest()

    def test_hash_binary_file(self, tmp_path):
        data = bytes(range(256))
        f = tmp_path / "binary.bin"
        f.write_bytes(data)
        assert content_hash(f) == hashlib.sha256(data).hexdigest()

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("aaa")
        f2.write_text("bbb")
        assert content_hash(f1) != content_hash(f2)

    def test_same_content_same_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("same")
        f2.write_text("same")
        assert content_hash(f1) == content_hash(f2)


class TestContentHashBytes:
    def test_hash_bytes(self):
        data = b"test data"
        assert content_hash_bytes(data) == hashlib.sha256(data).hexdigest()

    def test_hash_empty_bytes(self):
        assert content_hash_bytes(b"") == hashlib.sha256(b"").hexdigest()

    def test_consistency_with_content_hash(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"consistent")
        assert content_hash(f) == content_hash_bytes(b"consistent")


# ---------------------------------------------------------------------------
# Sidebar extraction / replacement
# ---------------------------------------------------------------------------

# Realistic Quarto sidebar HTML — links have class="sidebar-item-text sidebar-link"
_DEMO_PAGE1_HREF = f"{GRAFTS_BUILD_RELPATH}/demo/page1.html"
SAMPLE_SIDEBAR = (
    '<nav id="quarto-sidebar" class="sidebar collapse collapse-horizontal sidebar-navigation floating overflow-auto">'
    '<ul class="sidebar-section depth1">'
    '<li class="sidebar-item"><div class="sidebar-item-container">'
    '<a href="index.html" class="sidebar-item-text sidebar-link active">'
    '<span class="menu-text">Home</span></a></div></li>'
    '<li class="sidebar-item"><div class="sidebar-item-container">'
    f'<a href="{_DEMO_PAGE1_HREF}" class="sidebar-item-text sidebar-link">'
    '<span class="menu-text">Page 1</span></a></div></li>'
    '</ul></nav>'
)

SAMPLE_HTML_WITH_SIDEBAR = f"""<!DOCTYPE html>
<html><body>
{SAMPLE_SIDEBAR}
<div id="content">Hello</div>
</body></html>"""

SAMPLE_HTML_NO_SIDEBAR = """<!DOCTYPE html>
<html><body><div>No sidebar here</div></body></html>"""


class TestExtractSidebar:
    def test_extracts_sidebar(self):
        result = _extract_sidebar(SAMPLE_HTML_WITH_SIDEBAR)
        assert result is not None
        assert 'id="quarto-sidebar"' in result
        assert "Page 1" in result

    def test_returns_none_when_no_sidebar(self):
        assert _extract_sidebar(SAMPLE_HTML_NO_SIDEBAR) is None

    def test_returns_none_for_empty_html(self):
        assert _extract_sidebar("") is None

    def test_handles_single_quoted_id(self):
        html = "<nav id='quarto-sidebar'>content</nav>"
        result = _extract_sidebar(html)
        assert result is not None


class TestReplaceSidebar:
    """Tests use realistic Quarto HTML where <a> tags already have class attributes."""

    def test_replaces_sidebar_content(self):
        fresh = '<nav id="quarto-sidebar"><ul><li>New nav</li></ul></nav>'
        result = _replace_sidebar(SAMPLE_HTML_WITH_SIDEBAR, fresh, "other.html")
        assert "New nav" in result

    def test_sets_active_on_existing_class_attr(self):
        """Bug C regression: active must be added to the existing class, not as a duplicate attr."""
        result = _replace_sidebar(
            SAMPLE_HTML_WITH_SIDEBAR, SAMPLE_SIDEBAR, _DEMO_PAGE1_HREF
        )
        # "active" should appear in the class for page1
        assert 'class="active sidebar-item-text sidebar-link"' in result
        # Must NOT produce a duplicate class attribute
        assert 'class="sidebar-link active" class=' not in result

    def test_strips_active_only_from_class_attrs(self):
        """Bug B regression: page titles containing 'active' must not be corrupted."""
        sidebar_with_active_title = (
            '<nav id="quarto-sidebar">'
            '<a href="a.html" class="sidebar-item-text sidebar-link active">'
            '<span class="menu-text">My active Projects</span></a>'
            '<a href="b.html" class="sidebar-item-text sidebar-link">'
            '<span class="menu-text">Other</span></a>'
            '</nav>'
        )
        result = _replace_sidebar(SAMPLE_HTML_WITH_SIDEBAR, sidebar_with_active_title, "b.html")
        # Title text must be preserved
        assert "My active Projects" in result
        # "active" stripped from a.html's class
        assert 'href="a.html" class="active' not in result
        # "active" added to b.html's class
        assert 'class="active sidebar-item-text sidebar-link"' in result

    def test_no_change_when_no_sidebar_in_original(self):
        fresh = '<nav id="quarto-sidebar">fresh</nav>'
        result = _replace_sidebar(SAMPLE_HTML_NO_SIDEBAR, fresh, "x.html")
        assert result == SAMPLE_HTML_NO_SIDEBAR

    def test_removes_active_from_previously_active_link(self):
        result = _replace_sidebar(
            SAMPLE_HTML_WITH_SIDEBAR, SAMPLE_SIDEBAR, _DEMO_PAGE1_HREF
        )
        # index.html was "active" in the fresh sidebar — should be stripped
        # Find the <a> for index.html and confirm "active" is NOT in its class
        import re
        index_link = re.search(r'<a\s[^>]*href="index\.html"[^>]*>', result)
        assert index_link is not None
        assert "active" not in index_link.group(0)

    def test_backslash_in_sidebar_content(self):
        r"""Bug D regression: backslashes in sidebar must not be treated as regex escapes."""
        sidebar_with_backslash = (
            r'<nav id="quarto-sidebar">'
            r'<a href="math.html" class="sidebar-item-text sidebar-link">'
            r'<span class="menu-text">Section \(1\) — Intro</span></a>'
            r'</nav>'
        )
        result = _replace_sidebar(SAMPLE_HTML_WITH_SIDEBAR, sidebar_with_backslash, "math.html")
        # Backslash content must survive intact
        assert r"Section \(1\)" in result

    def test_preserves_other_classes(self):
        """Active injection must not drop existing classes like sidebar-item-text."""
        result = _replace_sidebar(
            SAMPLE_HTML_WITH_SIDEBAR, SAMPLE_SIDEBAR, _DEMO_PAGE1_HREF
        )
        import re
        link = re.search(rf'<a\s[^>]*href="{re.escape(_DEMO_PAGE1_HREF)}"[^>]*>', result)
        assert link is not None
        class_match = re.search(r'class="([^"]*)"', link.group(0))
        assert class_match is not None
        classes = class_match.group(1).split()
        assert "active" in classes
        assert "sidebar-item-text" in classes
        assert "sidebar-link" in classes


# ---------------------------------------------------------------------------
# fix_navigation
# ---------------------------------------------------------------------------


class TestFixNavigation:
    def _setup_site(self, tmp_path):
        """Create a mock ``_site/`` with a fresh trunk page and a cached graft page."""
        site_dir = tmp_path / "_site"
        site_dir.mkdir()

        fresh_html = (
            '<!DOCTYPE html><html><body>'
            '<nav id="quarto-sidebar" class="sidebar">'
            '<ul><li><a href="index.html" class="sidebar-item-text sidebar-link active">'
            '<span class="menu-text">Home</span></a></li>'
            f'<li><a href="{GRAFTS_BUILD_RELPATH}/demo/page.html" class="sidebar-item-text sidebar-link">'
            '<span class="menu-text">Demo Page</span></a></li></ul>'
            '</nav><div>Fresh</div></body></html>'
        )
        (site_dir / "index.html").write_text(fresh_html)

        graft_dir = site_dir / GRAFTS_BUILD_RELPATH / "demo"
        graft_dir.mkdir(parents=True)
        cached_html = (
            '<!DOCTYPE html><html><body>'
            '<nav id="quarto-sidebar" class="sidebar">'
            '<ul><li><a href="index.html" class="sidebar-item-text sidebar-link">'
            '<span class="menu-text">Old nav</span></a></li></ul>'
            '</nav><div>Cached</div></body></html>'
        )
        (graft_dir / "page.html").write_text(cached_html)
        return site_dir

    def test_updates_cached_page_sidebar(self, tmp_path):
        site_dir = self._setup_site(tmp_path)
        assert fix_navigation(site_dir, ["demo"]) == 1
        page = (site_dir / GRAFTS_BUILD_RELPATH / "demo" / "page.html").read_text()
        assert "Demo Page" in page  # fresh sidebar content injected

    def test_returns_zero_when_no_fresh_page(self, tmp_path):
        site_dir = tmp_path / "_site"
        site_dir.mkdir()
        assert fix_navigation(site_dir, ["demo"]) == 0

    def test_returns_zero_when_no_cached_grafts(self, tmp_path):
        site_dir = self._setup_site(tmp_path)
        assert fix_navigation(site_dir, []) == 0

    def test_explicit_fresh_page_path(self, tmp_path):
        site_dir = self._setup_site(tmp_path)
        fresh = site_dir / "index.html"
        assert fix_navigation(site_dir, ["demo"], fresh_page_path=fresh) == 1

    def test_skips_non_page_html(self, tmp_path):
        site_dir = self._setup_site(tmp_path)
        asset = site_dir / GRAFTS_BUILD_RELPATH / "demo" / "widget.html"
        asset.write_text("<div>just a widget</div>")
        assert fix_navigation(site_dir, ["demo"]) == 1  # only page.html

    def test_handles_missing_graft_dir(self, tmp_path):
        site_dir = self._setup_site(tmp_path)
        # "missing" graft dir doesn't exist → should not error
        assert fix_navigation(site_dir, ["missing"]) == 0


# ---------------------------------------------------------------------------
# Git-dependent: branch helpers
# ---------------------------------------------------------------------------


class TestCacheBranchExists:
    def test_false_when_no_branch(self, git_repo):
        with patch("quarto_graft.cache._get_repo", return_value=git_repo):
            assert cache_branch_exists() is False

    def test_true_after_creation(self, repo_with_cache):
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert cache_branch_exists() is True


class TestEnsureLocalCacheBranch:
    def test_noop_when_local_exists(self, repo_with_cache):
        """If local _cache already exists, return True without touching it."""
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert ensure_local_cache_branch() is True
            assert CACHE_BRANCH in repo_with_cache.branches.local

    def test_creates_from_remote(self, git_repo):
        """Simulates CI: no local _cache, but origin/_cache remote ref exists."""
        _create_empty_cache_branch(git_repo)
        cache_commit_oid = git_repo.branches[CACHE_BRANCH].target

        # Create a fake remote ref pointing at the same commit
        git_repo.references.create(
            f"refs/remotes/origin/{CACHE_BRANCH}", cache_commit_oid, force=True,
        )
        # Delete the local branch
        git_repo.branches.delete(CACHE_BRANCH)
        assert CACHE_BRANCH not in git_repo.branches.local

        with patch("quarto_graft.cache._get_repo", return_value=git_repo):
            assert ensure_local_cache_branch() is True
            assert CACHE_BRANCH in git_repo.branches.local
            assert git_repo.branches[CACHE_BRANCH].target == cache_commit_oid

    def test_false_when_no_local_and_no_remote(self, git_repo):
        """No _cache anywhere — return False."""
        with patch("quarto_graft.cache._get_repo", return_value=git_repo):
            assert ensure_local_cache_branch() is False
            assert CACHE_BRANCH not in git_repo.branches.local


class TestCreateEmptyCacheBranch:
    def test_creates_branch(self, git_repo):
        assert CACHE_BRANCH not in git_repo.branches.local
        _create_empty_cache_branch(git_repo)
        assert CACHE_BRANCH in git_repo.branches.local

    def test_manifest_is_empty(self, git_repo):
        _create_empty_cache_branch(git_repo)
        commit = git_repo.revparse_single(CACHE_BRANCH)
        tree = commit.peel(pygit2.Tree)
        blob = git_repo.get(tree[CACHE_MANIFEST_NAME].id)
        assert json.loads(blob.data) == {"version": 1, "pages": {}}

    def test_rootless_commit(self, git_repo):
        _create_empty_cache_branch(git_repo)
        commit = git_repo.revparse_single(CACHE_BRANCH)
        assert list(commit.parent_ids) == []


class TestIterTreeBlobs:
    def test_finds_manifest(self, repo_with_cache):
        commit = repo_with_cache.revparse_single(CACHE_BRANCH)
        tree = commit.peel(pygit2.Tree)
        paths = [p for p, _, _ in _iter_tree_blobs(repo_with_cache, tree)]
        assert CACHE_MANIFEST_NAME in paths

    def test_nested_blobs(self, git_repo):
        index = pygit2.Index()
        for path in ["a/b/c.txt", "a/d.txt", "e.txt"]:
            blob_id = git_repo.create_blob(b"data")
            index.add(pygit2.IndexEntry(path, blob_id, pygit2.GIT_FILEMODE_BLOB))
        _write_rootless_commit(git_repo, index)

        commit = git_repo.revparse_single(CACHE_BRANCH)
        tree = commit.peel(pygit2.Tree)
        paths = sorted(p for p, _, _ in _iter_tree_blobs(git_repo, tree))
        assert paths == ["a/b/c.txt", "a/d.txt", "e.txt"]


# ---------------------------------------------------------------------------
# Git-dependent: manifest loading
# ---------------------------------------------------------------------------


class TestLoadCacheManifest:
    def test_empty_when_no_cache_branch(self, git_repo):
        with patch("quarto_graft.cache._get_repo", return_value=git_repo):
            assert load_cache_manifest() == {"version": 1, "pages": {}}

    def test_loads_populated_manifest(self, repo_with_cache):
        _populate_cache(repo_with_cache, "demo", {
            "page.qmd": ("abc123", {"page.html": b"<html>cached</html>"})
        })
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            manifest = load_cache_manifest()
        assert "demo/page.qmd" in manifest["pages"]
        assert manifest["pages"]["demo/page.qmd"]["content_hash"] == "abc123"


# ---------------------------------------------------------------------------
# Git-dependent: lookup
# ---------------------------------------------------------------------------


class TestLookupCachedPage:
    def test_returns_entry_on_hash_match(self, repo_with_cache):
        _populate_cache(repo_with_cache, "demo", {
            "page.qmd": ("hash123", {"page.html": b"<html>hi</html>"})
        })
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            entry = lookup_cached_page("demo", "page.qmd", "hash123")
        assert entry is not None
        assert entry["content_hash"] == "hash123"
        assert entry["output_files"] == ["page.html"]

    def test_returns_none_on_hash_mismatch(self, repo_with_cache):
        _populate_cache(repo_with_cache, "demo", {
            "page.qmd": ("hash123", {"page.html": b"<html>hi</html>"})
        })
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert lookup_cached_page("demo", "page.qmd", "wrong") is None

    def test_returns_none_when_not_cached(self, repo_with_cache):
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert lookup_cached_page("demo", "missing.qmd", "any") is None

    def test_returns_none_when_no_cache_branch(self, git_repo):
        with patch("quarto_graft.cache._get_repo", return_value=git_repo):
            assert lookup_cached_page("demo", "page.qmd", "any") is None


# ---------------------------------------------------------------------------
# Git-dependent: restore
# ---------------------------------------------------------------------------


class TestRestoreCachedFiles:
    def test_restores_single_file(self, repo_with_cache, tmp_path):
        html = b"<html><body>Restored</body></html>"
        _populate_cache(repo_with_cache, "demo", {
            "page.qmd": ("h1", {"page.html": html})
        })
        dest = tmp_path / "output"
        dest.mkdir()
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert restore_cached_files("demo", ["page.html"], dest) is True
        assert (dest / "page.html").read_bytes() == html

    def test_restores_nested_files(self, repo_with_cache, tmp_path):
        _populate_cache(repo_with_cache, "demo", {
            "ch/page.qmd": ("h2", {
                "ch/page.html": b"<html>nested</html>",
                "ch/page_files/fig.png": b"\x89PNG",
            })
        })
        dest = tmp_path / "output"
        dest.mkdir()
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            ok = restore_cached_files("demo", ["ch/page.html", "ch/page_files/fig.png"], dest)
        assert ok is True
        assert (dest / "ch" / "page.html").exists()
        assert (dest / "ch" / "page_files" / "fig.png").read_bytes() == b"\x89PNG"

    def test_returns_false_when_file_missing_from_cache(self, repo_with_cache, tmp_path):
        dest = tmp_path / "output"
        dest.mkdir()
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert restore_cached_files("demo", ["nonexistent.html"], dest) is False

    def test_returns_false_when_no_cache_branch(self, git_repo, tmp_path):
        dest = tmp_path / "output"
        dest.mkdir()
        with patch("quarto_graft.cache._get_repo", return_value=git_repo):
            assert restore_cached_files("demo", ["page.html"], dest) is False


# ---------------------------------------------------------------------------
# Git-dependent: update_cache_after_render
# ---------------------------------------------------------------------------


class TestUpdateCacheAfterRender:
    def _make_site(self, tmp_path, branch_key, pages):
        """Create ``_site/<GRAFTS_BUILD_RELPATH>/<branch_key>/`` with rendered *pages*."""
        site_dir = tmp_path / "_site"
        graft_dir = site_dir / GRAFTS_BUILD_RELPATH / branch_key
        graft_dir.mkdir(parents=True, exist_ok=True)
        for rel, content in pages.items():
            f = graft_dir / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(content)
        return site_dir

    def test_caches_new_render(self, repo_with_cache, tmp_path):
        site_dir = self._make_site(tmp_path, "demo", {
            "page.html": b"<html>rendered</html>"
        })
        states = {"demo": {"page_hashes": {"page.qmd": "h-abc"}, "cached_pages": []}}
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert update_cache_after_render(site_dir, states) == 1
            manifest = load_cache_manifest()
        entry = manifest["pages"]["demo/page.qmd"]
        assert entry["content_hash"] == "h-abc"
        assert "page.html" in entry["output_files"]

    def test_skips_already_cached(self, repo_with_cache, tmp_path):
        site_dir = self._make_site(tmp_path, "demo", {
            "page.html": b"<html>rendered</html>"
        })
        states = {"demo": {"page_hashes": {"page.qmd": "h"}, "cached_pages": ["page.qmd"]}}
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert update_cache_after_render(site_dir, states) == 0

    def test_prunes_deleted_pages(self, repo_with_cache, tmp_path):
        _populate_cache(repo_with_cache, "demo", {
            "p1.qmd": ("h1", {"p1.html": b"<html>p1</html>"}),
            "p2.qmd": ("h2", {"p2.html": b"<html>p2</html>"}),
        })
        # Build now only has p1 — p2 was deleted from graft
        site_dir = self._make_site(tmp_path, "demo", {})
        states = {"demo": {"page_hashes": {"p1.qmd": "h1"}, "cached_pages": ["p1.qmd"]}}
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            update_cache_after_render(site_dir, states)
            manifest = load_cache_manifest()
        assert "demo/p1.qmd" in manifest["pages"]
        assert "demo/p2.qmd" not in manifest["pages"]

    def test_prunes_deleted_page_removes_blobs(self, repo_with_cache, tmp_path):
        """When a cached page is deleted from the graft, its output blobs
        must be removed from the ``_cache`` tree — not just the manifest entry."""
        _populate_cache(repo_with_cache, "demo", {
            "keep.qmd": ("h1", {"keep.html": b"<html>keep</html>"}),
            "gone.qmd": ("h2", {
                "gone.html": b"<html>gone</html>",
                "gone_files/fig.png": b"\x89PNG",
            }),
        })
        # Only "keep" survives
        site_dir = self._make_site(tmp_path, "demo", {})
        states = {"demo": {"page_hashes": {"keep.qmd": "h1"}, "cached_pages": ["keep.qmd"]}}
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            update_cache_after_render(site_dir, states)
        # Verify blobs for deleted page are gone from the tree
        commit = repo_with_cache.revparse_single(CACHE_BRANCH)
        tree = commit.peel(pygit2.Tree)
        paths = [p for p, _, _ in _iter_tree_blobs(repo_with_cache, tree)]
        assert not any("gone" in p for p in paths)
        # Kept page blobs still present
        assert any("keep" in p for p in paths)

    def test_prunes_all_pages_when_graft_emptied(self, repo_with_cache, tmp_path):
        """If every page in a graft is removed, all cache entries for that
        graft are pruned while other grafts remain untouched."""
        _populate_cache(repo_with_cache, "alpha", {
            "a.qmd": ("ha", {"a.html": b"<html>A</html>"}),
        })
        _populate_cache(repo_with_cache, "beta", {
            "b.qmd": ("hb", {"b.html": b"<html>B</html>"}),
        })
        # alpha has no pages left; beta still has its page
        site_dir = self._make_site(tmp_path, "beta", {})
        states = {
            "alpha": {"page_hashes": {}, "cached_pages": []},
            "beta": {"page_hashes": {"b.qmd": "hb"}, "cached_pages": ["b.qmd"]},
        }
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            update_cache_after_render(site_dir, states)
            manifest = load_cache_manifest()
        assert "alpha/a.qmd" not in manifest["pages"]
        assert "beta/b.qmd" in manifest["pages"]

    def test_caches_page_with_assets(self, repo_with_cache, tmp_path):
        site_dir = self._make_site(tmp_path, "demo", {"analysis.html": b"<html>A</html>"})
        asset_dir = tmp_path / "_site" / GRAFTS_BUILD_RELPATH / "demo" / "analysis_files"
        asset_dir.mkdir()
        (asset_dir / "fig1.png").write_bytes(b"\x89PNG")

        states = {"demo": {"page_hashes": {"analysis.qmd": "hx"}, "cached_pages": []}}
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert update_cache_after_render(site_dir, states) == 1
            manifest = load_cache_manifest()
        ofs = manifest["pages"]["demo/analysis.qmd"]["output_files"]
        assert "analysis.html" in ofs
        assert "analysis_files/fig1.png" in ofs

    def test_multiple_grafts(self, repo_with_cache, tmp_path):
        site_dir = tmp_path / "_site"
        for bk, html in [("ga", b"<html>A</html>"), ("gb", b"<html>B</html>")]:
            d = site_dir / GRAFTS_BUILD_RELPATH / bk
            d.mkdir(parents=True)
            (d / "index.html").write_bytes(html)

        states = {
            "ga": {"page_hashes": {"index.qmd": "ha"}, "cached_pages": []},
            "gb": {"page_hashes": {"index.qmd": "hb"}, "cached_pages": []},
        }
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert update_cache_after_render(site_dir, states) == 2

    def test_rootless_commit_after_update(self, repo_with_cache, tmp_path):
        site_dir = self._make_site(tmp_path, "demo", {"p.html": b"<html>x</html>"})
        states = {"demo": {"page_hashes": {"p.qmd": "h"}, "cached_pages": []}}
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            update_cache_after_render(site_dir, states)
        commit = repo_with_cache.revparse_single(CACHE_BRANCH)
        assert list(commit.parent_ids) == []

    def test_warns_when_rendered_file_missing(self, repo_with_cache, tmp_path):
        site_dir = tmp_path / "_site"
        (site_dir / GRAFTS_BUILD_RELPATH / "demo").mkdir(parents=True)
        # page.html does NOT exist in _site
        states = {"demo": {"page_hashes": {"page.qmd": "h"}, "cached_pages": []}}
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert update_cache_after_render(site_dir, states) == 0

    def test_empty_build_states(self, repo_with_cache, tmp_path):
        site_dir = tmp_path / "_site"
        site_dir.mkdir()
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert update_cache_after_render(site_dir, {}) == 0


# ---------------------------------------------------------------------------
# Git-dependent: clear_cache
# ---------------------------------------------------------------------------


class TestClearCache:
    def test_full_clear_recreates_empty(self, repo_with_cache):
        _populate_cache(repo_with_cache, "demo", {
            "page.qmd": ("h", {"page.html": b"<html>x</html>"})
        })
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            clear_cache(delete_remote=False)
            manifest = load_cache_manifest()
        assert manifest["pages"] == {}

    def test_full_clear_branch_still_exists(self, repo_with_cache):
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            clear_cache(delete_remote=False)
        assert CACHE_BRANCH in repo_with_cache.branches.local

    def test_clear_specific_graft(self, repo_with_cache):
        _populate_cache(repo_with_cache, "keep-me", {
            "k.qmd": ("hk", {"k.html": b"<html>keep</html>"})
        })
        _populate_cache(repo_with_cache, "remove-me", {
            "r.qmd": ("hr", {"r.html": b"<html>remove</html>"})
        })
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            clear_cache(graft_name="remove-me", delete_remote=False)
            manifest = load_cache_manifest()
        assert "keep-me/k.qmd" in manifest["pages"]
        assert "remove-me/r.qmd" not in manifest["pages"]

    def test_clear_specific_graft_removes_blobs(self, repo_with_cache):
        _populate_cache(repo_with_cache, "demo", {
            "page.qmd": ("h", {"page.html": b"<html>demo</html>"})
        })
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            clear_cache(graft_name="demo", delete_remote=False)
        # Verify the blob file is also gone from the tree
        commit = repo_with_cache.revparse_single(CACHE_BRANCH)
        tree = commit.peel(pygit2.Tree)
        paths = [p for p, _, _ in _iter_tree_blobs(repo_with_cache, tree)]
        assert not any(p.startswith("demo/") for p in paths)

    def test_clear_nonexistent_graft_is_noop(self, repo_with_cache):
        """Clearing a graft that's not in cache should not error."""
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            clear_cache(graft_name="nonexistent", delete_remote=False)
            manifest = load_cache_manifest()
        assert manifest["pages"] == {}


# ---------------------------------------------------------------------------
# cache_status
# ---------------------------------------------------------------------------


class TestCacheStatus:
    def test_empty_cache(self, repo_with_cache):
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            assert cache_status() == []

    def test_returns_entries(self, repo_with_cache):
        _populate_cache(repo_with_cache, "demo", {
            "page.qmd": ("abcdef1234567890abcdef", {"page.html": b"<html>x</html>"})
        })
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            entries = cache_status()
        assert len(entries) == 1
        assert entries[0]["page_key"] == "demo/page.qmd"
        assert entries[0]["content_hash"] == "abcdef123456"  # truncated to 12 chars
        assert entries[0]["output_files"] == 1

    def test_sorted_by_page_key(self, repo_with_cache):
        _populate_cache(repo_with_cache, "beta", {
            "b.qmd": ("hb", {"b.html": b"B"})
        })
        _populate_cache(repo_with_cache, "alpha", {
            "a.qmd": ("ha", {"a.html": b"A"})
        })
        with patch("quarto_graft.cache._get_repo", return_value=repo_with_cache):
            entries = cache_status()
        keys = [e["page_key"] for e in entries]
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# build.py: manifest entry cache fields
# ---------------------------------------------------------------------------


class TestUpdateManifestEntry:
    def test_includes_cached_pages(self):
        from quarto_graft.build import _update_manifest_entry

        manifest = {}
        _update_manifest_entry(
            manifest, "branch1", "branch1", "Title",
            ["page.qmd"], now="2026-01-01T00:00:00Z",
            cached_pages=["page.qmd"],
        )
        entry = manifest["branch1"]
        # page_hashes is NOT stored in manifest (lives in build-state.json)
        assert "page_hashes" not in entry
        assert entry["cached_pages"] == ["page.qmd"]

    def test_omits_cache_fields_when_none(self):
        from quarto_graft.build import _update_manifest_entry

        manifest = {}
        _update_manifest_entry(
            manifest, "branch1", "branch1", "Title",
            ["page.qmd"], now="2026-01-01T00:00:00Z",
        )
        entry = manifest["branch1"]
        assert "page_hashes" not in entry
        assert "cached_pages" not in entry

    def test_omits_cached_pages_when_empty(self):
        from quarto_graft.build import _update_manifest_entry

        manifest = {}
        _update_manifest_entry(
            manifest, "branch1", "branch1", "Title",
            ["page.qmd"], now="2026-01-01T00:00:00Z",
            cached_pages=[],
        )
        entry = manifest["branch1"]
        assert "cached_pages" not in entry


class TestManifestEntryFromResult:
    def test_includes_cached_pages(self):
        from quarto_graft.build import BuildResult, _manifest_entry_from_result

        result = BuildResult(
            branch="b", branch_key="b", title="T", status="ok",
            head_sha="abc", last_good_sha="abc", built_at="now",
            exported_relpaths=["p.qmd"], exported_dest_paths=[],
            page_hashes={"p.qmd": "h1"}, cached_pages=["p.qmd"],
        )
        entry = _manifest_entry_from_result(result)
        # page_hashes stays on BuildResult but is NOT persisted to manifest
        assert "page_hashes" not in entry
        assert entry["cached_pages"] == ["p.qmd"]

    def test_omits_cache_fields_when_none(self):
        from quarto_graft.build import BuildResult, _manifest_entry_from_result

        result = BuildResult(
            branch="b", branch_key="b", title="T", status="ok",
            head_sha="abc", last_good_sha="abc", built_at="now",
            exported_relpaths=["p.qmd"], exported_dest_paths=[],
        )
        entry = _manifest_entry_from_result(result)
        assert "page_hashes" not in entry
        assert "cached_pages" not in entry
