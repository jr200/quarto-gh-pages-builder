from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Project root — lazy, overridable for testing
# ---------------------------------------------------------------------------
# The CLI is meant to run from the user's project root, not the package install
# directory.  In production, get_root() returns Path.cwd().resolve().
# Tests can set  ``_root_override``  before exercising code under test so that
# every ROOT-derived path points inside a temporary directory.

_root_override: Path | None = None


def get_root() -> Path:
    """Return the project root directory.

    Defaults to ``Path.cwd().resolve()``.  Tests may set
    ``constants._root_override = some_tmp_path`` to redirect all
    ROOT-derived paths.
    """
    if _root_override is not None:
        return _root_override
    return Path.cwd().resolve()


# Templates are bundled with the package under src/quarto_graft/.
PACKAGE_ROOT = Path(__file__).resolve().parent
TRUNK_TEMPLATES_DIR = PACKAGE_ROOT / "trunk-templates"
GRAFT_TEMPLATES_DIR = PACKAGE_ROOT / "graft-templates"

# Pre-render directory name (lives on graft branches, not trunk)
PRERENDER_DIR_NAME = "_prerendered"
PRERENDER_MANIFEST_NAME = ".graft-prerender.json"

# Quarto config filenames
QUARTO_CONFIG_YAML = "_quarto.yaml"

# Marker for graft attachment points in _quarto.yaml
GRAFT_COLLAR_MARKER = "_GRAFT_COLLAR"

# Marker for auto-generated content in _quarto.yaml
YAML_AUTOGEN_MARKER = "_autogen_branch"

# Template source names
TEMPLATE_SOURCE_BUILTIN = "builtin"
TRUNK_ADDONS_DIR = "with-addons"

# Protected branch names that cannot be used as grafts
TRUNK_BRANCHES = {"main", "master"}
# Render cache branch name
CACHE_BRANCH = "_cache"

PROTECTED_BRANCHES = TRUNK_BRANCHES.union({"gh-pages", CACHE_BRANCH})

# Relative path from project root to the graft build output directory.
# Used in _quarto.yaml path entries and _site/ rendered output lookups.
GRAFTS_BUILD_RELPATH = "dist"


# ---------------------------------------------------------------------------
# Lazy ROOT-derived attributes — computed on every access so that
# ``_root_override`` always takes effect.
# ---------------------------------------------------------------------------

def __getattr__(name: str):  # noqa: N807 – module-level __getattr__ (PEP 562)
    _derived = {
        "ROOT": lambda: get_root(),
        "GRAFTS_MANIFEST_FILE": lambda: get_root() / "grafts.lock",
        "GRAFTS_CONFIG_FILE": lambda: get_root() / "grafts.yaml",
        "WORKTREES_CACHE": lambda: get_root() / ".grafts-cache" / "worktrees",
        "BUILD_STATE_FILE": lambda: get_root() / ".grafts-cache" / "build-state.json",
        "QUARTO_PROJECT_YAML": lambda: get_root() / "_quarto.yaml",
        "MAIN_DOCS": lambda: get_root(),
        "GRAFTS_BUILD_DIR": lambda: get_root() / GRAFTS_BUILD_RELPATH,
    }
    if name in _derived:
        return _derived[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
