param(
    [int]$Port = 18108,
    [int]$StartupTimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "app\backend"
$outLog = Join-Path $repoRoot "run-check-out.log"
$errLog = Join-Path $repoRoot "run-check-err.log"

function Test-Python311 {
    param([string]$PythonExe, [string[]]$Prefix = @())

    try {
        & $PythonExe @($Prefix + @("-c", "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)")) | Out-Null
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

$process = Start-Process `
    -FilePath $python.Exe `
    -ArgumentList @($python.Prefix + @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "$Port", "--log-level", "info")) `
    -WorkingDirectory $backendDir `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

$healthStatus = $null
$healthBody = $null

try {
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $lastError = $null
    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) {
            throw "Backend exited early with code $($process.ExitCode). See $errLog"
        }

        try {
            $response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
            $healthStatus = $response.StatusCode
            $healthBody = $response.Content
            break
        } catch {
            $lastError = $_.Exception.Message
            Start-Sleep -Milliseconds 500
        }
    }

    if (-not $healthStatus) {
        throw "Health check timed out: $lastError"
    }
} finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
}

Write-Output "PYTHON=$($python.Exe) $($python.Prefix -join ' ')"
Write-Output "HEALTH_STATUS=$healthStatus"
Write-Output "HEALTH_BODY=$healthBody"
