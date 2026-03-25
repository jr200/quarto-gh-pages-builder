from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import constants
from .branches import BranchSpec, branch_to_key, load_manifest, read_branches_list, save_manifest
from .constants import (
    GRAFT_COLLAR_MARKER,
    GRAFTS_BUILD_RELPATH,
    QUARTO_CONFIG_YAML,
    YAML_AUTOGEN_MARKER,
)
from .file_utils import atomic_write_yaml
from .yaml_utils import get_yaml_loader

logger = logging.getLogger(__name__)

# Source formats we are willing to import from grafts
SUPPORTED_SOURCE_EXTS = {
    ".qmd",
    ".md",
    ".rmd",
    ".rmarkdown",
    ".ipynb",
}


def load_quarto_config(docs_dir: Path) -> dict[str, Any]:
    """Load Quarto configuration from docs directory."""
    qfile_yaml = docs_dir / QUARTO_CONFIG_YAML
    if qfile_yaml.exists():
        cfg_path = qfile_yaml
    else:
        raise RuntimeError(f"No {QUARTO_CONFIG_YAML} found in {docs_dir}")
    yaml_loader = get_yaml_loader()
    return yaml_loader.load(cfg_path.read_text(encoding="utf-8")) or {}


def list_available_collars(config_path: Path | None = None) -> list[str]:
    """
    List all available collar attachment points defined in the trunk _quarto.yaml.

    Searches for _GRAFT_COLLAR markers in the sidebar/chapters structure.
    Returns a list of collar names (e.g., ['main', 'notes', 'bugs']).
    """
    config_path = config_path or constants.QUARTO_PROJECT_YAML
    if not config_path.exists():
        raise RuntimeError(f"No {QUARTO_CONFIG_YAML} found at {config_path}")

    yaml_loader = get_yaml_loader()
    config = yaml_loader.load(config_path.read_text(encoding="utf-8")) or {}

    collars: list[str] = []

    def find_collars(node: Any) -> None:
        """Recursively search for _GRAFT_COLLAR markers."""
        if isinstance(node, dict):
            # Check if this dict has a _GRAFT_COLLAR key
            if GRAFT_COLLAR_MARKER in node:
                collar_name = node[GRAFT_COLLAR_MARKER]
                if isinstance(collar_name, str) and collar_name not in collars:
                    collars.append(collar_name)
            # Recurse into dict values
            for value in node.values():
                find_collars(value)
        elif isinstance(node, list):
            # Recurse into list items
            for item in node:
                find_collars(item)

    # Search in website.sidebar.contents
    sidebar_contents = config.get("website", {}).get("sidebar", {}).get("contents", [])
    find_collars(sidebar_contents)

    # Search in book.chapters
    book_chapters = config.get("book", {}).get("chapters", [])
    find_collars(book_chapters)

    return collars


def flatten_quarto_contents(entries: Any) -> list[str]:
    """
    Flatten Quarto-style contents/chapters structures into an ordered list of files.
    """
    files: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            files.append(node)
            return
        if isinstance(node, dict):
            if "file" in node and isinstance(node["file"], str):
                files.append(node["file"])
            elif "href" in node and isinstance(node["href"], str):
                files.append(node["href"])
            for key in ("contents", "chapters"):
                if key in node:
                    val = node[key]
                    # Handle both list and string values for contents/chapters
                    if isinstance(val, list):
                        for child in val:
                            walk(child)
                    elif isinstance(val, str):
                        files.append(val)

    if isinstance(entries, list):
        for e in entries:
            walk(e)

    return files


def extract_nav_structure(cfg: dict[str, Any]) -> Any:
    """
    Extract the navigation structure (sidebar or chapters) from a graft's _quarto.yaml.
    Returns the raw contents/chapters structure to be preserved in the manifest.
    """
    website = cfg.get("website") or {}
    sidebar = website.get("sidebar") or {}
    sidebar_contents = sidebar.get("contents")

    if sidebar_contents:
        return sidebar_contents

    book = cfg.get("book") or {}
    book_chapters = book.get("chapters")

    if book_chapters:
        return book_chapters

    return None


def _glob_matches(pattern: str, path: str) -> bool:
    """Check if *path* matches a Quarto-style glob pattern.

    Handles ``**`` (recursive) by treating the prefix before ``**`` as a
    directory prefix match.  Simpler patterns (e.g. ``*.qmd``) fall back to
    :func:`fnmatch.fnmatch`.
    """
    if "**" in pattern:
        prefix = pattern.split("**")[0]
        return path.startswith(prefix)
    import fnmatch

    return fnmatch.fnmatch(path, pattern)


