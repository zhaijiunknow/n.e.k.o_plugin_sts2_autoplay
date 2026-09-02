param(
    [string]$ProjectRoot = "",
    [int]$HostApiPort   = 18080,   # the user's Steam host (this mod's /state)
    [int]$ClientApiPort = 18081,   # the catgirl (default coop_client_port)
    [string]$ExePath = "D:\Steam\steamapps\common\Slay the Spire 2\SlayTheSpire2.exe",
    [int]$LobbyPollAttempts = 240,
    [int]$LobbyPollDelayMs  = 500,
    [switch]$DriveHost,            # also ready the host + navigate so both reach COMBAT
    [switch]$KeepGamesRunning      # do NOT stop the catgirl on exit
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

$scriptRoot = Join-Path $ProjectRoot "scripts"
$startSession = Join-Path $scriptRoot "start-game-session.ps1"
$hostBaseUrl = "http://127.0.0.1:$HostApiPort"
$clientBaseUrl = "http://127.0.0.1:$ClientApiPort"
$catgirlSession = $null

function Ensure-SteamAppId {
    param([string]$AppId = "2868840")
    $gameRoot = Split-Path -Parent $ExePath
    $file = Join-Path $gameRoot "steam_appid.txt"
    if (-not (Test-Path $file)) {
        Set-Content -Path $file -Value $AppId -Encoding ascii -NoNewline
        Write-Host "[steam-join] created $file ($AppId)"
    }
    else {
        $cur = (Get-Content $file -Raw).Trim()
        if ($cur -ne $AppId) {
            Set-Content -Path $file -Value $AppId -Encoding ascii -NoNewline
            Write-Host "[steam-join] updated $file -> $AppId"
        }
    }
}

function Invoke-ApiJson {
    param(
        [string]$BaseUrl,
        [string]$Method,
        [string]$Path,
        $Body = $null,
        [int]$TimeoutSec = 10,
        [int]$RetryCount = 15,
        [int]$RetryDelayMs = 1000
    )
    $uri = $BaseUrl.TrimEnd("/") + $Path
    for ($attempt = 0; $attempt -lt $RetryCount; $attempt++) {
        try {
            if ($null -ne $Body) {
                $jsonBody = $Body | ConvertTo-Json -Depth 8 -Compress
                return Invoke-RestMethod -Uri $uri -Method $Method -ContentType "application/json" -Body $jsonBody -TimeoutSec $TimeoutSec
            }
            return Invoke-RestMethod -Uri $uri -Method $Method -TimeoutSec $TimeoutSec
        }
        catch {
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
                return $_.ErrorDetails.Message | ConvertFrom-Json
            }
            if ($_.Exception.Response -and $_.Exception.Response.GetResponseStream()) {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $content = $reader.ReadToEnd()
                if ($content) { return $content | ConvertFrom-Json }
            }
            $isLastAttempt = $attempt -ge ($RetryCount - 1)
            if ($isLastAttempt) { throw }
            Start-Sleep -Milliseconds $RetryDelayMs
        }
    }
}

function Get-State {
    param([string]$BaseUrl)
    return (Invoke-ApiJson -BaseUrl $BaseUrl -Method "GET" -Path "/state").data
}

function Invoke-Action {
    param([string]$BaseUrl, [hashtable]$Payload)
    return (Invoke-ApiJson -BaseUrl $BaseUrl -Method "POST" -Path "/action" -Body $Payload)
}

function Invoke-ActionExpectOk {
    param([string]$BaseUrl, [hashtable]$Payload, [string]$Description)
    $resp = Invoke-Action -BaseUrl $BaseUrl -Payload $Payload
    if (-not $resp.ok) {
        throw "${Description} failed: $($resp | ConvertTo-Json -Depth 8 -Compress)"
    }
    return $resp
}

function Wait-ForState {
    param(
        [string]$BaseUrl,
        [string]$Description,
        [scriptblock]$Condition,
        [int]$PollAttempts = 180,
        [int]$PollDelayMs = 250
    )
    for ($attempt = 0; $attempt -lt $PollAttempts; $attempt++) {
        $state = Get-State -BaseUrl $BaseUrl
        if (& $Condition $state) { return $state }
        Start-Sleep -Milliseconds $PollDelayMs
    }
    throw "Timed out waiting for state at ${BaseUrl}: $Description"
}

