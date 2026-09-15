"""Platform options for background services with redirected logs."""

import os
import subprocess


def background_process_options() -> dict:
    if os.name == "nt":
        # DETACHED_PROCESS makes Windows ignore CREATE_NO_WINDOW; venv child
        # interpreters can consequently allocate their own visible console.
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}
