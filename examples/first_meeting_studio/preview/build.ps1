param([Parameter(ValueFromRemainingArguments=$true)][string[]]$BuildArguments)
$ErrorActionPreference = 'Stop'
python (Join-Path $PSScriptRoot 'build.py') @BuildArguments
exit $LASTEXITCODE