function Resolve-BlockingModal {
    param([string]$BaseUrl, [int]$MaxAttempts = 4)
    for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt++) {
        $state = Get-State -BaseUrl $BaseUrl
        if ($state.screen -ne "MODAL") { return $state }
        $actions = @($state.available_actions)
        if ($actions -contains "confirm_modal") {
            $null = Invoke-Action -BaseUrl $BaseUrl -Payload @{ action = "confirm_modal" }
            Start-Sleep -Milliseconds 250
            continue
        }
        if ($actions -contains "dismiss_modal") {
            $null = Invoke-Action -BaseUrl $BaseUrl -Payload @{ action = "dismiss_modal" }
            Start-Sleep -Milliseconds 250
            continue
        }
        throw "Modal is blocking progress at ${BaseUrl}: $($state | ConvertTo-Json -Depth 8 -Compress)"
    }
    return Get-State -BaseUrl $BaseUrl
}

function Wait-ForCombatPlayable {
    param([string]$BaseUrl, [string]$Description)
    return Wait-ForState -BaseUrl $BaseUrl -Description $Description -Condition {
        param($s)
        $s.screen -eq "COMBAT" -and $s.in_combat -and $null -ne $s.combat -and
        @($s.combat.enemies).Count -ge 1 -and
        @($s.available_actions) -contains "play_card"
    } -PollAttempts $LobbyPollAttempts -PollDelayMs $LobbyPollDelayMs
}

