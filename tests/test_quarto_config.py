"""Tests for quarto_config module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quarto_graft.constants import GRAFTS_BUILD_RELPATH
from quarto_graft.quarto_config import (
    _find_all_collars,
    collect_exported_relpaths,
    derive_section_title,
    expand_nav_globs,
    extract_nav_structure,
    filter_nav_missing,
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
            "book:\n  chapters:\n    - index.qmd\n    - _GRAFT_COLLAR: main\n",
            encoding="utf-8",
        )
        result = list_available_collars(config_path=cfg)
        assert result == ["main"]

    def test_no_collars(self, tmp_path):
        cfg = tmp_path / "_quarto.yaml"
        cfg.write_text(
            "website:\n  sidebar:\n    contents:\n      - index.qmd\n",
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
            "website:\n  sidebar:\n    contents:\n      - _GRAFT_COLLAR: main\n      - _GRAFT_COLLAR: main\n",
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
# expand_nav_globs
# ---------------------------------------------------------------------------


class TestExpandNavGlobs:
    def test_expands_recursive_glob_in_list(self):
        nav = ["index.qmd", "investigations/**"]
        src = ["index.qmd", "investigations/a.ipynb", "investigations/sub/b.qmd"]
        result = expand_nav_globs(nav, src)
        # Glob expansion now preserves directory hierarchy
        assert result == [
            "index.qmd",
            "investigations/a.ipynb",
            {"section": "Sub", "contents": ["investigations/sub/b.qmd"]},
        ]

    def test_expands_glob_in_section_contents(self):
        nav = [
            "index.qmd",
            {"section": "JIRAs", "contents": "jira/**"},
        ]
        src = ["index.qmd", "jira/t1.ipynb", "jira/t2.qmd"]
        result = expand_nav_globs(nav, src)
        assert result == [
            "index.qmd",
            {"section": "JIRAs", "contents": ["jira/t1.ipynb", "jira/t2.qmd"]},
        ]

    def test_preserves_non_glob_strings(self):
        nav = ["index.qmd", "page.qmd"]
        src = ["index.qmd", "page.qmd"]
        result = expand_nav_globs(nav, src)
        assert result == ["index.qmd", "page.qmd"]

    def test_returns_none_for_none(self):
        assert expand_nav_globs(None, ["a.qmd"]) is None

    def test_no_match_keeps_glob(self):
        nav = ["missing/**"]
        src = ["other/a.qmd"]
        result = expand_nav_globs(nav, src)
        assert result == ["missing/**"]

    def test_auto_string_expands_to_hierarchy(self):
        """Top-level 'auto' should build a hierarchical nav from file paths."""
        src = [
            "a/x.qmd",
            "a/b/c/1.qmd",
            "a/b/c/2.qmd",
            "a/b/c/3.qmd",
        ]
        result = expand_nav_globs("auto", src)
        assert isinstance(result, list)
        # Should have one top-level section "A"
        assert len(result) == 1
        a_section = result[0]
        assert a_section["section"] == "A"
        a_contents = a_section["contents"]
        # a/x.qmd (local file) then section "B"
        assert a_contents[0] == "a/x.qmd"
        b_section = a_contents[1]
        assert b_section["section"] == "B"
        c_section = b_section["contents"][0]
        assert c_section["section"] == "C"
        assert sorted(c_section["contents"]) == [
            "a/b/c/1.qmd",
            "a/b/c/2.qmd",
            "a/b/c/3.qmd",
        ]

    def test_auto_in_list_expands_and_flattens(self):
        """'auto' as a list item should expand into the parent list."""
        nav = ["intro.qmd", "auto"]
        src = ["guide/page.qmd", "guide/other.qmd"]
        result = expand_nav_globs(nav, src)
        assert result[0] == "intro.qmd"
        # auto expanded and flattened: guide section
        guide = result[1]
        assert guide["section"] == "Guide"
        assert sorted(guide["contents"]) == ["guide/other.qmd", "guide/page.qmd"]

    def test_auto_as_section_contents(self):
        """'auto' as a section's contents should be expanded."""
        nav = [{"section": "My Stuff", "contents": "auto"}]
        src = ["a/x.qmd", "a/b/c/1.qmd"]
        result = expand_nav_globs(nav, src)
        section = result[0]
        assert section["section"] == "My Stuff"
        # contents should now be the expanded hierarchy
        assert isinstance(section["contents"], list)
        a_section = section["contents"][0]
        assert a_section["section"] == "A"

    def test_auto_excludes_index_files(self):
        """'auto' should exclude index files like Quarto does."""
        src = ["index.qmd", "page.qmd", "sub/index.qmd", "sub/other.qmd"]
        result = expand_nav_globs("auto", src)
        # Flatten all file entries to check index is excluded
        from quarto_graft.quarto_config import flatten_quarto_contents

        files = flatten_quarto_contents(result)
        assert "index.qmd" not in files
        assert "sub/index.qmd" not in files
        assert "page.qmd" in files
        assert "sub/other.qmd" in files

    def test_glob_preserves_intermediate_empty_dirs(self):
        """Glob expansion must preserve intermediate dirs with no direct files."""
        nav = ["a/**"]
        src = ["a/b/c/d.qmd", "a/b/c/e.qmd"]
        result = expand_nav_globs(nav, src)
        # a/b has no files → b section still present with c section inside
        b_section = result[0]
        assert b_section["section"] == "B"
        c_section = b_section["contents"][0]
        assert c_section["section"] == "C"
        assert sorted(c_section["contents"]) == ["a/b/c/d.qmd", "a/b/c/e.qmd"]

    def test_glob_mixed_depths(self):
        """Glob with files at multiple depths creates correct hierarchy."""
        nav = ["proj/**"]
        src = ["proj/root.qmd", "proj/sub/deep/page.qmd"]
        result = expand_nav_globs(nav, src)
        assert result[0] == "proj/root.qmd"
        sub_section = result[1]
        assert sub_section["section"] == "Sub"
        deep_section = sub_section["contents"][0]
        assert deep_section["section"] == "Deep"
        assert deep_section["contents"] == ["proj/sub/deep/page.qmd"]

    def test_auto_preserves_intermediate_empty_dirs(self):
        """Directories with no direct files but with subdirs must be preserved."""
        src = [
            "a/b/c/1.qmd",
            "a/b/c/2.qmd",
            "a/x.qmd",
        ]
        result = expand_nav_globs("auto", src)
        # a > b > c should all exist even though b has no direct files
        a = result[0]
        assert a["section"] == "A"
        # a has x.qmd and section B
        assert a["contents"][0] == "a/x.qmd"
        b = a["contents"][1]
        assert b["section"] == "B"
        # b has no direct files, only section C
        c = b["contents"][0]
        assert c["section"] == "C"
        assert "a/b/c/1.qmd" in c["contents"]
        assert "a/b/c/2.qmd" in c["contents"]

    def test_multiple_sections_with_globs(self):
        """Mimics the jayshan graft structure from the bug report."""
        nav = [
            "docs/index.qmd",
            {"section": "Investigations", "contents": "investigations/**"},
            {"section": "JIRAs", "contents": "jira/**"},
            {"section": "Support", "contents": "support/**"},
        ]
        src = [
            "docs/index.qmd",
            "investigations/inv1.ipynb",
            "jira/TRD-1234/page.ipynb",
            "support/fixgw.ipynb",
            "support/index.qmd",
        ]
        result = expand_nav_globs(nav, src)
        assert result == [
            "docs/index.qmd",
            {"section": "Investigations", "contents": ["investigations/inv1.ipynb"]},
            {
                "section": "JIRAs",
                "contents": [
                    {"section": "Trd 1234", "contents": ["jira/TRD-1234/page.ipynb"]},
                ],
            },
            {"section": "Support", "contents": ["support/fixgw.ipynb", "support/index.qmd"]},
        ]


