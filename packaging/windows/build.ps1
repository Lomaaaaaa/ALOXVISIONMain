<#
.SYNOPSIS
    Builds ALOX Vision (Python) for Windows: a clean venv, the PyInstaller folder AloxVisionPy, then the Inno Setup
    installer.

.DESCRIPTION
    1. Creates a clean virtual environment (build\venv under this folder) with Python 3.13 x64.
    2. Installs this repository with the extras [stage,api,ui,vision,licence], plus pyinstaller and harvesters.
    3. Runs PyInstaller with alox.spec: dist\AloxVisionPy\ (alox.exe, alox-ui.exe, the check tools, _internal\).
    4. Smoke test of the folder: alox.exe --version and alox.exe --simulate --check.
    5. Runs Inno Setup's ISCC.exe with alox.iss when it is installed: dist\installer\AloxVisionPy-<version>-setup.exe.

    Every output stays under packaging\windows\build and packaging\windows\dist (both ignored by git).

.PARAMETER Python
    The Python 3.13 x64 interpreter used to create the venv. Default: the launcher "py -3.13".

.PARAMETER LicencePublicKeyFile
    Optional licence_public_key.txt (base64 public key printed by tools\licence_generator.py keygen) to ship next to
    alox.exe. Never give the private key.

.PARAMETER SkipInstaller
    Build the PyInstaller folder only.

.PARAMETER SkipSmokeTest
    Do not run the built alox.exe.

.PARAMETER KeepVenv
    Reuse build\venv when it exists (faster rebuilds; the default recreates it).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Python "C:\Python313\python.exe" -LicencePublicKeyFile C:\keys\licence_public_key.txt
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$LicencePublicKeyFile = "",
    [switch]$SkipInstaller,
    [switch]$SkipSmokeTest,
    [switch]$KeepVenv
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = (Resolve-Path (Join-Path $Here "..\..")).Path
$BuildDir = Join-Path $Here "build"
$DistDir = Join-Path $Here "dist"
$Venv = Join-Path $BuildDir "venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$Bundle = Join-Path $DistDir "AloxVisionPy"
$Spec = Join-Path $Here "alox.spec"
$Iss = Join-Path $Here "alox.iss"

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "BUILD FAILED: $Message" -ForegroundColor Red
    exit 1
}

function Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# Runs a native program and stops the build when it fails ($ErrorActionPreference does not cover exit codes).
# Their stderr (PyInstaller and pip log there) is not an error: "Continue" keeps Windows PowerShell 5.1 from stopping
# on it when the output is redirected.
function Invoke-Native([string]$What, [string]$Exe, [string[]]$Arguments) {
    $ErrorActionPreference = "Continue"
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "$What failed (exit code $LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
}

# ------------------------------------------------------------------------------------------- 1. Python and the venv
Step "Python 3.13 x64"
if ($Python -eq "") {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        Fail "the Python launcher 'py' is not installed. Install Python 3.13 x64 from python.org (with the launcher), or give -Python <path to python.exe>."
    }
    $PythonExe = "py"
    $PythonArgs = @("-3.13")
} else {
    if (-not (Test-Path $Python)) { Fail "-Python: '$Python' not found." }
    $PythonExe = $Python
    $PythonArgs = @()
}
$ErrorActionPreference = "Continue"
$probe = & $PythonExe @PythonArgs -c "import platform, struct, sys; print('%d.%d %d' % (sys.version_info[0], sys.version_info[1], struct.calcsize('P') * 8))"
$probeExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($probeExit -ne 0 -or -not $probe) {
    Fail "cannot run Python 3.13 ($PythonExe $PythonArgs). Install Python 3.13 x64 from python.org, or give -Python."
}
$probe = "$probe".Trim()
if ($probe -ne "3.13 64") {
    Fail "Python 3.13 64-bit is required, found '$probe' (version, bits) with $PythonExe $PythonArgs."
}
Write-Host "Python: $PythonExe $PythonArgs ($probe)"

$found = Select-String -Path (Join-Path $Repo "src\alox\__init__.py") -Pattern '^__version__\s*=\s*"([0-9]+(\.[0-9]+){0,3})"'
if (-not $found) { Fail "cannot read a numeric __version__ from src\alox\__init__.py" }
$Version = $found.Matches[0].Groups[1].Value
Write-Host "ALOX Vision version: $Version"

if ((Test-Path $Venv) -and -not $KeepVenv) {
    Step "Removing the old venv $Venv"
    Remove-Item -Recurse -Force $Venv
}
if (-not (Test-Path $VenvPython)) {
    Step "Creating the venv $Venv"
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
    Invoke-Native "creating the venv" $PythonExe ($PythonArgs + @("-m", "venv", $Venv))
}

