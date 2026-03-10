from __future__ import annotations

import threading

from ruamel.yaml import YAML  # type: ignore

_thread_local = threading.local()


def get_yaml_loader() -> YAML:
    """Get or create a thread-local YAML loader instance.

    Each thread gets its own ``YAML`` object because ``ruamel.yaml.YAML``
    maintains internal mutable state during load/dump and is not thread-safe.
    """
    loader: YAML | None = getattr(_thread_local, "yaml_loader", None)
    if loader is None:
        loader = YAML()
        loader.preserve_quotes = True
        loader.width = 4096  # Prevent line wrapping
        _thread_local.yaml_loader = loader
    return loader
