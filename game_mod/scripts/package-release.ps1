param(
    [string]$ProjectRoot = "",
    [string]$Configuration = "Release",
    [string]$OutputRoot = "",
    [string]$GodotExe = ""
)

$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot

function Resolve-FullPath {
    param([string]$PathValue)

    return [System.IO.Path]::GetFullPath($PathValue)
}

function Resolve-ProjectRoot {
    param([string]$InputRoot)

    if ([string]::IsNullOrWhiteSpace($InputRoot)) {
        return (Resolve-Path (Join-Path $scriptRoot "..")).Path
    }

    return (Resolve-Path $InputRoot).Path
}

function Get-UniquePath {
    param(
        [string]$BasePath,
        [string]$Extension = ""
    )

    $candidate = if ([string]::IsNullOrWhiteSpace($Extension)) {
        $BasePath
    } else {
        "$BasePath$Extension"
    }

    if (-not (Test-Path $candidate)) {
        return $candidate
    }

    $index = 2
    while ($true) {
        $candidate = if ([string]::IsNullOrWhiteSpace($Extension)) {
            "$BasePath-$index"
        } else {
            "$BasePath-$index$Extension"
        }

        if (-not (Test-Path $candidate)) {
            return $candidate
        }

        $index += 1
    }
}

$ProjectRoot = Resolve-ProjectRoot -InputRoot $ProjectRoot

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $ProjectRoot "build/release"
} else {
    $OutputRoot = Resolve-FullPath -PathValue $OutputRoot
}

$manifestPath = Join-Path $ProjectRoot "nekospire/mod_manifest.json"
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$version = $manifest.version
$releaseBaseName = "sts2-ai-agent-v$version-windows"

$buildScript = Join-Path $ProjectRoot "scripts/build-mod.ps1"
$stagingModDir = Join-Path $ProjectRoot "build/mods/nekospire"
$releaseDir = Get-UniquePath -BasePath (Join-Path $OutputRoot $releaseBaseName)
$zipPath = Get-UniquePath -BasePath (Join-Path $OutputRoot $releaseBaseName) -Extension ".zip"

$modOutputDir = Join-Path $releaseDir "mod"
$scriptOutputDir = Join-Path $releaseDir "scripts"
$docsOutputDir = Join-Path $releaseDir "docs"

Write-Host "[package-release] Building release mod artifacts..."
$buildArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", $buildScript,
    "-ProjectRoot", $ProjectRoot,
    "-Configuration", $Configuration
)
if (-not [string]::IsNullOrWhiteSpace($GodotExe)) {
    $buildArgs += @("-GodotExe", $GodotExe)
}

powershell @buildArgs | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "build-mod.ps1 failed with exit code $LASTEXITCODE"
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
New-Item -ItemType Directory -Force -Path $modOutputDir | Out-Null
New-Item -ItemType Directory -Force -Path $scriptOutputDir | Out-Null
New-Item -ItemType Directory -Force -Path $docsOutputDir | Out-Null

Copy-Item -Path (Join-Path $stagingModDir "nekospire.dll") -Destination (Join-Path $modOutputDir "nekospire.dll") -Force
Copy-Item -Path (Join-Path $stagingModDir "nekospire.pck") -Destination (Join-Path $modOutputDir "nekospire.pck") -Force
Copy-Item -Path (Join-Path $stagingModDir "mod_id.json") -Destination (Join-Path $modOutputDir "mod_id.json") -Force

Copy-Item -Path (Join-Path $ProjectRoot "README.md") -Destination (Join-Path $releaseDir "README.md") -Force
Copy-Item -Path (Join-Path $ProjectRoot "CHANGELOG.md") -Destination (Join-Path $releaseDir "CHANGELOG.md") -Force

Copy-Item -Path (Join-Path $ProjectRoot "docs/game-knowledge") -Destination (Join-Path $docsOutputDir "game-knowledge") -Recurse -Force
Copy-Item -Path (Join-Path $ProjectRoot "docs/release-readiness.md") -Destination (Join-Path $docsOutputDir "release-readiness.md") -Force

Compress-Archive -Path (Join-Path $releaseDir "*") -DestinationPath $zipPath

Write-Host "[package-release] Release directory: $releaseDir"
Write-Host "[package-release] Release zip: $zipPath"
