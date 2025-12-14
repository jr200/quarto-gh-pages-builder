"""Tests for branches module."""

import pytest

from quarto_graft.branches import _validate_label, branch_to_key


class TestBranchToKey:
    """Test branch name to filesystem key conversion."""

    def test_simple_branch(self):
        """Test simple branch name."""
        assert branch_to_key("demo") == "demo"

    def test_slash_conversion(self):
        """Test that slashes are converted to hyphens."""
        assert branch_to_key("graft/demo") == "graft-demo"

    def test_backslash_conversion(self):
        """Test that backslashes are converted to hyphens."""
        assert branch_to_key("graft\\demo") == "graft-demo"

    def test_multiple_dots_collapsed(self):
        """Test that sequences of dots are collapsed."""
        assert branch_to_key("graft...demo") == "graft.demo"

    def test_leading_trailing_stripped(self):
        """Test that leading/trailing dots and hyphens are removed."""
        assert branch_to_key("--demo--") == "demo"
        assert branch_to_key("..demo..") == "demo"

    def test_path_traversal_rejected(self):
        """Test that path traversal attempts are rejected."""
        with pytest.raises(ValueError, match="dangerous path"):
            branch_to_key(".")

        with pytest.raises(ValueError, match="dangerous path"):
            branch_to_key("..")

        with pytest.raises(ValueError, match="dangerous path"):
            branch_to_key("~")

        with pytest.raises(ValueError, match="path traversal"):
            branch_to_key("foo..bar")


class TestValidateLabel:
    """Test label validation."""

    def test_valid_labels(self):
        """Test that valid labels pass validation."""
        _validate_label("test", "simple")
        _validate_label("test", "with-hyphens")
        _validate_label("test", "with_underscores")
        _validate_label("test", "with.dots")
        _validate_label("test", "with/slashes")
        _validate_label("test", "CamelCase123")

    def test_whitespace_rejected(self):
        """Test that whitespace in labels is rejected."""
        with pytest.raises(ValueError, match="whitespace"):
            _validate_label("test", "has spaces")

        with pytest.raises(ValueError, match="whitespace"):
            _validate_label("test", "has\ttab")

        with pytest.raises(ValueError, match="whitespace"):
            _validate_label("test", "has\nnewline")

    def test_special_chars_rejected(self):
        """Test that special characters are rejected."""
        with pytest.raises(ValueError, match="only letters"):
            _validate_label("test", "has@symbol")

        with pytest.raises(ValueError, match="only letters"):
            _validate_label("test", "has$dollar")

        with pytest.raises(ValueError, match="only letters"):
            _validate_label("test", "has!exclamation")
