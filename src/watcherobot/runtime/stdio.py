"""UTF-8 contract for Runtime command-line output, including frozen builds."""

import sys


def configure_runtime_stdio() -> None:
    """Configure CLI streams explicitly; frozen Python ignores encoding env vars.

    Embedded callers may replace streams with StringIO or leave them absent.
    Those streams have no byte encoding to configure.
    """
    for stream, errors in ((sys.stdout, "strict"), (sys.stderr, "backslashreplace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors=errors)
