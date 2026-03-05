"""Pre-render graft content for faster trunk builds."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .constants import PRERENDER_DIR_NAME, PRERENDER_MANIFEST_NAME, QUARTO_CONFIG_YAML
from .yaml_utils import get_yaml_loader

logger = logging.getLogger(__name__)


def _find_quarto_command() -> list[str]:
    """Find the quarto command to use, checking for uv first, then falling back to quarto."""
    try:
        subprocess.run(
            ["uv", "--version"],
            check=True,
            capture_output=True,
        )
        return ["uv", "run", "quarto"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ["quarto"]


def _find_project_root(project_dir: Path | None = None) -> Path:
    """Find the graft project root by looking for _quarto.yaml."""
    root = project_dir or Path.cwd()
    if (root / QUARTO_CONFIG_YAML).exists():
        return root
    raise RuntimeError(
        f"No {QUARTO_CONFIG_YAML} found in {root}. "
        "Run this command from a graft project directory."
    )


def _get_output_dir(project_root: Path) -> Path:
    """Read the Quarto output directory from _quarto.yaml (default: _site)."""
    config_path = project_root / QUARTO_CONFIG_YAML
    yaml_loader = get_yaml_loader()
    cfg = yaml_loader.load(config_path.read_text(encoding="utf-8")) or {}
    project = cfg.get("project") or {}
    output_dir_name = project.get("output-dir", "_site")
    return project_root / output_dir_name


def _get_source_commit(project_root: Path) -> str | None:
    """Get the current HEAD commit SHA, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def archive_graft(project_dir: Path | None = None) -> Path:
    """
    Pre-render a graft by running quarto render and storing the output.

    Runs from the graft branch. Does NOT require trunk context (grafts.yaml/grafts.lock).

    1. Finds the graft project root (_quarto.yaml)
    2. Runs `quarto render` to produce the output
    3. Copies the rendered output to _prerendered/
    4. Creates a manifest file (.graft-prerender.json)

    Args:
        project_dir: Path to the graft project root (default: cwd)

    Returns:
        Path to the _prerendered/ directory

    Raises:
        RuntimeError: If no _quarto.yaml found or quarto render fails
    """
    project_root = _find_project_root(project_dir)
    output_dir = _get_output_dir(project_root)
    prerender_dir = project_root / PRERENDER_DIR_NAME

    # Run quarto render
    quarto_cmd = _find_quarto_command()
    logger.info(f"[archive] Running {' '.join(quarto_cmd)} render in {project_root}")

    result = subprocess.run(
        [*quarto_cmd, "render"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "unknown error"
        raise RuntimeError(f"quarto render failed (exit {result.returncode}): {stderr}")

    # Verify output exists
    if not output_dir.exists() or not any(output_dir.iterdir()):
        raise RuntimeError(
            f"quarto render completed but output directory is empty: {output_dir}"
        )

    # Remove stale pre-rendered content
    if prerender_dir.exists():
        shutil.rmtree(prerender_dir)
        logger.info(f"[archive] Removed stale {PRERENDER_DIR_NAME}/")

    # Copy rendered output to _prerendered/
    shutil.copytree(output_dir, prerender_dir)
    logger.info(f"[archive] Copied {output_dir} -> {prerender_dir}")

    # Collect file list
    files = [
        p.relative_to(prerender_dir).as_posix()
        for p in prerender_dir.rglob("*")
        if p.is_file()
    ]

    # Create manifest
    manifest = {
        "prerendered_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_commit": _get_source_commit(project_root),
        "files": sorted(files),
    }
    manifest_path = prerender_dir / PRERENDER_MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info(f"[archive] Created {PRERENDER_MANIFEST_NAME} ({len(files)} files)")

    return prerender_dir


def restore_graft(project_dir: Path | None = None) -> bool:
    """
    Remove pre-rendered content from a graft project.

    Runs from the graft branch. Simply deletes the _prerendered/ directory.

    Args:
        project_dir: Path to the graft project root (default: cwd)

    Returns:
        True if content was removed, False if nothing to remove
    """
    project_root = _find_project_root(project_dir)
    prerender_dir = project_root / PRERENDER_DIR_NAME

    if not prerender_dir.exists():
        logger.info(f"[restore] No {PRERENDER_DIR_NAME}/ found, nothing to remove")
        return False

    shutil.rmtree(prerender_dir)
    logger.info(f"[restore] Removed {prerender_dir}")
    return True


def is_prerendered(worktree_dir: Path) -> bool:
    """
    Check if a graft worktree contains pre-rendered content.

    Used by trunk build to detect whether to use pre-rendered HTML
    instead of exporting source files.

    Args:
        worktree_dir: Path to the graft worktree

    Returns:
        True if valid pre-render manifest exists
    """
    manifest_path = worktree_dir / PRERENDER_DIR_NAME / PRERENDER_MANIFEST_NAME
    if not manifest_path.exists():
        return False
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return isinstance(data, dict) and "prerendered_at" in data
    except (json.JSONDecodeError, OSError):
        return False


def load_prerender_manifest(worktree_dir: Path) -> dict | None:
    """
    Load the pre-render manifest from a graft worktree.

    Args:
        worktree_dir: Path to the graft worktree

    Returns:
        Manifest dict, or None if not found or invalid
    """
    manifest_path = worktree_dir / PRERENDER_DIR_NAME / PRERENDER_MANIFEST_NAME
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
