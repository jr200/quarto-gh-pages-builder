"""Tests for release module."""

from __future__ import annotations

import os
from unittest.mock import patch

from quarto_graft.release import edit_release_notes


class TestEditReleaseNotes:
    def test_returns_edited_text(self, tmp_path):
        """Editor that writes new content returns the edited text."""

        def fake_editor(args):
            path = args[1]
            with open(path, "w") as f:
                f.write("Custom release notes\n")

        with patch("subprocess.check_call", side_effect=fake_editor):
            result = edit_release_notes("original notes")
        assert result == "Custom release notes"

    def test_strips_comment_lines(self, tmp_path):
        """Lines starting with '#' are stripped from the result."""

        def fake_editor(args):
            path = args[1]
            with open(path, "w") as f:
                f.write("Keep this\n# Remove this\nAnd this\n")

        with patch("subprocess.check_call", side_effect=fake_editor):
            result = edit_release_notes("original")
        assert result == "Keep this\nAnd this"

    def test_returns_none_on_empty(self):
        """An empty file (or only comments) returns None to signal abort."""

        def fake_editor(args):
            path = args[1]
            with open(path, "w") as f:
                f.write("# only comments\n# nothing else\n")

        with patch("subprocess.check_call", side_effect=fake_editor):
            result = edit_release_notes("original")
        assert result is None

    def test_returns_none_on_blank(self):
        """A file with only whitespace returns None."""

        def fake_editor(args):
            path = args[1]
            with open(path, "w") as f:
                f.write("   \n\n  \n")

        with patch("subprocess.check_call", side_effect=fake_editor):
            result = edit_release_notes("original")
        assert result is None

    def test_uses_visual_env(self):
        """Prefers $VISUAL over $EDITOR."""
        called_with = []

        def capture_editor(args):
            called_with.append(args[0])
            path = args[1]
            with open(path, "w") as f:
                f.write("edited\n")

        with (
            patch.dict(os.environ, {"VISUAL": "my-visual", "EDITOR": "my-editor"}),
            patch("subprocess.check_call", side_effect=capture_editor),
        ):
            edit_release_notes("notes")
        assert called_with[0] == "my-visual"

    def test_falls_back_to_editor_env(self):
        """Falls back to $EDITOR when $VISUAL is not set."""
        called_with = []

        def capture_editor(args):
            called_with.append(args[0])
            path = args[1]
            with open(path, "w") as f:
                f.write("edited\n")

        with (
            patch.dict(os.environ, {"EDITOR": "my-editor"}, clear=False),
            patch("subprocess.check_call", side_effect=capture_editor),
        ):
            # Remove VISUAL if present
            env = os.environ.copy()
            env.pop("VISUAL", None)
            with patch.dict(os.environ, env, clear=True):
                edit_release_notes("notes")
        assert called_with[0] == "my-editor"

    def test_preserves_original_content_for_editing(self):
        """The temp file should contain the original notes for the user to edit."""
        seen_content = []

        def capture_content(args):
            path = args[1]
            with open(path) as f:
                seen_content.append(f.read())
            # Write something back so it doesn't abort
            with open(path, "w") as f:
                f.write("final\n")

        with patch("subprocess.check_call", side_effect=capture_content):
            edit_release_notes("# Main branch changes\n\nSome notes here")
        assert "# Main branch changes" in seen_content[0]
        assert "Some notes here" in seen_content[0]

    def test_unchanged_content_returns_without_comments(self):
        """If user doesn't edit, the original notes (sans help comments) are returned."""

        def noop_editor(args):
            pass  # Don't modify the file

        with patch("subprocess.check_call", side_effect=noop_editor):
            result = edit_release_notes("My release notes")
        assert result == "My release notes"

    def test_temp_file_cleaned_up(self):
        """Temp file is removed even if editor succeeds."""
        saved_path = []

        def save_path(args):
            saved_path.append(args[1])
            with open(args[1], "w") as f:
                f.write("edited\n")

        with patch("subprocess.check_call", side_effect=save_path):
            edit_release_notes("notes")
        assert not os.path.exists(saved_path[0])
