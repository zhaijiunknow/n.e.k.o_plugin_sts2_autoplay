<#
.SYNOPSIS
  内嵌 CombatSolver 引擎的 vendor 同步器：把上游仓库按既定边界重新 vendor 进 nekospire，或只做只读差距报告。

.DESCRIPTION
  本仓库的引擎不是"整棵拷过来"，而是"按边界取一个闭包，再叠三层本地适配"：
    1) vendor 闭包   = 上游 src/{Engine,Prediction,Runtime,Search} 里非外壳的部分
    2) 本地保留文件  = GameRef.cs（反射适配）/ EntryShim.cs / SolverPolicyEnums.cs（补被排除的声明）
    3) 接缝          = CombatSolverFacade.cs / CombatSolverRuntime.cs
  边界与适配规则写在 Solver/VENDORING-RULES.json（手写，唯一事实来源）；
  现状普查写在 Solver/VENDORING-MANIFEST.json（本脚本生成，不要手改）。

  默认 **只读**：-Report 只普查 + 打差距，不动任何源码。
  -Apply 才会改写 Solver/ 下的 vendor 源码，且先自动备份到一个 zip。

  做不到的部分：本机没有游戏，脚本只能保证"编译通过 + 测试通过"，
  求解器在真实对局里的行为必须由作者在游戏内验证。

.EXAMPLE
  # 只读差距报告（安全，默认模式）
  .\sync-vendor.ps1

.EXAMPLE
  # 真的执行再 vendor（会先备份，然后改写源码并编译）
  .\sync-vendor.ps1 -Apply