def _build_auto_nav(relpaths: list[str]) -> list[Any]:
    """Build a hierarchical nav structure from a flat list of file paths.

    Mimics Quarto's ``auto`` behaviour: creates ``section`` entries for
    directories, excludes index files, and preserves the full relative
    paths so that :func:`rewrite_paths` can process them later.
    """
    _index_stems = {"index"}

    filtered = sorted(rp for rp in relpaths if Path(rp).stem.lower() not in _index_stems)

    def _build_level(paths: list[str], prefix: str) -> list[Any]:
        local_files: list[str] = []
        subdirs: dict[str, list[str]] = {}

        for p in paths:
            rel = p[len(prefix) :] if prefix else p
            parts = Path(rel).parts
            if len(parts) == 1:
                local_files.append(p)
            else:
                subdir = parts[0]
                if subdir not in subdirs:
                    subdirs[subdir] = []
                subdirs[subdir].append(p)

        items: list[Any] = []
        items.extend(local_files)
        for dirname in sorted(subdirs):
            section_name = dirname.replace("-", " ").replace("_", " ").title()
            contents = _build_level(subdirs[dirname], f"{prefix}{dirname}/")
            if contents:
                items.append({"section": section_name, "contents": contents})
        return items

    return _build_level(filtered, "")


def _build_glob_nav(matches: list[str], prefix: str) -> list[Any]:
    """Build a hierarchical nav structure from glob-matched file paths.

    Like :func:`_build_auto_nav` but keeps index files and strips the glob
    *prefix* so that subdirectories beneath it become sections.
    """

    def _build_level(paths: list[str], level_prefix: str) -> list[Any]:
        local_files: list[str] = []
        subdirs: dict[str, list[str]] = {}

        for p in paths:
            rel = p[len(level_prefix) :] if level_prefix else p
            parts = Path(rel).parts
            if len(parts) == 1:
                local_files.append(p)
            else:
                subdir = parts[0]
                if subdir not in subdirs:
                    subdirs[subdir] = []
                subdirs[subdir].append(p)

        items: list[Any] = []
        items.extend(local_files)
        for dirname in sorted(subdirs):
            section_name = dirname.replace("-", " ").replace("_", " ").title()
            contents = _build_level(subdirs[dirname], f"{level_prefix}{dirname}/")
            if contents:
                items.append({"section": section_name, "contents": contents})
        return items

    return _build_level(sorted(matches), prefix)


def expand_nav_globs(nav_structure: Any, src_relpaths: list[str]) -> Any:
    """Expand glob patterns and ``auto`` in a nav structure into explicit entries.

    Quarto sidebar entries like ``contents: investigations/**`` are glob
    patterns that Quarto expands at render time.  The ``auto`` keyword tells
    Quarto to auto-generate the sidebar from the file system.  When cached
    pages are served as pre-rendered ``.html`` files (no source files on disk),
    Quarto's auto-expansion fails because it cannot find source files.

    By expanding globs and ``auto`` into explicit file lists *before* saving
    the manifest, :func:`rewrite_paths` in :func:`apply_manifest` can convert
    each entry individually to an ``href`` for cached pages or a ``file`` path
    for non-cached pages.
    """
    if nav_structure is None:
        return None

    def _is_auto(value: Any) -> bool:
        return isinstance(value, str) and value.lower() == "auto"

    def _expand(node: Any) -> Any:
        if isinstance(node, str):
            if node.lower() == "auto":
                return _build_auto_nav(src_relpaths)
            if "*" in node:
                matches = sorted(rp for rp in src_relpaths if _glob_matches(node, rp))
                if matches:
                    # Determine the fixed prefix before the glob wildcard
                    glob_prefix = node.split("*")[0]
                    return _build_glob_nav(matches, glob_prefix)
            return node
        elif isinstance(node, dict):
            result = {}
            for key, value in node.items():
                if key in ("contents", "chapters"):
                    expanded = _expand(value)
                    result[key] = expanded
                else:
                    result[key] = value
            return result
        elif isinstance(node, list):
            expanded_list: list[Any] = []
            for item in node:
                expanded = _expand(item)
                if isinstance(expanded, list) and not isinstance(item, list):
                    # A glob or auto was expanded — flatten into the parent list
                    expanded_list.extend(expanded)
                else:
                    expanded_list.append(expanded)
            return expanded_list
        return node

    return _expand(nav_structure)


