<#
    Task Scheduler wrapper for classroom_image_sync.py.

    Kept compatible with Windows PowerShell 5.1 so the scheduled task can call
    the always-present powershell.exe rather than depending on pwsh being
    installed. Runs with --no-browser: a scheduled run must never block on a
    consent window nobody is there to click.
#>

$ErrorActionPreference = "Stop"

$root   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$python = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $root "classroom_image_sync.py"

if (-not (Test-Path $python)) {
    Write-Error "Virtualenv missing at $python. Run: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

& $python $script --no-browser
$code = $LASTEXITCODE

switch ($code) {
    0 { Write-Output "Sync completed." }
    2 { Write-Warning "Consent expired. Run '.venv\Scripts\python.exe classroom_image_sync.py' by hand once to re-authorise." }
    default { Write-Warning "Sync failed with exit code $code. See logs\sync.log." }
}

exit $code
