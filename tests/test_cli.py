"""Tests for cli module."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from quarto_graft.build import BuildResult
from quarto_graft.cli import (
    TemplateValidator,
    _configure_logging,
    _load_build_state,
    _write_build_state,
    require_trunk,
)

# ---------------------------------------------------------------------------
# _configure_logging
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    """Test logging configuration via mocking basicConfig."""

    def test_default_level(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("logging.basicConfig") as mock_bc:
                _configure_logging()
        mock_bc.assert_called_once_with(level=logging.INFO, format="%(levelname)s %(message)s")

    def test_explicit_level(self):
        with patch("logging.basicConfig") as mock_bc:
            _configure_logging("DEBUG")
        mock_bc.assert_called_once_with(level=logging.DEBUG, format="%(levelname)s %(message)s")

    def test_env_override(self):
        with patch.dict("os.environ", {"QBB_LOG_LEVEL": "WARNING"}):
            with patch("logging.basicConfig") as mock_bc:
                _configure_logging()
        mock_bc.assert_called_once_with(level=logging.WARNING, format="%(levelname)s %(message)s")

    def test_case_insensitive(self):
        with patch("logging.basicConfig") as mock_bc:
            _configure_logging("debug")
        mock_bc.assert_called_once_with(level=logging.DEBUG, format="%(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# require_trunk
# ---------------------------------------------------------------------------


class TestRequireTrunk:
    def test_passes_when_config_exists(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            constants._root_override = tmp_path
            (tmp_path / "grafts.yaml").write_text("branches: []")
            require_trunk()  # should not raise
        finally:
            constants._root_override = None

    def test_exits_when_config_missing(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            constants._root_override = tmp_path
            with pytest.raises((SystemExit, Exception)):
                require_trunk()
        finally:
            constants._root_override = None


# ---------------------------------------------------------------------------
# _write_build_state / _load_build_state
# ---------------------------------------------------------------------------


class TestBuildState:
    def test_roundtrip(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            constants._root_override = tmp_path
            (tmp_path / ".grafts-cache").mkdir(parents=True, exist_ok=True)

            results = {
                "graft/demo": BuildResult(
                    branch="graft/demo",
                    branch_key="demo",
                    title="Demo",
                    status="ok",
                    head_sha="abc",
                    last_good_sha="abc",
                    built_at="2026-01-01T00:00:00Z",
                    exported_relpaths=["page.qmd"],
                    exported_dest_paths=[],
                    page_hashes={"page.qmd": "hash123"},
                    cached_pages=["page.qmd"],
                ),
            }

            _write_build_state(results, [])
            state = _load_build_state()

            assert "demo" in state
            assert state["demo"]["page_hashes"] == {"page.qmd": "hash123"}
            assert state["demo"]["cached_pages"] == ["page.qmd"]
        finally:
            constants._root_override = None

    def test_skips_results_without_page_hashes(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            constants._root_override = tmp_path
            (tmp_path / ".grafts-cache").mkdir(parents=True, exist_ok=True)

            results = {
                "graft/demo": BuildResult(
                    branch="graft/demo",
                    branch_key="demo",
                    title="Demo",
                    status="broken",
                    head_sha=None,
                    last_good_sha=None,
                    built_at="2026-01-01T00:00:00Z",
                    exported_relpaths=[],
                    exported_dest_paths=[],
                    page_hashes=None,
                ),
            }

            _write_build_state(results, [])
            state = _load_build_state()
            assert state == {}
        finally:
            constants._root_override = None

    def test_load_returns_empty_when_no_file(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            constants._root_override = tmp_path
            state = _load_build_state()
            assert state == {}
        finally:
            constants._root_override = None

    def test_load_returns_empty_on_corrupt_json(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            constants._root_override = tmp_path
            (tmp_path / ".grafts-cache").mkdir(parents=True, exist_ok=True)
            (tmp_path / ".grafts-cache" / "build-state.json").write_text(
                "not valid json{{{", encoding="utf-8"
            )
            state = _load_build_state()
            assert state == {}
        finally:
            constants._root_override = None


# ---------------------------------------------------------------------------
# TemplateValidator
# ---------------------------------------------------------------------------


class TestTemplateValidator:
    def test_discover_builtin_templates(self, tmp_path):
        builtin_dir = tmp_path / "templates"
        builtin_dir.mkdir()
        (builtin_dir / "markdown").mkdir()
        (builtin_dir / "notebook").mkdir()
        (builtin_dir / ".hidden").mkdir()  # should be included (doesn't start with "with-")

        validator = TemplateValidator(builtin_dir, "trunk")
        templates = validator.discover_templates()

        assert "markdown" in templates
        assert "notebook" in templates

    def test_excludes_with_addon_dirs(self, tmp_path):
        builtin_dir = tmp_path / "templates"
        builtin_dir.mkdir()
        (builtin_dir / "markdown").mkdir()
        (builtin_dir / "with-addon").mkdir()  # should be excluded

        validator = TemplateValidator(builtin_dir, "trunk")
        templates = validator.discover_templates()

        assert "markdown" in templates
        assert "with-addon" not in templates

    def test_empty_builtin_dir(self, tmp_path):
        builtin_dir = tmp_path / "templates"
        builtin_dir.mkdir()

        validator = TemplateValidator(builtin_dir, "trunk")
        templates = validator.discover_templates()
        assert templates == {}

    def test_nonexistent_builtin_dir(self, tmp_path):
        validator = TemplateValidator(tmp_path / "nonexistent", "trunk")
        templates = validator.discover_templates()
        assert templates == {}

    def test_validate_template_exact_match(self, tmp_path):
        builtin_dir = tmp_path / "templates"
        builtin_dir.mkdir()
        (builtin_dir / "markdown").mkdir()

        validator = TemplateValidator(builtin_dir, "trunk")
        name, path = validator.validate_template("markdown")
        assert name == "markdown"
        assert path == builtin_dir / "markdown"

    def test_validate_template_not_found(self, tmp_path):
        builtin_dir = tmp_path / "templates"
        builtin_dir.mkdir()

        validator = TemplateValidator(builtin_dir, "trunk")
        with pytest.raises((SystemExit, Exception)):
            validator.validate_template("nonexistent")


# ---------------------------------------------------------------------------
# _discover_grafts / _git_local_branches / _yaml_branches
# ---------------------------------------------------------------------------


class TestDiscoverGrafts:
    def test_discover_grafts_combines_sources(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            constants._root_override = tmp_path

            # Set up grafts.yaml
            from quarto_graft.yaml_utils import get_yaml_loader
            yaml_loader = get_yaml_loader()
            gf = tmp_path / "grafts.yaml"
            data = {"branches": [
                {"name": "demo", "branch": "graft/demo", "collar": "main"},
            ]}
            with open(gf, "w") as f:
                yaml_loader.dump(data, f)

            # Set up grafts.lock
            mf = tmp_path / "grafts.lock"
            mf.write_text(json.dumps({"graft/lock-only": {"title": "Lock"}}))

            from quarto_graft.cli import _discover_grafts
            with patch("quarto_graft.cli._git_local_branches", return_value={"graft/git-only"}):
                result = _discover_grafts()

            assert "graft/demo" in result["all"]
            assert "graft/git-only" in result["all"]
            assert "graft/lock-only" in result["all"]
            assert "graft/demo" in result["grafts.yaml"]
            assert "graft/git-only" in result["git"]
            assert "graft/lock-only" in result["grafts.lock"]
        finally:
            constants._root_override = None

    def test_filters_protected_branches(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            constants._root_override = tmp_path
            (tmp_path / "grafts.yaml").write_text("branches: []")
            (tmp_path / "grafts.lock").write_text("{}")

            from quarto_graft.cli import _discover_grafts
            with patch("quarto_graft.cli._git_local_branches", return_value={"main", "graft/demo"}):
                result = _discover_grafts()

            assert "main" not in result["all"]
            assert "graft/demo" in result["all"]
        finally:
            constants._root_override = None


class TestGitLocalBranches:
    def test_returns_branches(self):
        from quarto_graft.cli import _git_local_branches
        with patch("quarto_graft.cli.list_local_branches", return_value=["feature", "main"]):
            with patch("quarto_graft.cli.constants") as mock_constants:
                mock_constants.ROOT = Path("/tmp")
                result = _git_local_branches()
        assert result == {"main", "feature"}

    def test_handles_error(self):
        from quarto_graft.cli import _git_local_branches
        with patch("quarto_graft.cli.list_local_branches", side_effect=Exception("fail")):
            with patch("quarto_graft.cli.constants") as mock_constants:
                mock_constants.ROOT = Path("/tmp")
                result = _git_local_branches()
        assert result == set()


class TestYamlBranches:
    def test_returns_branches(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            constants._root_override = tmp_path
            from quarto_graft.yaml_utils import get_yaml_loader
            yaml_loader = get_yaml_loader()
            gf = tmp_path / "grafts.yaml"
            data = {"branches": [
                {"name": "a", "branch": "graft/a", "collar": "main"},
                {"name": "b", "branch": "graft/b", "collar": "main"},
            ]}
            with open(gf, "w") as f:
                yaml_loader.dump(data, f)

            from quarto_graft.cli import _yaml_branches
            result = _yaml_branches()
            assert result == {"graft/a", "graft/b"}
        finally:
            constants._root_override = None

    def test_returns_empty_when_no_file(self, tmp_path):
        import quarto_graft.constants as constants
        try:
            constants._root_override = tmp_path
            from quarto_graft.cli import _yaml_branches
            result = _yaml_branches()
            assert result == set()
        finally:
            constants._root_override = None
