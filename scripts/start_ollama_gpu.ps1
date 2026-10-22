# Project-local GPU server. Does not change the user's main Ollama app/server.
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$ollamaPath = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (-not (Test-Path -LiteralPath $ollamaPath)) { throw 'Ollama executable not found.' }
$listener = Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $version = Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/version' -TimeoutSec 10
    Write-Output "Ollama already listening on 11435 ($($version.version))."
    exit 0
}
$settings = @{
    OLLAMA_HOST = '127.0.0.1:11435'
    OLLAMA_IGPU_ENABLE = '1'
    OLLAMA_VULKAN = '1'
    OLLAMA_NUM_PARALLEL = '1'
    OLLAMA_NO_CLOUD = '1'
}
$previous = @{}
try {
    foreach ($name in $settings.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
    }
    $server = Start-Process -FilePath $ollamaPath -ArgumentList 'serve' -WindowStyle Hidden -PassThru `
        -WorkingDirectory $projectPath `
        -RedirectStandardOutput (Join-Path $projectPath 'logs\ollama-gpu.stdout.log') `
        -RedirectStandardError (Join-Path $projectPath 'logs\ollama-gpu.stderr.log')
    $server.Id | Set-Content -LiteralPath (Join-Path $projectPath 'data\ollama-gpu-server.pid')
    Write-Output "GPU Ollama server started: PID $($server.Id), http://127.0.0.1:11435"
}
finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
