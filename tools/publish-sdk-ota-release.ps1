$ErrorActionPreference = 'Stop'
$sdkPython = if ($env:WATCHER_SDK_PYTHON) { $env:WATCHER_SDK_PYTHON } else { 'python' }
& $sdkPython (Join-Path $PSScriptRoot 'publish_sdk_ota_release.py') @args
exit $LASTEXITCODE
