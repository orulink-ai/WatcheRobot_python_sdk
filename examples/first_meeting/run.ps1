$ErrorActionPreference = 'Stop'
python (Join-Path $PSScriptRoot 'launch.py') @args
exit $LASTEXITCODE
