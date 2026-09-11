<#
.SYNOPSIS
    Bring the machine up to the build on the share, then start it.

.DESCRIPTION
    What a user is given is one shortcut. Everything else - which build is
    current, whether this machine has it, fetching the difference - is this
    script's business, so nobody copies a folder by hand again.

    The share holds the built folder as it is, not an archive, so an update
    copies only the files that changed. Chromium is 428 MB of the 730 and never
    changes between builds, which is the whole reason for mirroring rather than
    downloading a zip each time.

    What the application writes - the reviewed choices, the saved sign-in, the
    import journals - lives beside its executable and is never touched by an
    update. Those three directories are excluded from the mirror, so they
    survive it rather than being deleted as "not on the share".

.PARAMETER Share
    The folder published to. Read from share.txt beside this script when not
    given, so the same launcher serves everyone without being edited.

.PARAMETER CheckOnly
    Bring the machine up to date and say what happened, without starting the
    application. For a scheduled pre-sync, and for the test that exercises this.
#>
[CmdletBinding()]
param(
    [string]$Share,
    [string]$Local,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$name = "CAMDS-IMDS-Importer"

function Say($text, $colour = "Gray") { Write-Host $text -ForegroundColor $colour }

# The data the application keeps beside itself. Never mirrored, never deleted.
$keep = @("config", ".runtime", "output")

if (-not $Share) {
    $pointer = Join-Path $here "share.txt"
    if (-not (Test-Path $pointer)) {
        Say "No share is configured." Red
        Say "Put the published folder's path in $pointer, one line, and run this again."
        exit 2
    }
    $Share = (Get-Content $pointer -TotalCount 1).Trim()
}
if (-not $Local) { $Local = Join-Path $here "app" }

$exe = Join-Path $Local "$name.exe"
$installed = Join-Path $here "installed.txt"

function Start-App {
    if ($CheckOnly) { return }
    if (-not (Test-Path $exe)) {
        Say "Nothing to start: $exe is not here, and the share could not supply it." Red
        exit 3
    }
    Start-Process -FilePath $exe -WorkingDirectory $Local
}

# Already open: its files cannot be replaced, and a second window on the same
# folder would be two runs sharing one set of choices and one saved session.
$running = Get-Process -Name $name -ErrorAction SilentlyContinue
if ($running) {
    Say "$name is already running." Yellow
    Say "Close it first if you want the newest build; this window can be closed."
    exit 0
}

$published = Join-Path $Share "version.txt"
if (-not (Test-Path $published)) {
    Say "The share is not reachable, or nothing is published there yet:" Yellow
    Say "  $Share"
    Say "Starting the build this machine already has."
    Start-App
    exit 0
}

$latest = (Get-Content $published -TotalCount 1).Trim()
$here_version = if (Test-Path $installed) { (Get-Content $installed -TotalCount 1).Trim() } else { "" }

if ($latest -eq $here_version -and (Test-Path $exe)) {
    Say "Up to date: $latest" Green
    Start-App
    exit 0
}

Say "A newer build is published." Cyan
Say ("  this machine : " + $(if ($here_version) { $here_version } else { "nothing installed yet" }))
Say "  on the share : $latest"
Say "Copying only what changed; the first time is the whole 730 MB."

$source = Join-Path $Share "app"
New-Item -ItemType Directory -Force $Local | Out-Null
# /MIR so a file dropped from the build is dropped here too; /XD so the three
# directories the application writes are neither copied nor deleted.
$arguments = @($source, $Local, "/MIR", "/XD") + $keep + @("/R:2", "/W:2", "/NFL", "/NDL", "/NP")
& robocopy.exe @arguments | Out-Null
# Robocopy says what it did in the exit code: under 8 is a kind of success,
# 8 and over is a failure. Treated as an ordinary program it looks like it
# fails every time it copies anything.
if ($LASTEXITCODE -ge 8) {
    Say "Copying failed (robocopy $LASTEXITCODE)." Red
    Say "The build already on this machine is untouched; starting that."
    Start-App
    exit 4
}

Set-Content -Path $installed -Value $latest -Encoding utf8
Say "Updated to $latest" Green
Start-App
