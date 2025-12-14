"""Utilities for safe file operations."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .yaml_utils import get_yaml_loader


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """
    Write text to a file atomically.

    Uses a temporary file in the same directory to ensure atomicity.
    If the write fails, the original file is left unchanged.

    Args:
        path: Destination file path
        content: Text content to write
        encoding: Text encoding (default: utf-8)
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create temp file in same directory to ensure same filesystem
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp_path = Path(tmp.name)
        try:
            tmp.write(content)
            tmp.flush()
            # Ensure data is written to disk
            tmp.file.flush()
            # Atomically replace the target file
            tmp_path.replace(path)
        except Exception:
            # Clean up temp file on error
            if tmp_path.exists():
                tmp_path.unlink()
            raise


def atomic_write_json(path: Path, data: dict[str, Any], indent: int = 2) -> None:
    """
    Write JSON to a file atomically.

    Args:
        path: Destination file path
        data: Dictionary to serialize as JSON
        indent: JSON indentation level
    """
    content = json.dumps(data, indent=indent, sort_keys=True)
    atomic_write_text(path, content, encoding="utf-8")


def atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    """
    Write YAML to a file atomically, preserving quotes and formatting.

    Args:
        path: Destination file path
        data: Dictionary to serialize as YAML
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    yaml_loader = get_yaml_loader()

    # Create temp file in same directory
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp_path = Path(tmp.name)
        try:
            yaml_loader.dump(data, tmp)
            tmp.flush()
            tmp.file.flush()
            tmp_path.replace(path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
