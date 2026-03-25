"""Tests for archive module (pre-render functionality)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_graft_project(tmp_path):
    """Set up a minimal graft project structure for archive tests."""
    # Create _quarto.yaml
    (tmp_path / "_quarto.yaml").write_text(
        "project:\n  type: website\n  output-dir: _site\n\n"
        "website:\n  title: Test Graft\n  sidebar:\n    contents:\n"
        "      - docs/index.qmd\n      - docs/chapter1.qmd\n"
    )

    # Create source files
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.qmd").write_text("---\ntitle: Index\n---\nHello")
    (docs_dir / "chapter1.qmd").write_text("---\ntitle: Chapter 1\n---\nContent")

    return tmp_path


@pytest.fixture
def mock_site_output(mock_graft_project):
    """Create a mock _site/ output as if quarto render had run."""
    site_dir = mock_graft_project / "_site"
    site_dir.mkdir()
    docs_out = site_dir / "docs"
    docs_out.mkdir()
    (docs_out / "index.html").write_text("<html><body>Index</body></html>")
    (docs_out / "chapter1.html").write_text("<html><body>Chapter 1</body></html>")

    # Create site_libs
    libs_dir = site_dir / "site_libs"
    libs_dir.mkdir()
    (libs_dir / "bootstrap.min.css").write_text("/* bootstrap */")

    return mock_graft_project


class TestArchiveGraft:
    def test_pre_renders_and_copies_site_output(self, mock_site_output):
        from quarto_graft.archive import archive_graft

        project = mock_site_output

        # Mock subprocess.run to simulate quarto render success
        # (site output is already created by fixture)
        with patch("quarto_graft.archive.subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
            result = archive_graft(project_dir=project)

        assert result == project / "_prerendered"
        assert (project / "_prerendered" / "docs" / "index.html").exists()
        assert (project / "_prerendered" / "docs" / "chapter1.html").exists()
        assert (project / "_prerendered" / "site_libs" / "bootstrap.min.css").exists()

    def test_creates_prerender_manifest(self, mock_site_output):
        from quarto_graft.archive import archive_graft

        project = mock_site_output

        with patch("quarto_graft.archive.subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
            archive_graft(project_dir=project)

        manifest_path = project / "_prerendered" / ".graft-prerender.json"
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text())
        assert "prerendered_at" in manifest
        assert "files" in manifest
        assert isinstance(manifest["files"], list)
        assert len(manifest["files"]) > 0
        assert "docs/index.html" in manifest["files"]

    def test_raises_if_no_quarto_yaml(self, tmp_path):
        from quarto_graft.archive import archive_graft

        with pytest.raises(RuntimeError, match="_quarto.yaml"):
            archive_graft(project_dir=tmp_path)

    def test_raises_if_quarto_render_fails(self, mock_graft_project):
        from quarto_graft.archive import archive_graft

        with patch("quarto_graft.archive.subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 1, "stderr": "render error", "stdout": ""})()
            with pytest.raises(RuntimeError, match="quarto render failed"):
                archive_graft(project_dir=mock_graft_project)

    def test_raises_if_output_empty(self, mock_graft_project):
        from quarto_graft.archive import archive_graft

        # Create empty _site/
        (mock_graft_project / "_site").mkdir()

        with patch("quarto_graft.archive.subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
            with pytest.raises(RuntimeError, match="empty"):
                archive_graft(project_dir=mock_graft_project)

    def test_replaces_stale_prerendered(self, mock_site_output):
        from quarto_graft.archive import archive_graft

        project = mock_site_output

        # Create stale _prerendered/
        stale = project / "_prerendered"
        stale.mkdir()
        (stale / "old_file.html").write_text("stale")

        with patch("quarto_graft.archive.subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
            archive_graft(project_dir=project)

        # Stale file should be gone, new content present
        assert not (project / "_prerendered" / "old_file.html").exists()
        assert (project / "_prerendered" / "docs" / "index.html").exists()

    def test_reads_custom_output_dir(self, tmp_path):
        from quarto_graft.archive import archive_graft

        # Create _quarto.yaml with custom output-dir
        (tmp_path / "_quarto.yaml").write_text("project:\n  type: website\n  output-dir: _output\n")

        # Create output in custom location
        output_dir = tmp_path / "_output"
        output_dir.mkdir()
        (output_dir / "index.html").write_text("<html>test</html>")

        with patch("quarto_graft.archive.subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
            result = archive_graft(project_dir=tmp_path)

        assert (result / "index.html").exists()


class TestRestoreGraft:
    def test_removes_prerendered_dir(self, mock_graft_project):
        from quarto_graft.archive import restore_graft

        # Create _prerendered/
        prerender = mock_graft_project / "_prerendered"
        prerender.mkdir()
        (prerender / "index.html").write_text("<html>test</html>")

        result = restore_graft(project_dir=mock_graft_project)

        assert result is True
        assert not prerender.exists()

    def test_returns_false_if_nothing_to_remove(self, mock_graft_project):
        from quarto_graft.archive import restore_graft

        result = restore_graft(project_dir=mock_graft_project)
        assert result is False

    def test_raises_if_no_quarto_yaml(self, tmp_path):
        from quarto_graft.archive import restore_graft

        with pytest.raises(RuntimeError, match="_quarto.yaml"):
            restore_graft(project_dir=tmp_path)


class TestIsPrerendered:
    def test_true_when_valid_manifest(self, tmp_path):
        from quarto_graft.archive import is_prerendered

        prerender = tmp_path / "_prerendered"
        prerender.mkdir()
        manifest = {"prerendered_at": "2025-01-01T00:00:00Z", "files": []}
        (prerender / ".graft-prerender.json").write_text(json.dumps(manifest))

        assert is_prerendered(tmp_path) is True

    def test_false_when_no_directory(self, tmp_path):
        from quarto_graft.archive import is_prerendered

        assert is_prerendered(tmp_path) is False

    def test_false_when_no_manifest(self, tmp_path):
        from quarto_graft.archive import is_prerendered

        prerender = tmp_path / "_prerendered"
        prerender.mkdir()
        (prerender / "index.html").write_text("<html>test</html>")

        assert is_prerendered(tmp_path) is False

    def test_false_when_invalid_json(self, tmp_path):
        from quarto_graft.archive import is_prerendered

        prerender = tmp_path / "_prerendered"
        prerender.mkdir()
        (prerender / ".graft-prerender.json").write_text("not json")

        assert is_prerendered(tmp_path) is False

    def test_false_when_missing_key(self, tmp_path):
        from quarto_graft.archive import is_prerendered

        prerender = tmp_path / "_prerendered"
        prerender.mkdir()
        (prerender / ".graft-prerender.json").write_text('{"files": []}')

        assert is_prerendered(tmp_path) is False


class TestLoadPrerenderManifest:
    def test_loads_valid_manifest(self, tmp_path):
        from quarto_graft.archive import load_prerender_manifest

        prerender = tmp_path / "_prerendered"
        prerender.mkdir()
        manifest_data = {
            "prerendered_at": "2025-01-01T00:00:00Z",
            "source_commit": "abc1234",
            "files": ["index.html"],
        }
        (prerender / ".graft-prerender.json").write_text(json.dumps(manifest_data))

        result = load_prerender_manifest(tmp_path)
        assert result == manifest_data

    def test_returns_none_when_missing(self, tmp_path):
        from quarto_graft.archive import load_prerender_manifest

        assert load_prerender_manifest(tmp_path) is None

    def test_returns_none_for_invalid_json(self, tmp_path):
        from quarto_graft.archive import load_prerender_manifest

        prerender = tmp_path / "_prerendered"
        prerender.mkdir()
        (prerender / ".graft-prerender.json").write_text("not json")

        assert load_prerender_manifest(tmp_path) is None


class TestArchiveRestoreRoundTrip:
    def test_archive_then_restore(self, mock_site_output):
        from quarto_graft.archive import archive_graft, is_prerendered, restore_graft

        project = mock_site_output

        with patch("quarto_graft.archive.subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
            archive_graft(project_dir=project)

        assert is_prerendered(project) is True

        restore_graft(project_dir=project)

        assert is_prerendered(project) is False
        assert not (project / "_prerendered").exists()
