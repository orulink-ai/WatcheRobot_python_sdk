$ErrorActionPreference = 'Stop'
$sdkPython = if ($env:WATCHER_SDK_PYTHON) { $env:WATCHER_SDK_PYTHON } else { 'python' }
& $sdkPython (Join-Path $PSScriptRoot 'run_sdk.py') @args
exit $LASTEXITCODE
