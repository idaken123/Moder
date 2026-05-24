param(
    [string]$RuntimeDir = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $repoRoot "app"

if ($RuntimeDir) {
    $env:MH_AGENT_RUNTIME_DIR = $RuntimeDir
} elseif (-not $env:MH_AGENT_RUNTIME_DIR) {
    $repoRuntime = Join-Path $repoRoot "runtime"
    $packagedRuntime = "D:\Modex-MH-Agent\runtime"

    if (Test-Path (Join-Path $repoRuntime "python\python.exe")) {
        $env:MH_AGENT_RUNTIME_DIR = $repoRuntime
    } elseif (Test-Path (Join-Path $packagedRuntime "python\python.exe")) {
        $env:MH_AGENT_RUNTIME_DIR = $packagedRuntime
    }
}

$env:MH_AGENT_BACKEND_DIR = Join-Path $appDir "backend"
if (-not $env:ELECTRON_MIRROR) {
    $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
}

Write-Output "App: $appDir"
Write-Output "Backend: $env:MH_AGENT_BACKEND_DIR"
if ($env:MH_AGENT_RUNTIME_DIR) {
    Write-Output "Runtime: $env:MH_AGENT_RUNTIME_DIR"
} else {
    Write-Output "Runtime: not set; Electron will look for Python 3.11 on this machine"
}

Set-Location $appDir
npx --yes electron .
