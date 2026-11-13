<#
.SYNOPSIS
    Builds AlphaHarness.exe on this Windows machine with the app (and the Submit Advisor
    patch) baked in, so no GitHub release is needed.

.DESCRIPTION
    The normal AlphaHarness.exe is a small launcher: on first start it downloads the app from a
    GitHub release. This script builds a launcher that carries the app inside it instead:

      1. builds the web UI            (pnpm)
      2. builds the app wheel         (uv build), stamped with a version
      3. exports the pinned dependency list the release workflow uses
      4. copies the launcher and patches the COPY (your source files are not touched) so that
         it installs the bundled wheel instead of downloading one
      5. packages it with PyInstaller into a single AlphaHarness.exe

    Run it from the repository root (the folder that contains backend, frontend, launcher):

        powershell -ExecutionPolicy Bypass -File .\build-windows-exe.ps1

    Needs: uv, Node.js, and an internet connection (for npm and PyPI packages, and for the
    Python 3.14 that uv fetches). Pass -InstallMissing to let winget install uv and Node.

.PARAMETER Version
    The version to stamp, digits and dots only (for example 2026.10.13.905). Default: today's
    date plus the time, so every build is newer than the last. The launcher only installs a
    build whose version is HIGHER than the one already installed on the machine, so if you have
    a newer one, pass a higher -Version.

.PARAMETER OutDir
    Where AlphaHarness.exe is written. Default: .\dist-exe

.PARAMETER KeepUpdateCheck
    Leave the in-app updater pointing at the official GitHub releases. By default it is turned
    off for this build, because an official update would replace the patched app with the
    unpatched one.

.PARAMETER InstallMissing
    Install a missing uv or Node.js with winget instead of stopping.

.PARAMETER KeepWork
    Keep the temporary build folder (useful when something fails).
#>
[CmdletBinding()]
param(
    [string]$Version,
    [string]$OutDir,
    [switch]$KeepUpdateCheck,
    [switch]$InstallMissing,
    [switch]$KeepWork
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$PyInstallerVersion = '6.22.3'

# --- helpers ---------------------------------------------------------------------------------

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# PowerShell does not stop on a failing program by itself; every call goes through here.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE."
    }
}

