<#
.SYNOPSIS
    Build CAMDS IMDS Importer into something another Windows machine can run
    with no Python installed.

.DESCRIPTION
    Two things make this application large: PySide6 (about 640 MB installed)
    and the Chromium that Playwright drives (about 430 MB). What you do about
    the second one is the only real decision here.

    Default: one .exe, no browser inside. The first run downloads Chromium once
    into the user's own profile, which needs the internet - and CAMDS needs the
    internet anyway. The file is around 150 MB and starts in a few seconds.

    -IncludeBrowser: nothing to download, nothing to install, works on a machine
    that has never seen Playwright. Use it with -OneDir. In a single file the
    whole 600 MB is unpacked to %TEMP% on every launch, which takes about a
    minute each time; as a folder it starts at once.

.PARAMETER IncludeBrowser
    Bundle Chromium so the build needs no download on the target machine.

.PARAMETER OneDir
    Produce a folder instead of a single file. Starts immediately, because
    nothing is unpacked at launch. Copy or zip the whole folder.

.PARAMETER Clean
    Discard the build virtual environment and start from scratch.

.EXAMPLE
    .\build.ps1
    One file, browser downloaded on first run.

.EXAMPLE
    .\build.ps1 -IncludeBrowser -OneDir
    A self-contained folder. Nothing to install on the target machine.
#>
[CmdletBinding()]
param(
    [switch]$IncludeBrowser,
    [switch]$OneDir,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = $PSScriptRoot
$buildVenv = Join-Path $root ".venv-build"
$distDir = Join-Path $root "dist"
$workDir = Join-Path $root "build"
$name = "CAMDS-IMDS-Importer"

function Step($text) { Write-Host "`n=== $text" -ForegroundColor Cyan }
function Note($text) { Write-Host "    $text" -ForegroundColor DarkGray }

function Invoke-Native {
    <#
        Run an executable and judge it by its exit code.

        Windows PowerShell 5.1 turns anything a native program writes to stderr
        into an ErrorRecord, and $ErrorActionPreference = "Stop" then ends the
        script. PyInstaller writes its ordinary progress there, so its first
        INFO line killed this build before it started. Only the exit code says
        whether a program failed.
    #>
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory)][string]$What
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Exe @Arguments } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE." }
}

# Python 3.11 is a hard requirement: StrEnum is used throughout.
Step "Checking Python"
$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) { throw "Python is not on PATH. Install Python 3.11 or newer first." }
$version = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$version -lt [version]"3.11") {
    throw "Python $version found, but 3.11 or newer is required (StrEnum)."
}
Note "Python $version"

if ($Clean -and (Test-Path $buildVenv)) {
    Step "Removing the previous build environment"
    Remove-Item -Recurse -Force $buildVenv
}

Step "Preparing the build environment"
if (-not (Test-Path $buildVenv)) { Invoke-Native python @("-m", "venv", $buildVenv) "Creating the virtual environment" }
$venvPython = Join-Path $buildVenv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "Could not create a virtual environment at $buildVenv" }

Invoke-Native $venvPython @("-m", "pip", "install", "--upgrade", "pip", "--quiet") "pip"
Invoke-Native $venvPython @("-m", "pip", "install", "-r",
    (Join-Path $root "camds_imds_importer\requirements.txt"), "--quiet") "Installing dependencies"
Invoke-Native $venvPython @("-m", "pip", "install", "pyinstaller", "--quiet") "Installing PyInstaller"
Note "Dependencies installed"

