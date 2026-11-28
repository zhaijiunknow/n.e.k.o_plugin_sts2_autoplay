<#
.SYNOPSIS
  一键部署：改完本仓库后，把 Mod 部署到游戏、把插件部署到 NEKO。
    - Mod   : 构建 Release 并部署到本地 mods/nekospire 和创意工坊订阅文件夹（本地同版或高版优先），并刷新 ModUploader 上传内容。
    - 插件 : 把仓库根部的插件源码同步到 NEKO 插件目录 plugin/plugins/sts2_autoplay。

.DESCRIPTION
  修改仓库后运行本脚本即可让游戏与 NEKO 都加载最新代码。Mod 侧复用 scripts/deploy-workshop.ps1。

.EXAMPLE
  .\deploy-all.ps1

.PARAMETER ProjectRoot
  仓库根目录。默认取本脚本所在目录（脚本放仓库根时即本目录）。

.PARAMETER NekoPluginDir
  NEKO 插件目录，可以给多个；存在的每一个都会部署。默认两个：
    - %LOCALAPPDATA%/N.E.K.O/.neko-plugin-installations/plugins/sts2_autoplay
      （安装版 NEKO 实际加载的目录：`GET /plugins/sts2_autoplay` 的 config_path 指向这里）
    - D:/NekoClaw/N.E.K.O/plugin/plugins/sts2_autoplay（源码版 NEKO）
  只部署到后者而 NEKO 是安装版时，磁盘上换了新文件、运行的仍是旧代码（2026-11-24 实测踩过）。

.PARAMETER UploadContentDir
  ModUploader content 目录（留空不刷新上传内容）。默认指向仓库内 NEKOSpire/content。
#>
param(
    [string]$ProjectRoot = "",
    [string[]]$NekoPluginDir = @(
        (Join-Path $env:LOCALAPPDATA "N.E.K.O/.neko-plugin-installations/plugins/sts2_autoplay"),
        "D:/NekoClaw/N.E.K.O/plugin/plugins/sts2_autoplay"
    ),
    [string]$UploadContentDir = ""
)
$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = $scriptRoot
}

$wsContentDefault = Join-Path $ProjectRoot "game_mod/Godot_v4.5.1-stable_win64.exe/ModUploader-win-x64/NEKOSpire/content"
if ([string]::IsNullOrWhiteSpace($UploadContentDir) -and (Test-Path $wsContentDefault)) {
    $UploadContentDir = $wsContentDefault
}

Write-Host "[deploy-all] ProjectRoot = $ProjectRoot"
Write-Host "[deploy-all] NekoPlugin  = $($NekoPluginDir -join '; ')"

# ---------- 1) 插件 -> NEKO 插件目录 ----------
Write-Host "[deploy-all] === plugin -> N.E.K.O plugins ==="
foreach ($target in $NekoPluginDir) {
    if (-not (Test-Path $target)) {
        Write-Warning "[deploy-all] 插件目录不存在，跳过：$target"
        continue
    }
    # 拷贝仓库根部的插件文件（.py、plugin.toml 等）与子目录
    Copy-Item -Force (Join-Path $ProjectRoot "*.py") $target
    foreach ($cfg in @("plugin.toml", "plugin.toml.lock", "config.example.toml", "ruff.toml", "pyproject.toml", "policy_manifest.json", "README.md", "README.en.md")) {
        $p = Join-Path $ProjectRoot $cfg
        if (Test-Path $p) { Copy-Item -Force $p $target }
    }
    foreach ($dir in @("static", "strategies", "i18n", "policy_sidecar")) {
        $src = Join-Path $ProjectRoot $dir
        if (Test-Path $src) { Copy-Item -Recurse -Force $src $target -Exclude "control_status.json", "policy_status.json", "__pycache__" }
    }
    # Resolve and check every recursive cleanup target before deleting it.
    $resolvedTarget = (Resolve-Path -LiteralPath $target).ProviderPath.TrimEnd('\', '/')
    foreach ($cache in @(Get-ChildItem -LiteralPath $resolvedTarget -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue)) {
        $cachePath = [System.IO.Path]::GetFullPath($cache.FullName)
        if (-not $cachePath.StartsWith($resolvedTarget + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -or
            ($cache.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing cache cleanup outside plugin directory or through a link: $cachePath"
        }
        Remove-Item -LiteralPath $cachePath -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[deploy-all] plugin deployed -> $target"
}

# ---------- 2) Mod -> 游戏（创意工坊订阅文件夹）+ 刷新上传内容 ----------
Write-Host "[deploy-all] === mod -> game workshop ==="
$deployWs = Join-Path $ProjectRoot "game_mod/scripts/deploy-workshop.ps1"
if (-not (Test-Path $deployWs)) {
    Write-Warning "[deploy-all] deploy-workshop.ps1 不存在，跳过 Mod 部署：$deployWs"
}
elseif (Get-Process -Name "SlayTheSpire2" -ErrorAction SilentlyContinue) {
    # 游戏在运行时 neko_comm/nekospire.dll 被占用，拷不进去；这里的部署需先退出游戏。
    Write-Warning "[deploy-all] 检测到游戏正在运行（SlayTheSpire2），nekospire.dll 被占用，跳过 Mod 部署。"
    Write-Warning "[deploy-all] 请 退出游戏 后重新运行本脚本，以完成 Mod 部署（插件已部署）。"
}
else {
    $gameMod = Join-Path $ProjectRoot "game_mod"
    if (-not [string]::IsNullOrWhiteSpace($UploadContentDir)) {
        & $deployWs -ProjectRoot $gameMod -UploadContentDir $UploadContentDir
    }
    else {
        & $deployWs -ProjectRoot $gameMod
    }
    if ($LASTEXITCODE -ne 0) { throw "deploy-workshop.ps1 失败（exit $LASTEXITCODE）" }
}

Write-Host "[deploy-all] Done."
