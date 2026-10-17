#!/bin/sh
exec "${WATCHER_SDK_PYTHON:-python3}" "$(dirname "$0")/verify_sdk_management_release.py" "$@"
