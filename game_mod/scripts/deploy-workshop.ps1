<#
.SYNOPSIS
  构建 nekospire mod 并直接部署到 Steam 创意工坊订阅文件夹，让游戏加载的已订阅副本始终是最新构建。
  同时部署一份到游戏本地 mods/nekospire，避免工坊自动更新将开发版覆盖回旧版。
  同时可选地刷新 ModUploader 工作区的上传 payload（NEKOSpire/content），使 `upload -w` 也是新的。

.DESCRIPTION
  复用与 build-mod.ps1 相同的构建步骤（dotnet Release + Godot PCK），部署到游戏 mods/nekospire
  和 <SteamRoot>/steamapps/workshop/content/<AppId>/<ItemId>。只覆盖 dll/pck/mod_id.json，
  保留散装 nekospire_ui/ 资产；首次本地部署时从工坊目录迁移一份，不覆盖已有本地资产。

.EXAMPLE
  # 缺省即部署到 3794941932（D:/Steam 的 STS2 订阅）
  .\scripts\deploy-workshop.ps1

.PARAMETER Configuration
  Release（默认）/ Debug。

.PARAMETER ProjectRoot
  game_mod 目录。默认取本脚本上级目录。

.PARAMETER GodotExe
  Godot console exe（用于打包 PCK）。默认仓库内置 Godot_v4.5.1-stable_win64_console.exe。

.PARAMETER SteamRoot
  Steam 安装根目录。默认 D:/Steam。

.PARAMETER WorkshopAppId
  默认 2868840（杀戮尖塔 2）。

.PARAMETER WorkshopItemId
  目标 item id。默认 3794941932。

.PARAMETER UploadContentDir
  需一并刷新的 ModUploader 上传 content 目录；留空则跳过。
#>
param(
    [string]$Configuration = "Release",
    [string]$ProjectRoot = "",
    [string]$GodotExe = "",
    [string]$SteamRoot = "D:/Steam",
    [int]$WorkshopAppId = 2868840,
    [string]$WorkshopItemId = "3794941932",
    [string]$UploadContentDir = ""
)
$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot
if (Get-Process -Name "SlayTheSpire2" -ErrorAction SilentlyContinue) {
    throw "请先退出所有游戏实例再部署，避免主机和猫娘加载不同版本。"
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $scriptRoot "..")).Path
}
if ([string]::IsNullOrWhiteSpace($GodotExe)) {
    $GodotExe = Join-Path $ProjectRoot "Godot_v4.5.1-stable_win64.exe/Godot_v4.5.1-stable_win64_console.exe"
}
if (-not (Test-Path $GodotExe)) {
    throw "Godot exe not found: $GodotExe"
}
# STS2_DATA_DIR 指向真实游戏数据目录，否则 CS0246。
if ([string]::IsNullOrWhiteSpace($env:STS2_DATA_DIR)) {
    $env:STS2_DATA_DIR = Join-Path $SteamRoot "steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64"
}

$mod = "nekospire"
$csproj = Join-Path $ProjectRoot "nekospire/nekospire.csproj"
$manifest = Join-Path $ProjectRoot "nekospire/mod_manifest.json"
$modId = Join-Path $ProjectRoot "nekospire/mod_id.json"
$pckBuilder = Join-Path $ProjectRoot "tools/pck_builder"
$pckScript = Join-Path $pckBuilder "build_pck.gd"
$staging = Join-Path $ProjectRoot "build/mods/$mod"
$dllOut = Join-Path $ProjectRoot "nekospire/bin/$Configuration/net9.0/$mod.dll"
$pckOut = Join-Path $staging "$mod.pck"
$wsDeploy = Join-Path $SteamRoot "steamapps/workshop/content/$WorkshopAppId/$WorkshopItemId"
# STS2's ModManager prefers a local mod when its manifest version is >= the Workshop copy.
# Workshop updates can rewrite the subscribed directory while a host is running. A child launched
# afterwards must still see our build, not the old remote payload. No Steam settings/subscriptions change.
$gameRoot = Split-Path -Parent ([System.IO.Path]::GetFullPath($env:STS2_DATA_DIR))
$localDeploy = Join-Path $gameRoot "mods/$mod"

Write-Host "[deploy-workshop] csproj = $csproj"
Write-Host "[deploy-workshop] target = $wsDeploy (appId=$WorkshopAppId item=$WorkshopItemId)"
Write-Host "[deploy-workshop] protected local copy = $localDeploy"

# 1) 构建 DLL
Write-Host "[deploy-workshop] dotnet build -c $Configuration ..."
dotnet build $csproj -c $Configuration | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "dotnet build failed with exit code $LASTEXITCODE"
}

# 2) 打包 PCK
New-Item -ItemType Directory -Force -Path $staging | Out-Null
& $GodotExe --headless --path $pckBuilder --script $pckScript -- $manifest $pckOut | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Godot PCK build failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path $pckOut)) {
    throw "PCK not produced: $pckOut"
}

if (Get-Process -Name "SlayTheSpire2" -ErrorAction SilentlyContinue) {
    throw "构建期间游戏已启动；未部署，请退出全部实例后重试。"
}

# 3) Local copy survives Steam's Workshop auto-updater. Only replace our three payload files;
# do not delete the directory, change mod IDs, or pin an artificial version to block future upgrades.
New-Item -ItemType Directory -Force -Path $localDeploy | Out-Null
foreach ($source in @($dllOut, $pckOut, $modId)) {
    $destination = Join-Path $localDeploy (Split-Path -Leaf $source)
    Copy-Item -LiteralPath $source -Destination $destination -Force
    if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $destination).Hash) {
        throw "Local deployment verification failed: $destination"
    }
}
# Migrate optional loose artwork once. Never overwrite a user's existing local artwork.
$workshopArt = Join-Path $wsDeploy "nekospire_ui"
$localArt = Join-Path $localDeploy "nekospire_ui"
if ((Test-Path -LiteralPath $workshopArt) -and -not (Test-Path -LiteralPath $localArt)) {
    Copy-Item -LiteralPath $workshopArt -Destination $localDeploy -Recurse
}
Write-Host "[deploy-workshop] verified local copy -> $localDeploy"

# 4) 覆盖部署到创意工坊订阅文件夹（不动 nekospire_ui/）
New-Item -ItemType Directory -Force -Path $wsDeploy | Out-Null
Copy-Item -Force $dllOut (Join-Path $wsDeploy "$mod.dll")
Copy-Item -Force $pckOut (Join-Path $wsDeploy "$mod.pck")
Copy-Item -Force $modId (Join-Path $wsDeploy "mod_id.json")
Write-Host "[deploy-workshop] deployed -> $wsDeploy"

# 5) 可选：刷新上传 payload，令后台上传也是这份
if (-not [string]::IsNullOrWhiteSpace($UploadContentDir)) {
    New-Item -ItemType Directory -Force -Path $UploadContentDir | Out-Null
    Copy-Item -Force $dllOut (Join-Path $UploadContentDir "$mod.dll")
    Copy-Item -Force $pckOut (Join-Path $UploadContentDir "$mod.pck")
    Copy-Item -Force $modId (Join-Path $UploadContentDir "mod_id.json")
    Write-Host "[deploy-workshop] refreshed upload content -> $UploadContentDir"
}

Write-Host "[deploy-workshop] Done."
