import json
import os
import subprocess
import sys

import pytest

from watcherobot.runtime.background_process import background_process_options


def test_background_options_preserve_hidden_console_contract():
    options = background_process_options()
    if os.name == "nt":
        assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert not options["creationflags"] & subprocess.DETACHED_PROCESS
    else:
        assert options == {"start_new_session": True}


@pytest.mark.skipif(os.name != "nt", reason="Windows console inheritance")
def test_real_python_and_nested_child_have_no_console():
    probe = "import ctypes,json; print(json.dumps({'console': ctypes.windll.kernel32.GetConsoleWindow()}))"
    child = (
        "import subprocess,sys; "
        f"subprocess.run([sys.executable, '-c', {probe!r}], check=True)"
    )
    for code in (probe, child):
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
            **background_process_options(),
        )
        assert json.loads(result.stdout)["console"] == 0
