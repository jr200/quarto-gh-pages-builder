# {{ graft_name }}

This graft ships a minimal Python + Quarto + marimo starter:

- uv-managed environment
- Python package at `src/{{ package_name }}/`
- Quarto docs with an interactive marimo notebook
- Make targets to render/preview/clean

Tweak the package and docs, then run `make render` to build the section.
