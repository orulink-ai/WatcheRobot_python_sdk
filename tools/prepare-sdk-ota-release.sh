#!/bin/sh
exec "${WATCHER_SDK_PYTHON:-python3}" "$(dirname "$0")/prepare_sdk_ota_release.py" "$@"
