[CmdletBinding()]
param(
    [switch]$CreateDesktopShortcut,
    [switch]$Force,
    [string]$PythonExecutable
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $PythonExecutable = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
}
$specPath = Join-Path $repositoryRoot "QPX600DP.spec"
$applicationName = "QPX600DP Electrolyser Control"
$releaseDirectory = Join-Path $repositoryRoot "dist\$applicationName"
$applicationExecutable = Join-Path $releaseDirectory "$applicationName.exe"

if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Create .venv and install requirements-dev.txt before packaging."
}

& $pythonExecutable -c "import PyInstaller; print(PyInstaller.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed in .venv. Install requirements-dev.txt."
}

if ((Test-Path -LiteralPath $releaseDirectory) -and -not $Force) {
    throw "Release folder already exists: $releaseDirectory. Review it, then rerun with -Force."
}

$startedAt = Get-Date
$arguments = @("-m", "PyInstaller", "--clean")
if ($Force) {
    $arguments += "--noconfirm"
}
$arguments += $specPath

Push-Location $repositoryRoot
try {
    & $pythonExecutable @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $applicationExecutable -PathType Leaf)) {
    throw "Packaged executable was not produced: $applicationExecutable"
}

New-Item -ItemType Directory -Force -Path (
    Join-Path $releaseDirectory "Data"
), (
    Join-Path $releaseDirectory "logs"
) | Out-Null

$commit = (& git -C $repositoryRoot rev-parse HEAD).Trim()
$branch = (& git -C $repositoryRoot branch --show-current).Trim()
$finishedAt = Get-Date
$buildInformation = @(
    "Application: $applicationName"
    "Source branch: $branch"
    "Source commit: $commit"
    "Build UTC: $($finishedAt.ToUniversalTime().ToString('o'))"
    "Python: $(& $pythonExecutable --version 2>&1)"
    "PyInstaller: $(& $pythonExecutable -c 'import PyInstaller; print(PyInstaller.__version__)')"
)
Set-Content -LiteralPath (
    Join-Path $releaseDirectory "BUILD_INFO.txt"
) -Value $buildInformation -Encoding UTF8

$shortcutPath = $null
if ($CreateDesktopShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktop "$applicationName.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $applicationExecutable
    $shortcut.WorkingDirectory = $releaseDirectory
    $shortcut.IconLocation = "$applicationExecutable,0"
    $shortcut.Description = "Launch QPX600DP Electrolyser Control"
    $shortcut.Save()
}

$elapsed = $finishedAt - $startedAt
[pscustomobject]@{
    Executable = $applicationExecutable
    ReleaseDirectory = $releaseDirectory
    DesktopShortcut = $shortcutPath
    BuildSeconds = [math]::Round($elapsed.TotalSeconds, 3)
}
