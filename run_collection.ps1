# run_collection.ps1
# Called by Windows Task Scheduler 4x per weekday.
# Loads credentials from .env, runs the Python collector, then commits and
# pushes new snapshots to GitHub. All output is appended to logs\collection.log
# and also printed to the console so you can see it during manual runs.

# Fallback in case $PSScriptRoot is empty in some invocation contexts
$projectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path $MyInvocation.MyCommand.Path -Parent }
$logFile     = Join-Path $projectRoot "logs\collection.log"
$pythonExe   = Join-Path $projectRoot ".venv\Scripts\python.exe"
$scriptPath  = Join-Path $projectRoot "fuel_snapshot.py"
$envFile     = Join-Path $projectRoot ".env"

New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot "logs") | Out-Null

function Write-Log ([string]$msg) {
    $ts   = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $line = "[$ts] $msg"
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

Write-Log "=== run started ==="
Write-Log "root: $projectRoot"
Write-Log ".env exists: $(Test-Path $envFile)"
Write-Log "python exists: $(Test-Path $pythonExe)"

if (-not (Test-Path $envFile)) {
    Write-Log "ERROR: .env not found - aborting"
    exit 1
}

# Load credentials from .env
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
    }
}
Write-Log "credentials loaded"

# Run the Python collector
Write-Log "launching Python collector..."
Set-Location $projectRoot
$pythonOut = & $pythonExe $scriptPath 2>&1
$exitCode  = $LASTEXITCODE
Write-Log "Python exited with code $exitCode"
$pythonOut | ForEach-Object { Write-Log "  $($_.ToString())" }

if ($exitCode -ne 0) {
    Write-Log "ERROR: collector exited $exitCode - skipping commit"
    exit $exitCode
}

# Stage new snapshot files only, then commit and push
$addOut = git add "data/raw/" 2>&1
$addOut | ForEach-Object { Write-Log "  git add: $($_.ToString())" }

$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Log "nothing new to commit"
    Write-Log "=== done ==="
    exit 0
}

$commitTs  = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$commitOut = git commit -m "snapshot: $commitTs" 2>&1
$commitOut | ForEach-Object { Write-Log "  git commit: $($_.ToString())" }

# Integrate remote commits first (the CI bot pushes gold rebuilds to main).
# Snapshot commits touch only data/raw/ and the bot touches only data/gold/,
# so this rebase should never conflict in normal operation.
$pullOut = git pull --rebase origin main 2>&1
$pullOut | ForEach-Object { Write-Log "  git pull --rebase: $($_.ToString())" }
if ($LASTEXITCODE -ne 0) {
    $abortOut = git rebase --abort 2>&1
    $abortOut | ForEach-Object { Write-Log "  git rebase --abort: $($_.ToString())" }
    Write-Log "ERROR: git pull --rebase failed - snapshot committed locally, push skipped, will retry next run"
    exit 1
}

$pushOut = git push 2>&1
$pushOut | ForEach-Object { Write-Log "  git push: $($_.ToString())" }
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: git push failed - snapshot committed locally, will retry next run"
    exit 1
}

$fileCount = ($staged | Measure-Object).Count
Write-Log "pushed $fileCount file(s)"
Write-Log "=== done ==="
