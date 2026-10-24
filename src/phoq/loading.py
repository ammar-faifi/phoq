"""Importing user code: setup scripts, analysis scripts, party files and protocol
packages."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path


def module_name_for(path: Path) -> str:
    """A private, deterministic module name for a user file or package
    (same in every process)."""
    return f"phoq_user_{path.stem}_{hashlib.md5(str(path).encode()).hexdigest()[:6]}"


def import_source(source: str | Path):
    """Import user code and return the module.

    ``source`` is a ``.py`` file (re-executed on every call, so edits are picked up), a
    directory with an ``__init__.py`` (imported once as a package under a private name,
    so its relative imports work), or a dotted module name. Scripts get a private module
    name, so an ``if __name__ == "__main__":`` guard stays inert.
    """
    text = str(source)
    path = Path(text)
    if path.is_dir():
        init = path / "__init__.py"
        if not init.exists():
            raise ImportError(f"{path} is not a package: no __init__.py")
        path = path.resolve()
        name = module_name_for(path)
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(
            name, init, submodule_search_locations=[str(path)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise
        return module
    if path.suffix == ".py" or path.exists():
        path = path.resolve()
        spec = importlib.util.spec_from_file_location(module_name_for(path), path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(text)


def locate(name: str, search: tuple[str, ...] = ("examples",)) -> tuple[object, str]:
    """Import a protocol or setup given as a path, a folder under ``search`` dirs, or a
    dotted name.

    Returns ``(module, locator)``; passing the locator to :func:`import_source` in
    another process imports the same thing there.
    """
    text = str(name)
    candidates = [Path(text), *(Path(d) / text for d in search)]
    for cand in candidates:
        if cand.exists():
            return import_source(cand), str(cand.resolve())
    return importlib.import_module(text), text
