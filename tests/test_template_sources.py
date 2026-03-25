"""Tests for template_sources module."""

from __future__ import annotations

import io
import tarfile
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from quarto_graft.template_sources import (
    TemplateSource,
    load_template_sources_from_config,
)

# ---------------------------------------------------------------------------
# TemplateSource — local path resolution
# ---------------------------------------------------------------------------


class TestResolveLocalPath:
    def test_absolute_path(self, tmp_path):
        template_dir = tmp_path / "my-templates"
        template_dir.mkdir()
        src = TemplateSource({"path": str(template_dir)})
        assert src.resolve() == template_dir

    def test_relative_path(self, tmp_path):
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        with patch("quarto_graft.template_sources.constants") as mock_constants:
            mock_constants.ROOT = tmp_path
            src = TemplateSource({"path": "templates"})
            assert src.resolve() == template_dir

    def test_nonexistent_path_raises(self, tmp_path):
        src = TemplateSource({"path": str(tmp_path / "nonexistent")})
        with pytest.raises(RuntimeError, match="does not exist"):
            src.resolve()

    def test_caches_resolved_path(self, tmp_path):
        template_dir = tmp_path / "tpl"
        template_dir.mkdir()
        src = TemplateSource({"path": str(template_dir)})
        first = src.resolve()
        second = src.resolve()
        assert first is second


# ---------------------------------------------------------------------------
# TemplateSource — invalid spec
# ---------------------------------------------------------------------------


class TestInvalidSpec:
    def test_missing_keys_raises(self):
        src = TemplateSource({"bogus": "value"})
        with pytest.raises(RuntimeError, match="must have 'path', 'url', or 'github'"):
            src.resolve()


# ---------------------------------------------------------------------------
# TemplateSource — zip extraction
# ---------------------------------------------------------------------------


def _make_zip(files: dict[str, str]) -> bytes:
    """Create an in-memory zip archive with the given {path: content} entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestExtractZip:
    def test_strips_single_root_dir(self, tmp_path):
        content = _make_zip(
            {
                "root/a.txt": "aaa",
                "root/sub/b.txt": "bbb",
            }
        )
        dest = tmp_path / "out"
        src = TemplateSource({"path": "."})
        src._extract_zip(content, dest)

        assert (dest / "a.txt").read_text() == "aaa"
        assert (dest / "sub" / "b.txt").read_text() == "bbb"
        # Root dir should be stripped
        assert not (dest / "root").exists()

    def test_multiple_roots_extracts_directly(self, tmp_path):
        content = _make_zip(
            {
                "dir1/a.txt": "aaa",
                "dir2/b.txt": "bbb",
            }
        )
        dest = tmp_path / "out"
        src = TemplateSource({"path": "."})
        src._extract_zip(content, dest)

        assert (dest / "dir1" / "a.txt").read_text() == "aaa"
        assert (dest / "dir2" / "b.txt").read_text() == "bbb"

    def test_empty_zip_raises(self, tmp_path):
        content = _make_zip({})
        dest = tmp_path / "out"
        src = TemplateSource({"path": "."})
        with pytest.raises(RuntimeError, match="Empty zip archive"):
            src._extract_zip(content, dest)

    def test_zip_path_traversal_multi_root_no_validation(self, tmp_path):
        """Zip extractall with multiple roots has no explicit path validation.

        Unlike _extract_tar, _extract_zip does not validate member paths
        when there are multiple root directories. Python 3.12+ blocks
        traversal at the zipfile.extractall level, but the code itself
        does not perform any checks — safe files should still extract.
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("dir1/safe.txt", "safe")
            zf.writestr("dir2/also-safe.txt", "safe")
        content = buf.getvalue()

        dest = tmp_path / "out"
        src = TemplateSource({"path": "."})
        src._extract_zip(content, dest)

        assert (dest / "dir1" / "safe.txt").read_text() == "safe"
        assert (dest / "dir2" / "also-safe.txt").read_text() == "safe"


