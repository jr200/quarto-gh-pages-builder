"""Tests for quarto_config module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quarto_graft.quarto_config import (
    _find_all_collars,
    collect_exported_relpaths,
    derive_section_title,
    extract_nav_structure,
    flatten_quarto_contents,
    is_collar_marker,
    list_available_collars,
    load_quarto_config,
)

# ---------------------------------------------------------------------------
# load_quarto_config
# ---------------------------------------------------------------------------


class TestLoadQuartoConfig:
    def test_loads_yaml(self, tmp_path):
        cfg_file = tmp_path / "_quarto.yaml"
        cfg_file.write_text("project:\n  type: website\n", encoding="utf-8")
        cfg = load_quarto_config(tmp_path)
        assert cfg["project"]["type"] == "website"

    def test_raises_when_missing(self, tmp_path):
        with pytest.raises(RuntimeError, match="No _quarto.yaml"):
            load_quarto_config(tmp_path)

    def test_returns_empty_dict_for_empty_file(self, tmp_path):
        cfg_file = tmp_path / "_quarto.yaml"
        cfg_file.write_text("", encoding="utf-8")
        cfg = load_quarto_config(tmp_path)
        assert cfg == {}


# ---------------------------------------------------------------------------
# list_available_collars
# ---------------------------------------------------------------------------


class TestListAvailableCollars:
    def test_finds_collars_in_sidebar(self, tmp_path):
        cfg = tmp_path / "_quarto.yaml"
        cfg.write_text(
            "website:\n"
            "  sidebar:\n"
            "    contents:\n"
            "      - index.qmd\n"
            "      - _GRAFT_COLLAR: main\n"
            "      - _GRAFT_COLLAR: notes\n",
            encoding="utf-8",
        )
        result = list_available_collars(config_path=cfg)
        assert result == ["main", "notes"]

    def test_finds_collars_in_book_chapters(self, tmp_path):
        cfg = tmp_path / "_quarto.yaml"
        cfg.write_text(
            "book:\n"
            "  chapters:\n"
            "    - index.qmd\n"
            "    - _GRAFT_COLLAR: main\n",
            encoding="utf-8",
        )
        result = list_available_collars(config_path=cfg)
        assert result == ["main"]

    def test_no_collars(self, tmp_path):
        cfg = tmp_path / "_quarto.yaml"
        cfg.write_text(
            "website:\n"
            "  sidebar:\n"
            "    contents:\n"
            "      - index.qmd\n",
            encoding="utf-8",
        )
        result = list_available_collars(config_path=cfg)
        assert result == []

    def test_raises_when_file_missing(self, tmp_path):
        with pytest.raises(RuntimeError, match="No _quarto.yaml"):
            list_available_collars(config_path=tmp_path / "_quarto.yaml")

    def test_nested_collars(self, tmp_path):
        cfg = tmp_path / "_quarto.yaml"
        cfg.write_text(
            "website:\n"
            "  sidebar:\n"
            "    contents:\n"
            "      - section: Top\n"
            "        contents:\n"
            "          - _GRAFT_COLLAR: nested\n",
            encoding="utf-8",
        )
        result = list_available_collars(config_path=cfg)
        assert result == ["nested"]

    def test_deduplicates_collars(self, tmp_path):
        cfg = tmp_path / "_quarto.yaml"
        cfg.write_text(
            "website:\n"
            "  sidebar:\n"
            "    contents:\n"
            "      - _GRAFT_COLLAR: main\n"
            "      - _GRAFT_COLLAR: main\n",
            encoding="utf-8",
        )
        result = list_available_collars(config_path=cfg)
        assert result == ["main"]


# ---------------------------------------------------------------------------
# flatten_quarto_contents
# ---------------------------------------------------------------------------


class TestFlattenQuartoContents:
    def test_simple_strings(self):
        result = flatten_quarto_contents(["a.qmd", "b.qmd"])
        assert result == ["a.qmd", "b.qmd"]

    def test_file_entries(self):
        result = flatten_quarto_contents([{"file": "a.qmd"}, {"file": "b.qmd"}])
        assert result == ["a.qmd", "b.qmd"]

    def test_href_entries(self):
        result = flatten_quarto_contents([{"href": "a.html"}])
        assert result == ["a.html"]

    def test_nested_sections(self):
        entries = [
            {
                "section": "Part 1",
                "contents": ["ch1.qmd", "ch2.qmd"],
            },
            "ch3.qmd",
        ]
        result = flatten_quarto_contents(entries)
        assert result == ["ch1.qmd", "ch2.qmd", "ch3.qmd"]

    def test_deeply_nested(self):
        entries = [
            {
                "section": "Top",
                "contents": [
                    {"section": "Sub", "contents": ["deep.qmd"]},
                ],
            },
        ]
        result = flatten_quarto_contents(entries)
        assert result == ["deep.qmd"]

    def test_book_chapters(self):
        entries = [
            {"part": "Part 1", "chapters": ["ch1.qmd", "ch2.qmd"]},
        ]
        result = flatten_quarto_contents(entries)
        assert result == ["ch1.qmd", "ch2.qmd"]

    def test_empty_list(self):
        assert flatten_quarto_contents([]) == []

    def test_non_list_returns_empty(self):
        assert flatten_quarto_contents("not a list") == []

    def test_mixed_entries(self):
        entries = [
            "standalone.qmd",
            {"file": "explicit.qmd"},
            {"section": "S", "contents": ["nested.qmd"]},
        ]
        result = flatten_quarto_contents(entries)
        assert result == ["standalone.qmd", "explicit.qmd", "nested.qmd"]

    def test_string_contents(self):
        """contents can be a plain string (single file)."""
        entries = [{"section": "S", "contents": "single.qmd"}]
        result = flatten_quarto_contents(entries)
        assert result == ["single.qmd"]


# ---------------------------------------------------------------------------
# extract_nav_structure
# ---------------------------------------------------------------------------


class TestExtractNavStructure:
    def test_returns_sidebar_contents(self):
        cfg = {
            "website": {"sidebar": {"contents": ["a.qmd", "b.qmd"]}},
        }
        result = extract_nav_structure(cfg)
        assert result == ["a.qmd", "b.qmd"]

    def test_returns_book_chapters(self):
        cfg = {
            "book": {"chapters": ["ch1.qmd"]},
        }
        result = extract_nav_structure(cfg)
        assert result == ["ch1.qmd"]

    def test_sidebar_takes_precedence(self):
        cfg = {
            "website": {"sidebar": {"contents": ["sidebar.qmd"]}},
            "book": {"chapters": ["book.qmd"]},
        }
        result = extract_nav_structure(cfg)
        assert result == ["sidebar.qmd"]

    def test_returns_none_when_empty(self):
        assert extract_nav_structure({}) is None

    def test_returns_none_for_empty_sidebar(self):
        cfg = {"website": {"sidebar": {}}}
        assert extract_nav_structure(cfg) is None

    def test_returns_none_for_empty_book(self):
        cfg = {"book": {}}
        assert extract_nav_structure(cfg) is None


# ---------------------------------------------------------------------------
# derive_section_title
# ---------------------------------------------------------------------------


class TestDeriveSectionTitle:
    def test_website_title(self):
        cfg = {"website": {"title": "My Site"}}
        assert derive_section_title(cfg, "branch") == "My Site"

    def test_book_title(self):
        cfg = {"book": {"title": "My Book"}}
        assert derive_section_title(cfg, "branch") == "My Book"

    def test_website_title_preferred(self):
        cfg = {"website": {"title": "Site"}, "book": {"title": "Book"}}
        assert derive_section_title(cfg, "branch") == "Site"

    def test_falls_back_to_branch(self):
        assert derive_section_title({}, "feature/demo") == "feature/demo"

    def test_empty_title_falls_back(self):
        cfg = {"website": {"title": ""}}
        assert derive_section_title(cfg, "branch") == "branch"


# ---------------------------------------------------------------------------
# is_collar_marker
# ---------------------------------------------------------------------------


class TestIsCollarMarker:
    def test_valid_collar_marker(self):
        assert is_collar_marker({"_GRAFT_COLLAR": "main"}) is True

    def test_string_not_marker(self):
        assert is_collar_marker("_GRAFT_COLLAR") is False

    def test_dict_without_marker(self):
        assert is_collar_marker({"section": "A"}) is False

    def test_list_not_marker(self):
        assert is_collar_marker(["_GRAFT_COLLAR"]) is False

    def test_none_not_marker(self):
        assert is_collar_marker(None) is False


# ---------------------------------------------------------------------------
# _find_all_collars
# ---------------------------------------------------------------------------


class TestFindAllCollars:
    def test_finds_top_level_collars(self):
        seq = [
            "index.qmd",
            {"_GRAFT_COLLAR": "main"},
            "other.qmd",
        ]
        result = _find_all_collars(seq)
        assert "main" in result
        list_ref, idx = result["main"]
        assert list_ref is seq
        assert idx == 1

    def test_finds_nested_collars(self):
        inner = [{"_GRAFT_COLLAR": "notes"}]
        seq = [
            {"section": "Part", "contents": inner},
        ]
        result = _find_all_collars(seq)
        assert "notes" in result
        list_ref, idx = result["notes"]
        assert list_ref is inner
        assert idx == 0

    def test_multiple_collars(self):
        seq = [
            {"_GRAFT_COLLAR": "a"},
            {"_GRAFT_COLLAR": "b"},
        ]
        result = _find_all_collars(seq)
        assert set(result.keys()) == {"a", "b"}

    def test_empty_list(self):
        assert _find_all_collars([]) == {}

    def test_chapters_key(self):
        inner = [{"_GRAFT_COLLAR": "ch"}]
        seq = [{"part": "Part1", "chapters": inner}]
        result = _find_all_collars(seq)
        assert "ch" in result


# ---------------------------------------------------------------------------
# collect_exported_relpaths
# ---------------------------------------------------------------------------


class TestCollectExportedRelpaths:
    def _make_docs(self, tmp_path, files: list[str]) -> Path:
        """Create a docs directory with the given files."""
        docs = tmp_path / "docs"
        docs.mkdir()
        for f in files:
            p = docs / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"# {f}\n", encoding="utf-8")
        return docs

    def test_sidebar_contents_ordering(self, tmp_path):
        docs = self._make_docs(tmp_path, ["b.qmd", "a.qmd", "c.qmd"])
        cfg = {"website": {"sidebar": {"contents": ["b.qmd", "a.qmd", "c.qmd"]}}}
        result = collect_exported_relpaths(docs, cfg)
        assert result == ["b.qmd", "a.qmd", "c.qmd"]

    def test_book_chapters(self, tmp_path):
        docs = self._make_docs(tmp_path, ["ch1.qmd", "ch2.qmd"])
        cfg = {"book": {"chapters": ["ch1.qmd", "ch2.qmd"]}}
        result = collect_exported_relpaths(docs, cfg)
        assert result == ["ch1.qmd", "ch2.qmd"]

    def test_project_render(self, tmp_path):
        docs = self._make_docs(tmp_path, ["a.qmd", "b.qmd"])
        cfg = {"project": {"render": ["*.qmd"]}}
        result = collect_exported_relpaths(docs, cfg)
        assert set(result) == {"a.qmd", "b.qmd"}

    def test_fallback_scan(self, tmp_path):
        docs = self._make_docs(tmp_path, ["page.qmd", "sub/nested.qmd"])
        cfg = {}
        result = collect_exported_relpaths(docs, cfg)
        assert "page.qmd" in result
        assert "sub/nested.qmd" in result

    def test_skips_missing_files(self, tmp_path):
        docs = self._make_docs(tmp_path, ["exists.qmd"])
        cfg = {"website": {"sidebar": {"contents": ["exists.qmd", "missing.qmd"]}}}
        result = collect_exported_relpaths(docs, cfg)
        assert result == ["exists.qmd"]

    def test_directory_entry(self, tmp_path):
        docs = self._make_docs(tmp_path, ["subdir/a.qmd", "subdir/b.qmd"])
        cfg = {"website": {"sidebar": {"contents": ["subdir"]}}}
        result = collect_exported_relpaths(docs, cfg)
        assert "subdir/a.qmd" in result
        assert "subdir/b.qmd" in result

    def test_deduplicates(self, tmp_path):
        docs = self._make_docs(tmp_path, ["a.qmd"])
        cfg = {"website": {"sidebar": {"contents": ["a.qmd", "a.qmd"]}}}
        result = collect_exported_relpaths(docs, cfg)
        assert result == ["a.qmd"]

    def test_supported_extensions(self, tmp_path):
        files = ["a.qmd", "b.md", "c.rmd", "d.ipynb", "e.txt", "f.py"]
        docs = self._make_docs(tmp_path, files)
        cfg = {}
        result = collect_exported_relpaths(docs, cfg)
        assert "a.qmd" in result
        assert "b.md" in result
        assert "c.rmd" in result
        assert "d.ipynb" in result
        assert "e.txt" not in result
        assert "f.py" not in result

    def test_excludes_quarto_internals(self, tmp_path):
        docs = self._make_docs(tmp_path, [
            "page.qmd",
            ".quarto/something.qmd",
            "_site/output.qmd",
        ])
        cfg = {}
        result = collect_exported_relpaths(docs, cfg)
        assert "page.qmd" in result
        assert ".quarto/something.qmd" not in result
        assert "_site/output.qmd" not in result

    def test_glob_pattern(self, tmp_path):
        docs = self._make_docs(tmp_path, ["ch/a.qmd", "ch/b.qmd", "other.qmd"])
        cfg = {"website": {"sidebar": {"contents": ["ch/*.qmd"]}}}
        result = collect_exported_relpaths(docs, cfg)
        assert "ch/a.qmd" in result
        assert "ch/b.qmd" in result
        assert "other.qmd" not in result

    def test_auto_entry(self, tmp_path):
        docs = self._make_docs(tmp_path, ["index.qmd", "page.qmd", "sub/nested.qmd"])
        cfg = {"website": {"sidebar": {"contents": ["auto"]}}}
        result = collect_exported_relpaths(docs, cfg)
        # auto excludes index files
        assert "index.qmd" not in result
        assert "page.qmd" in result
        assert "sub/nested.qmd" in result

    def test_string_sidebar_contents(self, tmp_path):
        """sidebar.contents can be a plain string."""
        docs = self._make_docs(tmp_path, ["page.qmd"])
        cfg = {"website": {"sidebar": {"contents": "page.qmd"}}}
        result = collect_exported_relpaths(docs, cfg)
        assert result == ["page.qmd"]


# ---------------------------------------------------------------------------
# apply_manifest (integration-ish, mocking IO)
# ---------------------------------------------------------------------------


class TestApplyManifest:
    """Smoke tests for apply_manifest — heavier logic is tested via flatten/collar/etc."""

    def _setup_project(self, tmp_path, project_type="website"):
        """Set up a minimal project for apply_manifest testing."""
        import quarto_graft.constants as constants
        constants._root_override = tmp_path

        if project_type == "website":
            quarto_yaml = {
                "project": {"type": "website"},
                "website": {
                    "sidebar": {
                        "contents": [
                            "index.qmd",
                            {"_GRAFT_COLLAR": "main"},
                        ]
                    }
                },
            }
        else:
            quarto_yaml = {
                "project": {"type": "book"},
                "book": {
                    "chapters": [
                        "index.qmd",
                        {"_GRAFT_COLLAR": "main"},
                    ]
                },
            }

        from quarto_graft.yaml_utils import get_yaml_loader
        yaml_loader = get_yaml_loader()
        qf = tmp_path / "_quarto.yaml"
        with open(qf, "w") as f:
            yaml_loader.dump(quarto_yaml, f)

        return tmp_path

    def _setup_grafts_config(self, tmp_path, branches):
        """Write a grafts.yaml with given branch specs."""
        from quarto_graft.yaml_utils import get_yaml_loader
        yaml_loader = get_yaml_loader()
        data = {"branches": branches}
        gf = tmp_path / "grafts.yaml"
        with open(gf, "w") as f:
            yaml_loader.dump(data, f)

    def _setup_manifest(self, tmp_path, manifest):
        """Write a grafts.lock with given manifest."""
        mf = tmp_path / "grafts.lock"
        mf.write_text(json.dumps(manifest), encoding="utf-8")

    def test_website_mode_injects_graft(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            self._setup_project(tmp_path, "website")
            self._setup_grafts_config(tmp_path, [
                {"name": "demo", "branch": "graft/demo", "collar": "main"},
            ])
            self._setup_manifest(tmp_path, {
                "graft/demo": {
                    "title": "Demo",
                    "branch_key": "demo",
                    "exported": ["page.qmd"],
                    "last_checked": "2026-01-01T00:00:00Z",
                    "structure": ["page.qmd"],
                },
            })

            from quarto_graft.quarto_config import apply_manifest
            apply_manifest()

            # Read back and verify graft was injected
            from quarto_graft.yaml_utils import get_yaml_loader
            yaml_loader = get_yaml_loader()
            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            contents = result["website"]["sidebar"]["contents"]

            # Should have index.qmd, collar marker, and the auto-generated section
            assert len(contents) == 3
            autogen = contents[2]
            assert autogen.get("_autogen_branch") == "graft/demo"
            assert autogen.get("section") == "Demo"
        finally:
            constants._root_override = None

    def test_book_mode_injects_graft(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            self._setup_project(tmp_path, "book")
            self._setup_grafts_config(tmp_path, [
                {"name": "demo", "branch": "graft/demo", "collar": "main"},
            ])
            self._setup_manifest(tmp_path, {
                "graft/demo": {
                    "title": "Demo",
                    "branch_key": "demo",
                    "exported": ["ch.qmd"],
                    "last_checked": "2026-01-01T00:00:00Z",
                    "structure": ["ch.qmd"],
                },
            })

            from quarto_graft.quarto_config import apply_manifest
            apply_manifest()

            from quarto_graft.yaml_utils import get_yaml_loader
            yaml_loader = get_yaml_loader()
            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            chapters = result["book"]["chapters"]

            assert len(chapters) == 3
            autogen = chapters[2]
            assert autogen.get("_autogen_branch") == "graft/demo"
            assert autogen.get("part") == "Demo"
        finally:
            constants._root_override = None

    def test_prunes_removed_branches(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            self._setup_project(tmp_path, "website")
            self._setup_grafts_config(tmp_path, [])  # no branches
            self._setup_manifest(tmp_path, {
                "old/branch": {
                    "title": "Old",
                    "branch_key": "old",
                    "exported": ["x.qmd"],
                    "last_checked": "2026-01-01T00:00:00Z",
                },
            })

            from quarto_graft.quarto_config import apply_manifest
            apply_manifest()

            # Manifest should be empty now
            from quarto_graft.branches import load_manifest
            m = load_manifest()
            assert "old/branch" not in m
        finally:
            constants._root_override = None

    def test_prerendered_graft_adds_resources(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            self._setup_project(tmp_path, "website")
            self._setup_grafts_config(tmp_path, [
                {"name": "pre", "branch": "graft/pre", "collar": "main"},
            ])
            self._setup_manifest(tmp_path, {
                "graft/pre": {
                    "title": "Pre",
                    "branch_key": "pre",
                    "exported": ["index.html"],
                    "last_checked": "2026-01-01T00:00:00Z",
                    "structure": ["index.qmd"],
                    "prerendered": True,
                },
            })

            from quarto_graft.quarto_config import apply_manifest
            apply_manifest()

            from quarto_graft.yaml_utils import get_yaml_loader
            yaml_loader = get_yaml_loader()
            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            resources = result.get("project", {}).get("resources", [])
            assert "grafts__/pre/**" in resources
        finally:
            constants._root_override = None

    def test_raises_when_no_structure(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            self._setup_project(tmp_path, "website")

            # No website.sidebar.contents and no book.chapters — remove them
            qf = tmp_path / "_quarto.yaml"
            qf.write_text("project:\n  type: default\n", encoding="utf-8")
            self._setup_grafts_config(tmp_path, [])
            self._setup_manifest(tmp_path, {})

            from quarto_graft.quarto_config import apply_manifest
            with pytest.raises(RuntimeError, match="Neither book.chapters nor website.sidebar"):
                apply_manifest()
        finally:
            constants._root_override = None