#>
[CmdletBinding(DefaultParameterSetName = 'Report')]
param(
    # 上游 CombatSolver 克隆位置
    [string]$UpstreamRoot = "D:\NekoClaw\CombatSolver",

    # 只读：普查 + 差距报告（默认）
    [Parameter(ParameterSetName = 'Report')]
    [switch]$Report,

    # 执行：备份后把 vendor 闭包换成上游版本，保留本地适配层，然后编译
    [Parameter(ParameterSetName = 'Apply')]
    [switch]$Apply,

    # 备份 zip 的输出目录
    [string]$BackupDir = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$scriptRoot = $PSScriptRoot
$gameMod    = (Resolve-Path (Join-Path $scriptRoot "..")).Path
$engineRoot = Join-Path $gameMod "nekospire\Solver"
$rulesPath  = Join-Path $engineRoot "VENDORING-RULES.json"
$manifestPath = Join-Path $engineRoot "VENDORING-MANIFEST.json"

if (-not (Test-Path $rulesPath))  { throw "找不到规则文件: $rulesPath" }
if (-not (Test-Path $UpstreamRoot)) { throw "找不到上游克隆: $UpstreamRoot（先 git clone https://github.com/Torch1230/CombatSolver.git）" }

$rules = Get-Content $rulesPath -Raw -Encoding UTF8 | ConvertFrom-Json
# 本脚本只认识 schema/1 的规则（0.28 -> 0.50.1 的 Engine/Search 架构：三方合并 + 按文件名排除）。
# 内嵌引擎换成上游 1.0.0（HotKernel）后规则升到 schema/2，同步改由 vendor-combat-solver.py 负责。
# 在新规则上继续跑会按不存在的排除清单清空并重拷 vendor 子树，所以这里直接拒绝。
if ($rules.schema -ne "nekospire-combat-solver-vendoring-rules/1") {
    throw "sync-vendor.ps1 已随旧引擎作废（规则 schema = $($rules.schema)）。请改用: python game_mod\scriptsendor-combat-solver.py"
}
$upSrc = Join-Path $UpstreamRoot "src"
if (-not (Test-Path $upSrc)) { throw "上游缺少 src/ 目录: $upSrc" }

function Write-Head([string]$t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }

# ---------- 上游版本核对 ----------
Write-Head "上游版本"
$upVer = "?"
$verFile = Join-Path $UpstreamRoot "CombatSolver.json"
if (Test-Path $verFile) {
    $upVer = (Get-Content $verFile -Raw -Encoding UTF8 | ConvertFrom-Json).version
}
$upCommit = (git -C $UpstreamRoot rev-parse --short=7 HEAD 2>$null)
Write-Host "  版本   : $upVer (规则期望 $($rules.upstream.expected_version))"
Write-Host "  提交   : $upCommit (规则期望 $($rules.upstream.expected_commit))"
# 前缀比对：补齐历史后 --short 可能给出更长缩写，不能要求逐字相等
if ($upVer -ne $rules.upstream.expected_version -or
    -not ($upCommit -like "$($rules.upstream.expected_commit)*" -or $rules.upstream.expected_commit -like "$upCommit*")) {
    Write-Warning "  上游与规则里记录的版本/提交不一致 —— 边界可能需要重新确认后再 -Apply。"
}

# ---------- 判定：某上游文件是否被排除 ----------
function Get-ExclusionKind {
    param([string]$Subtree, [string]$RelPath)
    if ($rules.exclude_subtrees.PSObject.Properties.Name -contains $Subtree) { return "hard" }
    $name = Split-Path $RelPath -Leaf
    foreach ($p in $rules.exclude_file_patterns.hard)      { if ($name -like $p)      { return "hard" } }
    foreach ($p in $rules.exclude_file_patterns.candidate) { if ($name -like $p)      { return "candidate" } }
    return "none"
}

function Test-IsGodotCoupled {
    param([string]$FullPath)
    $t = Get-Content $FullPath -Raw -Encoding UTF8
    return ($t -match '(?m)^using Godot;' -or $t -match ': Node\b')
}

function Get-NormalizedLines {
    param([string]$FullPath)
    $t = Get-Content $FullPath -Raw -Encoding UTF8
    if ($t.Length -gt 0 -and [int]$t[0] -eq 0xFEFF) { $t = $t.Substring(1) }
    return ($t -replace "`r`n", "`n" -replace "`r", "`n")
}

# ---------- 普查 ----------
$subtrees = @($rules.vendor_subtrees)
$census = @()
foreach ($sub in $subtrees) {
    $mineDir = Join-Path $engineRoot $sub
    $upDir   = Join-Path $upSrc $sub
    if (-not (Test-Path $mineDir)) { continue }
    foreach ($f in (Get-ChildItem $mineDir -Recurse -File -Include *.cs)) {
        $rel = $f.FullName.Substring($mineDir.Length + 1).Replace('\', '/')
        $upF = Join-Path $upDir ($rel -replace '/', '\')
        $localText = Get-NormalizedLines $f.FullName
        $rec = [ordered]@{
            subtree          = $sub
            path             = $rel
            local_lines      = ($localText -split "`n").Count
            uses_gameref     = ($localText -match 'GameRef\.')
            upstream_lines   = $null
            status           = "local-only"
        }
        if (Test-Path $upF) {
            $upText = Get-NormalizedLines $upF
            $rec.upstream_lines = ($upText -split "`n").Count
            $rec.status = if ($upText -eq $localText) { "identical" } else { "changed" }
        }
        $census += [pscustomobject]$rec
    }
}

Write-Head "现状普查（本地已 vendor 的 $($census.Count) 个文件）"
$census | Group-Object subtree | ForEach-Object {
    $g = $_.Group
    $upTotal = (Get-ChildItem (Join-Path $upSrc $_.Name) -Recurse -File -Include *.cs -ErrorAction SilentlyContinue | Measure-Object).Count
    # 注意：Where-Object 只命中一个对象时返回单对象，其 .Count 为 $null，必须用 @() 包成数组
    Write-Host ("  {0,-11} 本地 {1,4}   上游同子树共 {2,4}   逐字相同 {3,3}   有差异 {4,3}   本地独有 {5,2}   用GameRef {6,3}" -f `
        $_.Name, @($g).Count,
        $upTotal,
        @($g | Where-Object status -eq "identical").Count,
        @($g | Where-Object status -eq "changed").Count,
        @($g | Where-Object status -eq "local-only").Count,
        @($g | Where-Object uses_gameref).Count)
}

# ---------- 差距：上游有哪些文件没被 vendor ----------
Write-Head "差距：上游在 vendor 子树里、但本地没有的文件"
$newCore = @(); $newShell = @(); $newCandidate = @()
foreach ($sub in $subtrees) {
    $upDir   = Join-Path $upSrc $sub
    $mineDir = Join-Path $engineRoot $sub
    if (-not (Test-Path $upDir)) { continue }
    foreach ($f in (Get-ChildItem $upDir -Recurse -File -Include *.cs)) {
        $rel = $f.FullName.Substring($upDir.Length + 1).Replace('\', '/')
        $localF = Join-Path $mineDir ($rel -replace '/', '\')
        if (Test-Path $localF) { continue }
        $kind = Get-ExclusionKind -Subtree $sub -RelPath $rel
        $godot = Test-IsGodotCoupled $f.FullName
        $row = [pscustomobject]@{ subtree = $sub; path = $rel; godot = $godot; lines = (Get-NormalizedLines $f.FullName -ErrorAction SilentlyContinue | Out-String).Length }
        switch ($kind) {
            "hard"      { $newShell     += $row }
            "candidate" { $newCandidate += $row }
            default     { $newCore      += $row }
        }
    }
}
Write-Host ("  按规则判定应当 vendor（新文件）: {0}" -f @($newCore).Count) -ForegroundColor Yellow
Write-Host ("    其中含 Godot 耦合、必须去 Godot 化后编入: {0}" -f @($newCore | Where-Object godot).Count)
Write-Host ("  规则标为 candidate（需人工确认外壳与否）: {0}" -f @($newCandidate).Count)
Write-Host ("  规则排除（上游外壳，不进 vendor）: {0}" -f @($newShell).Count)

if (@($newCore | Where-Object godot).Count -gt 0) {
    Write-Head "含 Godot 的新核心文件（必须 vendor，但要先去掉 Godot 调用）"
    $newCore | Where-Object godot | Sort-Object subtree, path | ForEach-Object { Write-Host ("  [{0}] {1}" -f $_.subtree, $_.path) }
    Write-Host "  说明：这些多是 CombatBeamSolver 的 partial 分片，属于搜索大脑本体；排除它们会引发大量 missing-type 连锁错误。"
}

if (@($newCandidate).Count -gt 0) {
    Write-Head "candidate 清单（请你判断该不该进）"
    $newCandidate | Group-Object subtree | ForEach-Object {
        Write-Host "  [$($_.Name)]"
        $_.Group | Sort-Object path | ForEach-Object { Write-Host ("     {0}{1}" -f $_.path, $(if ($_.godot) { "   (含 Godot)" } else { "" })) }
    }
}

# ---------- 三方合并分析（需要上游完整历史）----------
# 这一步回答真正决定工作量的问题：哪些文件能整份取上游新版本，哪些必须人工合并。
#   base = 上游基线标签（规则里的 provenance.baseline_tag，已用逐字比对钉死）
#   我们 = 本地 Solver/<sub>
#   上游 = 上游 HEAD
Write-Head "三方合并分析（base = $($rules.upstream.baseline_tag)）"
$baseTag = $rules.upstream.baseline_tag
$baseDir = $null
$hasHistory = $true
if (-not $baseTag) {
    Write-Warning "  规则里没有 baseline_tag，跳过三方分析。"
    $hasHistory = $false
} else {
    $null = git -C $UpstreamRoot rev-parse --verify "$baseTag^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "  上游克隆里找不到标签 $baseTag（可能还是浅克隆）。执行："
        Write-Warning "    git -C `"$UpstreamRoot`" fetch --unshallow --tags"
        $hasHistory = $false
    }
}

$tri = $null
if ($hasHistory) {
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("neko-vendor-base-" + [guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp "base.zip"
        git -C $UpstreamRoot archive --format=zip -o $zip $baseTag src 2>$null
        if ($LASTEXITCODE -ne 0) { throw "git archive 失败" }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $baseDir = Join-Path $tmp "src"

        $cTakeUpstream = @()   # 与 base 逐字相同 -> 直接取上游新版
        $cConflict    = @()    # 我们改了、上游也改了 -> 人工合并
        $cKeepOurs    = @()    # 我们改了、上游没动 -> 保留我们的
        $cUpDeleted   = @()    # 上游删了 -> 需决策
        $cLocalOnly   = @()

        foreach ($sub in $subtrees) {
            $mineDir = Join-Path $engineRoot $sub
            $baseSub = Join-Path $baseDir $sub
            if (-not (Test-Path $mineDir)) { continue }
            foreach ($f in (Get-ChildItem $mineDir -Recurse -File -Include *.cs)) {
                $rel = $f.FullName.Substring($mineDir.Length + 1)
                $b = Join-Path $baseSub $rel
                $h = Join-Path (Join-Path $upSrc $sub) $rel
                $localText = Get-NormalizedLines $f.FullName
                $gameref = $localText -match 'GameRef\.'
                if (-not (Test-Path $b)) { $cLocalOnly += "$sub/$($rel -replace '\\','/')"; continue }
                $baseText = Get-NormalizedLines $b
                if ($baseText -eq $localText) {
                    $cTakeUpstream += "$sub/$($rel -replace '\\','/')"
                } else {
                    if (-not (Test-Path $h)) { $cUpDeleted += "$sub/$($rel -replace '\\','/')"; continue }
                    $headText = Get-NormalizedLines $h
                    if ($headText -ne $baseText) {
                        $cConflict += [pscustomobject]@{ path = "$sub/$($rel -replace '\\','/')"; gameref = $gameref }
                    } else {
                        $cKeepOurs += [pscustomobject]@{ path = "$sub/$($rel -replace '\\','/')"; gameref = $gameref }
                    }
                }
            }
        }

        Write-Host ("  与 base 逐字相同 -> 可整份取上游新版本 : {0}" -f @($cTakeUpstream).Count) -ForegroundColor Green
        Write-Host ("  我们改了 + 上游也改了 -> 人工三方合并 : {0}" -f @($cConflict).Count) -ForegroundColor Yellow
        Write-Host ("  我们改了 + 上游没动   -> 保留我们的     : {0}" -f @($cKeepOurs).Count)
        Write-Host ("  上游已删除 -> 需决策                    : {0}" -f @($cUpDeleted).Count)
        Write-Host ("  基线里不存在（本地独有）                : {0}" -f @($cLocalOnly).Count)

        if (@($cConflict).Count -gt 0) {
            Write-Head "需要人工三方合并的文件（★ = 含 GameRef 反射适配）"
            $cConflict | Sort-Object path | ForEach-Object { Write-Host ("  {0} {1}" -f $(if ($_.gameref) { "★" } else { " " }), $_.path) }
        }
        if (@($cUpDeleted).Count -gt 0) {
            Write-Head "上游已删除、但我们还在用的文件"
            $cUpDeleted | ForEach-Object { Write-Host "  $_" }
        }

        $tri = [ordered]@{
            baseline_tag = $baseTag
            take_upstream_verbatim = @($cTakeUpstream).Count
            manual_three_way_merge = @($cConflict).Count
            keep_ours              = @($cKeepOurs).Count
            upstream_deleted       = @($cUpDeleted).Count
            local_only             = @($cLocalOnly).Count
            conflict_files         = @($cConflict)
            keep_ours_files        = @($cKeepOurs)
            upstream_deleted_files = @($cUpDeleted)
        }
    }
    catch {
        Write-Warning "  三方分析失败：$($_.Exception.Message)"
    }
    finally {
        if ($tmp -and (Test-Path $tmp)) { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

# ---------- 写清单 ----------
$summary = [ordered]@{}
foreach ($sub in $subtrees) {
    $rows = $census | Where-Object subtree -eq $sub
    $summary[$sub] = [ordered]@{
        vendored              = @($rows).Count
        identical_to_upstream = @($rows | Where-Object status -eq "identical").Count
        changed_from_upstream = @($rows | Where-Object status -eq "changed").Count
        local_only            = @($rows | Where-Object status -eq "local-only").Count
        uses_gameref          = @($rows | Where-Object uses_gameref).Count
        upstream_total        = @(Get-ChildItem (Join-Path $upSrc $sub) -Recurse -File -Include *.cs -ErrorAction SilentlyContinue).Count
    }
}
$manifest = [ordered]@{
    schema          = "nekospire-combat-solver-vendoring/1"
    generated_by    = "game_mod/scripts/sync-vendor.ps1"
    generated_at    = (Get-Date).ToString("s")
    upstream        = [ordered]@{
        repository = $rules.upstream.repository
        commit     = $upCommit
        version    = $upVer
        license    = $rules.upstream.license
        holders    = $rules.upstream.license_holders
    }
    local           = [ordered]@{
        engine_root   = $rules.local.engine_root
        namespace     = $rules.local.namespace_root
        sdk           = "Microsoft.NET.Sdk (no Godot.NET.Sdk)"
        publicizer    = "none (Krafs.Publicizer is not honored by the mod loader for a plain-SDK plugin mod; private access goes through GameRef reflection)"
    }
    vendor_subtrees = $subtrees
    preserved_local_files = @($rules.always_preserve_local | ForEach-Object { $_.path })
    summary         = $summary
    three_way       = $tri
    totals          = [ordered]@{
        vendored              = @($census).Count
        identical_to_upstream = @($census | Where-Object status -eq "identical").Count
        changed_from_upstream = @($census | Where-Object status -eq "changed").Count
        local_only            = @($census | Where-Object status -eq "local-only").Count
        uses_gameref          = @($census | Where-Object uses_gameref).Count
        upstream_new_core     = @($newCore).Count
        upstream_new_shell    = @($newShell).Count
        upstream_new_candidate= @($newCandidate).Count
    }
    files = $census
}
$json = $manifest | ConvertTo-Json -Depth 8
# 不带 BOM 写出：其它工具（python/jq）读 JSON 时不必处理 BOM。
# 注意：本 .ps1 自身必须是 UTF-8 with BOM，否则 Windows PowerShell 5.1 会按 GBK 解析中文注释而报语法错。
[System.IO.File]::WriteAllText($manifestPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host ""
Write-Host "清单已写出: $manifestPath" -ForegroundColor Green

# ---------- 执行模式 ----------
if (-not $Apply) {
    Write-Head "只读模式结束"
    Write-Host "  没有改动任何源码。要真正执行再 vendor，加 -Apply。" -ForegroundColor Green
    Write-Host "  注意：即使 -Apply 成功，也只能保证'编译通过'，求解器在游戏内的行为必须由你实测。" -ForegroundColor Yellow
    return
}

Write-Head "执行模式：准备再 vendor"
if ([string]::IsNullOrWhiteSpace($BackupDir)) { $BackupDir = Join-Path $gameMod "build\vendor-backup" }
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$zip = Join-Path $BackupDir "Solver-$stamp.zip"
Write-Host "  备份当前 Solver/ -> $zip"
Compress-Archive -Path (Join-Path $engineRoot "*") -DestinationPath $zip -Force

# 1) 用上游版本替换 vendor 子树（排除外壳）
foreach ($sub in $subtrees) {
    $upDir   = Join-Path $upSrc $sub
    $mineDir = Join-Path $engineRoot $sub
    if (-not (Test-Path $upDir)) { continue }
    if (Test-Path $mineDir) { Remove-Item $mineDir -Recurse -Force }
    foreach ($f in (Get-ChildItem $upDir -Recurse -File -Include *.cs)) {
        $rel = $f.FullName.Substring($upDir.Length + 1).Replace('\', '/')
        if ((Get-ExclusionKind -Subtree $sub -RelPath $rel) -ne "none") { continue }
        $dest = Join-Path $mineDir ($rel -replace '/', '\')
        New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
        Copy-Item $f.FullName $dest -Force
    }
    Write-Host "  [$sub] 已按规则换成上游版本"
}

# 2) 恢复本地适配层（绝不能被上游覆盖）
foreach ($entry in $rules.always_preserve_local) {
    $rel = $entry.path -replace '^Solver/', ''
    $from = Join-Path $engineRoot $rel
    # 备份 zip 里那份才是本地原版；这里从 zip 里取回
    Write-Host "  保留本地文件: $($entry.path)"
}
Write-Warning "  本地适配层需要从备份 $zip 里取回 GameRef.cs / EntryShim.cs / SolverPolicyEnums.cs / CombatSolverFacade.cs / CombatSolverRuntime.cs —— 本脚本不自动做这一步，避免静默用错版本。"

# 3) 编译
Write-Head "编译"
Push-Location (Join-Path $gameMod "nekospire")
$out = dotnet build nekospire.csproj -c Release -v q --nologo 2>&1
Pop-Location
$errs = $out | Select-String -Pattern ": error "
Write-Host ("  错误数: {0}" -f $errs.Count) -ForegroundColor $(if ($errs.Count -eq 0) { "Green" } else { "Red" })
if ($errs.Count -gt 0) {
    Write-Host "  按错误码分类（对照 VENDORING-RULES.json 的 adaptation_rules 逐类修）:"
    $errs | ForEach-Object { if ($_.Line -match "(CS\d{4})") { $Matches[1] } } | Group-Object | Sort-Object Count -Descending |
        ForEach-Object { Write-Host ("    {0,5}  {1}" -f $_.Count, $_.Name) }
}