try {
    Write-Host "=== Steam lobby join test ==="
    Write-Host "[steam-join] host=$hostBaseUrl client=$clientBaseUrl"

    Ensure-SteamAppId

    # 1. The user's Steam-hosted room must be open. Read its lobby id.
    $hostState = Get-State -BaseUrl $hostBaseUrl
    $lobbyId = [string]$hostState.multiplayer.lobby_id
    if ([string]::IsNullOrWhiteSpace($lobbyId)) {
        throw "No Steam lobby id on host. Open a multiplayer room in the game UI (Steam) and sit on the character-select screen, then re-run. (host.state.multiplayer=$($hostState.multiplayer | ConvertTo-Json -Depth 4 -Compress))"
    }
    Write-Host "[steam-join] host lobby_id=$lobbyId (player_count=$($hostState.multiplayer.player_count), net_game_type=$($hostState.multiplayer.net_game_type))"

    # 2. Launch the catgirl with +connect_lobby; preserve the (user's) host.
    Write-Host "[steam-join] launching catgirl (port $ClientApiPort, +connect_lobby $lobbyId)..."
    $catgirlJson = & $startSession -ExePath $ExePath -EnableDebugActions -ApiPort $ClientApiPort -ConnectLobbyId $lobbyId -KeepExistingProcesses
    $catgirlSession = $catgirlJson | ConvertFrom-Json
    Write-Host "[steam-join] catgirl pid=$($catgirlSession.pid) base=$($catgirlSession.base_url)"

    # 3. Catgirl auto-joins -> lands on CHARACTER_SELECT as a client.
    $null = Wait-ForState -BaseUrl $clientBaseUrl -Description "catgirl auto-joined Steam lobby (CHARACTER_SELECT)" -Condition {
        param($s)
        $s.screen -eq "CHARACTER_SELECT" -and $null -ne $s.multiplayer -and
        $s.multiplayer.is_multiplayer -and $s.multiplayer.net_game_type -ne "host"
    } -PollAttempts $LobbyPollAttempts -PollDelayMs $LobbyPollDelayMs
    Write-Host "[steam-join] catgirl joined via Steam lobby"

    # 4. Catgirl selects DEFECT (the autoplay drives it; we only observe).
    $null = Wait-ForState -BaseUrl $clientBaseUrl -Description "catgirl selected DEFECT" -Condition {
        param($s)
        $s.character_select.selected_character_id -eq "DEFECT"
    } -PollAttempts $LobbyPollAttempts -PollDelayMs $LobbyPollDelayMs
    Write-Host "[steam-join] catgirl selected DEFECT"

    # 5. Host sees the second player.
    $null = Wait-ForState -BaseUrl $hostBaseUrl -Description "host sees 2 players" -Condition {
        param($s) [int]$s.multiplayer.player_count -eq 2
    } -PollAttempts $LobbyPollAttempts -PollDelayMs $LobbyPollDelayMs

    # 6. Ready up: if -DriveHost, ready the host (embark); otherwise wait for the user to ready.
    if ($DriveHost) {
        $hostState = Get-State -BaseUrl $hostBaseUrl
        if (@($hostState.available_actions) -contains "embark") {
            Write-Host "[steam-join] -DriveHost: host embark (ready)"
            $null = Invoke-ActionExpectOk -BaseUrl $hostBaseUrl -Payload @{ action = "embark" } -Description "host embark"
        }
    }
    else {
        Write-Host "[steam-join] waiting for the user to ready the host on the host UI..."
    }

    # 7. Run starts (both ready, 2 players) -> MAP.
    $null = Wait-ForState -BaseUrl $clientBaseUrl -Description "run started (2 players)" -Condition {
        param($s)
        $s.screen -ne "CHARACTER_SELECT" -and $null -ne $s.run -and
        @($s.run.players).Count -eq 2 -and $s.multiplayer.is_multiplayer
    } -PollAttempts $LobbyPollAttempts -PollDelayMs $LobbyPollDelayMs
    $null = Wait-ForState -BaseUrl $clientBaseUrl -Description "catgirl on MAP" -Condition {
        param($s) $s.screen -eq "MAP" -and @($s.map.available_nodes).Count -ge 1
    } -PollAttempts $LobbyPollAttempts -PollDelayMs $LobbyPollDelayMs
    Write-Host "[steam-join] co-op run started; catgirl on MAP"

    # 8. COMBAT: the catgirl autoplay votes node 0. -DriveHost makes the host vote node 0 too.
    $combatReached = $false
    if ($DriveHost) {
        $null = Invoke-ActionExpectOk -BaseUrl $hostBaseUrl -Payload @{ action = "choose_map_node"; option_index = 0 } -Description "host vote node 0"
        $null = Wait-ForCombatPlayable -BaseUrl $clientBaseUrl -Description "catgirl combat ready"
        $null = Wait-ForState -BaseUrl $clientBaseUrl -Description "catgirl played a card (autoplay)" -Condition {
            param($s) $s.combat.player.cards_played_this_turn -ge 1
        } -PollAttempts $LobbyPollAttempts -PollDelayMs $LobbyPollDelayMs
        $combatReached = $true
        Write-Host "[steam-join] COMBAT reached; catgirl auto-played a card"
    }
    else {
        Write-Host "[steam-join] COMBAT requires the host to also navigate (re-run with -DriveHost); MAP is the terminal assertion this run."
    }

    # 9. Summary.
    $finalHost = Get-State -BaseUrl $hostBaseUrl
    $finalClient = Get-State -BaseUrl $clientBaseUrl
    [pscustomobject]@{
        host = [pscustomobject]@{
            base_url = $hostBaseUrl
            screen = $finalHost.screen
            player_count = @($finalHost.multiplayer.player_count)
            lobby_id = $finalHost.multiplayer.lobby_id
        }
        catgirl = [pscustomobject]@{
            pid = $catgirlSession.pid
            base_url = $clientBaseUrl
            screen = $finalClient.screen
            selected_character_id = $finalClient.character_select.selected_character_id
            player_count = @($finalClient.run.players).Count
            combat_reached = $combatReached
            cards_played_this_turn = if ($finalClient.combat) { $finalClient.combat.player.cards_played_this_turn } else { $null }
        }
    } | ConvertTo-Json -Depth 6
}
finally {
    if (-not $KeepGamesRunning -and $null -ne $catgirlSession -and $catgirlSession.pid) {
        Write-Host "[steam-join] stopping catgirl pid $($catgirlSession.pid) (host left running)"
        Stop-Process -Id $catgirlSession.pid -Force -ErrorAction SilentlyContinue
    }
}