function Test-Tool([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# A tool installed a moment ago is on the registry PATH but not on this session's.
function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Install-WithWinget([string]$Id) {
    if (-not (Test-Tool 'winget')) {
        throw "winget is not available. Install '$Id' by hand and run this script again."
    }
    Invoke-Native 'winget' @('install', '--id', $Id, '-e', '--silent',
        '--accept-package-agreements', '--accept-source-agreements')
    Update-SessionPath
}

# Replace text that must occur exactly once. If the launcher changes upstream this stops the
# build instead of producing an exe that quietly downloads the wrong thing.
function Edit-Text {
    param([string]$Text, [string]$Old, [string]$New, [string]$Label)
    $count = ([regex]::Matches($Text, [regex]::Escape($Old))).Count
    if ($count -ne 1) {
        throw "Launcher patch '$Label' expected exactly 1 match but found $count. The launcher has changed; this script needs updating."
    }
    return $Text.Replace($Old, $New)
}

# Writes a patched COPY of the launcher. The original file is only read.
function New-PatchedLauncher {
    param(
        [string]$Source,
        [string]$Destination,
        [string]$Version,
        [bool]$QuietUpdates
    )
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $nl = "`n"
    # Git on Windows may have turned the file into CRLF; edit against LF so matches are exact.
    $text = [IO.File]::ReadAllText($Source, $utf8) -replace "`r`n", "`n"

    $text = Edit-Text -Text $text -Label 'version' `
        -Old 'BUILD_VERSION = "0.0.0"' `
        -New ('BUILD_VERSION = "{0}"' -f $Version)

    # Install the wheel and pins packed inside this exe, when they are for this build.
    $text = Edit-Text -Text $text -Label 'bundled wheel' `
        -Old '    wheel = wheel or f"{DOWNLOAD}/v{version}/alpha_harness-{version}-py3-none-any.whl"' `
        -New (@(
            '    name = f"alpha_harness-{version}-py3-none-any.whl"',
            '    packed, packed_constraints = bundled(name), bundled("constraints.txt")',
            '    # A local build carries its own wheel, so it installs this app and not a release.',
            '    local = version == BUILD_VERSION and packed.is_file() and packed_constraints.is_file()',
            '    wheel = str(packed) if local else (wheel or f"{DOWNLOAD}/v{version}/{name}")'
        ) -join $nl)

    $text = Edit-Text -Text $text -Label 'bundled constraints' `
        -Old '    constraints = f"{DOWNLOAD}/v{version}/constraints.txt"' `
        -New '    constraints = str(packed_constraints) if local else f"{DOWNLOAD}/v{version}/constraints.txt"'

    if ($QuietUpdates) {
        # The app reads this variable to decide where to look for updates. A name with no
        # owner has no releases, so it reports "nothing to update" instead of offering an
        # official build that would replace this one.
        $text = Edit-Text -Text $text -Label 'update check' `
            -Old '        PID_VARIABLE: str(os.getpid()),' `
            -New (@(
                '        PID_VARIABLE: str(os.getpid()),',
                '        # Local build: there is no release to follow.',
                '        "ALPHA_HARNESS_REPOSITORY": "local-build",'
            ) -join $nl)
    }

    [IO.File]::WriteAllText($Destination, $text, $utf8)
}

# Lets the functions above be loaded without building anything: ". .\build-windows-exe.ps1"
if ($MyInvocation.InvocationName -eq '.') { return }

# --- main ------------------------------------------------------------------------------------

try {
    if ($env:OS -ne 'Windows_NT') {
        throw 'This builds a Windows .exe and has to run on Windows.'
    }

    $Root = $PSScriptRoot
    if (-not $Root) { throw 'Run this as a file (powershell -File .\build-windows-exe.ps1).' }
    foreach ($needed in @('backend\pyproject.toml', 'frontend\package.json',
            'launcher\alpha_harness_launcher.py')) {
        if (-not (Test-Path ([IO.Path]::Combine($Root, $needed)))) {
            throw "Cannot find $needed. Put this script in the repository root and run it from there."
        }
    }
    $Backend = [IO.Path]::Combine($Root, 'backend')
    $Frontend = [IO.Path]::Combine($Root, 'frontend')
    $Launcher = [IO.Path]::Combine($Root, 'launcher')
    if (-not $OutDir) { $OutDir = [IO.Path]::Combine($Root, 'dist-exe') }

    if (-not $Version) {
        $now = Get-Date
        # The time is a plain integer (905, not 0905): a leading zero would not survive the
        # wheel's version normalisation and the file names would stop matching.
        $Version = '{0}.{1}.{2}.{3}' -f $now.Year, $now.Month, $now.Day, ([int]$now.ToString('HHmm'))
    }
    if ($Version -notmatch '^(0|[1-9]\d*)(\.(0|[1-9]\d*)){1,3}$') {
        throw "Version '$Version' must be digits and dots with no leading zeros, e.g. 2026.10.13.905."
    }
    Write-Host "Building AlphaHarness $Version"

    # --- prerequisites ---
    Write-Step 'Checking the tools this needs'
    if (-not (Test-Tool 'uv')) {
        if ($InstallMissing) { Install-WithWinget 'astral-sh.uv' }
    }
    if (-not (Test-Tool 'uv')) {
        throw 'uv is missing. Install it (winget install astral-sh.uv) or run again with -InstallMissing.'
    }
    $uv = (Get-Command uv -CommandType Application | Select-Object -First 1).Source

    if (-not (Test-Tool 'node')) {
        if ($InstallMissing) { Install-WithWinget 'OpenJS.NodeJS' }
    }
    if (-not (Test-Tool 'node')) {
        throw 'Node.js is missing. Install it (winget install OpenJS.NodeJS) or run again with -InstallMissing.'
    }
    $wantNode = ([IO.File]::ReadAllText([IO.Path]::Combine($Frontend, '.node-version'))).Trim()
    $haveNode = (& node --version).Trim().TrimStart('v')
    if ($haveNode.Split('.')[0] -ne $wantNode.Split('.')[0]) {
        Write-Warning "The project is built with Node $wantNode; you have $haveNode. Continuing, but if the UI build fails, install Node $wantNode."
    }

    $manifest = [IO.File]::ReadAllText([IO.Path]::Combine($Frontend, 'package.json'))
    if ($manifest -notmatch '"packageManager"\s*:\s*"pnpm@([0-9.]+)"') {
        throw 'Could not read the pnpm version from frontend\package.json.'
    }
    $wantPnpm = $Matches[1]
    $havePnpm = ''
    if (Test-Tool 'pnpm') { $havePnpm = (& pnpm --version).Trim() }
    if ($havePnpm -ne $wantPnpm) {
        Write-Host "Installing pnpm $wantPnpm (found '$havePnpm')"
        Invoke-Native 'npm' @('install', '-g', "pnpm@$wantPnpm")
        Update-SessionPath
    }

    # A clean work folder every time.
    $Work = [IO.Path]::Combine([IO.Path]::GetTempPath(), "alpha-harness-build-$Version")
    if (Test-Path $Work) { Remove-Item $Work -Recurse -Force }
    $Dist = [IO.Path]::Combine($Work, 'wheel')
    $LauncherWork = [IO.Path]::Combine($Work, 'launcher')
    New-Item -ItemType Directory -Path $Dist, $LauncherWork | Out-Null

    # --- 1. web UI ---
    Write-Step 'Building the web UI'
    Push-Location $Frontend
    try {
        Invoke-Native 'pnpm' @('install', '--frozen-lockfile')
        Invoke-Native 'pnpm' @('build')
    } finally { Pop-Location }

    # --- 2 + 3. wheel and pinned dependencies ---
    Write-Step "Building the app wheel $Version"
    $pyproject = [IO.Path]::Combine($Backend, 'pyproject.toml')
    $originalPyproject = [IO.File]::ReadAllBytes($pyproject)
    Push-Location $Backend
    try {
        # Stamped in place and put back afterwards, so your source keeps its own version.
        Invoke-Native $uv @('version', '--frozen', $Version)
        Invoke-Native $uv @('build', '--wheel', '--out-dir', $Dist)
        Invoke-Native $uv @('export', '--frozen', '--no-dev', '--no-emit-project',
            '-o', ([IO.Path]::Combine($Dist, 'constraints.txt')))
    } finally {
        Pop-Location
        [IO.File]::WriteAllBytes($pyproject, $originalPyproject)
    }
    $wheelName = "alpha_harness-$Version-py3-none-any.whl"
    $Wheel = [IO.Path]::Combine($Dist, $wheelName)
    $Pins = [IO.Path]::Combine($Dist, 'constraints.txt')
    if (-not (Test-Path $Wheel)) {
        throw "Expected $wheelName in $Dist but it is not there. Found: $((Get-ChildItem $Dist).Name -join ', ')"
    }

    # --- 4. the launcher, patched in a copy ---
    Write-Step 'Preparing the launcher'
    $PatchedLauncher = [IO.Path]::Combine($LauncherWork, 'alpha_harness_launcher.py')
    New-PatchedLauncher -Source ([IO.Path]::Combine($Launcher, 'alpha_harness_launcher.py')) `
        -Destination $PatchedLauncher -Version $Version -QuietUpdates (-not $KeepUpdateCheck)
    Copy-Item ([IO.Path]::Combine($Launcher, 'mark.ico')) $LauncherWork -ErrorAction SilentlyContinue
    # The launcher uses uv to build the app's environment on the user's machine.
    Copy-Item $uv ([IO.Path]::Combine($LauncherWork, 'uv.exe'))
    Invoke-Native ([IO.Path]::Combine($LauncherWork, 'uv.exe')) @('--version')

    # --- 5. the exe ---
    Write-Step 'Packaging AlphaHarness.exe (this takes a few minutes)'
    $pyiArguments = @(
        'tool', 'run', '--python', '3.14', '--from', "pyinstaller==$PyInstallerVersion", 'pyinstaller',
        '--noconfirm', '--clean', '--onefile', '--windowed', '--name', 'AlphaHarness',
        '--distpath', $OutDir,
        '--workpath', ([IO.Path]::Combine($Work, 'pyinstaller')),
        '--specpath', $Work,
        '--paths', ([IO.Path]::Combine($Backend, 'src')),
        '--add-binary', ([IO.Path]::Combine($LauncherWork, 'uv.exe') + ';.'),
        '--add-data', ($Wheel + ';.'),
        '--add-data', ($Pins + ';.')
    )
    $icon = [IO.Path]::Combine($LauncherWork, 'mark.ico')
    if (Test-Path $icon) { $pyiArguments += @('--icon', $icon) }
    $pyiArguments += $PatchedLauncher
    Invoke-Native $uv $pyiArguments

    $Exe = [IO.Path]::Combine($OutDir, 'AlphaHarness.exe')
    if (-not (Test-Path $Exe)) { throw "PyInstaller finished but $Exe was not created." }

    $size = '{0:N1} MB' -f ((Get-Item $Exe).Length / 1MB)
    $hash = (Get-FileHash $Exe -Algorithm SHA256).Hash
    Write-Host ''
    Write-Host 'Done.' -ForegroundColor Green
    Write-Host "  Exe:     $Exe ($size)"
    Write-Host "  Version: $Version"
    Write-Host "  SHA-256: $hash"
    Write-Host ''
    Write-Host 'First run: it builds the app environment (downloads Python 3.14 and the'
    Write-Host 'dependencies), which takes a few minutes with no window. Progress is written to:'
    Write-Host "  $env:LOCALAPPDATA\AlphaHarness\launcher.log"
    Write-Host 'Windows may warn that the app is unsigned: More info, then Run anyway. Antivirus'
    Write-Host 'tools sometimes flag any PyInstaller exe; this one is unsigned and built by you.'
    if (-not $KeepUpdateCheck) {
        Write-Host 'The in-app updater is off in this build, so it will not replace the patch.'
    }
}
catch {
    Write-Host ''
    Write-Host "Build failed: $($_.Exception.Message)" -ForegroundColor Red
    $script:failed = $true
}
finally {
    if ((Get-Variable Work -ErrorAction SilentlyContinue) -and $Work -and (Test-Path $Work)) {
        if ($KeepWork -or (Get-Variable failed -Scope Script -ErrorAction SilentlyContinue)) {
            Write-Host "Build folder kept: $Work"
        } else {
            Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
if (Get-Variable failed -Scope Script -ErrorAction SilentlyContinue) { exit 1 }
