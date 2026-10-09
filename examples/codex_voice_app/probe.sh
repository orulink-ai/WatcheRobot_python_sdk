#!/usr/bin/env sh
set -eu
probe_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_command=${WATCHER_VOICE_PYTHON:-python}
exec "$python_command" "$probe_root/probe.py" "$@"
