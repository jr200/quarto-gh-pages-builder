"""Tests for file_utils module."""

import json

from quarto_graft.file_utils import atomic_write_json, atomic_write_text, atomic_write_yaml


class TestAtomicWriteText:
    """Test atomic text file writing."""

    def test_writes_content(self, tmp_path):
        """Test that content is written correctly."""
        file_path = tmp_path / "test.txt"
        content = "Hello, world!"

        atomic_write_text(file_path, content)

        assert file_path.exists()
        assert file_path.read_text() == content

    def test_overwrites_existing(self, tmp_path):
        """Test that existing files are overwritten."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("Old content")

        new_content = "New content"
        atomic_write_text(file_path, new_content)

        assert file_path.read_text() == new_content

    def test_creates_parent_directories(self, tmp_path):
        """Test that parent directories are created if needed."""
        file_path = tmp_path / "subdir" / "nested" / "test.txt"
        content = "Nested file"

        atomic_write_text(file_path, content)

        assert file_path.exists()
        assert file_path.read_text() == content

    def test_no_temp_files_left_behind(self, tmp_path):
        """Test that temporary files are cleaned up."""
        file_path = tmp_path / "test.txt"
        atomic_write_text(file_path, "content")

        # Check that no .tmp files remain
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


class TestAtomicWriteJSON:
    """Test atomic JSON writing."""

    def test_writes_valid_json(self, tmp_path):
        """Test that valid JSON is written."""
        file_path = tmp_path / "test.json"
        data = {"key": "value", "number": 42, "nested": {"a": 1}}

        atomic_write_json(file_path, data)

        assert file_path.exists()
        loaded = json.loads(file_path.read_text())
        assert loaded == data

    def test_sorted_keys(self, tmp_path):
        """Test that keys are sorted."""
        file_path = tmp_path / "test.json"
        data = {"z": 1, "a": 2, "m": 3}

        atomic_write_json(file_path, data)

        content = file_path.read_text()
        # Keys should appear in sorted order
        assert content.index('"a"') < content.index('"m"') < content.index('"z"')


class TestAtomicWriteYAML:
    """Test atomic YAML writing."""

    def test_writes_valid_yaml(self, tmp_path):
        """Test that valid YAML is written."""
        file_path = tmp_path / "test.yaml"
        data = {"key": "value", "number": 42, "list": [1, 2, 3]}

        atomic_write_yaml(file_path, data)

        assert file_path.exists()
        content = file_path.read_text()
        # Basic YAML format checks
        assert "key: value" in content or "key:" in content

    def test_creates_parent_directories(self, tmp_path):
        """Test that parent directories are created."""
        file_path = tmp_path / "subdir" / "test.yaml"
        data = {"test": "data"}

        atomic_write_yaml(file_path, data)

        assert file_path.exists()
