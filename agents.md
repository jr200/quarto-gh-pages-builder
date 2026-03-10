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
| **Worktree cache** | `.grafts-cache/` — temporary git worktrees used during builds |

### How a Build Works

1. `trunk build` iterates over graft branches listed in `grafts.yaml`
2. Each graft is checked out into a worktree under `.grafts-cache/`
3. Quarto renders each graft into `grafts__/<graft-key>/`
4. The trunk's `_quarto.yaml` sidebar is updated to incorporate graft navigation at the appropriate collars
5. The full site is rendered from the trunk
6. Broken grafts fall back to the last-good-build and produce a warning header — they never block the site

### Module Map

```
src/quarto_graft/
├── cli.py              # Typer-based CLI entry point; trunk and graft sub-commands
├── build.py            # Build orchestration — worktree checkout, quarto render, manifest update
├── branches.py         # Branch creation, manifest/lock handling, Jinja2 template rendering
├── git_utils.py        # pygit2-based git operations — worktrees, auth, ref parsing
├── quarto_config.py    # _quarto.yaml parsing, collar discovery, navigation assembly
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
- Test files: `test_branches.py`, `test_file_utils.py`, `test_archive.py`

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
quarto-graft trunk init             # initialize trunk from template
quarto-graft trunk build            # build all grafts + site
quarto-graft trunk lock             # update _quarto.yaml from grafts.lock
quarto-graft trunk list             # list trunk templates

quarto-graft graft create <name>    # create a new graft branch
quarto-graft graft build -b <name>  # build a single graft
quarto-graft graft list             # list grafts with status
quarto-graft graft destroy <name>   # remove a graft branch
quarto-graft graft archive          # pre-render for faster builds
quarto-graft graft restore          # remove pre-rendered content
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
- Protected branches (`main`, `master`, `gh-pages`) cannot be used as graft names.
- Template variables use Jinja2 `{{ double_brace }}` syntax with `StrictUndefined`.
