# pycode installer for Windows (PowerShell 5+).
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# What it does:
#   1. checks python (3.10+) and git
#   2. clones the repo and pip-installs it into a persistent venv
#   3. creates a pycode.cmd shim and adds it to the user PATH
#
# Environment overrides:
#   PYCODE_REPO   git URL (default: the official repository)
#   PYCODE_HOME   install location (default: %LOCALAPPDATA%\pycode)

$ErrorActionPreference = "Stop"

$Repo = if ($env:PYCODE_REPO) { $env:PYCODE_REPO } else { "https://github.com/tillietoons-gif/crazycode" }
$Home_ = if ($env:PYCODE_HOME) { $env:PYCODE_HOME } else { "$env:LOCALAPPDATA\pycode" }
$SrcDir = "$Home_\src"
$VenvDir = "$Home_\venv"
$BinDir = "$Home_\bin"

function Fail($msg) {
    Write-Error "error: $msg"
    exit 1
}

Write-Host "==> checking prerequisites"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { Fail "python is required (3.10+) - install from python.org or 'winget install Python.Python.3.11'" }
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) { Fail "git is required - install from git-scm.com or 'winget install Git.Git'" }

$pyv = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
$parts = $pyv.Split(".")
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 10)) {
    Fail "python 3.10+ is required (found $pyv)"
}
Write-Host "    python $pyv ok"

Write-Host "==> fetching pycode"
New-Item -ItemType Directory -Force $Home_ | Out-Null
if (Test-Path "$SrcDir\.git") {
    git -C $SrcDir pull --ff-only
    if ($LASTEXITCODE -ne 0) { Write-Host "    (pull failed; keeping existing source)" }
} else {
    git clone --depth 1 $Repo $SrcDir
    if ($LASTEXITCODE -ne 0) { Fail "git clone failed" }
}

Write-Host "==> installing into $VenvDir"
& python -m venv $VenvDir
if ($LASTEXITCODE -ne 0) { Fail "venv creation failed" }
& "$VenvDir\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$VenvDir\Scripts\pip.exe" install --quiet $SrcDir
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }

Write-Host "==> creating pycode shim in $BinDir"
New-Item -ItemType Directory -Force $BinDir | Out-Null
Set-Content -Path "$BinDir\pycode.cmd" -Value "@`"$VenvDir\Scripts\pycode.exe`" %*"

Write-Host "==> updating user PATH"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
    Write-Host "    added $BinDir to your user PATH (restart any open terminals)"
} else {
    Write-Host "    $BinDir already on PATH"
}

Write-Host ""
Write-Host "pycode is installed."
Write-Host ""
Write-Host "Get started:"
Write-Host "  pycode --wizard      first-run setup (provider + API key)"
Write-Host "  pycode `"your task`"   run a task"