Step "Installing the Chromium Playwright drives"
if ($IncludeBrowser) {
    # "0" puts the browser inside the playwright package, where PyInstaller can
    # collect it. Anywhere else and it would not travel with the build.
    $env:PLAYWRIGHT_BROWSERS_PATH = "0"
    Note "Into the package, so it is bundled (adds about 430 MB)"
} else {
    Remove-Item Env:\PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
    Note "Into this machine's profile; the target machine downloads its own on first run"
}
# A browser this machine already has is the browser this build needs. Playwright
# keeps one per profile, and a bundled build looks inside its own package
# instead - so without this the same 430 MB is fetched a second time on a
# machine that is already carrying it, and on a network that will not serve it
# the build fails with one sitting on the disk.
if ($IncludeBrowser) {
    $packaged = Join-Path $buildVenv "Lib\site-packages\playwright\driver\package\.local-browsers"
    $profileBrowsers = Join-Path $env:LOCALAPPDATA "ms-playwright"
    $alreadyPackaged = Test-Path (Join-Path $packaged "chromium-*")
    if (-not $alreadyPackaged -and (Test-Path $profileBrowsers)) {
        $carried = Get-ChildItem $profileBrowsers -Directory |
            Where-Object { $_.Name -like "chromium-*" -or $_.Name -like "winldd-*" }
        if ($carried) {
            Step "Taking the browser this machine already has"
            New-Item -ItemType Directory -Force $packaged | Out-Null
            foreach ($one in $carried) {
                Copy-Item -Recurse -Force $one.FullName (Join-Path $packaged $one.Name)
                Note $one.Name
            }
            Note "Copied from $profileBrowsers; nothing to download"
        }
    }
}

