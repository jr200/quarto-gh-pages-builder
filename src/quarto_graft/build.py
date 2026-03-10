from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .archive import is_prerendered
from .branches import (
    BranchSpec,
    ManifestEntry,
    branch_to_key,
    load_manifest,
    read_branches_list,
    save_manifest,
)
from .constants import GRAFTS_BUILD_DIR, PRERENDER_DIR_NAME, PRERENDER_MANIFEST_NAME
from .git_utils import (
    fetch_origin,
    managed_worktree,
    run_git,
)
from .quarto_config import (
    collect_exported_relpaths,
    derive_section_title,
    extract_nav_structure,
    load_quarto_config,
)

logger = logging.getLogger(__name__)


@dataclass
class BuildResult:
    branch: str
    branch_key: str
    title: str
    status: Literal["ok", "fallback", "broken", "skipped"]
    head_sha: str | None
    last_good_sha: str | None
    built_at: str
    exported_relpaths: list[str]
    exported_dest_paths: list[Path]
    nav_structure: Any = None
    prerendered: bool = False
    duration_secs: float = 0.0
    error_message: str | None = None


def _temp_worktree_name(branch_key: str, label: str) -> str:
    """Return a unique, short worktree name to avoid collisions."""
    return f"{label}-{branch_key}-{uuid4().hex[:6]}"


def inject_failure_header(
    qmd: Path,
    branch: str,
    head_sha: str | None,
    last_good_sha: str,
) -> None:
    """Inject a warning header when using fallback content."""
    text = qmd.read_text(encoding="utf-8")

    if head_sha:
        head_short = head_sha[:7] if len(head_sha) >= 7 else head_sha
        head_line = f"failed to build at latest commit `{head_short}`."
    else:
        head_line = "failed to build at its latest known HEAD (branch missing or unreachable)."

    last_good_short = last_good_sha[:7] if len(last_good_sha) >= 7 else last_good_sha

    header = f"""::: callout-warning
This branch **`{branch}`** {head_line}

You are seeing content from the last known good commit **`{last_good_short}`**.
:::

"""
    qmd.write_text(header + text, encoding="utf-8")


