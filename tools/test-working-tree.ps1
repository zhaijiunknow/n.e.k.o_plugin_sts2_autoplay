param(
    [string]$NekoRoot = 'D:\NekoClaw\N.E.K.O',
    [string[]]$TestArgs = @('-q')
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$tests = Join-Path $repo 'tests'
$python = Join-Path $NekoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Host Python not found: $python. Set -NekoRoot to your N.E.K.O checkout."
}
$previousNekoRoot = $env:NEKO_ROOT
$testExitCode = 1
Push-Location -LiteralPath $tests
try {
    $env:NEKO_ROOT = $NekoRoot
    & $python -X utf8 -m pytest -p neko_repo_shim -p no:cacheprovider "--rootdir=$tests" @TestArgs
    $testExitCode = $LASTEXITCODE
}
finally {
    $env:NEKO_ROOT = $previousNekoRoot
    Pop-Location
}
exit $testExitCode
