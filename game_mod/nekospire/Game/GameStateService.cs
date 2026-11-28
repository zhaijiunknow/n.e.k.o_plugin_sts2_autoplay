using System.Collections;
using System.Reflection;
using System.Text.RegularExpressions;
using Godot;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Multiplayer;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Characters;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Multiplayer.Game.Lobby;
using MegaCrit.Sts2.Core.MonsterMoves.Intents;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Debug.Multiplayer;
using MegaCrit.Sts2.Core.Nodes.Ftue;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.CharacterSelect;
using MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Nodes.Screens.Timeline;
using MegaCrit.Sts2.Core.Nodes.Screens.Timeline.UnlockScreens;
using MegaCrit.Sts2.Core.Nodes.Screens.TreasureRoomRelic;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Timeline;
using MegaCrit.Sts2.addons.mega_text;
namespace NekoComm.Game;

internal static partial class GameStateService
{
    private const int StateVersion = 10;
    private static readonly HashSet<string> MissingCardsViewButtons = new();
    private const int AgentViewVersion = 4;
    private static readonly TimeSpan CombatActionSnapshotStableDelay = TimeSpan.FromMilliseconds(200);
    private static string? _lastCombatActionReadinessSignature;
    private static DateTime _lastCombatActionReadinessSinceUtc = DateTime.MinValue;
    private static readonly FieldInfo? StartRunLobbyMaxPlayersField =
        typeof(StartRunLobby).GetField("_maxPlayers", BindingFlags.Instance | BindingFlags.NonPublic);

    // 最近一次"界面节点认不出来"的类型全名（没有就是空串）。
    // 暴露到 /state 的 `unknown_screen_type`：下次遇到新界面，看一眼就知道该把哪个类型加进
    // ResolveScreen 的 switch，而不是只能对着 UNKNOWN 干瞪眼（2026-10-07 那次就是这么卡死的）。
    private static string _lastUnknownScreenType = "";

    public static string LastUnknownScreenType => _lastUnknownScreenType;

    public static GameStatePayload BuildStatePayload()
    {
        return BuildStatePayloadCore();
    }

    private static GameStatePayload BuildStatePayloadCore()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var combatState = CombatManager.Instance.DebugOnlyGetState();
        var runState = RunManager.Instance.DebugOnlyGetState();
        var screen = ResolveScreen(currentScreen);
        var session = BuildSessionPayload(currentScreen, runState);
        // ★ 必须在 BuildAvailableActionNames 之前算：动作清单要据此决定是否给 divine_crystal_sphere。
        var crystalSphere = BuildCrystalSpherePayload(currentScreen);
        var availableActions = BuildAvailableActionNames(currentScreen, combatState, runState, crystalSphere);
        var combat = BuildCombatPayload(combatState);
        var run = BuildRunPayload(currentScreen, combatState, runState);
        var multiplayer = BuildMultiplayerPayload(currentScreen, runState);
        var multiplayerLobby = BuildMultiplayerLobbyPayload(currentScreen);
        var map = BuildMapPayload(currentScreen, runState);
        var selection = BuildSelectionPayload(currentScreen);
        var characterSelect = BuildCharacterSelectPayload(currentScreen);
        var timeline = BuildTimelinePayload(currentScreen);
        var chest = BuildChestPayload(currentScreen);
        var eventPayload = BuildEventPayload(currentScreen);
        var shop = BuildShopPayload(currentScreen);
        var rest = BuildRestPayload(currentScreen, runState);
        var reward = BuildRewardPayload(currentScreen);
        var bundles = BuildBundlePayload(currentScreen);
        var modal = BuildModalPayload(currentScreen);
        var gameOver = BuildGameOverPayload(currentScreen, runState);
        var mainMenu = BuildMainMenuPayload(currentScreen, runState);