# ------------------------------------------------------------------------------------------- 2. the packages
Step "Installing alox[stage,api,ui,vision,licence], pyinstaller and harvesters"
Invoke-Native "upgrading pip" $VenvPython @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Native "installing the packages (network access to pypi.org needed)" $VenvPython @(
    "-m", "pip", "install", "$Repo[stage,api,ui,vision,licence]", "pyinstaller", "harvesters")
Invoke-Native "pip check" $VenvPython @("-m", "pip", "check")

# ------------------------------------------------------------------------------------------- 3. PyInstaller
Step "PyInstaller: $Bundle"
if (Test-Path $Bundle) { Remove-Item -Recurse -Force $Bundle }
Invoke-Native "PyInstaller" $VenvPython @("-m", "PyInstaller", "--noconfirm", "--clean",
    "--distpath", $DistDir, "--workpath", (Join-Path $BuildDir "pyinstaller"), $Spec)
foreach ($exe in @("alox.exe", "alox-ui.exe", "z_stage_check.exe", "xy_stage_check.exe", "wl_check.exe",
                   "camera_check.exe", "thermal_check.exe", "af_latency_check.exe")) {
    if (-not (Test-Path (Join-Path $Bundle $exe))) { Fail "PyInstaller did not produce $exe in $Bundle" }
}

if ($LicencePublicKeyFile -ne "") {
    if (-not (Test-Path $LicencePublicKeyFile)) { Fail "-LicencePublicKeyFile: '$LicencePublicKeyFile' not found." }
    $key = (Get-Content -Raw $LicencePublicKeyFile).Trim()
    if ($key -match "PRIVATE") { Fail "-LicencePublicKeyFile looks like a private key: give the PUBLIC key only." }
    Set-Content -Path (Join-Path $Bundle "licence_public_key.txt") -Value $key -Encoding ASCII
    Write-Host "licence public key added: $(Join-Path $Bundle 'licence_public_key.txt')"
}

# ------------------------------------------------------------------------------------------- 4. smoke test
if (-not $SkipSmokeTest) {
    Step "Smoke test of the built folder"
    $aloxExe = Join-Path $Bundle "alox.exe"
    Invoke-Native "alox.exe --version" $aloxExe @("--version")
    $smoke = Join-Path $BuildDir "smoke"
    if (Test-Path $smoke) { Remove-Item -Recurse -Force $smoke }
    New-Item -ItemType Directory -Force -Path $smoke | Out-Null
    $env:ALOX_EXE_DIR = $smoke          # no alox_setup.ini of the build PC is read
    try {
        Invoke-Native "alox.exe --simulate --check" $aloxExe @("--simulate", "--check", "--no-api")
    } finally {
        Remove-Item Env:\ALOX_EXE_DIR
    }
}

# ------------------------------------------------------------------------------------------- 5. Inno Setup
if ($SkipInstaller) {
    Write-Host ""
    Write-Host "Done (no installer): $Bundle" -ForegroundColor Green
    exit 0
}
Step "Inno Setup"
$candidates = @()
$onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($onPath) { $candidates += $onPath.Path }
foreach ($root in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LOCALAPPDATA "Programs"))) {
    if ($root) { $candidates += (Join-Path $root "Inno Setup 6\ISCC.exe") }
}
$Iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    Write-Host "Inno Setup 6 (ISCC.exe) not found: the installer was not built." -ForegroundColor Yellow
    Write-Host "Install Inno Setup 6.3 or later from https://jrsoftware.org/isdl.php and run this script again, or compile"
    Write-Host "$Iss in the Inno Setup IDE with /DAppVersion=$Version /DDistDir=$Bundle /DOutputDir=$(Join-Path $DistDir 'installer')."
    Write-Host "The application folder is ready: $Bundle" -ForegroundColor Green
    exit 0
}
Write-Host "ISCC: $Iscc"
$InstallerDir = Join-Path $DistDir "installer"
Invoke-Native "Inno Setup (ISCC.exe)" $Iscc @("/Qp", "/DAppVersion=$Version", "/DDistDir=$Bundle", "/DOutputDir=$InstallerDir", $Iss)
$setup = Join-Path $InstallerDir "AloxVisionPy-$Version-setup.exe"
if (-not (Test-Path $setup)) { Fail "ISCC.exe ran but $setup is missing." }
Write-Host ""
Write-Host "Done: $setup" -ForegroundColor Green
