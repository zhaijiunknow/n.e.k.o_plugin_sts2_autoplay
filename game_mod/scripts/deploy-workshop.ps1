<#
.SYNOPSIS
  构建 nekospire mod 并直接部署到 Steam 创意工坊订阅文件夹，让游戏加载的已订阅副本始终是最新构建。
  同时可选地刷新 ModUploader 工作区的上传 payload（NEKOSpire/content），使 `upload -w` 也是新的。

.DESCRIPTION
  复用与 build-mod.ps1 相同的构建步骤（dotnet Release + Godot PCK），但把部署目标从游戏 mods/ 目录换成
  <SteamRoot>/steamapps/workshop/content/<AppId>/<ItemId>。只覆盖 dll/pck/mod_id.json，**保留**该文件夹里
  的 nekospire_ui/ 资产目录（catgirl.png 等由 NekoUi.LoadUserTexture 读取）。

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
    $env:STS2_DATA_DIR = "D:/Steam/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64"
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

Write-Host "[deploy-workshop] csproj = $csproj"
Write-Host "[deploy-workshop] target = $wsDeploy (appId=$WorkshopAppId item=$WorkshopItemId)"

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

# 3) 覆盖部署到创意工坊订阅文件夹（不动 nekospire_ui/）
New-Item -ItemType Directory -Force -Path $wsDeploy | Out-Null
Copy-Item -Force $dllOut (Join-Path $wsDeploy "$mod.dll")
Copy-Item -Force $pckOut (Join-Path $wsDeploy "$mod.pck")
Copy-Item -Force $modId (Join-Path $wsDeploy "mod_id.json")
Write-Host "[deploy-workshop] deployed -> $wsDeploy"

# 4) 可选：刷新上传 payload，令后台上传也是这份
if (-not [string]::IsNullOrWhiteSpace($UploadContentDir)) {
    New-Item -ItemType Directory -Force -Path $UploadContentDir | Out-Null
    Copy-Item -Force $dllOut (Join-Path $UploadContentDir "$mod.dll")
    Copy-Item -Force $pckOut (Join-Path $UploadContentDir "$mod.pck")
    Copy-Item -Force $modId (Join-Path $UploadContentDir "mod_id.json")
    Write-Host "[deploy-workshop] refreshed upload content -> $UploadContentDir"
}

Write-Host "[deploy-workshop] Done."