        return new GameStatePayload
        {
            state_version = StateVersion,
            run_id = runState?.Rng.StringSeed ?? "run_unknown",
            screen = screen,
            unknown_screen_type = LastUnknownScreenType,
            session = session,
            in_combat = CombatManager.Instance.IsInProgress,
            turn = combatState?.RoundNumber,
            available_actions = availableActions,
            combat = combat,
            run = run,
            multiplayer = multiplayer,
            multiplayer_lobby = multiplayerLobby,
            map = map,
            selection = selection,
            character_select = characterSelect,
            timeline = timeline,
            chest = chest,
            @event = eventPayload,
            crystal_sphere = crystalSphere,
            shop = shop,
            rest = rest,
            reward = reward,
            bundles = bundles,
            modal = modal,
            game_over = gameOver,
            main_menu = mainMenu,
            agent_view = BuildAgentViewPayload(
                screen,
                session,
                runState?.Rng.StringSeed ?? "run_unknown",
                combatState?.RoundNumber,
                availableActions,
                combatState,
                runState,
                combat,
                run,
                map,
                selection,
                characterSelect,
                timeline,
                chest,
                eventPayload,
                shop,
                rest,
                reward,
                bundles,
                modal,
                gameOver)
        };
    }

    private static SessionPayload BuildSessionPayload(IScreenContext? currentScreen, RunState? runState)
    {
        if (GetMultiplayerTestScene() != null)
        {
            return new SessionPayload
            {
                mode = "multiplayer",
                phase = "multiplayer_lobby",
                control_scope = "local_player"
            };
        }

        var characterSelectScreen = GetCharacterSelectScreen(currentScreen);
        if (characterSelectScreen != null)
        {
            return new SessionPayload
            {
                mode = characterSelectScreen.Lobby.NetService.Type.IsMultiplayer() ? "multiplayer" : "singleplayer",
                phase = "character_select",
                control_scope = "local_player"
            };
        }

        if (runState != null)
        {
            return new SessionPayload
            {
                mode = RunManager.Instance.NetService.Type.IsMultiplayer() ? "multiplayer" : "singleplayer",
                phase = "run",
                control_scope = "local_player"
            };
        }

        return new SessionPayload
        {
            mode = "singleplayer",
            phase = "menu",
            control_scope = "local_player"
        };
    }

    public static string ResolveScreen(IScreenContext? currentScreen)
    {
        if (GetOpenModal() != null)
        {
            return "MODAL";
        }

        var screen = ResolveNonModalScreen(currentScreen);
        if (screen != "UNKNOWN")
        {
            _lastUnknownScreenType = "";
            return screen;
        }

        _lastUnknownScreenType = currentScreen?.GetType().FullName ?? "";
        // 认不出的界面**不能就此摆烂**：只报 UNKNOWN 的话，可用动作会退化成只剩 `save_and_quit`，
        // 插件既推不动也读不懂，整局就永久卡住（2026-10-07 实测：水晶球事件的"占卜"子界面把
        // 第 22 层卡了 2 小时 40 分，`step_count` 冻在 2395）。
        // 静态房间按"当前房间类型"兜底 —— 事件/营地/商店/宝箱这些界面的动作是可重试的，猜错代价小；
        // **战斗与地图不猜**：那里猜错会真的乱出牌、乱走格子，宁可继续 UNKNOWN。
        var fallback = ResolveScreenFromStaticRoom();
        if (fallback != null)
        {
            Log.Warn($"[NekoComm] Unhandled screen type {_lastUnknownScreenType}, falling back to {fallback}");
            return fallback;
        }

        if (currentScreen != null)
        {
            Log.Warn($"[NekoComm] Unhandled screen type: {currentScreen.GetType().FullName}");
        }

        return screen;
    }

    /// <summary>
    /// 界面节点认不出时，用**当前房间类型**兜底出一个可用屏幕。只覆盖静态房间（见调用处说明）。
    /// </summary>
    private static string? ResolveScreenFromStaticRoom()
    {
        try
        {
            var room = RunManager.Instance.DebugOnlyGetState()?.CurrentRoom;
            return room switch
            {
                EventRoom => "EVENT",
                RestSiteRoom => "REST",
                MerchantRoom => "SHOP",
                TreasureRoom => "CHEST",
                _ => null
            };
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// 「当前屏**是不是**一个事件屏」——**唯一**的事件屏判据。
    ///
    /// 为什么不能直接写 `currentScreen is NEventRoom`：`ResolveScreen` 在认不出屏时会用
    /// `ResolveScreenFromStaticRoom()` 兜底成 "EVENT"（见 :736-741 的说明与 2026-10-07 实测）。
    /// 水晶球事件的"占卜"子屏类型是 `NCrystalSphereScreen`，兜底后屏名说这是 EVENT，
    /// 但按类型判定说不是 —— 于是插件按屏名去找 `choose_event_option`，动作清单里却没有，
    /// 自相矛盾，整局永久卡死。
    ///
    /// ★ 这就是为什么 `BuildEventPayload` 与 `CanChooseEventOption` **必须共用本函数**：
    /// 只改一处会重新制造「屏名说能处理、守卫说不能」的矛盾，而且那种矛盾比两处都不改更难查，
    /// 因为它让现象与代码看起来是一致的。
    ///
    /// 判据刻意**与 `ResolveScreen` 同源**：直接复用 `ResolveScreenFromStaticRoom()`，
    /// 而不是另起一套"屏名/类型白名单"，否则两处又会各自漂移。
    /// </summary>
    private static bool IsEventScreen(IScreenContext? currentScreen)
    {
        if (currentScreen is NEventRoom)
        {
            return true;
        }

        // 屏类型认不出时才吃静态房间兜底 —— 与 ResolveScreen 的调用时序一致：
        // 那里也是先 ResolveNonModalScreen，返回 UNKNOWN 才 fallback。
        if (currentScreen != null && ResolveNonModalScreen(currentScreen) != "UNKNOWN")
        {
            return false;
        }

        return ResolveScreenFromStaticRoom() == "EVENT";
    }

    public static NProceedButton? GetProceedButton(IScreenContext? currentScreen)
    {
        if (currentScreen is null || currentScreen is NCardRewardSelectionScreen)
        {
            return null;
        }

        if (currentScreen is NRewardsScreen rewardsScreen)
        {
            var rewardProceedButton = GetRewardProceedButton(rewardsScreen);
            return IsProceedButtonUsable(rewardProceedButton)
                ? rewardProceedButton
                : null;
        }

        if (currentScreen is IRoomWithProceedButton roomWithProceedButton)
        {
            return IsProceedButtonUsable(roomWithProceedButton.ProceedButton)
                ? roomWithProceedButton.ProceedButton
                : null;
        }

        if (currentScreen is not Node rootNode)
        {
            return null;
        }

        return FindDescendants<NProceedButton>(rootNode)
            .FirstOrDefault(IsProceedButtonUsable);
    }

    public static NButton? GetCardsViewBackButton(IScreenContext? currentScreen)
    {
        if (currentScreen is not NCardsViewScreen cardsViewScreen)
        {
            return null;
        }

        // NSimpleCardsViewScreen (e.g. Pandora's Box results) disables BackButton and
        // wires ConfirmButton to the same OnReturnButtonPressed handler. It is a
        // read-only results screen, not a card selection: close it through that button.
        // Keep this type-specific so we never confirm an unrelated selection screen.
        var buttonPath = cardsViewScreen is NSimpleCardsViewScreen ? "ConfirmButton" : "BackButton";
        var backButton = cardsViewScreen.GetNodeOrNull<NButton>(buttonPath);
        // Once per screen type/button, not once per state poll. Never click here.
        var diagnosticKey = $"{cardsViewScreen.GetType().FullName}/{buttonPath}";
        if (backButton == null && MissingCardsViewButtons.Add(diagnosticKey))
            Log.Warn($"[NekoComm] Cards view close button missing: {diagnosticKey}");
        return backButton != null &&
            GodotObject.IsInstanceValid(backButton) &&
            backButton.IsVisibleInTree() &&
            backButton.IsEnabled
            ? backButton
            : null;
    }

    private static MapNodePayload BuildMapNodePayload(
        NMapPoint node,
        int index,
        IReadOnlyDictionary<string, MapPlayerVotePayload[]>? votesByCoord = null)
    {
        var voteKey = $"{node.Point.coord.row},{node.Point.coord.col}";
        votesByCoord ??= new Dictionary<string, MapPlayerVotePayload[]>();
        votesByCoord.TryGetValue(voteKey, out var voters);
        voters ??= Array.Empty<MapPlayerVotePayload>();

        return new MapNodePayload
        {
            index = index,
            row = node.Point.coord.row,
            col = node.Point.coord.col,
            node_type = node.Point.PointType.ToString(),
            state = node.State.ToString(),
            vote_count = voters.Length,
            has_local_vote = voters.Any(voter => voter.is_local),
            voted_player_ids = voters.Select(voter => voter.player_id).ToArray()
        };
    }

    private static MapGraphNodePayload BuildMapGraphNodePayload(
        MapPoint point,
        NMapPoint? mapNode,
        HashSet<MapCoord> visitedCoords,
        HashSet<MapCoord> availableCoords,
        MapCoord? currentCoord,
        MapCoord startCoord,
        MapCoord bossCoord,
        MapCoord? secondBossCoord)
    {
        return new MapGraphNodePayload
        {
            row = point.coord.row,
            col = point.coord.col,
            node_type = point.PointType.ToString(),
            state = ResolveMapPointState(point.coord, mapNode, visitedCoords, availableCoords, currentCoord),
            visited = visitedCoords.Contains(point.coord),
            is_current = currentCoord.HasValue && currentCoord.Value == point.coord,
            is_available = availableCoords.Contains(point.coord),
            is_start = point.coord == startCoord,
            is_boss = point.coord == bossCoord,
            is_second_boss = secondBossCoord.HasValue && point.coord == secondBossCoord.Value,
            parents = point.parents
                .OrderBy(parent => parent.coord.row)
                .ThenBy(parent => parent.coord.col)
                .Select(parent => BuildMapCoordPayload(parent.coord)!)
                .ToArray(),
            children = point.Children
                .OrderBy(child => child.coord.row)
                .ThenBy(child => child.coord.col)
                .Select(child => BuildMapCoordPayload(child.coord)!)
                .ToArray()
        };
    }

    private static MapCoordPayload? BuildMapCoordPayload(MapCoord? coord)
    {
        if (!coord.HasValue)
        {
            return null;
        }

        return new MapCoordPayload
        {
            row = coord.Value.row,
            col = coord.Value.col
        };
    }

    private static MapPlayerVotePayload[] BuildMapPlayerVotePayloads(RunState runState)
    {
        var localPlayer = GetLocalPlayer(runState);
        return runState.Players
            .Select(player =>
            {
                var vote = RunManager.Instance.MapSelectionSynchronizer.GetVote(player);
                return new MapPlayerVotePayload
                {
                    player_id = NetIdToString(player.NetId),
                    slot_index = runState.GetPlayerSlotIndex(player),
                    is_local = localPlayer != null && player.NetId == localPlayer.NetId,
                    coord = BuildMapCoordPayload(vote?.coord)
                };
            })
            .OrderBy(vote => vote.slot_index)
            .ToArray();
    }

    private static IReadOnlyList<MapPoint> GetAllMapPoints(ActMap map)
    {
        var points = new Dictionary<MapCoord, MapPoint>();

        void AddPoint(MapPoint? point)
        {
            if (point == null)
            {
                return;
            }

            points[point.coord] = point;
        }

        foreach (var point in map.GetAllMapPoints())
        {
            AddPoint(point);
        }

        AddPoint(map.StartingMapPoint);
        AddPoint(map.BossMapPoint);
        AddPoint(map.SecondBossMapPoint);

        return points.Values
            .OrderBy(point => point.coord.row)
            .ThenBy(point => point.coord.col)
            .ToArray();
    }

    private static string ResolveMapPointState(
        MapCoord coord,
        NMapPoint? mapNode,
        HashSet<MapCoord> visitedCoords,
        HashSet<MapCoord> availableCoords,
        MapCoord? currentCoord)
    {
        if (mapNode != null)
        {
            return mapNode.State.ToString();
        }

        if (availableCoords.Contains(coord))
        {
            return MapPointState.Travelable.ToString();
        }

        if (visitedCoords.Contains(coord) || (currentCoord.HasValue && currentCoord.Value == coord))
        {
            return MapPointState.Traveled.ToString();
        }

        return MapPointState.Untravelable.ToString();
    }

    private static CharacterSelectPlayerPayload BuildCharacterSelectPlayerPayload(StartRunLobbyPlayer player, ulong localPlayerId, GameMode gameMode)
    {
        return new CharacterSelectPlayerPayload
        {
            player_id = NetIdToString(player.id),
            slot_index = player.slotId,
            is_local = player.id == localPlayerId,
            character_id = player.character?.Id.Entry,
            character_name = player.character?.Title.GetFormattedText(),
            is_ready = player.isReady,
            // Game 0.113 split the per-player cap by mode; the lobby's shared cap uses the same pick
            // (StartRunLobby.UpdateMaxSharedAscension).
            max_multiplayer_ascension_unlocked = gameMode == GameMode.Blitz
                ? player.maxBlitzAscension
                : player.maxStandardAscension
        };
    }

    private static RunPlayerSummaryPayload BuildRunPlayerSummaryPayload(
        RunState runState,
        Player player,
        IReadOnlyCollection<ulong> connectedPlayerIds,
        ulong localPlayerId)
    {
        return new RunPlayerSummaryPayload
        {
            player_id = NetIdToString(player.NetId),
            slot_index = runState.GetPlayerSlotIndex(player),
            is_local = player.NetId == localPlayerId,
            is_connected = connectedPlayerIds.Contains(player.NetId),
            character_id = player.Character.Id.Entry,
            character_name = player.Character.Title.GetFormattedText(),
            current_hp = player.Creature.CurrentHp,
            max_hp = player.Creature.MaxHp,
            gold = player.Gold,
            is_alive = player.Creature.IsAlive
        };
    }

    private static CombatPlayerSummaryPayload BuildCombatPlayerSummaryPayload(
        Player player,
        CombatState combatState,
        IReadOnlyCollection<ulong> connectedPlayerIds,
        ulong localPlayerId)
    {
        return new CombatPlayerSummaryPayload
        {
            player_id = NetIdToString(player.NetId),
            slot_index = combatState.RunState is RunState runState ? runState.GetPlayerSlotIndex(player) : 0,
            is_local = player.NetId == localPlayerId,
            is_connected = connectedPlayerIds.Contains(player.NetId),
            character_id = player.Character.Id.Entry,
            character_name = player.Character.Title.GetFormattedText(),
            current_hp = player.Creature.CurrentHp,
            max_hp = player.Creature.MaxHp,
            block = player.Creature.Block,
            energy = player.PlayerCombatState?.Energy ?? 0,
            stars = player.PlayerCombatState?.Stars ?? 0,
            focus = player.Creature.GetPowerAmount<FocusPower>(),
            is_alive = player.Creature.IsAlive
        };
    }

    /// <summary>
    /// Co-op read view (Phase 0): returns, for EVERY combat player, the info the catgirl
    /// agent needs to observe and drive the partner (slot 1) — including that player's
    /// action-phase flag and its own hand — plus the shared enemies. Null when not in combat.
    /// </summary>
    public static CoopStatePayload? BuildCoopStatePayload()
    {
        var combatState = CombatManager.Instance.DebugOnlyGetState();
        if (combatState == null)
        {
            return null;
        }

        var connectedPlayerIds = GetConnectedPlayerIds(combatState.RunState as RunState);
        var localPlayerId = GetLocalPlayer(combatState)?.NetId ?? 0;
        var players = GetOrderedCombatPlayers(combatState)
            .Select(player => BuildCoopPlayerPayload(player, combatState, connectedPlayerIds, localPlayerId))
            .ToArray();
        var enemies = combatState.Enemies
            .Select((enemy, index) => BuildEnemyPayload(enemy, index))
            .ToArray();

        return new CoopStatePayload
        {
            screen = ActiveScreenContext.Instance.GetCurrentScreen() is { } screen ? ResolveScreen(screen) : null,
            current_side = combatState.CurrentSide.ToString(),
            is_combat = true,
            has_valid_combat_state = players.Length > 0,
            players = players,
            enemies = enemies
        };
    }

    private static CoopPlayerPayload BuildCoopPlayerPayload(
        Player player,
        CombatState combatState,
        IReadOnlyCollection<ulong> connectedPlayerIds,
        ulong localPlayerId)
    {
        var summary = BuildCombatPlayerSummaryPayload(player, combatState, connectedPlayerIds, localPlayerId);
        var hand = player.PlayerCombatState?.Hand.Cards.ToList() ?? new List<CardModel>();

        return new CoopPlayerPayload
        {
            player_id = summary.player_id,
            slot_index = summary.slot_index,
            is_local = summary.is_local,
            is_connected = summary.is_connected,
            character_id = summary.character_id,
            character_name = summary.character_name,
            current_hp = summary.current_hp,
            max_hp = summary.max_hp,
            block = summary.block,
            energy = summary.energy,
            stars = summary.stars,
            is_alive = summary.is_alive,
            is_action_phase = IsPlayerActionPhaseFor(combatState, player),
            hand = hand.Select((card, index) => BuildHandCardPayload(combatState, card, index)).ToArray()
        };
    }

}
