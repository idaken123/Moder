param(
    [int]$Port = 18088
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "app\backend"

function Test-Python311 {
    param([string]$PythonExe, [string[]]$Prefix = @())

    try {
        $cmd = @($Prefix + @("-c", "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"))
        & $PythonExe @cmd | Out-Null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Resolve-Python {
    $candidates = @()

    if ($env:MH_AGENT_PYTHON) {
        $candidates += [pscustomobject]@{ Exe = $env:MH_AGENT_PYTHON; Prefix = @() }
    }

    if ($env:MH_AGENT_RUNTIME_DIR) {
        $candidates += [pscustomobject]@{ Exe = (Join-Path $env:MH_AGENT_RUNTIME_DIR "python\python.exe"); Prefix = @() }
    }

    $candidates += [pscustomobject]@{ Exe = (Join-Path $repoRoot "runtime\python\python.exe"); Prefix = @() }
    $candidates += [pscustomobject]@{ Exe = (Join-Path $repoRoot "app\runtime\python\python.exe"); Prefix = @() }
    $candidates += [pscustomobject]@{ Exe = "D:\Modex-MH-Agent\runtime\python\python.exe"; Prefix = @() }
    $candidates += [pscustomobject]@{ Exe = "C:\Windows\py.exe"; Prefix = @("-3.11") }
    $candidates += [pscustomobject]@{ Exe = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"); Prefix = @() }
    $candidates += [pscustomobject]@{ Exe = "python"; Prefix = @() }

    foreach ($candidate in $candidates) {
        if (-not $candidate.Exe) { continue }
        if ($candidate.Exe -ne "python" -and -not (Test-Path $candidate.Exe)) { continue }
        if (Test-Python311 -PythonExe $candidate.Exe -Prefix $candidate.Prefix) {
            return $candidate
        }
    }

    throw "Python 3.11 was not found. Set MH_AGENT_PYTHON or MH_AGENT_RUNTIME_DIR to a CPython 3.11 runtime."
}

if (-not (Test-Path $backendDir)) {
    throw "Backend directory not found: $backendDir"
}

$python = Resolve-Python
$env:MH_DESKTOP = "1"
$env:API_PORT = "$Port"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($env:MH_AGENT_RUNTIME_DIR) {
    $runtimeDir = $env:MH_AGENT_RUNTIME_DIR
} elseif (Test-Path (Join-Path $repoRoot "runtime")) {
    $runtimeDir = Join-Path $repoRoot "runtime"
} else {
    $runtimeDir = Split-Path -Parent (Split-Path -Parent $python.Exe)
}

$extraPaths = @(
    (Join-Path $runtimeDir "node"),
    (Join-Path $runtimeDir "git\bin"),
    (Join-Path $runtimeDir "git\cmd"),
    (Join-Path $runtimeDir "texlive\bin\windows"),
    (Join-Path $runtimeDir "texlive\miktex\bin\x64")
) | Where-Object { Test-Path $_ }

if ($extraPaths.Count -gt 0) {
    $env:PATH = ($extraPaths -join ";") + ";" + $env:PATH
}

Write-Host "Backend: $backendDir"
Write-Host "Python: $($python.Exe) $($python.Prefix -join ' ')"
Write-Host "Port: $Port"

Set-Location $backendDir
& $python.Exe @($python.Prefix + @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "$Port", "--log-level", "info"))
