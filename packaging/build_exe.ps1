<#
.SYNOPSIS
    Builds the portable Windows package for OCR_Plate.

.DESCRIPTION
    Runs PyInstaller against packaging/OCR_Plate.spec, then stages the files the
    app reads at runtime (config, detector weights, documentation, sample videos,
    empty data folder) next to the generated .exe. The result is dist/OCR_Plate/, a folder
    that can be copied to any Windows 10/11 x64 machine and run offline.

.PARAMETER SkipSampleVideos
    Leave the ~300 MB sample_videos folder out of the package.

.PARAMETER Clean
    Remove previous build/ and dist/ output before building.
#>
[CmdletBinding()]
param(
    [switch]$SkipSampleVideos,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$specFile = Join-Path $PSScriptRoot "OCR_Plate.spec"
$distRoot = Join-Path $projectRoot "dist"
$workRoot = Join-Path $projectRoot "build"
$appDir = Join-Path $distRoot "OCR_Plate"

Write-Host "Project root : $projectRoot"

if ($Clean) {
    foreach ($dir in @($distRoot, $workRoot)) {
        if (Test-Path $dir) {
            Write-Host "Removing $dir"
            Remove-Item -Recurse -Force $dir
        }
    }
}

python -m PyInstaller $specFile --noconfirm --distpath $distRoot --workpath $workRoot
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

if (-not (Test-Path $appDir)) { throw "Expected output folder not found: $appDir" }

# --- Stage the runtime files the app expects beside the .exe -----------------

$configSource = Join-Path $projectRoot "config.json"
if (-not (Test-Path $configSource)) {
    $configSource = Join-Path $projectRoot "config.example.json"
}
Copy-Item $configSource (Join-Path $appDir "config.json") -Force
Write-Host "Staged config from $configSource"

$modelSource = Join-Path $projectRoot "license_plate_detector.pt"
if (-not (Test-Path $modelSource)) { throw "Detector weights not found: $modelSource" }
Copy-Item $modelSource $appDir -Force

foreach ($name in @("README.md")) {
    $path = Join-Path $projectRoot $name
    if (Test-Path $path) { Copy-Item $path $appDir -Force }
}

$docsSource = Join-Path $projectRoot "docs"
if (Test-Path $docsSource) {
    Copy-Item $docsSource $appDir -Recurse -Force
    Write-Host "Staged documentation from $docsSource"
}

foreach ($name in @("data", "data\snapshots", "data\backups", "logs")) {
    $path = Join-Path $appDir $name
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path | Out-Null }
}

if (-not $SkipSampleVideos) {
    $samples = Join-Path $projectRoot "sample_videos"
    if (Test-Path $samples) {
        Write-Host "Copying sample videos (this takes a moment)..."
        Copy-Item $samples $appDir -Recurse -Force
    }
}

$sizeBytes = (Get-ChildItem $appDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ""
Write-Host "Build complete: $appDir"
Write-Host ("Package size  : {0:N1} GB" -f ($sizeBytes / 1GB))
Write-Host "Run it with   : $appDir\OCR_Plate.exe"
