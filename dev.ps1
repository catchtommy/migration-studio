# Starts the backend (:8000) and the UI (:5173) on Windows. Ctrl+C stops both.
# The first run also sets things up: backend\.venv with the backend installed, and the UI's packages.
# Needs Python 3.12+ (python.org, or "py" launcher), Node.js 20+ (nodejs.org) and Git for Windows.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"

function Fail($message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

# --- tools --------------------------------------------------------------------------------------------------------
$LocalNode = Join-Path $Root ".tools\node"
if (Test-Path (Join-Path $LocalNode "node.exe")) { $env:Path = "$LocalNode;$env:Path" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fail "Node.js 20 or later is needed. Install the LTS version from https://nodejs.org, open a new terminal and run dev.cmd again."
}
$nodeVersion = (& node --version).Trim()
if ([int]($nodeVersion.TrimStart("v").Split(".")[0]) -lt 20) {
    Fail "Node.js $nodeVersion is too old (the UI needs 20 or later). Install the LTS version from https://nodejs.org."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "Git is needed. Install Git for Windows from https://git-scm.com/download/win and run dev.cmd again."
}

# --- first run: the backend's virtual environment -----------------------------------------------------------------
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $candidates = @(
        @{ Exe = "py"; Pre = @("-3") },
        @{ Exe = "python"; Pre = @() },
        @{ Exe = "python3"; Pre = @() }
    )
    $base = $null
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $probe = $c.Pre + @("-c", "import sys; print(int(sys.version_info >= (3, 12)))")
        $ok = (& $c.Exe @probe 2>$null)
        if ("$ok".Trim() -eq "1") { $base = $c; break }
    }
    if (-not $base) {
        Fail "Python 3.12 or later is needed. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run dev.cmd again."
    }
    Write-Host "First run: creating backend\.venv and installing the backend (a few minutes)..."
    $venvArgs = $base.Pre + @("-m", "venv", (Join-Path $Backend ".venv"))
    & $base.Exe @venvArgs
    if ($LASTEXITCODE -ne 0) { Fail "Creating the virtual environment failed." }
    & $Python -m pip install --upgrade pip
    Push-Location $Backend
    & $Python -m pip install -e ".[dev]"
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) {
        Remove-Item -Recurse -Force (Join-Path $Backend ".venv")
        Fail "Installing the backend failed (see above). Fix the cause and run dev.cmd again."
    }
}

# --- first run: the UI's packages ---------------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
    Write-Host "First run: installing the UI's packages..."
    Push-Location $Frontend
    & npm install
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Fail "npm install failed (see above)." }
}

# --- start both ---------------------------------------------------------------------------------------------------
$backendProc = Start-Process -FilePath $Python -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8000", "--reload" `
    -WorkingDirectory $Backend -NoNewWindow -PassThru
$frontendProc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npx", "vite", "--port", "5173", "--strictPort" `
    -WorkingDirectory $Frontend -NoNewWindow -PassThru
Write-Host ""
Write-Host "Backend: http://localhost:8000   UI: http://localhost:5173   (Ctrl+C stops both)" -ForegroundColor Green
try {
    while (-not $backendProc.HasExited -and -not $frontendProc.HasExited) { Start-Sleep -Seconds 1 }
    if ($backendProc.HasExited) { Write-Host "The backend stopped (exit code $($backendProc.ExitCode))." -ForegroundColor Yellow }
    if ($frontendProc.HasExited) { Write-Host "The UI stopped (exit code $($frontendProc.ExitCode))." -ForegroundColor Yellow }
}
finally {
    foreach ($p in @($backendProc, $frontendProc)) {
        if ($p -and -not $p.HasExited) { & taskkill /PID $p.Id /T /F 2>$null | Out-Null }
    }
}