def create_broken_stub(
    branch_key: str,
    branch: str,
    head_sha: str | None,
    out_dir: Path,
) -> list[Path]:
    """Create a stub page when no successful build exists."""
    msg_sha = (
        f" at commit `{head_sha[:7]}`"
        if head_sha and len(head_sha) >= 7
        else (f" at commit `{head_sha}`" if head_sha else "")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "index.qmd"
    target.write_text(
        f"""---
title: "{branch_key}"
---

::: callout-warning
This branch **`{branch}`** failed to build{msg_sha}, and there is no previous successful build recorded.

Please fix the build for branch **`{branch}`**.
:::
""",
        encoding="utf-8",
    )
    return [target]


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


def _convert_source_to_qmd(src: Path, dest_qmd: Path) -> None:
    """
    Convert a branch source file (non-notebook) to a .qmd file for inclusion in the main book.
    """
    dest_qmd.parent.mkdir(parents=True, exist_ok=True)

    suffix = src.suffix.lower()
    if suffix == ".qmd":
        shutil.copy2(src, dest_qmd)
        return

    if suffix in {".md", ".rmd", ".rmarkdown"}:
        quarto_cmd = _find_quarto_command()
        subprocess.run(
            [*quarto_cmd, "convert", str(src), "--output", str(dest_qmd)],
            check=True,
        )
        return

    logger.warning(f"_convert_source_to_qmd called on unsupported type: {src}")


def _export_from_worktree(
    branch: str,
    branch_key: str,
    ref: str,
    worktree_name: str,
    inject_warning: bool = False,
    warn_head_sha: str | None = None,
    warn_last_good_sha: str | None = None,
) -> tuple[str, str, list[str], list[Path], Any, bool]:
    """
    Export content from a worktree for the given ref.
    Returns: (sha, section_title, exported_relpaths, exported_dest_paths, nav_structure, prerendered)
    """
    try:
        with managed_worktree(ref, worktree_name) as wt_dir:
            sha = run_git(["rev-parse", "HEAD"], cwd=wt_dir)

            project_dir = wt_dir
            cfg = load_quarto_config(project_dir)
            section_title = derive_section_title(cfg, branch)
            nav_structure = extract_nav_structure(cfg)

            # Check for pre-rendered content
            prerender_dir = wt_dir / PRERENDER_DIR_NAME
            if is_prerendered(wt_dir):
                logger.info(f"[{branch}] Using pre-rendered content from {PRERENDER_DIR_NAME}/")
                dest_dir = GRAFTS_BUILD_DIR / branch_key
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                # Copy pre-rendered content (exclude the manifest JSON)
                shutil.copytree(
                    prerender_dir, dest_dir,
                    ignore=shutil.ignore_patterns(PRERENDER_MANIFEST_NAME),
                )

                # Collect all exported files
                exported_relpaths = [
                    p.relative_to(dest_dir).as_posix()
                    for p in dest_dir.rglob("*") if p.is_file()
                ]
                exported_dest_paths = [dest_dir / r for r in exported_relpaths]

                return sha, section_title, exported_relpaths, exported_dest_paths, nav_structure, True

            # Normal source file export
            src_relpaths = collect_exported_relpaths(project_dir, cfg)

            dest_dir = GRAFTS_BUILD_DIR / branch_key
            dest_dir.mkdir(parents=True, exist_ok=True)

            exported_dest_paths: list[Path] = []
            exported_relpaths_for_main: list[str] = []

            for src_rel in src_relpaths:
                src = project_dir / src_rel
                if not src.exists():
                    logger.warning(f"[{branch}] source listed but missing: {src_rel}")
                    continue

                ext = src.suffix.lower()

                if ext == ".ipynb":
                    dest_rel = src_rel
                    dest = dest_dir / dest_rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                else:
                    rel_obj = Path(src_rel)
                    if rel_obj.suffix.lower() == ".qmd":
                        dest_rel = rel_obj.as_posix()
                    else:
                        dest_rel = rel_obj.with_suffix(".qmd").as_posix()

                    dest = dest_dir / dest_rel
                    _convert_source_to_qmd(src, dest)

                    if inject_warning and warn_last_good_sha:
                        inject_failure_header(dest, branch, warn_head_sha, warn_last_good_sha)

                exported_dest_paths.append(dest)
                exported_relpaths_for_main.append(dest_rel)

            return sha, section_title, exported_relpaths_for_main, exported_dest_paths, nav_structure, False
    except Exception as e:
        logger.error(f"[{branch}] Export from worktree failed: {e}", exc_info=True)
        raise


def _update_manifest_entry(
    manifest: dict[str, ManifestEntry],
    branch: str,
    branch_key: str,
    title: str,
    exported_relpaths: list[str],
    nav_structure: Any = None,
    last_good: str | None = None,
    now: str | None = None,
    prerendered: bool = False,
) -> None:
    """Update a manifest entry for a branch."""
    if now is None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    entry: ManifestEntry = {
        "last_checked": now,
        "title": title,
        "branch_key": branch_key,
        "exported": exported_relpaths,
    }
    if nav_structure is not None:
        entry["structure"] = nav_structure
    if last_good:
        entry["last_good"] = last_good
    if prerendered:
        entry["prerendered"] = True

    manifest[branch] = entry


def _create_broken_stub_and_update_manifest(
    manifest: dict[str, ManifestEntry],
    branch: str,
    branch_key: str,
    head_sha: str | None,
    update_manifest: bool,
    now: str,
) -> tuple[list[Path], list[str]]:
    """Create a broken stub and optionally update the manifest."""
    dest_dir = GRAFTS_BUILD_DIR / branch_key
    exported_dest_paths = create_broken_stub(branch_key, branch, head_sha, dest_dir)
    exported_relpaths = [p.relative_to(dest_dir).as_posix() for p in exported_dest_paths]

    if update_manifest:
        _update_manifest_entry(manifest, branch, branch_key, branch, exported_relpaths, now=now)
        save_manifest(manifest)

    return exported_dest_paths, exported_relpaths


def _branch_exists(ref: str) -> bool:
    """Check if a git reference exists."""
    try:
        run_git(["rev-parse", "--verify", ref])
        return True
    except subprocess.CalledProcessError:
        return False


def build_branch(spec: BranchSpec | str, update_manifest: bool = True, fetch: bool = True) -> BuildResult:
    """
    Build a single branch into grafts__/<branch_key>/... with fallback logic.
    """
    t0 = time.monotonic()

    if isinstance(spec, str):
        spec = {"name": spec, "branch": spec, "collar": ""}  # type: ignore[assignment]

    branch = spec["branch"]
    graft_name = spec["name"]

    manifest = load_manifest()
    entry = manifest.get(branch, {})
    prev_last_good = entry.get("last_good")

    branch_key = branch_to_key(graft_name)

    # Prefer remote ref if available, otherwise fall back to local
    head_ref = f"origin/{branch}" if _branch_exists(f"origin/{branch}") else branch
    head_sha: str | None = None

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    title: str = graft_name  # default
    exported_relpaths: list[str] = []
    exported_dest_paths: list[Path] = []
    status: Literal["ok", "fallback", "broken", "skipped"]
    nav_structure: Any = None
    prerendered: bool = False
    error_message: str | None = None
    result_last_good: str | None = prev_last_good

    if fetch:
        fetch_origin()

    # Validate branch exists before attempting build
    if not _branch_exists(head_ref):
        logger.error(f"Branch '{head_ref}' does not exist after fetch")
        error_message = f"Branch '{head_ref}' does not exist"
        # Check if we have a last_good to fall back to
        if prev_last_good and _branch_exists(prev_last_good):
            last_good_short = prev_last_good[:7] if len(prev_last_good) >= 7 else prev_last_good
            logger.info(f"Using last_good commit {last_good_short} for branch {branch}")
            try:
                sha, title, exported_relpaths, exported_dest_paths, nav_structure, prerendered = _export_from_worktree(
                    branch=branch,
                    branch_key=branch_key,
                    ref=prev_last_good,
                    worktree_name=_temp_worktree_name(branch_key, "lastgood"),
                    inject_warning=True,
                    warn_head_sha=None,
                    warn_last_good_sha=prev_last_good,
                )
                status = "fallback"
                result_last_good = prev_last_good
                if update_manifest:
                    _update_manifest_entry(
                        manifest, branch, branch_key, title, exported_relpaths,
                        nav_structure=nav_structure, last_good=prev_last_good, now=now,
                        prerendered=prerendered,
                    )
                    save_manifest(manifest)
            except Exception as e:
                logger.error(f"[{branch}] Fallback build also failed: {e}", exc_info=True)
                error_message = f"Branch missing; fallback also failed: {e}"
                status = "broken"
                result_last_good = None
                exported_dest_paths, exported_relpaths = _create_broken_stub_and_update_manifest(
                    manifest, branch, branch_key, None, update_manifest, now
                )
                title = branch
        else:
            # No branch and no fallback
            status = "broken"
            result_last_good = None
            exported_dest_paths, exported_relpaths = _create_broken_stub_and_update_manifest(
                manifest, branch, branch_key, None, update_manifest, now
            )
            title = branch
    else:
        try:
            head_sha = run_git(["rev-parse", head_ref])
            sha, title, exported_relpaths, exported_dest_paths, nav_structure, prerendered = _export_from_worktree(
                branch=branch,
                branch_key=branch_key,
                ref=head_ref,
                worktree_name=_temp_worktree_name(branch_key, "head"),
            )
            status = "ok"
            result_last_good = sha
            if update_manifest:
                _update_manifest_entry(
                    manifest, branch, branch_key, title, exported_relpaths,
                    nav_structure=nav_structure, last_good=sha, now=now,
                    prerendered=prerendered,
                )
                save_manifest(manifest)
        except Exception as e:
            logger.warning(f"[{branch}] HEAD build failed: {e}", exc_info=True)
            error_message = str(e)
            if prev_last_good and _branch_exists(prev_last_good):
                try:
                    sha, title, exported_relpaths, exported_dest_paths, nav_structure, prerendered = _export_from_worktree(
                        branch=branch,
                        branch_key=branch_key,
                        ref=prev_last_good,
                        worktree_name=_temp_worktree_name(branch_key, "lastgood"),
                        inject_warning=True,
                        warn_head_sha=head_sha or prev_last_good,
                        warn_last_good_sha=prev_last_good,
                    )
                    status = "fallback"
                    result_last_good = prev_last_good
                    if update_manifest:
                        _update_manifest_entry(
                            manifest, branch, branch_key, title, exported_relpaths,
                            nav_structure=nav_structure, last_good=prev_last_good, now=now,
                            prerendered=prerendered,
                        )
                        save_manifest(manifest)
                except Exception as fallback_err:
                    logger.error(f"[{branch}] Fallback build also failed: {fallback_err}", exc_info=True)
                    error_message = f"{e} (fallback also failed: {fallback_err})"
                    status = "broken"
                    result_last_good = None
                    exported_dest_paths, exported_relpaths = _create_broken_stub_and_update_manifest(
                        manifest, branch, branch_key, head_sha, update_manifest, now
                    )
                    title = branch
            else:
                # No fallback available – stub page
                status = "broken"
                result_last_good = None
                exported_dest_paths, exported_relpaths = _create_broken_stub_and_update_manifest(
                    manifest, branch, branch_key, head_sha, update_manifest, now
                )
                title = branch

    duration = time.monotonic() - t0

    return BuildResult(
        branch=branch,
        branch_key=branch_key,
        title=title,
        status=status,
        head_sha=head_sha,
        last_good_sha=result_last_good,
        built_at=now,
        exported_relpaths=exported_relpaths,
        exported_dest_paths=exported_dest_paths,
        nav_structure=nav_structure,
        prerendered=prerendered,
        duration_secs=duration,
        error_message=error_message,
    )


def resolve_head_sha(branch: str) -> str | None:
    """Get the current HEAD SHA for a branch, preferring the remote ref."""
    head_ref = f"origin/{branch}" if _branch_exists(f"origin/{branch}") else branch
    if not _branch_exists(head_ref):
        return None
    try:
        return run_git(["rev-parse", head_ref])
    except Exception:
        return None


def _manifest_entry_from_result(result: BuildResult) -> ManifestEntry:
    """Create a manifest entry from a build result."""
    entry: ManifestEntry = {
        "last_checked": result.built_at,
        "title": result.title,
        "branch_key": result.branch_key,
        "exported": result.exported_relpaths,
    }
    if result.nav_structure is not None:
        entry["structure"] = result.nav_structure
    if result.last_good_sha:
        entry["last_good"] = result.last_good_sha
    if result.prerendered:
        entry["prerendered"] = True
    return entry


def update_manifests(
    branches: list[BranchSpec | str] | None = None,
    update_manifest: bool = True,
    jobs: int = 1,
    only: set[str] | None = None,
    skip: set[str] | None = None,
    changed_only: bool = False,
    on_complete: Callable[[BuildResult], None] | None = None,
) -> dict[str, BuildResult]:
    """
    Build grafts and update the manifest.

    Args:
        branches: List of branch specs to build (default: from grafts.yaml)
        update_manifest: Whether to update grafts.lock
        jobs: Number of parallel workers (1 = sequential)
        only: If provided, only build grafts with these names
        skip: If provided, skip grafts with these names
        changed_only: If True, skip grafts where HEAD matches last_good
        on_complete: Callback invoked after each graft completes (thread-safe)
    """
    fetch_origin()
    if branches is None:
        branches = read_branches_list()

    # Prune manifest entries for grafts no longer listed
    manifest = load_manifest()
    branch_set = {b if isinstance(b, str) else b["branch"] for b in branches}
    removed = [b for b in manifest.keys() if b not in branch_set]
    if removed:
        for b in removed:
            manifest.pop(b, None)
        save_manifest(manifest)

    # Apply --only / --skip filters
    filtered: list[BranchSpec | str] = []
    for spec in branches:
        graft_name = spec if isinstance(spec, str) else spec["name"]
        if only and graft_name not in only:
            continue
        if skip and graft_name in skip:
            continue
        filtered.append(spec)

    # Detect unchanged grafts for --changed and build the rest
    results: dict[str, BuildResult] = {}
    to_build: list[BranchSpec | str] = []

    for spec in filtered:
        branch_name = spec if isinstance(spec, str) else spec["branch"]
        graft_name = spec if isinstance(spec, str) else spec["name"]

        if changed_only:
            entry = manifest.get(branch_name, {})
            last_good = entry.get("last_good")
            if last_good:
                current_sha = resolve_head_sha(branch_name)
                if current_sha and current_sha == last_good:
                    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                    result = BuildResult(
                        branch=branch_name,
                        branch_key=branch_to_key(graft_name),
                        title=entry.get("title", graft_name),
                        status="skipped",
                        head_sha=current_sha,
                        last_good_sha=last_good,
                        built_at=now,
                        exported_relpaths=entry.get("exported", []),
                        exported_dest_paths=[],
                        nav_structure=entry.get("structure"),
                        prerendered=entry.get("prerendered", False),
                    )
                    results[branch_name] = result
                    if on_complete:
                        on_complete(result)
                    continue
        to_build.append(spec)

    # Build grafts — parallel or sequential
    parallel = jobs > 1 and len(to_build) > 1

    if parallel:
        # In parallel mode, defer manifest updates to consolidation step
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {}
            for spec in to_build:
                branch_name = spec if isinstance(spec, str) else spec["branch"]
                graft_name = spec if isinstance(spec, str) else spec["name"]
                future = pool.submit(build_branch, spec, update_manifest=False, fetch=False)
                futures[future] = (branch_name, graft_name)

            for future in as_completed(futures):
                branch_name, graft_name = futures[future]
                try:
                    res = future.result()
                except Exception as e:
                    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                    res = BuildResult(
                        branch=branch_name,
                        branch_key=branch_to_key(graft_name),
                        title=graft_name,
                        status="broken",
                        head_sha=None,
                        last_good_sha=None,
                        built_at=now,
                        exported_relpaths=[],
                        exported_dest_paths=[],
                        error_message=str(e),
                    )
                results[branch_name] = res
                if on_complete:
                    on_complete(res)

        # Consolidate manifest from parallel results
        if update_manifest:
            manifest = load_manifest()
            for b in removed:
                manifest.pop(b, None)
            for branch_name, res in results.items():
                if res.status == "skipped":
                    continue
                manifest[branch_name] = _manifest_entry_from_result(res)
            save_manifest(manifest)
    else:
        for spec in to_build:
            branch_name = spec if isinstance(spec, str) else spec["branch"]
            graft_name = spec if isinstance(spec, str) else spec["name"]
            logger.info(f"=== Building branch {branch_name} (graft '{graft_name}') ===")
            res = build_branch(spec, update_manifest=update_manifest, fetch=False)
            logger.info(f"  -> {res.status} ({len(res.exported_dest_paths)} files exported)")
            results[branch_name] = res
            if on_complete:
                on_complete(res)

    return results
