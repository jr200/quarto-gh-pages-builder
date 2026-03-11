# quarto project root - i.e., location of _quarto.yaml
PROJECT_ROOT := docs

.PHONY: all env build render preview clean clean-grafts clean-all cov cov-html bump release

all: render

## Setup Python deps (bookbuilder, PyYAML, etc.)
env:
	@echo "Syncing uv environment..."
	uv venv --clear && uv sync

## Linting
.PHONY: lint
lint:
	@echo "Running ruff..."
	uv run ruff check . --fix
	uv run ruff check --output-format=github .

## Render the main Quarto document
render:
	@echo "Rendering main Quarto project in $(PROJECT_ROOT)/..."
	uv run quarto render "$(PROJECT_ROOT)" --no-execute

## Preview the composed document (builds grafts first)
preview: render
	@echo "Starting Quarto preview for $(PROJECT_ROOT)/..."
	uv run quarto preview "$(PROJECT_ROOT)"

## Clean build artifacts
clean:
	@echo "Cleaning Quarto build artifacts..."
	rm -rf "$(PROJECT_ROOT)/_site" "$(PROJECT_ROOT)/.quarto"


clean-all: clean
	rm -rf .venv .ruff_cache .mypy_cache htmlcov .coverage
	find . -type f -name '*.py[co]' -delete
	find . -type d -name '__pycache__' -delete
	find . -type d -name '.mypy_cache' -print0 | xargs -0 rm -rf

cov:
	uv run pytest --cov --cov-report=term-missing

cov-html: cov
	uv run python -m coverage html
	@echo "Coverage report: htmlcov/index.html"

test:
	uv run pytest

## Version bump: make bump v=patch|minor|major (default: patch)
v ?= patch

bump:
	@if [ "$(v)" != "patch" ] && [ "$(v)" != "minor" ] && [ "$(v)" != "major" ]; then \
		echo "Usage: make bump v=patch|minor|major"; exit 1; \
	fi
	@python3 -c "\
import re, pathlib; \
p = pathlib.Path('pyproject.toml'); \
txt = p.read_text(); \
m = re.search(r'version = \"(\d+)\.(\d+)\.(\d+)\"', txt); \
a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3)); \
part = '$(v)'; \
nv = (f'{a+1}.0.0' if part == 'major' else f'{a}.{b+1}.0' if part == 'minor' else f'{a}.{b}.{c+1}'); \
p.write_text(txt[:m.start()] + f'version = \"{nv}\"' + txt[m.end():]); \
print(f'Bumped version: {m.group(1)}.{m.group(2)}.{m.group(3)} -> {nv}')"
	uv lock

## Release: runs lint + test, validates version/branch, tags and creates GitHub release
release: lint test
	@BRANCH=$$(git rev-parse --abbrev-ref HEAD); \
	if [ "$$BRANCH" != "master" ]; then \
		echo "Error: releases must be made from master (current: $$BRANCH)"; exit 1; \
	fi; \
	VERSION=$$(python3 -c "import re; print(re.search(r'version = \"([^\"]+)\"', open('pyproject.toml').read()).group(1))"); \
	TAG="v$$VERSION"; \
	if git rev-parse "$$TAG" >/dev/null 2>&1; then \
		echo "Error: tag $$TAG already exists — did you forget to bump?"; exit 1; \
	fi; \
	if [ -n "$$(git status --porcelain)" ]; then \
		echo "Error: working tree is not clean — commit changes first"; exit 1; \
	fi; \
	echo "Releasing $$TAG..."; \
	git tag "$$TAG" && \
	git push origin "$$TAG" && \
	gh release create "$$TAG" --generate-notes --title "$$TAG" && \
	echo "Released $$TAG"
