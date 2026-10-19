param(
    [string]$Version = "1.0.0",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$required = @(
    "hosts_opt.py",
    "hosts_opt_gui.py",
    "config.json",
    "install-task.ps1",
    "uninstall-task.ps1",
    "run-hidden.vbs",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md"
)
foreach ($file in $required) {
    if (-not (Test-Path (Join-Path $root $file))) {
        throw "Missing required file: $file"
    }
}

if (-not $SkipInstall) {
    python -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Installing PyInstaller failed."
    }
}

$buildRoot = Join-Path $root "build"
$exeDir = Join-Path $buildRoot "dist"
$workDir = Join-Path $buildRoot "work"
$specDir = Join-Path $buildRoot "spec"
$releaseRoot = Join-Path $root "release"
$appDir = Join-Path $releaseRoot "hosts-opt-$Version"
$zipPath = Join-Path $releaseRoot "hosts-opt-$Version-windows-x64.zip"

Remove-Item -Recurse -Force $buildRoot -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $appDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $exeDir, $workDir, $specDir, $appDir | Out-Null

$dataFiles = @(
    "config.json",
    "install-task.ps1",
    "uninstall-task.ps1",
    "run-hidden.vbs",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md"
)

$pyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--console",
    "--name", "hosts-opt",
    "--distpath", $exeDir,
    "--workpath", $workDir,
    "--specpath", $specDir,
    "--paths", $root
)
foreach ($name in $dataFiles) {
    $pyInstallerArgs += @("--add-data", "$(Join-Path $root $name);.")
}
$pyInstallerArgs += (Join-Path $root "hosts_opt_gui.py")

& python @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

Copy-Item (Join-Path $exeDir "hosts-opt.exe") (Join-Path $appDir "hosts-opt.exe") -Force
foreach ($file in @("config.json", "install-task.ps1", "uninstall-task.ps1", "run-hidden.vbs", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")) {
    Copy-Item (Join-Path $root $file) (Join-Path $appDir $file) -Force
}

if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}
Compress-Archive -Path (Join-Path $appDir "*") -DestinationPath $zipPath -CompressionLevel Optimal

$hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash
"$hash  hosts-opt-$Version-windows-x64.zip" | Set-Content "$zipPath.sha256" -Encoding ASCII

Write-Host ""
Write-Host "Built: $zipPath"
Write-Host "SHA256: $hash"
Write-Host ""
Write-Host "Upload with:"
Write-Host "  gh release create v$Version `"$zipPath`" --title `"hosts-opt v$Version`" --notes-file RELEASE_NOTES.md"
