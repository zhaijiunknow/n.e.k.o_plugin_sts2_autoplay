<#
.SYNOPSIS
  一键启动「猫娘 co-op」会话：拉起 host + catgirl client，建/加 co-op 大厅，选角，双方 ready 开局。

.DESCRIPTION
  复用 scripts/start-game-session.ps1 启动两个游戏实例（不同 API 端口、均带 debug 环境变量），
  然后自动完成 co-op 会话引导：
    host   : 进 multiplayer test 场景 -> host_multiplayer_lobby -> select_character
    client : 进 multiplayer test 场景 -> join_multiplayer_lobby -> select_character -> ready
    host   : ready
  双方 ready 后 run 启动、进入地图。
  加 -EnterCombat 会让两边投票进第一场战斗（选第 0 个可走节点），并等进入 COMBAT。

  host 本身也可不加 -SkipHostLaunch，本脚本默认也替你把 host 拉起来（方便整链路自动化）。

.PARAMETER GameRoot
  Slay the Spire 2 游戏根目录。默认 D 盘 Steam 路径。

.PARAMETER ExePath
  游戏 exe 路径。默认 <GameRoot>\SlayTheSpire2.exe。

.PARAMETER HostApiPort
  host 的 API 端口。默认 18080（读 STS2_API_PORT）。

.PARAMETER ClientApiPort
  猫娘 client 的 API 端口。默认 18081。

.PARAMETER HostCharacterIndex
  host 选角下标（0 IRONCLAD / 1 SILENT / 2 REGENT / 3 NECROBINDER / 4 DEFECT）。默认 1 = SILENT。

.PARAMETER ClientCharacterIndex
  猫娘 client 选角下标。默认 4 = DEFECT。

.PARAMETER SkipHostLaunch
  跳过启动 host（当你已从 Steam 手动开着 host 时用；此时 host 需已处于 MULTIPLAYER_LOBBY 场景）。

.PARAMETER EnterCombat
  开局后自动让两边投票进第一场战斗并等待 COMBAT。

.EXAMPLE
  # 全自动：拉起 host + 猫娘 client，建 co-op 会话，进第一场战斗
  .\scripts\Start-NekoCoop.ps1 -EnterCombat
#>
param(
    [string]$GameRoot = "D:\Steam\steamapps\common\Slay the Spire 2",
    [string]$ExePath = "",
    [int]$HostApiPort = 18080,
    [int]$ClientApiPort = 18081,
    [int]$HostCharacterIndex = 1,
    [int]$ClientCharacterIndex = 4,
    [switch]$SkipHostLaunch,
    [switch]$EnterCombat,
    [switch]$ClientAutoplays    # catgirl client drives itself (join/select/ready/vote) via the mod autoplay
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $GameRoot "SlayTheSpire2.exe"
}
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$startSession = Join-Path $scriptRoot "start-game-session.ps1"

function Ensure-SteamAppId {
    param([string]$AppId = "2868840")
    $gameRoot = Split-Path -Parent $ExePath
    $file = Join-Path $gameRoot "steam_appid.txt"
    if (-not (Test-Path $file)) {
        Set-Content -Path $file -Value $AppId -Encoding ascii -NoNewline
        Write-Host "[coop] created $file ($AppId)"
        return
    }
    $cur = (Get-Content $file -Raw).Trim()
    if ($cur -ne $AppId) {
        Set-Content -Path $file -Value $AppId -Encoding ascii -NoNewline
        Write-Host "[coop] updated $file -> $AppId"
    }
}

function Invoke-Action {
    param([string]$BaseUrl, [hashtable]$Payload, [string]$Label)
    $Body = $Payload | ConvertTo-Json -Compress
    Write-Host "[coop] $Label ..." -NoNewline
    try {
        $Resp = Invoke-RestMethod -Method Post -Uri "$BaseUrl/action" -Body $Body -ContentType "application/json" -TimeoutSec 60
    } catch {
        throw "$Label HTTP error: $_"
    }
    if (-not $Resp.ok) {
        throw "$Label refused: code=$($Resp.error.code) msg=$($Resp.error.message)"
    }
    Write-Host " OK"
    return $Resp.data.state
}

function Get-StateScreen {
    param([string]$BaseUrl)
    try {
        $Resp = Invoke-RestMethod -Method Get -Uri "$BaseUrl/state" -TimeoutSec 5
        return $Resp.data.screen
    } catch {
        return $null
    }
}

