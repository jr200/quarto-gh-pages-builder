"""Tests for archive module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def mock_project(tmp_path, monkeypatch):
    """Set up a minimal project structure for archive tests."""
    import quarto_graft.archive as archive_mod
    import quarto_graft.branches as branches_mod

    # Patch constants used by the archive and branches modules
    monkeypatch.setattr(archive_mod, "GRAFTS_BUILD_DIR", tmp_path / "grafts__")
    monkeypatch.setattr(archive_mod, "GRAFTS_ARCHIVE_DIR", tmp_path / ".grafts-archive")
    monkeypatch.setattr(branches_mod, "GRAFTS_MANIFEST_FILE", tmp_path / "grafts.lock")

    # Create build output
    build_dir = tmp_path / "grafts__" / "my-graft"
    build_dir.mkdir(parents=True)
    (build_dir / "index.qmd").write_text("---\ntitle: Test\n---\nHello")
    (build_dir / "chapter1.qmd").write_text("---\ntitle: Chapter 1\n---\nContent")

    # Create manifest
    manifest = {
        "graft/my-graft": {
            "last_good": "abc1234def5678",
            "last_checked": "2025-01-01T00:00:00Z",
            "title": "My Graft",
            "branch_key": "my-graft",
            "exported": ["index.qmd", "chapter1.qmd"],
            "structure": [{"section": "My Graft", "contents": ["index.qmd", "chapter1.qmd"]}],
        },
        "graft/other": {
            "last_good": "bbb2222",
            "last_checked": "2025-01-01T00:00:00Z",
            "title": "Other Graft",
            "branch_key": "other",
            "exported": ["index.qmd"],
        },
    }
    (tmp_path / "grafts.lock").write_text(json.dumps(manifest))

    return tmp_path


def _load_manifest(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "grafts.lock").read_text())


class TestArchiveGraft:
    def test_archives_build_output(self, mock_project):
        from quarto_graft.archive import archive_graft

        result = archive_graft("graft/my-graft", "my-graft")

        assert result is True
        assert (mock_project / ".grafts-archive" / "my-graft" / "index.qmd").exists()
        assert (mock_project / ".grafts-archive" / "my-graft" / "chapter1.qmd").exists()
        assert not (mock_project / "grafts__" / "my-graft").exists()

    def test_marks_manifest_as_archived(self, mock_project):
        from quarto_graft.archive import archive_graft

        archive_graft("graft/my-graft", "my-graft")
        manifest = _load_manifest(mock_project)

        assert manifest["graft/my-graft"]["archived"] is True
        assert "archived_at" in manifest["graft/my-graft"]

    def test_preserves_manifest_metadata(self, mock_project):
        from quarto_graft.archive import archive_graft

        archive_graft("graft/my-graft", "my-graft")
        manifest = _load_manifest(mock_project)
        entry = manifest["graft/my-graft"]

        assert entry["exported"] == ["index.qmd", "chapter1.qmd"]
        assert entry["title"] == "My Graft"
        assert entry["last_good"] == "abc1234def5678"
        assert entry["structure"] == [{"section": "My Graft", "contents": ["index.qmd", "chapter1.qmd"]}]

    def test_raises_if_already_archived(self, mock_project):
        from quarto_graft.archive import archive_graft

        archive_graft("graft/my-graft", "my-graft")
        with pytest.raises(RuntimeError, match="already archived"):
            archive_graft("graft/my-graft", "my-graft")

    def test_returns_false_if_no_content(self, mock_project):
        from quarto_graft.archive import archive_graft

        # other has no build output directory
        result = archive_graft("graft/other", "other")
        assert result is False

    def test_does_not_affect_other_entries(self, mock_project):
        from quarto_graft.archive import archive_graft

        archive_graft("graft/my-graft", "my-graft")
        manifest = _load_manifest(mock_project)

        assert "archived" not in manifest["graft/other"]
        assert manifest["graft/other"]["title"] == "Other Graft"

    def test_replaces_stale_archive(self, mock_project):
        from quarto_graft.archive import archive_graft, restore_graft

        archive_graft("graft/my-graft", "my-graft")
        restore_graft("graft/my-graft", "my-graft")

        # Modify content
        (mock_project / "grafts__" / "my-graft" / "new.qmd").write_text("new content")

        archive_graft("graft/my-graft", "my-graft")

        # New content should be in archive
        assert (mock_project / ".grafts-archive" / "my-graft" / "new.qmd").exists()


class TestRestoreGraft:
    def test_restores_archived_content(self, mock_project):
        from quarto_graft.archive import archive_graft, restore_graft

        archive_graft("graft/my-graft", "my-graft")
        result = restore_graft("graft/my-graft", "my-graft")

        assert result is True
        assert (mock_project / "grafts__" / "my-graft" / "index.qmd").exists()
        assert (mock_project / "grafts__" / "my-graft" / "chapter1.qmd").exists()
        assert not (mock_project / ".grafts-archive" / "my-graft").exists()

    def test_clears_archived_flag(self, mock_project):
        from quarto_graft.archive import archive_graft, restore_graft

        archive_graft("graft/my-graft", "my-graft")
        restore_graft("graft/my-graft", "my-graft")
        manifest = _load_manifest(mock_project)

        assert "archived" not in manifest["graft/my-graft"]
        assert "archived_at" not in manifest["graft/my-graft"]

    def test_preserves_metadata_after_restore(self, mock_project):
        from quarto_graft.archive import archive_graft, restore_graft

        archive_graft("graft/my-graft", "my-graft")
        restore_graft("graft/my-graft", "my-graft")
        manifest = _load_manifest(mock_project)
        entry = manifest["graft/my-graft"]

        assert entry["exported"] == ["index.qmd", "chapter1.qmd"]
        assert entry["title"] == "My Graft"

    def test_returns_false_if_nothing_archived(self, mock_project):
        from quarto_graft.archive import restore_graft

        result = restore_graft("graft/my-graft", "my-graft")
        assert result is False

    def test_replaces_existing_build_output(self, mock_project):
        from quarto_graft.archive import archive_graft, restore_graft

        archive_graft("graft/my-graft", "my-graft")

        # Create different content in the build dir
        stale_dir = mock_project / "grafts__" / "my-graft"
        stale_dir.mkdir(parents=True)
        (stale_dir / "stale.qmd").write_text("stale")

        restore_graft("graft/my-graft", "my-graft")

        # Stale content should be gone, archived content restored
        assert not (mock_project / "grafts__" / "my-graft" / "stale.qmd").exists()
        assert (mock_project / "grafts__" / "my-graft" / "index.qmd").exists()


class TestListArchivedGrafts:
    def test_lists_archived(self, mock_project):
        from quarto_graft.archive import archive_graft, list_archived_grafts

        archive_graft("graft/my-graft", "my-graft")
        archived = list_archived_grafts()

        assert len(archived) == 1
        assert archived[0][0] == "graft/my-graft"
        assert archived[0][1]["title"] == "My Graft"

    def test_empty_when_none_archived(self, mock_project):
        from quarto_graft.archive import list_archived_grafts

        archived = list_archived_grafts()
        assert len(archived) == 0

    def test_excludes_archived_without_directory(self, mock_project):
        import shutil

        from quarto_graft.archive import archive_graft, list_archived_grafts

        archive_graft("graft/my-graft", "my-graft")

        # Manually remove the archive directory (simulate external deletion)
        shutil.rmtree(mock_project / ".grafts-archive" / "my-graft")

        archived = list_archived_grafts()
        assert len(archived) == 0


class TestArchiveRestoreRoundTrip:
    def test_full_round_trip(self, mock_project):
        from quarto_graft.archive import archive_graft, list_archived_grafts, restore_graft

        # Read original content
        original_content = (mock_project / "grafts__" / "my-graft" / "index.qmd").read_text()

        # Archive
        archive_graft("graft/my-graft", "my-graft")
        assert len(list_archived_grafts()) == 1
        assert not (mock_project / "grafts__" / "my-graft").exists()

        # Restore
        restore_graft("graft/my-graft", "my-graft")
        assert len(list_archived_grafts()) == 0

        # Verify content preserved
        restored_content = (mock_project / "grafts__" / "my-graft" / "index.qmd").read_text()
        assert restored_content == original_content

        # Verify manifest clean
        manifest = _load_manifest(mock_project)
        assert "archived" not in manifest["graft/my-graft"]
