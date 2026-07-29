"""Single Daemon implementation owned by the watcherobot Runtime."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runtime import DaemonRuntime

__all__ = ["DaemonRuntime"]


def __getattr__(name: str) -> Any:
    if name == "DaemonRuntime":
        from .runtime import DaemonRuntime

        return DaemonRuntime
    raise AttributeError(name)
