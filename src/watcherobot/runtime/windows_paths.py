"""Windows extended paths for native imports in the SDK frozen Runtime."""

from __future__ import annotations

import importlib.machinery
import ntpath
import os
import sys
from types import ModuleType
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from importlib.abc import MetaPathFinder
else:
    # Importing importlib.abc can itself import native modules before the fix.
    MetaPathFinder = object


def _extended_path(path: str) -> str:
    path = ntpath.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def native_executable_options(command: Sequence[str]) -> dict[str, str]:
    """Pass lpApplicationName explicitly; Windows otherwise limits argv[0]."""
    if os.name == "nt" and command and ntpath.isabs(command[0]):
        return {"executable": _extended_path(command[0])}
    return {}


class _FrozenNativePathFinder(MetaPathFinder):
    """Use public importlib loaders, restricted to the frozen dependency tree.

    PyInstaller's regular file finder may not see a .pyd beyond MAX_PATH, or
    may find it but pass a non-extended filename to LoadLibraryExW. Resolve
    native extensions with extended paths before that finder. Python modules,
    builtins and imports outside the bundle keep their existing loaders.
    """

    def __init__(self, root: str) -> None:
        self.root = ntpath.normcase(_extended_path(root))

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        for directory in path if path is not None else [self.root]:
            extended = _extended_path(directory)
            try:
                if ntpath.commonpath([self.root, ntpath.normcase(extended)]) != self.root:
                    continue
            except ValueError:
                continue
            finder = importlib.machinery.FileFinder(
                extended,
                (importlib.machinery.ExtensionFileLoader, importlib.machinery.EXTENSION_SUFFIXES),
            )
            spec = finder.find_spec(fullname, target)
            if spec is not None and isinstance(spec.loader, importlib.machinery.ExtensionFileLoader):
                return spec
        return None


def configure_frozen_native_paths() -> None:
    """Enable long native paths only for Windows frozen processes, once."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    root = getattr(sys, "_MEIPASS", None)
    if not root or any(isinstance(finder, _FrozenNativePathFinder) for finder in sys.meta_path):
        return
    finder = _FrozenNativePathFinder(root)
    for index, existing in enumerate(sys.meta_path):
        if existing is importlib.machinery.PathFinder:
            sys.meta_path.insert(index, finder)
            return
    sys.meta_path.append(finder)