# ---------------------------------------------------------------------------
# TemplateSource — tar extraction
# ---------------------------------------------------------------------------


def _make_tar(files: dict[str, str], *, symlinks: dict[str, str] | None = None) -> bytes:
    """Create an in-memory tar.gz archive."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for link_name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name=link_name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tf.addfile(info)
    return buf.getvalue()


class TestExtractTar:
    def test_strips_single_root_dir(self, tmp_path):
        content = _make_tar(
            {
                "root/a.txt": "aaa",
                "root/sub/b.txt": "bbb",
            }
        )
        dest = tmp_path / "out"
        src = TemplateSource({"path": "."})
        src._extract_tar(content, dest)

        assert (dest / "a.txt").read_text() == "aaa"
        assert (dest / "sub" / "b.txt").read_text() == "bbb"

    def test_multiple_roots_no_strip(self, tmp_path):
        content = _make_tar(
            {
                "dir1/a.txt": "aaa",
                "dir2/b.txt": "bbb",
            }
        )
        dest = tmp_path / "out"
        src = TemplateSource({"path": "."})
        src._extract_tar(content, dest)

        assert (dest / "dir1" / "a.txt").read_text() == "aaa"
        assert (dest / "dir2" / "b.txt").read_text() == "bbb"

    def test_rejects_symlinks(self, tmp_path):
        content = _make_tar(
            {"root/safe.txt": "ok"},
            symlinks={"root/evil": "/etc/passwd"},
        )
        dest = tmp_path / "out"
        src = TemplateSource({"path": "."})
        src._extract_tar(content, dest)

        assert (dest / "safe.txt").read_text() == "ok"
        assert not (dest / "evil").exists()

    def test_rejects_path_traversal(self, tmp_path):
        content = _make_tar({"../escaped.txt": "ESCAPED"})
        dest = tmp_path / "out"
        src = TemplateSource({"path": "."})
        src._extract_tar(content, dest)

        assert not (tmp_path / "escaped.txt").exists()
        assert not (dest / "escaped.txt").exists()

    def test_rejects_absolute_paths(self, tmp_path):
        content = _make_tar({"/etc/evil.txt": "EVIL"})
        dest = tmp_path / "out"
        src = TemplateSource({"path": "."})
        src._extract_tar(content, dest)

        assert not (dest / "etc" / "evil.txt").exists()

    def test_empty_tar_raises(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz"):
            pass
        content = buf.getvalue()

        dest = tmp_path / "out"
        src = TemplateSource({"path": "."})
        with pytest.raises(RuntimeError, match="Empty tar archive"):
            src._extract_tar(content, dest)


# ---------------------------------------------------------------------------
# TemplateSource — GitHub URL parsing
# ---------------------------------------------------------------------------


class TestParseGithubUrl:
    def setup_method(self):
        self.src = TemplateSource({"path": "."})

    def test_basic_repo_url(self):
        result = self.src._parse_github_url("https://github.com/user/repo")
        assert result == {"repo": "user/repo", "ref": None}

    def test_repo_with_git_suffix(self):
        result = self.src._parse_github_url("https://github.com/user/repo.git")
        assert result == {"repo": "user/repo", "ref": None}

    def test_repo_with_tree_ref(self):
        result = self.src._parse_github_url("https://github.com/user/repo/tree/main")
        assert result == {"repo": "user/repo", "ref": "main"}

    def test_repo_with_tag_ref(self):
        result = self.src._parse_github_url("https://github.com/user/repo/tree/v1.0.0")
        assert result == {"repo": "user/repo", "ref": "v1.0.0"}

    def test_www_github(self):
        result = self.src._parse_github_url("https://www.github.com/user/repo")
        assert result == {"repo": "user/repo", "ref": None}

    def test_non_github_returns_none(self):
        assert self.src._parse_github_url("https://gitlab.com/user/repo") is None

    def test_too_short_path_returns_none(self):
        assert self.src._parse_github_url("https://github.com/user") is None

    def test_empty_url_returns_none(self):
        assert self.src._parse_github_url("https://github.com/") is None


# ---------------------------------------------------------------------------
# TemplateSource — URL resolution
# ---------------------------------------------------------------------------


class TestResolveUrl:
    def test_downloads_and_extracts_zip(self, tmp_path):
        zip_content = _make_zip({"root/template.txt": "hello"})

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = zip_content
        mock_response.headers = {"Content-Length": str(len(zip_content))}

        with patch("quarto_graft.template_sources._template_cache_dir", return_value=tmp_path / ".cache"):
            with patch("quarto_graft.template_sources.urlopen", return_value=mock_response):
                src = TemplateSource({"url": "https://example.com/templates.zip"})
                result = src.resolve()

        assert result.exists()
        assert (result / "template.txt").read_text() == "hello"

    def test_downloads_and_extracts_tar(self, tmp_path):
        tar_content = _make_tar({"root/template.txt": "hello"})

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = tar_content
        mock_response.headers = {"Content-Length": str(len(tar_content))}

        with patch("quarto_graft.template_sources._template_cache_dir", return_value=tmp_path / ".cache"):
            with patch("quarto_graft.template_sources.urlopen", return_value=mock_response):
                src = TemplateSource({"url": "https://example.com/templates.tar.gz"})
                result = src.resolve()

        assert result.exists()
        assert (result / "template.txt").read_text() == "hello"

    def test_rejects_oversized_download(self, tmp_path):
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.headers = {"Content-Length": str(200 * 1024 * 1024)}

        with patch("quarto_graft.template_sources._template_cache_dir", return_value=tmp_path / ".cache"):
            with patch("quarto_graft.template_sources.urlopen", return_value=mock_response):
                src = TemplateSource({"url": "https://example.com/huge.zip"})
                with pytest.raises(RuntimeError, match="too large"):
                    src.resolve()

    def test_uses_cache_on_second_call(self, tmp_path):
        zip_content = _make_zip({"root/tpl.txt": "cached"})

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = zip_content
        mock_response.headers = {"Content-Length": str(len(zip_content))}

        with patch("quarto_graft.template_sources._template_cache_dir", return_value=tmp_path / ".cache"):
            with patch("quarto_graft.template_sources.urlopen", return_value=mock_response) as mock_urlopen:
                src = TemplateSource({"url": "https://example.com/tpl.zip"})
                first = src.resolve()
                # Reset _resolved_path to simulate a new TemplateSource
                src._resolved_path = None
                second = src.resolve()

        # urlopen should only be called once (second call uses cache)
        assert mock_urlopen.call_count == 1
        assert first == second

    def test_unknown_format_raises(self, tmp_path):
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b"not an archive"
        mock_response.headers = {}

        with patch("quarto_graft.template_sources._template_cache_dir", return_value=tmp_path / ".cache"):
            with patch("quarto_graft.template_sources.urlopen", return_value=mock_response):
                src = TemplateSource({"url": "https://example.com/templates.unknown"})
                with pytest.raises(RuntimeError, match="Unknown archive format"):
                    src.resolve()


# ---------------------------------------------------------------------------
# TemplateSource — discover_templates / get_template_path
# ---------------------------------------------------------------------------


class TestDiscoverTemplates:
    def test_discovers_from_type_subdirectory(self, tmp_path):
        (tmp_path / "trunk-templates" / "basic").mkdir(parents=True)
        (tmp_path / "trunk-templates" / "advanced").mkdir(parents=True)
        # Dot-dirs and "with-" dirs should be excluded
        (tmp_path / "trunk-templates" / ".hidden").mkdir(parents=True)
        (tmp_path / "trunk-templates" / "with-extras").mkdir(parents=True)

        src = TemplateSource({"path": str(tmp_path)})
        templates = src.discover_templates("trunk")

        assert templates == ["advanced", "basic"]

    def test_discovers_from_root_fallback(self, tmp_path):
        (tmp_path / "my-template").mkdir()
        (tmp_path / "other-template").mkdir()

        src = TemplateSource({"path": str(tmp_path)})
        templates = src.discover_templates("trunk")

        assert "my-template" in templates
        assert "other-template" in templates

    def test_returns_empty_for_missing_source(self):
        src = TemplateSource({"path": "/nonexistent/path"})
        assert src.discover_templates("trunk") == []

    def test_prefers_type_subdirectory_over_root(self, tmp_path):
        (tmp_path / "trunk-templates" / "from-subdir").mkdir(parents=True)
        (tmp_path / "from-root").mkdir()

        src = TemplateSource({"path": str(tmp_path)})
        templates = src.discover_templates("trunk")

        assert templates == ["from-subdir"]


class TestGetTemplatePath:
    def test_finds_in_type_subdirectory(self, tmp_path):
        tpl = tmp_path / "graft-templates" / "notebook"
        tpl.mkdir(parents=True)

        src = TemplateSource({"path": str(tmp_path)})
        assert src.get_template_path("notebook", "graft") == tpl

    def test_finds_at_root(self, tmp_path):
        tpl = tmp_path / "notebook"
        tpl.mkdir()

        src = TemplateSource({"path": str(tmp_path)})
        assert src.get_template_path("notebook", "graft") == tpl

    def test_returns_none_when_missing(self, tmp_path):
        src = TemplateSource({"path": str(tmp_path)})
        assert src.get_template_path("nonexistent", "trunk") is None

    def test_returns_none_for_unresolvable_source(self):
        src = TemplateSource({"path": "/nonexistent"})
        assert src.get_template_path("any", "trunk") is None


# ---------------------------------------------------------------------------
# load_template_sources_from_config
# ---------------------------------------------------------------------------


class TestLoadTemplateSourcesFromConfig:
    def test_returns_empty_when_no_config_file(self, tmp_path):
        with patch("quarto_graft.constants.GRAFTS_CONFIG_FILE", tmp_path / "grafts.yaml"):
            result = load_template_sources_from_config()
        assert result == []

    def test_returns_empty_when_no_templates_key(self, tmp_path):
        config = tmp_path / "grafts.yaml"
        config.write_text("grafts:\n  - name: demo\n", encoding="utf-8")
        with patch("quarto_graft.constants.GRAFTS_CONFIG_FILE", config):
            result = load_template_sources_from_config()
        assert result == []

    def test_loads_local_source(self, tmp_path):
        config = tmp_path / "grafts.yaml"
        config.write_text(
            "templates:\n  - path: ./my-templates\n",
            encoding="utf-8",
        )
        with patch("quarto_graft.constants.GRAFTS_CONFIG_FILE", config):
            result = load_template_sources_from_config()
        assert len(result) == 1
        assert result[0].spec == {"path": "./my-templates"}
        assert "local:" in result[0].source_name

    def test_loads_github_source_with_ref(self, tmp_path):
        config = tmp_path / "grafts.yaml"
        config.write_text(
            "templates:\n  - github: user/repo\n    ref: v1.0\n",
            encoding="utf-8",
        )
        with patch("quarto_graft.constants.GRAFTS_CONFIG_FILE", config):
            result = load_template_sources_from_config()
        assert len(result) == 1
        assert "github:" in result[0].source_name
        assert "@v1.0" in result[0].source_name

    def test_skips_non_dict_entries(self, tmp_path):
        config = tmp_path / "grafts.yaml"
        config.write_text(
            "templates:\n  - just-a-string\n  - path: ./valid\n",
            encoding="utf-8",
        )
        with patch("quarto_graft.constants.GRAFTS_CONFIG_FILE", config):
            result = load_template_sources_from_config()
        assert len(result) == 1

    def test_warns_on_non_list_templates(self, tmp_path):
        config = tmp_path / "grafts.yaml"
        config.write_text("templates: not-a-list\n", encoding="utf-8")
        with patch("quarto_graft.constants.GRAFTS_CONFIG_FILE", config):
            result = load_template_sources_from_config()
        assert result == []
