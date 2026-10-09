"""Stable, non-empty diagnostic text even for exceptions with no message."""


class BusyRequest(RuntimeError):
    """An additional command was rejected, without failing the active task."""


def error_message(error: BaseException) -> str:
    return str(error).strip() or f'操作失败（{type(error).__name__}）'
