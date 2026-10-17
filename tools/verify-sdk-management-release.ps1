$ErrorActionPreference = 'Stop'
$sdkPython = if ($env:WATCHER_SDK_PYTHON) { $env:WATCHER_SDK_PYTHON } else { 'python' }
& $sdkPython (Join-Path $PSScriptRoot 'verify_sdk_management_release.py') @args
exit $LASTEXITCODE
