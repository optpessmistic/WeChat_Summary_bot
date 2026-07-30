$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$port = if ($env:WECHAT_SUMMARY_PORT) { $env:WECHAT_SUMMARY_PORT } else { "10420" }
$pythonVersion = if ($env:WECHAT_SUMMARY_PYTHON) { $env:WECHAT_SUMMARY_PYTHON } else { "3.11" }
$url = "http://127.0.0.1:$port"

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    Write-Host "uv was not found. Install it from https://docs.astral.sh/uv/ and run this script again." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# A virtual environment created by WSL contains Linux symlinks such as
# `lib64 -> lib`, which Windows uv cannot reliably replace on a mounted drive.
# Keep the Windows environment separate so the project can be used from both
# PowerShell and WSL without either platform deleting the other's environment.
$windowsEnvironment = Join-Path $PSScriptRoot ".venv-windows"
$env:UV_PROJECT_ENVIRONMENT = $windowsEnvironment

function Invoke-UvCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Description,
        [Parameter(Mandatory = $true)]
        [string[]]$UvArguments
    )

    & $uvCommand.Source @UvArguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed because uv exited with code $LASTEXITCODE."
    }
}

Write-Host "Preparing WeChat Summary..." -ForegroundColor Green
Write-Host "Python: $pythonVersion; environment: $windowsEnvironment"
Invoke-UvCommand -Description "Dependency setup" -UvArguments @(
    "sync", "--frozen", "--python", $pythonVersion
)
Invoke-UvCommand -Description "Database migration" -UvArguments @(
    "run", "alembic", "upgrade", "head"
)

$arguments = @(
    "run", "uvicorn", "wechat_summary_bot.main:app",
    "--host", "127.0.0.1",
    "--port", $port
)

$server = Start-Process -FilePath $uvCommand.Source -ArgumentList $arguments -PassThru -NoNewWindow
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($server.HasExited) {
            $server.WaitForExit()
            $server.Refresh()
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
    if (-not $env:WECHAT_SUMMARY_NO_BROWSER) {
        Start-Process $url
    }
    $server.WaitForExit()
    $server.Refresh()
    if ($server.ExitCode -ne 0) {
        throw "The local server exited with code $($server.ExitCode)."
    }
} finally {
    if (-not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
}