# ---------------------------------------------------------------------------
# filter_nav_missing
# ---------------------------------------------------------------------------


class TestFilterNavMissing:
    def test_removes_missing_file_from_flat_list(self):
        nav = ["a.qmd", "b.qmd", "c.qmd"]
        result = filter_nav_missing(nav, ["a.qmd", "c.qmd"])
        assert result == ["a.qmd", "c.qmd"]

    def test_keeps_all_when_all_exist(self):
        nav = ["a.qmd", "b.qmd", "c.qmd"]
        result = filter_nav_missing(nav, ["a.qmd", "b.qmd", "c.qmd"])
        assert result == ["a.qmd", "b.qmd", "c.qmd"]

    def test_removes_missing_dict_file_entry(self):
        nav = [
            {"file": "a.qmd"},
            {"file": "b.qmd", "text": "Page B"},
            {"file": "c.qmd"},
        ]
        result = filter_nav_missing(nav, ["a.qmd", "c.qmd"])
        assert len(result) == 2
        assert result[0] == {"file": "a.qmd"}
        assert result[1] == {"file": "c.qmd"}

    def test_removes_missing_dict_href_entry(self):
        nav = [{"href": "a.qmd"}, {"href": "gone.qmd"}]
        result = filter_nav_missing(nav, ["a.qmd"])
        assert result == [{"href": "a.qmd"}]

    def test_removes_from_nested_section(self):
        nav = [
            {
                "section": "My Section",
                "contents": ["a.qmd", "b.qmd", "c.qmd"],
            }
        ]
        result = filter_nav_missing(nav, ["a.qmd", "c.qmd"])
        assert result == [{"section": "My Section", "contents": ["a.qmd", "c.qmd"]}]

    def test_preserves_non_source_entries(self):
        """Non-source strings (section titles, URLs, etc.) are kept."""
        nav = ["a.qmd", "https://example.com", "b.qmd"]
        result = filter_nav_missing(nav, ["a.qmd"])
        assert result == ["a.qmd", "https://example.com"]

    def test_none_passthrough(self):
        assert filter_nav_missing(None, ["a.qmd"]) is None

    def test_removes_section_with_all_contents_gone(self):
        nav = [
            "a.qmd",
            {"section": "Empty", "contents": ["gone.qmd"]},
            "c.qmd",
        ]
        result = filter_nav_missing(nav, ["a.qmd", "c.qmd"])
        # Section kept (has title) but contents emptied
        assert len(result) == 3
        assert result[1] == {"section": "Empty"}


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
        docs = self._make_docs(
            tmp_path,
            [
                "page.qmd",
                ".quarto/something.qmd",
                "_site/output.qmd",
            ],
        )
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
            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "demo", "branch": "graft/demo", "collar": "main"},
                ],
            )
            self._setup_manifest(
                tmp_path,
                {
                    "graft/demo": {
                        "title": "Demo",
                        "branch_key": "demo",
                        "exported": ["page.qmd"],
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": ["page.qmd"],
                    },
                },
            )

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
            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "demo", "branch": "graft/demo", "collar": "main"},
                ],
            )
            self._setup_manifest(
                tmp_path,
                {
                    "graft/demo": {
                        "title": "Demo",
                        "branch_key": "demo",
                        "exported": ["ch.qmd"],
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": ["ch.qmd"],
                    },
                },
            )

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
            self._setup_manifest(
                tmp_path,
                {
                    "old/branch": {
                        "title": "Old",
                        "branch_key": "old",
                        "exported": ["x.qmd"],
                        "last_checked": "2026-01-01T00:00:00Z",
                    },
                },
            )

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
            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "pre", "branch": "graft/pre", "collar": "main"},
                ],
            )
            self._setup_manifest(
                tmp_path,
                {
                    "graft/pre": {
                        "title": "Pre",
                        "branch_key": "pre",
                        "exported": ["index.html"],
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": ["index.qmd"],
                        "prerendered": True,
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            apply_manifest()

            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            resources = result.get("project", {}).get("resources", [])
            assert f"{GRAFTS_BUILD_RELPATH}/pre/**" in resources
        finally:
            constants._root_override = None

    def test_cached_href_entries_get_text_field(self, tmp_path):
        """Cached pages using href: dict format must get a text field added,
        otherwise quarto drops them from the sidebar."""
        import quarto_graft.constants as constants

        try:
            self._setup_project(tmp_path, "website")
            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "demo", "branch": "graft/demo", "collar": "main"},
                ],
            )
            self._setup_manifest(
                tmp_path,
                {
                    "graft/demo": {
                        "title": "Demo",
                        "branch_key": "demo",
                        "exported": ["page.qmd", "notebook.ipynb"],
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": [
                            {"href": "page.qmd"},
                            {"href": "notebook.ipynb"},
                        ],
                        "cached_pages": ["page.qmd", "notebook.ipynb"],
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            apply_manifest()

            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            contents = result["website"]["sidebar"]["contents"]

            autogen = contents[2]
            assert autogen.get("_autogen_branch") == "graft/demo"
            items = autogen["contents"]
            assert len(items) == 2

            # Both should have text and href with .html extension
            assert items[0]["href"] == f"{GRAFTS_BUILD_RELPATH}/demo/page.html"
            assert items[0]["text"] == "Page"
            assert items[1]["href"] == f"{GRAFTS_BUILD_RELPATH}/demo/notebook.html"
            assert items[1]["text"] == "Notebook"
        finally:
            constants._root_override = None

    def test_cached_href_preserves_existing_text(self, tmp_path):
        """Cached pages that already have a text field should keep it."""
        import quarto_graft.constants as constants

        try:
            self._setup_project(tmp_path, "website")
            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "demo", "branch": "graft/demo", "collar": "main"},
                ],
            )
            self._setup_manifest(
                tmp_path,
                {
                    "graft/demo": {
                        "title": "Demo",
                        "branch_key": "demo",
                        "exported": ["page.qmd"],
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": [
                            {"text": "Custom Title", "href": "page.qmd"},
                        ],
                        "cached_pages": ["page.qmd"],
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            apply_manifest()

            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            contents = result["website"]["sidebar"]["contents"]

            autogen = contents[2]
            items = autogen["contents"]
            assert items[0]["text"] == "Custom Title"
            assert items[0]["href"] == f"{GRAFTS_BUILD_RELPATH}/demo/page.html"
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

    def test_cached_page_removed_then_sidebar_updated(self, tmp_path):
        """When a page is removed from a graft, the manifest structure and
        cached_pages should be updated so the sidebar no longer references it.

        Simulates: page.qmd and extra.qmd both cached → extra.qmd removed →
        apply_manifest should only produce a sidebar entry for page.qmd."""
        import quarto_graft.constants as constants

        try:
            self._setup_project(tmp_path, "website")
            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "demo", "branch": "graft/demo", "collar": "main"},
                ],
            )
            # Manifest as it would look AFTER the deleted page's rebuild:
            # structure no longer lists extra.qmd, cached_pages only has page.qmd
            self._setup_manifest(
                tmp_path,
                {
                    "graft/demo": {
                        "title": "Demo",
                        "branch_key": "demo",
                        "exported": ["page.qmd"],
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": ["page.qmd"],
                        "cached_pages": ["page.qmd"],
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            apply_manifest()

            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            contents = result["website"]["sidebar"]["contents"]

            autogen = contents[2]
            assert autogen.get("_autogen_branch") == "graft/demo"
            items = autogen["contents"]
            # Only page.qmd should appear — extra.qmd must NOT be present
            assert len(items) == 1
            assert items[0]["href"] == f"{GRAFTS_BUILD_RELPATH}/demo/page.html"
        finally:
            constants._root_override = None

    def test_stale_structure_references_deleted_cached_page(self, tmp_path):
        """Edge case: structure still lists a page that was removed from
        cached_pages (e.g. graft config wasn't updated). The page should
        appear as a file reference (not href) since it's not in cached_pages,
        pointing to a path that won't exist — exposing the inconsistency."""
        import quarto_graft.constants as constants

        try:
            self._setup_project(tmp_path, "website")
            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "demo", "branch": "graft/demo", "collar": "main"},
                ],
            )
            # Structure still mentions extra.qmd but it's NOT in cached_pages
            self._setup_manifest(
                tmp_path,
                {
                    "graft/demo": {
                        "title": "Demo",
                        "branch_key": "demo",
                        "exported": ["page.qmd"],
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": ["page.qmd", "extra.qmd"],
                        "cached_pages": ["page.qmd"],
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            apply_manifest()

            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            contents = result["website"]["sidebar"]["contents"]

            autogen = contents[2]
            items = autogen["contents"]
            assert len(items) == 2
            # page.qmd is cached → href with .html
            assert items[0]["href"] == f"{GRAFTS_BUILD_RELPATH}/demo/page.html"
            # extra.qmd is NOT cached → file reference dict with clean text
            assert items[1] == {"text": "Extra", "file": f"{GRAFTS_BUILD_RELPATH}/demo/extra.qmd"}
        finally:
            constants._root_override = None

    def test_deep_nesting_preserved_with_collar_at_level2(self, tmp_path):
        """5 levels of nesting in the graft, collar at level 2 in trunk.
        All nesting levels must be preserved after apply_manifest."""
        import quarto_graft.constants as constants

        try:
            # Trunk with collar at level 2
            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            constants._root_override = tmp_path

            quarto_yaml = {
                "project": {"type": "website"},
                "website": {
                    "sidebar": {
                        "contents": [
                            "index.qmd",
                            {
                                "section": "Trunk L1",
                                "contents": [
                                    {
                                        "section": "Trunk L2",
                                        "contents": [
                                            {"_GRAFT_COLLAR": "main"},
                                        ],
                                    },
                                ],
                            },
                        ]
                    }
                },
            }
            qf = tmp_path / "_quarto.yaml"
            with open(qf, "w") as f:
                yaml_loader.dump(quarto_yaml, f)

            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "deep", "branch": "graft/deep", "collar": "main"},
                ],
            )

            # Graft structure with 5 levels of section nesting
            graft_structure = [
                {
                    "section": "G-L1",
                    "contents": [
                        {
                            "section": "G-L2",
                            "contents": [
                                {
                                    "section": "G-L3",
                                    "contents": [
                                        {
                                            "section": "G-L4",
                                            "contents": [
                                                {
                                                    "section": "G-L5",
                                                    "contents": [
                                                        "deep-page.qmd",
                                                    ],
                                                },
                                            ],
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ]

            self._setup_manifest(
                tmp_path,
                {
                    "graft/deep": {
                        "title": "Deep Graft",
                        "branch_key": "deep",
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": graft_structure,
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            apply_manifest()

            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            contents = result["website"]["sidebar"]["contents"]

            # Navigate: index.qmd, Trunk L1 > Trunk L2 > collar > autogen
            trunk_l1 = contents[1]
            assert trunk_l1["section"] == "Trunk L1"
            trunk_l2 = trunk_l1["contents"][0]
            assert trunk_l2["section"] == "Trunk L2"

            # After the collar marker, the graft should be injected
            autogen = trunk_l2["contents"][1]
            assert autogen["_autogen_branch"] == "graft/deep"
            assert autogen["section"] == "Deep Graft"

            # Now verify all 5 levels of graft nesting are preserved
            g1 = autogen["contents"][0]
            assert g1["section"] == "G-L1"
            g2 = g1["contents"][0]
            assert g2["section"] == "G-L2"
            g3 = g2["contents"][0]
            assert g3["section"] == "G-L3"
            g4 = g3["contents"][0]
            assert g4["section"] == "G-L4"
            g5 = g4["contents"][0]
            assert g5["section"] == "G-L5"
            assert g5["contents"] == [{"text": "Deep Page", "file": f"{GRAFTS_BUILD_RELPATH}/deep/deep-page.qmd"}]
        finally:
            constants._root_override = None

    def test_deep_nesting_preserved_with_cached_pages(self, tmp_path):
        """5 levels of nesting in the graft with cached pages.
        Caching must not flatten the nested structure."""
        import quarto_graft.constants as constants

        try:
            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            constants._root_override = tmp_path

            quarto_yaml = {
                "project": {"type": "website"},
                "website": {
                    "sidebar": {
                        "contents": [
                            "index.qmd",
                            {
                                "section": "Trunk L1",
                                "contents": [
                                    {
                                        "section": "Trunk L2",
                                        "contents": [
                                            {"_GRAFT_COLLAR": "main"},
                                        ],
                                    },
                                ],
                            },
                        ]
                    }
                },
            }
            qf = tmp_path / "_quarto.yaml"
            with open(qf, "w") as f:
                yaml_loader.dump(quarto_yaml, f)

            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "deep", "branch": "graft/deep", "collar": "main"},
                ],
            )

            # Graft structure with 5 levels, pages at multiple levels
            graft_structure = [
                {
                    "section": "G-L1",
                    "contents": [
                        "l1-page.qmd",
                        {
                            "section": "G-L2",
                            "contents": [
                                "l2-page.qmd",
                                {
                                    "section": "G-L3",
                                    "contents": [
                                        "l3-page.qmd",
                                        {
                                            "section": "G-L4",
                                            "contents": [
                                                "l4-page.qmd",
                                                {
                                                    "section": "G-L5",
                                                    "contents": [
                                                        "l5-page.qmd",
                                                    ],
                                                },
                                            ],
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ]

            self._setup_manifest(
                tmp_path,
                {
                    "graft/deep": {
                        "title": "Deep Graft",
                        "branch_key": "deep",
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": graft_structure,
                        "cached_pages": ["l1-page.qmd", "l3-page.qmd", "l5-page.qmd"],
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            apply_manifest()

            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            contents = result["website"]["sidebar"]["contents"]

            trunk_l1 = contents[1]
            trunk_l2 = trunk_l1["contents"][0]
            autogen = trunk_l2["contents"][1]
            assert autogen["_autogen_branch"] == "graft/deep"

            # Verify all 5 levels preserved
            g1 = autogen["contents"][0]
            assert g1["section"] == "G-L1"

            # l1-page.qmd is cached → should be href dict
            g1_items = g1["contents"]
            assert isinstance(g1_items[0], dict)
            assert "href" in g1_items[0]
            assert g1_items[0]["href"] == f"{GRAFTS_BUILD_RELPATH}/deep/l1-page.html"

            g2 = g1_items[1]
            assert g2["section"] == "G-L2"

            # l2-page.qmd is NOT cached → should be a file dict with text
            g2_items = g2["contents"]
            assert g2_items[0] == {"text": "L2 Page", "file": f"{GRAFTS_BUILD_RELPATH}/deep/l2-page.qmd"}

            g3 = g2_items[1]
            assert g3["section"] == "G-L3"

            # l3-page.qmd is cached
            g3_items = g3["contents"]
            assert isinstance(g3_items[0], dict)
            assert g3_items[0]["href"] == f"{GRAFTS_BUILD_RELPATH}/deep/l3-page.html"

            g4 = g3_items[1]
            assert g4["section"] == "G-L4"

            g4_items = g4["contents"]
            assert g4_items[0] == {"text": "L4 Page", "file": f"{GRAFTS_BUILD_RELPATH}/deep/l4-page.qmd"}

            g5 = g4_items[1]
            assert g5["section"] == "G-L5"

            # l5-page.qmd is cached
            g5_items = g5["contents"]
            assert isinstance(g5_items[0], dict)
            assert g5_items[0]["href"] == f"{GRAFTS_BUILD_RELPATH}/deep/l5-page.html"
        finally:
            constants._root_override = None

    def test_deep_nesting_idempotent_with_caching(self, tmp_path):
        """Running apply_manifest twice must not flatten the nesting."""
        import quarto_graft.constants as constants

        try:
            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            constants._root_override = tmp_path

            quarto_yaml = {
                "project": {"type": "website"},
                "website": {
                    "sidebar": {
                        "contents": [
                            "index.qmd",
                            {
                                "section": "Trunk L1",
                                "contents": [
                                    {
                                        "section": "Trunk L2",
                                        "contents": [
                                            {"_GRAFT_COLLAR": "main"},
                                        ],
                                    },
                                ],
                            },
                        ]
                    }
                },
            }
            qf = tmp_path / "_quarto.yaml"
            with open(qf, "w") as f:
                yaml_loader.dump(quarto_yaml, f)

            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "deep", "branch": "graft/deep", "collar": "main"},
                ],
            )

            graft_structure = [
                {
                    "section": "G-L1",
                    "contents": [
                        {
                            "section": "G-L2",
                            "contents": [
                                {
                                    "section": "G-L3",
                                    "contents": [
                                        {
                                            "section": "G-L4",
                                            "contents": [
                                                {
                                                    "section": "G-L5",
                                                    "contents": [
                                                        "deep-page.qmd",
                                                    ],
                                                },
                                            ],
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ]

            self._setup_manifest(
                tmp_path,
                {
                    "graft/deep": {
                        "title": "Deep Graft",
                        "branch_key": "deep",
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": graft_structure,
                        "cached_pages": ["deep-page.qmd"],
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            # First apply
            apply_manifest()
            result1 = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))

            # Second apply (re-processes the already-modified YAML)
            apply_manifest()
            result2 = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))

            # Verify nesting preserved after both applies
            for result in [result1, result2]:
                contents = result["website"]["sidebar"]["contents"]
                trunk_l1 = contents[1]
                trunk_l2 = trunk_l1["contents"][0]
                autogen = trunk_l2["contents"][1]
                assert autogen["_autogen_branch"] == "graft/deep"

                g1 = autogen["contents"][0]
                assert g1["section"] == "G-L1"
                g2 = g1["contents"][0]
                assert g2["section"] == "G-L2"
                g3 = g2["contents"][0]
                assert g3["section"] == "G-L3"
                g4 = g3["contents"][0]
                assert g4["section"] == "G-L4"
                g5 = g4["contents"][0]
                assert g5["section"] == "G-L5"
        finally:
            constants._root_override = None

    def test_book_deep_nesting_preserved(self, tmp_path):
        """5 levels of nesting in a book-mode graft must be preserved."""
        import quarto_graft.constants as constants

        try:
            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            constants._root_override = tmp_path

            quarto_yaml = {
                "project": {"type": "book"},
                "book": {
                    "chapters": [
                        "index.qmd",
                        {
                            "part": "Trunk P1",
                            "chapters": [
                                {"_GRAFT_COLLAR": "main"},
                            ],
                        },
                    ]
                },
            }
            qf = tmp_path / "_quarto.yaml"
            with open(qf, "w") as f:
                yaml_loader.dump(quarto_yaml, f)

            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "deep", "branch": "graft/deep", "collar": "main"},
                ],
            )

            graft_structure = [
                {
                    "part": "G-P1",
                    "chapters": [
                        {
                            "part": "G-P2",
                            "chapters": [
                                {
                                    "part": "G-P3",
                                    "chapters": [
                                        {
                                            "part": "G-P4",
                                            "chapters": [
                                                {
                                                    "part": "G-P5",
                                                    "chapters": [
                                                        "deep-ch.qmd",
                                                    ],
                                                },
                                            ],
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ]

            self._setup_manifest(
                tmp_path,
                {
                    "graft/deep": {
                        "title": "Deep Graft",
                        "branch_key": "deep",
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": graft_structure,
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            apply_manifest()

            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            chapters = result["book"]["chapters"]

            trunk_p1 = chapters[1]
            assert trunk_p1["part"] == "Trunk P1"
            autogen = trunk_p1["chapters"][1]
            assert autogen["_autogen_branch"] == "graft/deep"
            assert autogen["part"] == "Deep Graft"

            g1 = autogen["chapters"][0]
            assert g1["part"] == "G-P1"
            g2 = g1["chapters"][0]
            assert g2["part"] == "G-P2"
            g3 = g2["chapters"][0]
            assert g3["part"] == "G-P3"
            g4 = g3["chapters"][0]
            assert g4["part"] == "G-P4"
            g5 = g4["chapters"][0]
            assert g5["part"] == "G-P5"
            assert g5["chapters"] == [{"text": "Deep Ch", "file": f"{GRAFTS_BUILD_RELPATH}/deep/deep-ch.qmd"}]
        finally:
            constants._root_override = None

    def test_auto_structure_with_deep_nesting_and_cache(self, tmp_path):
        """Graft using 'auto' with 5-level deep files, collar at level 2,
        all pages cached. The directory hierarchy must be preserved as sections."""
        import quarto_graft.constants as constants

        try:
            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            constants._root_override = tmp_path

            quarto_yaml = {
                "project": {"type": "website"},
                "website": {
                    "sidebar": {
                        "contents": [
                            "index.qmd",
                            {
                                "section": "Trunk L1",
                                "contents": [
                                    {
                                        "section": "Trunk L2",
                                        "contents": [
                                            {"_GRAFT_COLLAR": "main"},
                                        ],
                                    },
                                ],
                            },
                        ]
                    }
                },
            }
            qf = tmp_path / "_quarto.yaml"
            with open(qf, "w") as f:
                yaml_loader.dump(quarto_yaml, f)

            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "deep", "branch": "graft/deep", "collar": "main"},
                ],
            )

            # The graft used 'auto' — expand_nav_globs should have already
            # expanded it into a hierarchy before storing in the manifest.
            # Simulate what _export_from_worktree would produce after expansion:
            from quarto_graft.quarto_config import expand_nav_globs

            auto_expanded = expand_nav_globs(
                "auto",
                [
                    "a/x.qmd",
                    "a/b/c/1.qmd",
                    "a/b/c/2.qmd",
                    "a/b/c/3.qmd",
                ],
            )

            all_pages = ["a/x.qmd", "a/b/c/1.qmd", "a/b/c/2.qmd", "a/b/c/3.qmd"]
            self._setup_manifest(
                tmp_path,
                {
                    "graft/deep": {
                        "title": "Deep Graft",
                        "branch_key": "deep",
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": auto_expanded,
                        "cached_pages": all_pages,
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            apply_manifest()

            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            contents = result["website"]["sidebar"]["contents"]

            trunk_l1 = contents[1]
            trunk_l2 = trunk_l1["contents"][0]
            autogen = trunk_l2["contents"][1]
            assert autogen["_autogen_branch"] == "graft/deep"

            # Verify hierarchy: Graft > A > x.qmd + B > C > 1,2,3.qmd
            a_section = autogen["contents"][0]
            assert a_section["section"] == "A"

            # a/x.qmd is cached → href dict
            a_items = a_section["contents"]
            assert isinstance(a_items[0], dict)
            assert a_items[0]["href"] == f"{GRAFTS_BUILD_RELPATH}/deep/a/x.html"

            b_section = a_items[1]
            assert b_section["section"] == "B"

            c_section = b_section["contents"][0]
            assert c_section["section"] == "C"

            # All c-level files are cached → href dicts
            c_items = c_section["contents"]
            assert len(c_items) == 3
            hrefs = sorted(item["href"] for item in c_items)
            assert hrefs == [
                f"{GRAFTS_BUILD_RELPATH}/deep/a/b/c/1.html",
                f"{GRAFTS_BUILD_RELPATH}/deep/a/b/c/2.html",
                f"{GRAFTS_BUILD_RELPATH}/deep/a/b/c/3.html",
            ]
        finally:
            constants._root_override = None

    def test_all_cached_pages_removed_cleans_resources(self, tmp_path):
        """When all cached pages are removed from a graft, the project.resources
        entry for that graft should be cleaned up."""
        import quarto_graft.constants as constants

        try:
            self._setup_project(tmp_path, "website")
            self._setup_grafts_config(
                tmp_path,
                [
                    {"name": "demo", "branch": "graft/demo", "collar": "main"},
                ],
            )
            # No cached pages — all were removed
            self._setup_manifest(
                tmp_path,
                {
                    "graft/demo": {
                        "title": "Demo",
                        "branch_key": "demo",
                        "exported": ["page.qmd"],
                        "last_checked": "2026-01-01T00:00:00Z",
                        "structure": ["page.qmd"],
                    },
                },
            )

            from quarto_graft.quarto_config import apply_manifest

            apply_manifest()

            from quarto_graft.yaml_utils import get_yaml_loader

            yaml_loader = get_yaml_loader()
            result = yaml_loader.load((tmp_path / "_quarto.yaml").read_text(encoding="utf-8"))
            resources = result.get("project", {}).get("resources", [])
            # No cached pages → no resource glob for this graft
            assert not any("demo" in r for r in resources)
        finally:
            constants._root_override = None