function Wait-ForScreen {
    param([string]$BaseUrl, [string]$Target, [int]$MaxSeconds = 90, [string]$Desc)
    $deadline = (Get-Date).AddSeconds($MaxSeconds)
    while ((Get-Date) -lt $deadline) {
        $screen = Get-StateScreen -BaseUrl $BaseUrl
        if ($screen -eq $Target) {
            Write-Host "[coop] reached $Target ($Desc)"
            return $screen
        }
        Start-Sleep -Milliseconds 500
    }
    throw "timeout waiting for '$Target' on $BaseUrl ($Desc); last screen=$((Get-StateScreen -BaseUrl $BaseUrl))"
}

function Wait-ForClientLobbyReady {
    param([string]$BaseUrl, [int]$MaxSeconds = 90)
    $deadline = (Get-Date).AddSeconds($MaxSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-RestMethod -Method Get -Uri "$BaseUrl/state" -TimeoutSec 5
            $lobby = $resp.data.multiplayer_lobby
            if ($lobby -and $lobby.has_lobby -and $lobby.local_ready) {
                Write-Host "[coop] client is ready in lobby"
                return $true
            }
        } catch { }
        Start-Sleep -Milliseconds 700
    }
    throw "timeout waiting for client to be ready in lobby at $BaseUrl; last=$((Get-StateScreen -BaseUrl $BaseUrl))"
}

Write-Host "=== Neko co-op launcher ==="
Write-Host "[coop] game=$ExePath"
Write-Host "[coop] host=18080 client=18081 (chars host=$HostCharacterIndex client=$ClientCharacterIndex)"

Ensure-SteamAppId

# ---- host ----
$hostBase = "http://127.0.0.1:$HostApiPort"
if (-not $SkipHostLaunch) {
    Write-Host "[coop] starting host (port $HostApiPort, debug)..."
    & $startSession -ExePath $ExePath -EnableDebugActions -ApiPort $HostApiPort | Out-Host
}

# host: open multiplayer test scene
$null = Invoke-Action -BaseUrl $hostBase -Payload @{ action = "run_console_command"; command = "multiplayer test" } -Label "host open multiplayer test"
$null = Invoke-Action -BaseUrl $hostBase -Payload @{ action = "host_multiplayer_lobby" } -Label "host create lobby"
$null = Invoke-Action -BaseUrl $hostBase -Payload @{ action = "select_character"; option_index = $HostCharacterIndex } -Label "host select character"

# ---- client ----
$clientBase = "http://127.0.0.1:$ClientApiPort"
Write-Host "[coop] starting client (catgirl, port $ClientApiPort, debug)..."
& $startSession -ExePath $ExePath -EnableDebugActions -ApiPort $ClientApiPort -KeepExistingProcesses | Out-Host

if (-not $ClientAutoplays) {
    $null = Invoke-Action -BaseUrl $clientBase -Payload @{ action = "run_console_command"; command = "multiplayer test" } -Label "client open multiplayer test"
    $null = Invoke-Action -BaseUrl $clientBase -Payload @{ action = "join_multiplayer_lobby" } -Label "client join lobby"
    $null = Invoke-Action -BaseUrl $clientBase -Payload @{ action = "select_character"; option_index = $ClientCharacterIndex } -Label "client select character"
    $null = Invoke-Action -BaseUrl $clientBase -Payload @{ action = "ready_multiplayer_lobby" } -Label "client ready"
} else {
    Write-Host "[coop] -ClientAutoplays: catgirl client joins/selects/readies itself (port $ClientApiPort)"
    $null = Wait-ForClientLobbyReady -BaseUrl $clientBase
}
$null = Invoke-Action -BaseUrl $hostBase -Payload @{ action = "ready_multiplayer_lobby" } -Label "host ready"

# run should start -> map
Write-Host "[coop] both ready; waiting for run to start (MAP)..."
$null = Wait-ForScreen -BaseUrl $clientBase -Target "MAP" -MaxSeconds 60 -Desc "run started (client view)"

if ($EnterCombat) {
    # vote into first available node on both (same index 0)
    $null = Invoke-Action -BaseUrl $hostBase -Payload @{ action = "choose_map_node"; option_index = 0 } -Label "host vote node 0"
    if (-not $ClientAutoplays) {
        $null = Invoke-Action -BaseUrl $clientBase -Payload @{ action = "choose_map_node"; option_index = 0 } -Label "client vote node 0"
    }
    $null = Wait-ForScreen -BaseUrl $clientBase -Target "COMBAT" -MaxSeconds 90 -Desc "entered co-op combat (client view)"
    Write-Host "=== at COMBAT: catgirl client ready to play on port $ClientApiPort ==="
} else {
    Write-Host "=== co-op session live on MAP ==="
    Write-Host "    host(bot?/human)=port $HostApiPort ; catgirl client=port $ClientApiPort"
    Write-Host "    to enter a combat, vote the SAME map node on both sides (or rerun with -EnterCombat)."
}

Write-Host "Done."