def filter_nav_missing(nav_structure: Any, src_relpaths: list[str]) -> Any:
    """Remove explicit file entries from *nav_structure* that are not in *src_relpaths*.

    When a page is deleted from the graft but its ``_quarto.yml`` still
    references it, the nav structure will contain a stale entry.  Without
    filtering, ``apply_manifest`` emits a file reference to a path that
    doesn't exist, causing Quarto to silently drop it from the sidebar.
    """
    if nav_structure is None:
        return None

    src_set = set(src_relpaths)

    def _is_source_ref(value: str) -> bool:
        """Return True if *value* looks like a source file path."""
        return Path(value).suffix.lower() in SUPPORTED_SOURCE_EXTS

    def _filter(node: Any) -> Any:
        if isinstance(node, str):
            if _is_source_ref(node) and node not in src_set:
                return None  # sentinel: drop this entry
            return node
        elif isinstance(node, dict):
            # Check file/href values
            for key in ("file", "href"):
                val = node.get(key)
                if val and isinstance(val, str) and _is_source_ref(val) and val not in src_set:
                    return None
            result = {}
            for key, value in node.items():
                if key in ("contents", "chapters"):
                    filtered = _filter(value)
                    if filtered:  # keep non-empty lists
                        result[key] = filtered
                else:
                    result[key] = value
            return result
        elif isinstance(node, list):
            filtered_list: list[Any] = []
            for item in node:
                filtered = _filter(item)
                if filtered is not None:
                    filtered_list.append(filtered)
            return filtered_list
        return node

    return _filter(nav_structure)


