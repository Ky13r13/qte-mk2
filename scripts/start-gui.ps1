# Native Windows launcher. Uses only this checkout's existing environment.
param([ValidateRange(1, 65535)][int]$Port = 8765)
$ErrorActionPreference = "Stop"
$QteRoot = Split-Path -Parent $PSScriptRoot
$QtePython = Join-Path $QteRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $QtePython -PathType Leaf)) {
    throw "A Windows .venv is required in this checkout. See docs/windows-gui.md. Nothing was installed."
}
Push-Location $QteRoot
try {
    & $QtePython -m qte gui --repo-root $QteRoot --port $Port
    if ($LASTEXITCODE -ne 0) { throw "QTE exited with code $LASTEXITCODE. See the error above." }
} finally {
    Pop-Location
}
