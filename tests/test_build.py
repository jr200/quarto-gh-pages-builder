"""Tests for build module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from quarto_graft.build import (
    BuildResult,
    _manifest_entry_from_result,
    _temp_worktree_name,
    _update_manifest_entry,
    create_broken_stub,
    inject_failure_header,
    resolve_head_sha,
)

# ---------------------------------------------------------------------------
# _temp_worktree_name
# ---------------------------------------------------------------------------


class TestTempWorktreeName:
    def test_format(self):
        name = _temp_worktree_name("demo", "head")
        assert name.startswith("head-demo-")
        # 6 hex chars from uuid
        suffix = name.split("-", 2)[-1]
        assert len(suffix) == 6

    def test_uniqueness(self):
        names = {_temp_worktree_name("demo", "head") for _ in range(50)}
        assert len(names) == 50


# ---------------------------------------------------------------------------
# inject_failure_header
# ---------------------------------------------------------------------------


class TestInjectFailureHeader:
    def test_injects_header_with_sha(self, tmp_path):
        qmd = tmp_path / "page.qmd"
        qmd.write_text("# Hello\n\nContent here.", encoding="utf-8")

        inject_failure_header(qmd, "feature/x", "abcdef1234567", "1111111222222")
        text = qmd.read_text(encoding="utf-8")

        assert "::: callout-warning" in text
        assert "feature/x" in text
        assert "abcdef1" in text  # head_sha[:7]
        assert "1111111" in text  # last_good_sha[:7]
        assert text.endswith("# Hello\n\nContent here.")

    def test_injects_header_without_head_sha(self, tmp_path):
        qmd = tmp_path / "page.qmd"
        qmd.write_text("body", encoding="utf-8")

        inject_failure_header(qmd, "feature/x", None, "aaa1111bbb2222")
        text = qmd.read_text(encoding="utf-8")

        assert "branch missing or unreachable" in text
        assert "aaa1111" in text
        assert text.endswith("body")

    def test_short_shas_used_as_is(self, tmp_path):
        qmd = tmp_path / "page.qmd"
        qmd.write_text("x", encoding="utf-8")

        inject_failure_header(qmd, "b", "abc", "def")
        text = qmd.read_text(encoding="utf-8")
        assert "`abc`" in text
        assert "`def`" in text


# ---------------------------------------------------------------------------
# create_broken_stub
# ---------------------------------------------------------------------------


class TestCreateBrokenStub:
    def test_creates_index_qmd(self, tmp_path):
        out_dir = tmp_path / "grafts__" / "demo"
        paths = create_broken_stub("demo", "graft/demo", "abcdef1234567", out_dir)

        assert len(paths) == 1
        assert paths[0] == out_dir / "index.qmd"
        assert paths[0].exists()

    def test_content_includes_branch_name(self, tmp_path):
        out_dir = tmp_path / "out"
        create_broken_stub("demo", "graft/demo", "abcdef1234567", out_dir)
        text = (out_dir / "index.qmd").read_text(encoding="utf-8")

        assert "graft/demo" in text
        assert "abcdef1" in text
        assert "::: callout-warning" in text

    def test_content_without_sha(self, tmp_path):
        out_dir = tmp_path / "out"
        create_broken_stub("demo", "graft/demo", None, out_dir)
        text = (out_dir / "index.qmd").read_text(encoding="utf-8")

        assert "graft/demo" in text
        assert "no previous successful build" in text

    def test_creates_parent_dirs(self, tmp_path):
        out_dir = tmp_path / "deep" / "nested" / "dir"
        paths = create_broken_stub("demo", "br", None, out_dir)
        assert paths[0].exists()

    def test_short_sha(self, tmp_path):
        out_dir = tmp_path / "out"
        create_broken_stub("demo", "br", "abc", out_dir)
        text = (out_dir / "index.qmd").read_text(encoding="utf-8")
        assert "`abc`" in text


# ---------------------------------------------------------------------------
# _update_manifest_entry
# ---------------------------------------------------------------------------


class TestUpdateManifestEntry:
    def test_basic_entry(self):
        manifest: dict = {}
        _update_manifest_entry(
            manifest, "branch1", "branch1", "Title",
            ["page.qmd"], now="2026-01-01T00:00:00Z",
        )
        entry = manifest["branch1"]
        assert entry["title"] == "Title"
        assert entry["branch_key"] == "branch1"
        assert entry["exported"] == ["page.qmd"]
        assert entry["last_checked"] == "2026-01-01T00:00:00Z"

    def test_with_nav_structure(self):
        manifest: dict = {}
        nav = [{"section": "Ch1", "contents": ["a.qmd"]}]
        _update_manifest_entry(
            manifest, "b", "b", "T", ["a.qmd"],
            nav_structure=nav, now="2026-01-01T00:00:00Z",
        )
        assert manifest["b"]["structure"] == nav

    def test_with_last_good(self):
        manifest: dict = {}
        _update_manifest_entry(
            manifest, "b", "b", "T", [],
            last_good="abc123", now="2026-01-01T00:00:00Z",
        )
        assert manifest["b"]["last_good"] == "abc123"

    def test_with_prerendered(self):
        manifest: dict = {}
        _update_manifest_entry(
            manifest, "b", "b", "T", [],
            prerendered=True, now="2026-01-01T00:00:00Z",
        )
        assert manifest["b"]["prerendered"] is True

    def test_prerendered_false_omitted(self):
        manifest: dict = {}
        _update_manifest_entry(
            manifest, "b", "b", "T", [],
            prerendered=False, now="2026-01-01T00:00:00Z",
        )
        assert "prerendered" not in manifest["b"]

    def test_with_cached_pages(self):
        manifest: dict = {}
        _update_manifest_entry(
            manifest, "b", "b", "T", ["p.qmd"],
            cached_pages=["p.qmd"], now="2026-01-01T00:00:00Z",
        )
        assert manifest["b"]["cached_pages"] == ["p.qmd"]

    def test_empty_cached_pages_omitted(self):
        manifest: dict = {}
        _update_manifest_entry(
            manifest, "b", "b", "T", [],
            cached_pages=[], now="2026-01-01T00:00:00Z",
        )
        assert "cached_pages" not in manifest["b"]

    def test_none_optional_fields_omitted(self):
        manifest: dict = {}
        _update_manifest_entry(
            manifest, "b", "b", "T", [], now="2026-01-01T00:00:00Z",
        )
        for key in ("structure", "last_good", "prerendered", "cached_pages"):
            assert key not in manifest["b"]

    def test_auto_generates_now(self):
        manifest: dict = {}
        _update_manifest_entry(manifest, "b", "b", "T", [])
        assert "last_checked" in manifest["b"]
        assert manifest["b"]["last_checked"].endswith("Z")


# ---------------------------------------------------------------------------
# _manifest_entry_from_result
# ---------------------------------------------------------------------------


class TestManifestEntryFromResult:
    def _make_result(self, **overrides) -> BuildResult:
        defaults = {
            "branch": "b", "branch_key": "b", "title": "T", "status": "ok",
            "head_sha": "abc", "last_good_sha": "abc", "built_at": "2026-01-01T00:00:00Z",
            "exported_relpaths": ["p.qmd"], "exported_dest_paths": [],
        }
        defaults.update(overrides)
        return BuildResult(**defaults)

    def test_basic_fields(self):
        result = self._make_result()
        entry = _manifest_entry_from_result(result)
        assert entry["title"] == "T"
        assert entry["branch_key"] == "b"
        assert entry["exported"] == ["p.qmd"]
        assert entry["last_checked"] == "2026-01-01T00:00:00Z"
        assert entry["last_good"] == "abc"

    def test_with_nav_structure(self):
        nav = [{"section": "A", "contents": ["a.qmd"]}]
        entry = _manifest_entry_from_result(self._make_result(nav_structure=nav))
        assert entry["structure"] == nav

    def test_without_nav_structure(self):
        entry = _manifest_entry_from_result(self._make_result(nav_structure=None))
        assert "structure" not in entry

    def test_with_prerendered(self):
        entry = _manifest_entry_from_result(self._make_result(prerendered=True))
        assert entry["prerendered"] is True

    def test_without_prerendered(self):
        entry = _manifest_entry_from_result(self._make_result(prerendered=False))
        assert "prerendered" not in entry

    def test_with_cached_pages(self):
        entry = _manifest_entry_from_result(self._make_result(cached_pages=["p.qmd"]))
        assert entry["cached_pages"] == ["p.qmd"]

    def test_without_cached_pages(self):
        entry = _manifest_entry_from_result(self._make_result(cached_pages=None))
        assert "cached_pages" not in entry

    def test_no_last_good(self):
        entry = _manifest_entry_from_result(self._make_result(last_good_sha=None))
        assert "last_good" not in entry

    def test_page_hashes_not_in_entry(self):
        """page_hashes lives in build-state.json, not in manifest."""
        entry = _manifest_entry_from_result(
            self._make_result(page_hashes={"p.qmd": "h1"})
        )
        assert "page_hashes" not in entry


# ---------------------------------------------------------------------------
# resolve_head_sha
# ---------------------------------------------------------------------------


class TestResolveHeadSha:
    def test_returns_sha_when_remote_exists(self):
        with patch("quarto_graft.build._branch_exists", side_effect=lambda ref: ref == "origin/feature"):
            with patch("quarto_graft.build.run_git", return_value="abc123def456"):
                result = resolve_head_sha("feature")
        assert result == "abc123def456"

    def test_falls_back_to_local(self):
        def branch_exists(ref):
            return ref == "feature"

        with patch("quarto_graft.build._branch_exists", side_effect=branch_exists):
            with patch("quarto_graft.build.run_git", return_value="local123"):
                result = resolve_head_sha("feature")
        assert result == "local123"

    def test_returns_none_when_branch_missing(self):
        with patch("quarto_graft.build._branch_exists", return_value=False):
            result = resolve_head_sha("nonexistent")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch("quarto_graft.build._branch_exists", return_value=True):
            with patch("quarto_graft.build.run_git", side_effect=Exception("fail")):
                result = resolve_head_sha("feature")
        assert result is None


# ---------------------------------------------------------------------------
# BuildResult dataclass
# ---------------------------------------------------------------------------


class TestBuildResult:
    def test_defaults(self):
        r = BuildResult(
            branch="b", branch_key="bk", title="T", status="ok",
            head_sha="abc", last_good_sha="abc", built_at="now",
            exported_relpaths=[], exported_dest_paths=[],
        )
        assert r.nav_structure is None
        assert r.prerendered is False
        assert r.duration_secs == 0.0
        assert r.error_message is None
        assert r.page_hashes is None
        assert r.cached_pages is None

    def test_all_fields(self):
        r = BuildResult(
            branch="b", branch_key="bk", title="T", status="fallback",
            head_sha="abc", last_good_sha="def", built_at="now",
            exported_relpaths=["p.qmd"], exported_dest_paths=[Path("p.qmd")],
            nav_structure=[{"section": "A"}],
            prerendered=True, duration_secs=1.5,
            error_message="oops",
            page_hashes={"p.qmd": "h"},
            cached_pages=["p.qmd"],
        )
        assert r.status == "fallback"
        assert r.prerendered is True
        assert r.duration_secs == 1.5
        assert r.error_message == "oops"