def collect_exported_relpaths(docs_dir: Path, cfg: dict[str, Any]) -> list[str]:
    """
    Determine which *source documents* to export from this branch's docs/,
    preserving the branch author's intended order as far as possible.
    """

    def _resolve_entry(entry: str) -> list[Path]:
        """
        Resolve an entry from sidebar/chapters contents.
        Handles individual files, directories, glob patterns, and "auto".
        Returns a list of matching file paths.
        """
        # Handle "auto" - include all files except index pages
        if entry.lower() == "auto":
            matches = []
            index_names = {"index.qmd", "index.md", "index.rmd", "index.rmarkdown", "index.ipynb"}
            for p in sorted(docs_dir.rglob("*"), key=lambda p: p.as_posix()):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in SUPPORTED_SOURCE_EXTS:
                    continue
                # Exclude index files (home pages)
                if p.name.lower() in index_names:
                    continue
                # Exclude hidden files and special directories
                if any(part.startswith(".") for part in p.parts):
                    continue
                if any(part in {"_site", ".quarto", "__pycache__", "node_modules"} for part in p.parts):
                    continue
                matches.append(p)
            return matches

        # Check if it's a glob pattern (contains * or **)
        if "*" in entry:
            matches = []
            # Special handling for patterns ending with /** (recursive match)
            # In Python 3.12, glob("path/**") doesn't match files inside, only the directory
            # We need to use rglob or append "/*" to the pattern
            if entry.endswith("/**"):
                # Use rglob for recursive matching
                base_path = docs_dir / entry[:-3]  # Remove the "/**" suffix
                if base_path.exists() and base_path.is_dir():
                    for p in base_path.rglob("*"):
                        if p.is_file() and p.suffix.lower() in SUPPORTED_SOURCE_EXTS:
                            matches.append(p)
            else:
                # Use glob for other patterns
                for p in docs_dir.glob(entry):
                    if p.is_file() and p.suffix.lower() in SUPPORTED_SOURCE_EXTS:
                        matches.append(p)
            return sorted(matches, key=lambda p: p.as_posix())

        # Try direct path
        direct = docs_dir / entry

        # If it's a directory, find all supported files in it
        if direct.exists() and direct.is_dir():
            matches = []
            for p in sorted(direct.rglob("*"), key=lambda p: p.as_posix()):
                if p.is_file() and p.suffix.lower() in SUPPORTED_SOURCE_EXTS:
                    matches.append(p)
            return matches

        # If it's a file, return it
        if direct.exists() and direct.is_file():
            return [direct]

        # If not found directly, search recursively for exact relative match
        rel_path = Path(entry)
        for p in docs_dir.rglob(rel_path.name):
            if not p.is_file():
                continue
            try:
                # Check if the full relative path matches exactly
                if p.relative_to(docs_dir).as_posix() == entry:
                    return [p]
            except ValueError:
                continue

        return []

    project = cfg.get("project") or {}
    render_spec = project.get("render")

    website = cfg.get("website") or {}
    sidebar = website.get("sidebar") or {}
    sidebar_contents = sidebar.get("contents")

    book = cfg.get("book") or {}
    book_chapters = book.get("chapters")

    relpaths: list[str] = []

    # website.sidebar.contents: use nav order
    # Handle both string and list values for contents
    if isinstance(sidebar_contents, str):
        files_from_sidebar = [sidebar_contents]
    else:
        files_from_sidebar = flatten_quarto_contents(sidebar_contents)

    if files_from_sidebar:
        logger.debug(f"Processing sidebar contents: {files_from_sidebar}")
        for entry in files_from_sidebar:
            logger.debug(f"  Resolving entry: {entry!r}")
            paths = _resolve_entry(entry)
            logger.debug(f"    Found {len(paths)} path(s)")
            for p in paths:
                if p.suffix.lower() not in SUPPORTED_SOURCE_EXTS:
                    continue
                rel = p.relative_to(docs_dir).as_posix()
                logger.debug(f"    Adding: {rel}")
                if rel not in relpaths:
                    relpaths.append(rel)
        logger.debug(f"Total sidebar files: {len(relpaths)}")
        if relpaths:
            return relpaths

    # book.chapters: for branch-type "book" projects
    # Handle both string and list values for chapters
    if isinstance(book_chapters, str):
        files_from_book = [book_chapters]
    else:
        files_from_book = flatten_quarto_contents(book_chapters)

    if files_from_book:
        for entry in files_from_book:
            paths = _resolve_entry(entry)
            for p in paths:
                if p.suffix.lower() not in SUPPORTED_SOURCE_EXTS:
                    continue
                rel = p.relative_to(docs_dir).as_posix()
                if rel not in relpaths:
                    relpaths.append(rel)
        if relpaths:
            return relpaths

    # project.render: canonical, keep order
    if isinstance(render_spec, list) and render_spec:
        for entry in render_spec:
            if not isinstance(entry, str):
                continue
            for p in docs_dir.glob(entry):
                if p.is_dir():
                    continue
                if p.suffix.lower() not in SUPPORTED_SOURCE_EXTS:
                    continue
                rel = p.relative_to(docs_dir).as_posix()
                if rel not in relpaths:
                    relpaths.append(rel)
        if relpaths:
            return relpaths

    # Fallback: scan docs/ for supported sources (order not guaranteed)
    for p in sorted(docs_dir.rglob("*"), key=lambda path: path.as_posix()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in SUPPORTED_SOURCE_EXTS:
            continue
        if any(part in {".quarto", "_site"} for part in p.parts):
            continue
        rel = p.relative_to(docs_dir).as_posix()
        relpaths.append(rel)

    return relpaths


def derive_section_title(cfg: dict[str, Any], branch: str) -> str:
    """Derive the section title from Quarto configuration or use branch name."""
    website = cfg.get("website") or {}
    book = cfg.get("book") or {}
    title = website.get("title") or book.get("title") or branch
    return str(title)


def is_collar_marker(item: Any) -> bool:
    """Check if item is a collar marker (_GRAFT_COLLAR)."""
    return isinstance(item, Mapping) and GRAFT_COLLAR_MARKER in item


def _find_all_collars(seq: list[Any]) -> dict[str, tuple[list[Any], int]]:
    """
    Find all collar markers in the structure.
    Returns dict mapping collar_name -> (list_ref, index).
    """
    collars: dict[str, tuple[list[Any], int]] = {}

    def search(items: list[Any]) -> None:
        for idx, item in enumerate(items):
            if is_collar_marker(item):
                collar_name = item[GRAFT_COLLAR_MARKER]
                if isinstance(collar_name, str):
                    collars[collar_name] = (items, idx)
            if isinstance(item, Mapping):
                for key in ("contents", "chapters"):
                    child = item.get(key)
                    if isinstance(child, list):
                        search(child)

    search(seq)
    return collars


def apply_manifest() -> None:
    """
    Update _quarto.yaml to match docs/grafts__ content, using
    grafts.lock and grafts.yaml.
    """
    quarto_file = constants.QUARTO_PROJECT_YAML
    # text = quarto_file.read_text(encoding="utf-8")

    with open(quarto_file) as fp:
        yaml_loader = get_yaml_loader()
        # data = yaml_loader.load(text) or {}
        data = yaml_loader.load(fp) or {}

    project = data.get("project") or {}
    project_type = str(project.get("type") or "").lower()

    manifest = load_manifest()
    branches: list[BranchSpec] = read_branches_list()
    branch_set = {b["branch"] for b in branches}

    # Prune manifest entries for branches no longer listed
    removed = [b for b in manifest.keys() if b not in branch_set]
    if removed:
        logger.info("Pruning grafts removed from grafts.yaml: %s", ", ".join(removed))
        for b in removed:
            manifest.pop(b, None)
        save_manifest(manifest)

    # Source file extensions that map to .html when pre-rendered
    _source_exts = {".qmd", ".md", ".ipynb", ".rmd", ".rmarkdown"}

    # Build auto-generated items grouped by collar
    def build_collar_items(item_type: str) -> dict[str, list[Any]]:
        """
        Build items grouped by collar, preserving the original structure from each graft.
        Rewrites all file paths to prepend grafts__/{branch_key}/.
        For pre-rendered grafts, converts source file extensions to .html and uses href links.
        """
        collar_items: dict[str, list[Any]] = {}
        content_key = "chapters" if item_type == "part" else "contents"

        def _to_html_href(file_path: str, branch_key: str) -> str | dict[str, str]:
            """Convert a source file path to a pre-rendered HTML href entry."""
            p = Path(file_path)
            if p.suffix.lower() in _source_exts:
                html_path = f"{GRAFTS_BUILD_RELPATH}/{branch_key}/{p.with_suffix('.html').as_posix()}"
            else:
                html_path = f"{GRAFTS_BUILD_RELPATH}/{branch_key}/{file_path}"
            text = p.stem.replace("-", " ").replace("_", " ").title()
            return {"text": text, "href": html_path}

        def _is_cached_page(file_path: str, cached_set: set[str] | None) -> bool:
            """Check if a source file path is in the cached pages set."""
            if not cached_set:
                return False
            return file_path in cached_set

        def rewrite_paths(
            node: Any,
            branch_key: str,
            prerendered: bool = False,
            cached_set: set[str] | None = None,
        ) -> Any:
            """Recursively rewrite file paths in a structure to prepend grafts__/{branch_key}/.

            For prerendered grafts, all pages use href with .html extension.
            For cached pages (per-page), only those in *cached_set* use href.
            """
            if isinstance(node, str):
                if prerendered or _is_cached_page(node, cached_set):
                    return _to_html_href(node, branch_key)
                # It's a file path - prepend the graft path
                return f"{GRAFTS_BUILD_RELPATH}/{branch_key}/{node}"
            elif isinstance(node, dict):
                # Recursively process dict values
                result = {}
                for key, value in node.items():
                    if key in (content_key, "chapters", "contents"):
                        # Recursively process contents/chapters
                        result[key] = rewrite_paths(value, branch_key, prerendered, cached_set)
                    elif key in ("file", "href"):
                        if prerendered or _is_cached_page(value, cached_set):
                            # Convert file refs to href with .html extension
                            p = Path(value)
                            if p.suffix.lower() in _source_exts:
                                result["href"] = (
                                    f"{GRAFTS_BUILD_RELPATH}/{branch_key}/{p.with_suffix('.html').as_posix()}"
                                )
                            else:
                                result["href"] = f"{GRAFTS_BUILD_RELPATH}/{branch_key}/{value}"
                            # Ensure a text field so quarto can display the sidebar entry
                            if "text" not in node:
                                result["text"] = p.stem.replace("-", " ").replace("_", " ").title()
                        else:
                            # These are file references
                            result[key] = f"{GRAFTS_BUILD_RELPATH}/{branch_key}/{value}"
                    else:
                        # Keep other keys as-is
                        result[key] = value
                return result
            elif isinstance(node, list):
                # Recursively process list items
                return [rewrite_paths(item, branch_key, prerendered, cached_set) for item in node]
            else:
                # Return as-is for other types
                return node

        for spec in branches:
            branch = spec["branch"]
            collar = spec["collar"]
            entry = manifest.get(branch)
            if not entry:
                continue
            title = entry.get("title") or spec["name"]
            branch_key = entry.get("branch_key") or branch_to_key(spec["name"])
            structure = entry.get("structure")
            is_prerendered = bool(entry.get("prerendered"))
            cached_pages_list = entry.get("cached_pages", [])
            cached_set = set(cached_pages_list) if cached_pages_list else None

            # If no structure is preserved, skip this graft
            if not structure:
                logger.warning(f"No structure found for graft '{branch}' - skipping")
                continue

            # Rewrite all paths in the structure
            rewritten_structure = rewrite_paths(
                structure, branch_key, prerendered=is_prerendered, cached_set=cached_set
            )

            # Wrap in a section/part with the graft title
            item = {
                item_type: title,
                content_key: rewritten_structure if isinstance(rewritten_structure, list) else [rewritten_structure],
                YAML_AUTOGEN_MARKER: branch,
            }

            if collar not in collar_items:
                collar_items[collar] = []
            collar_items[collar].append(item)
        return collar_items

    # Helper: update all collars with their grafts
    def splice_collars(seq: list[Any], collar_items: dict[str, list[Any]]) -> None:
        """Find all collar markers and inject the appropriate grafts after each."""
        collars = _find_all_collars(seq)

        # For each collar marker, inject the grafts that belong to it
        for collar_name, (target_list, marker_idx) in collars.items():
            items = collar_items.get(collar_name, [])

            # Find the end of existing autogenerated content
            end_idx = marker_idx + 1
            while end_idx < len(target_list):
                ch = target_list[end_idx]
                if not isinstance(ch, Mapping):
                    break
                if YAML_AUTOGEN_MARKER not in ch:
                    break
                end_idx += 1

            # Replace the autogenerated content
            target_list[marker_idx + 1 : end_idx] = items

    if project_type == "book" or ("book" in data and "chapters" in (data.get("book") or {})):
        # --- Book mode ---
        book = data.get("book") or {}
        chapters = book.get("chapters")
        if not isinstance(chapters, list):
            raise RuntimeError("book.chapters must be a list")

        collar_items = build_collar_items("part")
        splice_collars(chapters, collar_items)

    elif project_type == "website" or ("website" in data and "sidebar" in (data.get("website") or {})):
        # --- Website mode ---
        website = data.get("website") or {}
        sidebar = website.get("sidebar") or {}
        contents = sidebar.get("contents")
        if not isinstance(contents, list):
            raise RuntimeError("website.sidebar.contents must be a list")

        collar_items = build_collar_items("section")
        splice_collars(contents, collar_items)

    else:
        raise RuntimeError(
            "Neither book.chapters nor website.sidebar.contents found; cannot apply auto-generated chapter updates."
        )

    # Add project resources for pre-rendered and cached grafts so Quarto copies HTML as-is
    html_resources: list[str] = []
    for spec in branches:
        entry = manifest.get(spec["branch"])
        if not entry:
            continue
        bk = entry.get("branch_key") or branch_to_key(spec["name"])
        if entry.get("prerendered"):
            # Entire graft is pre-rendered
            html_resources.append(f"{GRAFTS_BUILD_RELPATH}/{bk}/**")
        elif entry.get("cached_pages"):
            # Graft has some cached pages — their .html files need to be resources
            html_resources.append(f"{GRAFTS_BUILD_RELPATH}/{bk}/**/*.html")

    if html_resources:
        project_cfg = data.setdefault("project", {})
        resources = list(project_cfg.get("resources", []))
        for r in html_resources:
            if r not in resources:
                resources.append(r)
        project_cfg["resources"] = resources

    # Clean up stale resource entries for grafts no longer pre-rendered/cached
    existing_resources = data.get("project", {}).get("resources", [])
    if existing_resources:
        active_set = set(html_resources)
        cleaned = [r for r in existing_resources if not r.startswith(f"{GRAFTS_BUILD_RELPATH}/") or r in active_set]
        if cleaned != existing_resources:
            data.setdefault("project", {})["resources"] = cleaned
            if not cleaned:
                data["project"].pop("resources", None)

    # Write YAML back atomically
    atomic_write_yaml(quarto_file, data)

    logger.info("Synced docs/ with manifest:")
    for spec in branches:
        branch = spec["branch"]
        entry = manifest.get(branch)
        if not entry or not entry.get("last_good"):
            continue
        logger.info(f"  - {branch}: title '{entry.get('title', branch)}'")
