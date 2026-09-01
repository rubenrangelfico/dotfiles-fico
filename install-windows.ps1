# install-windows.ps1 - set up FICO AI tooling on Windows
# Run as: powershell -ExecutionPolicy Bypass -File install-windows.ps1
#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$FicoDir = Join-Path $env:USERPROFILE ".fico"
$ScriptsDir = Join-Path $PSScriptRoot "fico-scripts"
$TasksDir = Join-Path $PSScriptRoot "platform\windows\tasks"
$ClaudeDir = Join-Path $PSScriptRoot "claude"
$ClaudeDest = Join-Path $env:USERPROFILE ".claude"

Write-Host "=== FICO AI Tooling - Windows Setup ===" -ForegroundColor Cyan
Write-Host "FICO_DIR:     $FicoDir"
Write-Host "Scripts from: $ScriptsDir"
Write-Host ""

# -- 1. Detect Python -------------------------------------------------------
$_py  = Get-Command python  -ErrorAction SilentlyContinue
$_py3 = Get-Command python3 -ErrorAction SilentlyContinue
if ($_py)       { $PythonPath = $_py.Source }
elseif ($_py3)  { $PythonPath = $_py3.Source }
else            { $PythonPath = $null }
if (-not $PythonPath) {
    Write-Error "Python not found. Install from https://python.org or via winget: winget install Python.Python.3"
    exit 1
}
$PyVersion = & $PythonPath --version 2>&1
Write-Host "Python: $PythonPath ($PyVersion)"

# -- 2. Create ~/.fico ------------------------------------------------------
New-Item -ItemType Directory -Force -Path $FicoDir | Out-Null
Write-Host "Created $FicoDir"

# -- 3. Copy scripts --------------------------------------------------------
Write-Host "Copying scripts to $FicoDir..."
Get-ChildItem "$ScriptsDir\*.py" | ForEach-Object {
    $dest = Join-Path $FicoDir $_.Name
    if (Test-Path $dest) {
        Write-Host "  [skip] $($_.Name) - already exists (delete to overwrite)"
    } else {
        Copy-Item $_.FullName $dest
        Write-Host "  [copy] $($_.Name)"
    }
}

# -- 4. Install pip dependencies --------------------------------------------
Write-Host ""
Write-Host "Installing Python dependencies..."
try {
    & $PythonPath -m pip install --quiet --upgrade requests websocket-client keyring win10toast 2>&1 | Out-Null
} catch { }
Write-Host "  Done."

# -- 5. Bootstrap ~/.teams_tokens.json --------------------------------------
$TokensFile = Join-Path $env:USERPROFILE ".teams_tokens.json"
if (-not (Test-Path $TokensFile)) {
    $TokensJson = '{
  "msalRefreshToken": "",
  "msalClientId": "",
  "graphToken": "",
  "mailGraphToken": "",
  "bearerToken": "",
  "skypeToken": ""
}'
    Set-Content -Path $TokensFile -Value $TokensJson -Encoding UTF8
    Write-Host "Created $TokensFile (fill in msalRefreshToken to enable fast-path refresh)"
} else {
    Write-Host "$TokensFile already exists - skipping"
}

# -- 6. Register Task Scheduler tasks ---------------------------------------
Write-Host ""
Write-Host "Registering Scheduled Tasks..."

$TaskXmls = @(
    "fico-msal-token-refresh.xml",
    "fico-teams-channel-watcher.xml",
    "fico-teams-token-refresh.xml"
)

foreach ($xmlFile in $TaskXmls) {
    $srcPath = Join-Path $TasksDir $xmlFile
    $taskName = [System.IO.Path]::GetFileNameWithoutExtension($xmlFile)

    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

    try {
        $xml = Get-Content $srcPath -Raw -Encoding UTF8
        Register-ScheduledTask -TaskName $taskName -Xml $xml | Out-Null
        Write-Host "  [registered] $taskName"
    } catch {
        Write-Host "  [WARN] Could not register ${taskName}: $_" -ForegroundColor Yellow
    }
}

# -- 7. Claude config -------------------------------------------------------
Write-Host ""
Write-Host "Claude config..."
New-Item -ItemType Directory -Force -Path $ClaudeDest | Out-Null

foreach ($f in @("settings.json", "CLAUDE.md")) {
    $src = Join-Path $ClaudeDir $f
    $dst = Join-Path $ClaudeDest $f
    if (Test-Path $src) {
        if (Test-Path $dst) {
            Write-Host "  [skip] $f - already exists"
        } else {
            Copy-Item $src $dst
            Write-Host "  [copy] $f"
        }
    }
}

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit $FicoDir\teams_channel_sound_watcher.py"
Write-Host "     Set YOUR_TEAM_ID and YOUR_CHANNEL_ID at the top"
Write-Host "  2. Populate $TokensFile with your msalRefreshToken"
Write-Host "     (get it from Teams browser localStorage the first time)"
Write-Host "  3. Run: python $FicoDir\refresh-tokens.py"
Write-Host "  4. Verify tasks in Task Scheduler or run:"
$verifyCmd = "Get-ScheduledTask | Where-Object { `$_.TaskName -like 'fico-*' }"
Write-Host "     $verifyCmd"
