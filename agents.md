# Agents Guide — quarto-graft

## Project Overview

**quarto-graft** is a Python CLI tool that enables multi-author collaboration on [Quarto](https://quarto.org) documentation websites. Authors work on isolated git branches ("grafts") that attach to named points ("collars") in a main branch ("trunk"), eliminating merge conflicts and letting each author maintain independent dependencies.

**Repository:** <https://github.com/jr200/quarto-graft>
**License:** MIT
**Python:** >=3.11
**Package manager:** [uv](https://docs.astral.sh/uv/)

## Architecture

### Core Concepts

| Term | Meaning |
|------|---------|
| **Trunk** | The main branch (`main`/`master`) — owns site layout, navigation, and collar definitions |
| **Graft** | An isolated git branch where one author writes content independently |
| **Collar** | A named attachment point in the trunk's `_quarto.yaml` sidebar where grafts connect (marked with `_GRAFT_COLLAR`) |
| **Manifest** | `grafts.lock` — tracks the build state of each graft branch |
| **Render cache** | `_cache` orphan branch — stores per-page rendered HTML to skip re-rendering unchanged pages |
| **Build directory** | `dist/` — assembled graft content, worktrees, and build state |
| **Worktree cache** | `dist/worktrees/` — temporary git worktrees used during builds |
| **Build state** | `dist/build-state.json` — transient per-page hashes for cache updates |

### How a Build Works

1. `trunk build` iterates over graft branches listed in `grafts.yaml`
2. Each graft is checked out into a worktree under `dist/worktrees/`
3. For each page, a content hash (`sha256`) is computed and checked against the `_cache` branch
4. **Cache hit:** pre-rendered `.html` is restored from cache into `dist/<graft-key>/` (skips quarto render)
5. **Cache miss:** source `.qmd` is exported into `dist/` for quarto to render
6. The trunk's `_quarto.yaml` sidebar is updated — cached pages use `href:` links, uncached use `file:` refs
7. The full site is rendered from the trunk (only uncached pages go through quarto→pandoc)
8. `trunk cache update` captures newly rendered HTML back to the `_cache` branch for next time
9. Broken grafts fall back to the last-good-build and produce a warning header — they never block the site

### Module Map

```
src/quarto_graft/
├── cli.py              # Typer-based CLI entry point; trunk, graft, and cache sub-commands
├── build.py            # Build orchestration — worktree checkout, per-page cache lookup, manifest update
├── cache.py            # Per-page render cache — content hashing, _cache branch I/O, nav post-processing
├── branches.py         # Branch creation, manifest/lock handling, Jinja2 template rendering
├── git_utils.py        # pygit2-based git operations — worktrees, auth, ref parsing
├── quarto_config.py    # _quarto.yaml parsing, collar discovery, navigation assembly (mixed cached/uncached)
├── archive.py          # Pre-rendering (archive/restore) for faster trunk builds
├── template_sources.py # Template resolution — builtin, local path, URL, GitHub repo
├── file_utils.py       # Atomic file writes (JSON/YAML)
├── yaml_utils.py       # ruamel.yaml loader with quote preservation
├── constants.py        # Paths, markers, protected branch names
├── graft-templates/    # Builtin graft templates (markdown, py-jupyter, py-marimo)
└── trunk-templates/    # Builtin trunk templates (default, with-addons/gh-pages)
```

### Key Design Decisions

- **pygit2 over git CLI** — all git operations use the C libgit2 bindings; no shell-out to `git`.
- **Atomic file writes** — config and manifest writes use temp files to prevent corruption.
- **Jinja2 templates with `StrictUndefined`** — missing variables fail loudly at init time.
- **Path traversal protection** — branch-to-filesystem-key conversion rejects `..` segments.
- **Last-good-build fallback** — a broken graft never blocks site publication.
- **All build artifacts in `dist/`** — graft content, worktrees, and build state all live under `dist/` because quarto ignores dot-prefixed folders.
- **Protected branches** — `main`, `master`, `gh-pages`, and `_cache` cannot be used as graft names.
- **Per-page render cache** — `_cache` branch stores rendered HTML keyed by `sha256(content)`. Uses rootless commits (no parent chain) to avoid history accumulation. Cached and uncached pages coexist within a single graft. Navigation sidebar in cached pages is fixed via HTML post-processing after render.
- **Archive vs. cache** — archive (`graft archive`) is graft-owner-driven and stores pre-rendered HTML on the graft branch itself. Cache (`trunk cache`) is trunk-owner-driven and stores rendered HTML on a separate `_cache` orphan branch. Archived grafts skip caching entirely.

## Development Setup

```bash
# Create venv and install all deps
make env

# Or manually
uv venv --clear && uv sync
```

## Common Tasks

| Task | Command |
|------|---------|
| Lint (auto-fix) | `make lint` |
| Run tests | `make test` |
| Run tests with coverage | `make cov` |
| Render docs | `make render` |
| Preview docs | `make preview` |
| Clean build artifacts | `make clean` |
| Full clean (venv, caches) | `make clean-all` |

## Linting

Ruff is the sole linter/formatter. Configuration lives in `ruff.toml`:

- Line length: **120**
- Target: **Python 3.11**
- Rule sets: `E`, `W`, `F`, `I`, `B`, `C4`, `UP`
- `E501` (line too long) is ignored — the formatter handles it
- Files under `graft-templates/` are excluded from linting

Run with:
```bash
uv run ruff check . --fix
```

## Testing

Tests live in `tests/` and use **pytest**. Configuration is in `pytest.ini`:

- Verbose output, short tracebacks, strict markers
- Test files: `test_archive.py`, `test_branches.py`, `test_build.py`, `test_cache.py`, `test_cli.py`, `test_file_utils.py`, `test_git_utils.py`, `test_quarto_config.py`, `test_template_sources.py`

```bash
uv run pytest            # run all tests
uv run pytest --cov      # with coverage
```

## CI/CD

GitHub Actions workflows form a chained pipeline:

1. **build_uv_python_wheel_pure.yml** — builds the wheel artifact
2. **build_quarto_docs.yml** — renders the documentation site
3. **publish_uv_pypi.yml** — publishes to PyPI (requires `PYPI_API_TOKEN`)
4. **publish_release.yml** — creates a GitHub release

Workflows are coordinated via `repository-dispatch` events and share reusable workflows from `jr200/github-action-templates`.

## CLI Entry Point

The CLI is built with [Typer](https://typer.tiangolo.com/) and registered as `quarto-graft` in `pyproject.toml`:

```
quarto-graft = "quarto_graft.cli:main"
```

### Key Commands

```bash
quarto-graft                        # interactive mode
quarto-graft status                 # show build status dashboard for all grafts
quarto-graft trunk init             # initialize trunk from template
quarto-graft trunk build            # build all grafts + site (with cache)
quarto-graft trunk lock             # update _quarto.yaml from grafts.lock
quarto-graft trunk list             # list trunk templates
quarto-graft trunk cache update     # capture rendered pages into _cache branch
quarto-graft trunk cache clear      # delete and recreate _cache branch
quarto-graft trunk cache status     # show per-page cache state

quarto-graft graft create <name>    # create a new graft branch
quarto-graft graft build -b <name>  # build a single graft
quarto-graft graft list             # list grafts with status
quarto-graft graft destroy <name>   # remove a graft branch
quarto-graft graft archive          # pre-render for faster builds
quarto-graft graft restore          # remove pre-rendered content
```

### Build Flags (`trunk build`)

```bash
quarto-graft trunk build --jobs 4         # parallel builds (4 workers)
quarto-graft trunk build --changed        # only rebuild grafts with new commits
quarto-graft trunk build --only ch1       # build only specific grafts (repeatable)
quarto-graft trunk build --skip ch2       # skip specific grafts (repeatable)
quarto-graft trunk build --no-cache       # bypass render cache, export all pages as .qmd
quarto-graft trunk build -j 4 --changed   # combine: parallel + incremental
```

## Important Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata, deps, build system (uv_build) |
| `Makefile` | Dev workflow shortcuts |
| `ruff.toml` | Linter/formatter config |
| `pytest.ini` | Test runner config |
| `src/quarto_graft/constants.py` | All path constants and markers — read this first when debugging path issues |
| `grafts.yaml` | (user project) Declares which graft branches exist and their collars |
| `grafts.lock` | (user project) Build manifest tracking last-known-good state per graft |
| `_quarto.yaml` | (user project) Quarto site config — collars are defined here |

## Conventions

- All source code is under `src/quarto_graft/`.
- Imports use `from __future__ import annotations` throughout.
- YAML handling always uses `ruamel.yaml` (never PyYAML) to preserve formatting and quoting.
- Git operations always use `pygit2` — never shell out to `git`.
- Protected branches (`main`, `master`, `gh-pages`, `_cache`) cannot be used as graft names.
- Template variables use Jinja2 `{{ double_brace }}` syntax with `StrictUndefined`.
- **Keep `agents.md` up to date.** After any change that adds, removes, or renames commands, flags, modules, conventions, or architectural patterns, update this file to reflect the current state of the project.
