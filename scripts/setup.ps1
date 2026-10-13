$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectPath
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python virtual environment.' }
    }
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install requirements.' }
    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
    }
    Write-Host 'Setup completed. Configure .env locally, then run: .\.venv\Scripts\python.exe main.py --once --dry-run'
} finally {
    Pop-Location
}
