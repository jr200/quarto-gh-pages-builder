"""Archive and restore graft build outputs."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from .branches import ManifestEntry, branch_to_key, load_manifest, save_manifest
from .constants import GRAFTS_ARCHIVE_DIR, GRAFTS_BUILD_DIR

logger = logging.getLogger(__name__)


def archive_graft(branch: str, branch_key: str) -> bool:
    """
    Archive a graft's exported content from grafts__/{branch_key}/ to
    .grafts-archive/{branch_key}/.

    Moves the build output directory and marks the manifest entry as archived.
    The manifest entry (exported list, structure, title) is preserved so that
    restore can fully reconstruct the graft without rebuilding.

    Args:
        branch: The git branch name (manifest key in grafts.lock)
        branch_key: The filesystem-safe key (directory name under grafts__/)

    Returns:
        True if content was archived, False if there was nothing to archive.

    Raises:
        RuntimeError: If the graft is already archived.
    """
    manifest = load_manifest()
    entry = manifest.get(branch)

    if entry and entry.get("archived"):
        raise RuntimeError(
            f"Graft '{branch}' is already archived. "
            "Use 'graft restore' first if you need to re-archive."
        )

    src_dir = GRAFTS_BUILD_DIR / branch_key
    if not src_dir.exists() or not any(src_dir.iterdir()):
        logger.warning(f"[archive] No exported content found at {src_dir}")
        return False

    dest_dir = GRAFTS_ARCHIVE_DIR / branch_key
    GRAFTS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    # Remove any stale archive for this branch_key
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    # Move the build output to the archive location
    shutil.move(str(src_dir), str(dest_dir))
    logger.info(f"[archive] Moved {src_dir} -> {dest_dir}")

    # Update manifest to mark as archived
    if entry is not None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        entry["archived"] = True
        entry["archived_at"] = now
        manifest[branch] = entry
        save_manifest(manifest)
        logger.info(f"[archive] Marked '{branch}' as archived in manifest")

    return True


def restore_graft(branch: str, branch_key: str) -> bool:
    """
    Restore a graft's archived content from .grafts-archive/{branch_key}/
    back to grafts__/{branch_key}/.

    Moves the archived directory back to the build output location and clears
    the archived flag in the manifest.

    Args:
        branch: The git branch name (manifest key in grafts.lock)
        branch_key: The filesystem-safe key (directory name under grafts__/)

    Returns:
        True if content was restored, False if there was nothing to restore.
    """
    archive_dir = GRAFTS_ARCHIVE_DIR / branch_key
    if not archive_dir.exists():
        logger.warning(f"[restore] No archived content found at {archive_dir}")
        return False

    dest_dir = GRAFTS_BUILD_DIR / branch_key

    # Remove any existing build output (e.g., broken stubs)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    GRAFTS_BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # Move archived content back to build location
    shutil.move(str(archive_dir), str(dest_dir))
    logger.info(f"[restore] Moved {archive_dir} -> {dest_dir}")

    # Update manifest to clear archived flag
    manifest = load_manifest()
    entry = manifest.get(branch)
    if entry is not None:
        entry.pop("archived", None)
        entry.pop("archived_at", None)
        manifest[branch] = entry
        save_manifest(manifest)
        logger.info(f"[restore] Cleared archived flag for '{branch}' in manifest")

    return True


def list_archived_grafts() -> list[tuple[str, ManifestEntry]]:
    """
    Return a list of (branch_name, manifest_entry) tuples for all archived grafts.

    Only returns grafts that are marked as archived in the manifest AND
    have content in the archive directory.
    """
    manifest = load_manifest()
    archived: list[tuple[str, ManifestEntry]] = []

    for branch, entry in manifest.items():
        if entry.get("archived"):
            branch_key = entry.get("branch_key", branch_to_key(branch))
            archive_dir = GRAFTS_ARCHIVE_DIR / branch_key
            if archive_dir.exists():
                archived.append((branch, entry))

    return archived
