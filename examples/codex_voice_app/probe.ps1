$ErrorActionPreference = 'Stop'
$pythonCommand = if ($env:WATCHER_VOICE_PYTHON) { $env:WATCHER_VOICE_PYTHON } else { 'python' }
& $pythonCommand (Join-Path $PSScriptRoot 'probe.py') @args
exit $LASTEXITCODE
