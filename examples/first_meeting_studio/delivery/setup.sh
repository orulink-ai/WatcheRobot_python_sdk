#!/usr/bin/env sh
exec python3 "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/setup.py" "$@"
