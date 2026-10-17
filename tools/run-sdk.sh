#!/bin/sh
exec "${WATCHER_SDK_PYTHON:-python3}" "$(dirname "$0")/run_sdk.py" "$@"
