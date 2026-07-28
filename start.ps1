$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$port = if ($env:WECHAT_SUMMARY_PORT) { $env:WECHAT_SUMMARY_PORT } else { "10420" }
$url = "http://127.0.0.1:$port"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv was not found. Install it from https://docs.astral.sh/uv/ and run this script again." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Preparing WeChat Summary..." -ForegroundColor Green
uv sync --frozen
uv run alembic upgrade head

$arguments = @(
    "run", "uvicorn", "wechat_summary_bot.main:app",
    "--host", "127.0.0.1",
    "--port", $port
)

$server = Start-Process -FilePath "uv" -ArgumentList $arguments -PassThru -NoNewWindow
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($server.HasExited) {
            throw "The local server exited with code $($server.ExitCode)."
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/health" -TimeoutSec 1
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "Timed out while waiting for the local server."
    }
    Write-Host "WeChat Summary is ready at $url" -ForegroundColor Green
    Start-Process $url
    Wait-Process -Id $server.Id
} finally {
    if (-not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
}
