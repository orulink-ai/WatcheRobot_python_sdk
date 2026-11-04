# Same arguments and exit codes as setup.sh; Python 3.10-3.12 required.
& python "$PSScriptRoot/setup.py" @args
exit $LASTEXITCODE
