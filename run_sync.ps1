<#
    Optional console wrapper for classroom_image_sync.py.

    Kept compatible with Windows PowerShell 5.1 for manual use. Scheduled runs
    use pythonw.exe with run_sync_background.py to avoid opening a console.
    Runs with --no-browser so it cannot block on a consent window.
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