# Node carries its own list of trusted authorities and does not read Windows'.
# Behind a proxy that re-signs HTTPS - every corporate one does - the download
# fails on a certificate Windows itself trusts, saying only
# "unable to get local issuer certificate".
try {
    # --no-shell: the headless shell is a second 271 MB browser, and this
    # application only ever launches a window. Without it the bundle carried
    # both, and a machine that cannot download went looking for the one it had
    # no use for.
    Invoke-Native $venvPython @("-m", "playwright", "install", "chromium", "--no-shell") "Installing Chromium"
} catch {
    Write-Host @"

The browser could not be downloaded.

If the error above says "unable to get local issuer certificate", the download
was not blocked: this network re-signs HTTPS, and Node does not read Windows'
own list of trusted authorities. Hand it that list and build again:

    `$pem = "`$env:USERPROFILE\corporate-roots.pem"
    Get-ChildItem Cert:\LocalMachine\Root, Cert:\LocalMachine\CA | ForEach-Object {
        "-----BEGIN CERTIFICATE-----"
        [Convert]::ToBase64String(`$_.RawData, 'InsertLineBreaks')
        "-----END CERTIFICATE-----"
    } | Set-Content `$pem -Encoding ascii
    `$env:NODE_EXTRA_CA_CERTS = `$pem

Or take the browser from a machine that already has one, and download nothing:
copy the chromium-* and winldd-* folders from that machine's
%LOCALAPPDATA%\ms-playwright into
    $buildVenv\Lib\site-packages\playwright\driver\package\.local-browsers
then run this script again - it will find them and skip the download.

"@ -ForegroundColor Yellow
    throw
}

Step "Stamping the build"
# So a running executable can say which commit it came from. Three runs were
# spent on a defect that was already fixed in the source, because nothing on
# screen could tell a stale build from a fresh one.
$commit = (& git rev-parse --short HEAD 2>$null)
if (-not $commit) { $commit = "no git" }
$dirty = ""
# Only tracked changes make a build differ from its commit. An untracked
# file beside the tree - a screenshot, a log - is not in the executable.
if (& git status --porcelain --untracked-files=no 2>$null) { $dirty = " +local changes" }
$stamp = "$commit$dirty  built $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
$stampFile = Join-Path $root "camds_imds_importer\build_stamp.txt"
# Written without a byte-order mark: Set-Content -Encoding utf8 puts one
# there on PowerShell 5.1, and it was displayed as a stray character in
# front of the commit.
[IO.File]::WriteAllText($stampFile, $stamp, (New-Object Text.UTF8Encoding $false))
Note $stamp

Step "Building"
# A previous build that is still running cannot be replaced, and Windows says
# only "access denied" about a file it will not name a reason for.
$previous = Join-Path $distDir "$name.exe"
if (Test-Path $previous) {
    try { [IO.File]::Open($previous, 'Open', 'ReadWrite', 'None').Dispose() }
    catch { throw "$previous is in use. Close the running application, then build again." }
}
if (Test-Path $distDir) { Remove-Item -Recurse -Force $distDir }
if (Test-Path $workDir) { Remove-Item -Recurse -Force $workDir }

$arguments = @(
    "--noconfirm"
    "--clean"
    "--name", $name
    "--windowed"                       # no console window behind the app
    "--icon", (Join-Path $root "camds_imds_importer\ui\assets\camds.ico")
    "--distpath", $distDir
    "--workpath", $workDir
    "--specpath", $workDir
    # Read at runtime by material_classifications.py and branding.py. The other
    # JSON and YAML files in the package are not read by the application.
    "--add-data", "$(Join-Path $root 'camds_imds_importer\camds\material_classifications.json');camds_imds_importer/camds"
    "--add-data", "$(Join-Path $root 'camds_imds_importer\ui\assets\camds.ico');camds_imds_importer/ui/assets"
    # So the running build can name the commit it came from.
    "--add-data", "$stampFile;camds_imds_importer"
    "--collect-all", "playwright"
    # PySide6 pulls in every Qt module by default. The application uses three.
    "--exclude-module", "PySide6.QtWebEngineCore"
    "--exclude-module", "PySide6.QtWebEngineWidgets"
    "--exclude-module", "PySide6.Qt3DCore"
    "--exclude-module", "PySide6.QtMultimedia"
    "--exclude-module", "PySide6.QtQuick"
    "--exclude-module", "PySide6.QtQml"
    "--exclude-module", "tkinter"
    "--exclude-module", "pytest"
)
# Always: the hook decides where Playwright looks, and a build without a
# bundled browser has to say so explicitly. A machine with a stray
# PLAYWRIGHT_BROWSERS_PATH=0 would otherwise send it hunting inside its own
# extraction directory, where nothing was bundled.
$arguments += @("--runtime-hook", (Join-Path $root "packaging\playwright_browsers_hook.py"))
$arguments += if ($OneDir) { "--onedir" } else { "--onefile" }
$arguments += (Join-Path $root "camds_imds_importer\app.py")

if ($IncludeBrowser -and -not $OneDir) {
    Write-Warning ("A bundled browser in a single file is unpacked to %TEMP% on every launch " +
                   "and takes about a minute to start. Add -OneDir unless one file matters more.")
}

Invoke-Native $venvPython (@("-m", "PyInstaller") + $arguments) "PyInstaller"

Step "Result"
$built = if ($OneDir) { Join-Path $distDir $name } else { Join-Path $distDir "$name.exe" }
if (-not (Test-Path $built)) { throw "Expected $built, which was not produced." }
$size = if ($OneDir) {
    (Get-ChildItem -Recurse $built | Measure-Object -Property Length -Sum).Sum
} else {
    (Get-Item $built).Length
}
Write-Host ("    {0}`n    {1:N0} MB" -f $built, ($size / 1MB)) -ForegroundColor Green

Write-Host @"

To use it on another machine
  Copy $(if ($OneDir) { "the whole $name folder" } else { "$name.exe" }).
  It writes beside itself, so put it somewhere writable, not Program Files:
    config\      the reviewed application and substance choices
    .runtime\    the saved CAMDS sign-in - treat it like a password
    output\      parsed trees and the import journals

  Copy config\ across as well to keep the choices already made. The journals
  decide what Resume can continue, so an interrupted import is only resumable
  on a machine that has its .jsonl, under the name it already has.
"@ -ForegroundColor Gray

if (-not $IncludeBrowser) {
    Write-Host @"
  The first launch downloads Chromium (about 430 MB) into the user's profile
  and needs the internet. Build with -IncludeBrowser -OneDir to avoid that.
"@ -ForegroundColor Yellow
}
