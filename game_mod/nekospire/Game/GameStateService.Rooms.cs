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

// Rooms implementation; shares the service API and state across partial files.
internal static partial class GameStateService
{
    private static string? GetLobbyIdentifier(INetGameService? netService)
    {
        try
        {
            return netService?.GetRawLobbyIdentifier();
        }
        catch
        {
            return null;
        }
    }

    private static MultiplayerPayload? BuildMultiplayerPayload(IScreenContext? currentScreen, RunState? runState)
    {
        var multiplayerTestScene = GetMultiplayerTestScene();
        var multiplayerTestLobby = multiplayerTestScene != null ? GetMultiplayerTestLobby(multiplayerTestScene) : null;
        if (multiplayerTestLobby != null)
        {
            return new MultiplayerPayload
            {
                is_multiplayer = true,
                net_game_type = multiplayerTestLobby.NetService.Type.ToString(),
                local_player_id = NetIdToString(multiplayerTestLobby.LocalPlayer.id),
                player_count = multiplayerTestLobby.Players.Count,
                connected_player_ids = multiplayerTestLobby.Players
                    .OrderBy(player => player.slotId)
                    .Select(player => NetIdToString(player.id))
                    .ToArray(),
                lobby_id = GetLobbyIdentifier(multiplayerTestLobby.NetService)
            };
        }

        var characterSelectScreen = GetCharacterSelectScreen(currentScreen);
        if (characterSelectScreen != null)
        {
            var lobby = characterSelectScreen.Lobby;
            if (!lobby.NetService.Type.IsMultiplayer())
            {
                return null;
            }

            return new MultiplayerPayload
            {
                is_multiplayer = true,
                net_game_type = lobby.NetService.Type.ToString(),
                local_player_id = NetIdToString(lobby.LocalPlayer.id),
                player_count = lobby.Players.Count,
                connected_player_ids = lobby.Players
                    .OrderBy(player => player.slotId)
                    .Select(player => NetIdToString(player.id))
                    .ToArray(),
                lobby_id = GetLobbyIdentifier(lobby.NetService)
            };
        }

        if (runState == null || !RunManager.Instance.NetService.Type.IsMultiplayer())
        {
            return null;
        }

        var localPlayer = GetLocalPlayer(runState);
        return new MultiplayerPayload
        {
            is_multiplayer = true,
            net_game_type = RunManager.Instance.NetService.Type.ToString(),
            local_player_id = localPlayer != null ? NetIdToString(localPlayer.NetId) : null,
            player_count = runState.Players.Count,
            connected_player_ids = GetConnectedPlayerIds(runState)
                .OrderBy(id => runState.GetPlayerSlotIndex(id))
                .Select(NetIdToString)
                .ToArray(),
            lobby_id = GetLobbyIdentifier(RunManager.Instance.NetService)
        };
    }

    private static MultiplayerLobbyPayload? BuildMultiplayerLobbyPayload(IScreenContext? currentScreen)
    {
        var scene = GetMultiplayerTestScene();
        if (scene == null)
        {
            return null;
        }

        var lobby = GetMultiplayerTestLobby(scene);
        var selectedCharacterId = lobby?.LocalPlayer.character?.Id.Entry
            ?? GetMultiplayerTestCharacterPaginator(scene)?.Character?.Id.Entry;
        var localPlayerId = lobby != null ? NetIdToString(lobby.LocalPlayer.id) : null;

        return new MultiplayerLobbyPayload
        {
            net_game_type = lobby?.NetService.Type.ToString() ?? NetGameType.Singleplayer.ToString(),
            lobby_id = GetLobbyIdentifier(lobby?.NetService),
            join_host = GetMultiplayerLobbyJoinHost(),
            join_port = GetMultiplayerLobbyJoinPort(),
            local_net_id_hint = NetIdToString(GetMultiplayerLobbyJoinNetIdHint()),
            has_lobby = lobby != null,
            is_host = lobby?.NetService.Type == NetGameType.Host,
            is_client = lobby?.NetService.Type == NetGameType.Client,
            local_ready = lobby?.LocalPlayer.isReady ?? false,
            can_host = CanHostMultiplayerLobby(currentScreen),
            can_join = CanJoinMultiplayerLobby(currentScreen),
            can_ready = CanReadyMultiplayerLobby(currentScreen),
            can_disconnect = CanDisconnectMultiplayerLobby(currentScreen),
            can_unready = CanUnready(currentScreen),
            selected_character_id = selectedCharacterId,
            player_count = lobby?.Players.Count ?? 0,
            max_players = lobby != null
                ? GetStartRunLobbyMaxPlayers(lobby)
                : 4,
            players = lobby?.Players
                .OrderBy(player => player.slotId)
                .Select(player => BuildCharacterSelectPlayerPayload(player, lobby.LocalPlayer.id, lobby.GameMode))
                .ToArray() ?? Array.Empty<CharacterSelectPlayerPayload>(),
            characters = GetMultiplayerLobbyCharacters()
                .Select((character, index) => new CharacterSelectOptionPayload
                {
                    index = index,
                    character_id = character.Id.Entry,
                    name = character.Title.GetFormattedText(),
                    is_locked = false,
                    is_selected = selectedCharacterId == character.Id.Entry,
                    is_random = false
                })
                .ToArray()
        };
    }

    private static MapPayload? BuildMapPayload(IScreenContext? currentScreen, RunState? runState)
    {
        if (!TryGetMapScreen(currentScreen, runState, out var mapScreen))
        {
            return null;
        }

        var visibleNodes = FindDescendants<NMapPoint>(mapScreen!)
            .Where(node => GodotObject.IsInstanceValid(node))
            .GroupBy(node => node.Point.coord)
            .ToDictionary(
                group => group.Key,
                group => group
                    .OrderBy(node => node.GlobalPosition.Y)
                    .ThenBy(node => node.GlobalPosition.X)
                    .First());

        var availableNodes = visibleNodes.Values
            .Where(node => node.IsEnabled)
            .OrderBy(node => node.Point.coord.row)
            .ThenBy(node => node.Point.coord.col)
            .ToArray();
        var availableCoords = new HashSet<MapCoord>(availableNodes.Select(node => node.Point.coord));
        var visitedCoords = new HashSet<MapCoord>(runState!.VisitedMapCoords);
        var allMapPoints = GetAllMapPoints(runState.Map);
        var playerVotes = BuildMapPlayerVotePayloads(runState);
        var localVote = playerVotes.FirstOrDefault(vote => vote.is_local)?.coord;
        var votesByCoord = playerVotes
            .Where(vote => vote.coord != null)
            .GroupBy(vote => $"{vote.coord!.row},{vote.coord.col}")
            .ToDictionary(group => group.Key, group => group.ToArray());

        return new MapPayload
        {
            current_node = BuildMapCoordPayload(runState!.CurrentMapCoord),
            is_travel_enabled = mapScreen!.IsTravelEnabled,
            is_traveling = mapScreen.IsTraveling,
            map_generation_count = RunManager.Instance.MapSelectionSynchronizer.MapGenerationCount,
            rows = runState.Map.GetRowCount(),
            cols = runState.Map.GetColumnCount(),
            starting_node = BuildMapCoordPayload(runState.Map.StartingMapPoint.coord),
            boss_node = BuildMapCoordPayload(runState.Map.BossMapPoint.coord),
            second_boss_node = BuildMapCoordPayload(runState.Map.SecondBossMapPoint?.coord),
            nodes = allMapPoints
                .Select(point => BuildMapGraphNodePayload(
                    point,
                    visibleNodes.TryGetValue(point.coord, out var mapNode) ? mapNode : null,
                    visitedCoords,
                    availableCoords,
                    runState.CurrentMapCoord,
                    runState.Map.StartingMapPoint.coord,
                    runState.Map.BossMapPoint.coord,
                    runState.Map.SecondBossMapPoint?.coord))
                .ToArray(),
            available_nodes = availableNodes.Select((node, index) => BuildMapNodePayload(node, index, votesByCoord)).ToArray(),
            local_vote = localVote,
            player_votes = playerVotes
        };
    }

    private static SelectionPayload? BuildSelectionPayload(IScreenContext? currentScreen)
    {
        var cards = GetDeckSelectionOptions(currentScreen);
        if (cards.Count == 0)
        {
            return null;
        }

        var combatHandSelection = TryGetCombatHandSelectionMetadata(currentScreen, out _, out var metadata)
            ? metadata
            : default;

        return new SelectionPayload
        {
            kind = currentScreen switch
            {
                NDeckUpgradeSelectScreen => "deck_upgrade_select",
                NDeckTransformSelectScreen => "deck_transform_select",
                NDeckEnchantSelectScreen => "deck_enchant_select",
                NChooseACardSelectionScreen => "choose_card_select",
                _ when TryGetCombatHandSelection(currentScreen, out var hand) => hand!.CurrentMode == NPlayerHand.Mode.UpgradeSelect
                    ? "combat_hand_upgrade_select"
                    : "combat_hand_select",
                _ => "deck_card_select"
            },
            prompt = GetDeckSelectionPrompt(currentScreen) ?? string.Empty,
            min_select = combatHandSelection.MinSelect,
            max_select = combatHandSelection.MaxSelect,
            selected_count = combatHandSelection.SelectedCount,
            requires_confirmation = combatHandSelection.RequiresConfirmation,
            can_confirm = combatHandSelection.CanConfirm,
            cards = cards.Select((holder, index) => BuildSelectionCardPayload(holder.CardModel!, index)).ToArray()
        };
    }

    private static CharacterSelectPayload? BuildCharacterSelectPayload(IScreenContext? currentScreen)
    {
        if (currentScreen is NMultiplayerLoadGameScreen load)
        {
            var lobby = CombatSolver.Engine.Common.GameRef.Get<LoadRunLobby>(load, "_runLobby");
            var local = lobby.Players.FirstOrDefault(p => p.id == lobby.NetService.NetId);
            return new CharacterSelectPayload
            {
                is_multiplayer = true, net_game_type = lobby.NetService.Type.ToString(),
                selected_character_id = lobby.Run.Players.FirstOrDefault(p => p.NetId == lobby.NetService.NetId)?.CharacterId?.Entry,
                can_embark = CanEmbark(currentScreen), local_ready = local.isReady,
                is_waiting_for_players = true, player_count = lobby.Players.Count,
                max_players = lobby.Run.Players.Count, characters = Array.Empty<CharacterSelectOptionPayload>(),
                players = Array.Empty<CharacterSelectPlayerPayload>()
            };
        }
        var screen = GetCharacterSelectScreen(currentScreen);
        if (screen == null)
        {
            return null;
        }

        var buttons = GetCharacterSelectButtons(currentScreen);
        try
        {
            var lobby = screen.Lobby;
            var localPlayer = lobby.LocalPlayer;
            var waitingPanel = screen.GetNodeOrNull<Control>("ReadyAndWaitingPanel");
            var selectedCharacterId = localPlayer.character?.Id.Entry;

            return new CharacterSelectPayload
            {
                selected_character_id = selectedCharacterId,
                is_multiplayer = lobby.NetService.Type.IsMultiplayer(),
                net_game_type = lobby.NetService.Type.ToString(),
                can_embark = CanEmbark(currentScreen),
                can_unready = CanUnready(currentScreen),
                can_increase_ascension = CanIncreaseAscension(currentScreen),
                can_decrease_ascension = CanDecreaseAscension(currentScreen),
                local_ready = localPlayer.isReady,
                is_waiting_for_players = waitingPanel?.Visible ?? false,
                player_count = lobby.Players.Count,
                max_players = GetStartRunLobbyMaxPlayers(lobby),
                ascension = lobby.Ascension,
                max_ascension = lobby.MaxAscension,
                seed = lobby.Seed,
                modifier_ids = lobby.Modifiers.Select(modifier => modifier.Id.Entry).ToArray(),
                players = lobby.Players
                    .OrderBy(player => player.slotId)
                    .Select(player => BuildCharacterSelectPlayerPayload(player, localPlayer.id, lobby.GameMode))
                    .ToArray(),
                characters = buttons.Select((button, index) => new CharacterSelectOptionPayload
                {
                    index = index,
                    character_id = button.Character.Id.Entry,
                    name = button.Character.Title.GetFormattedText(),
                    is_locked = button.IsLocked,
                    is_selected = button.IsRandom
                        ? selectedCharacterId == button.Character.Id.Entry
                        : selectedCharacterId == button.Character.Id.Entry,
                    is_random = button.IsRandom
                }).ToArray()
            };
        }
        catch
        {
            return new CharacterSelectPayload
            {
                players = Array.Empty<CharacterSelectPlayerPayload>(),
                characters = buttons.Select((button, index) => new CharacterSelectOptionPayload
                {
                    index = index,
                    character_id = button.Character.Id.Entry,
                    name = button.Character.Title.GetFormattedText(),
                    is_locked = button.IsLocked,
                    is_selected = false,
                    is_random = button.IsRandom
                }).ToArray()
            };
        }
    }

    private static EventPayload? BuildEventPayload(IScreenContext? currentScreen)
    {
        // ★ 与 CanChooseEventOption 共用 IsEventScreen —— 两处必须同源（见该函数注释）。
        if (!IsEventScreen(currentScreen))
        {
            return null;
        }

        try
        {
            var eventModel = RunManager.Instance.EventSynchronizer.GetLocalEvent();
            if (eventModel == null)
            {
                return null;
            }

            var options = new List<EventOptionPayload>();

            if (eventModel.IsFinished)
            {
                // Mirror NEventRoom.SetOptions(): synthesize a Proceed option
                options.Add(new EventOptionPayload
                {
                    index = 0,
                    text_key = "PROCEED",
                    title = "Proceed",
                    description = "",
                    is_locked = false,
                    is_proceed = true
                });
            }
            else
            {
                var currentOptions = eventModel.CurrentOptions;
                for (int i = 0; i < currentOptions.Count; i++)
                {
                    var opt = currentOptions[i];
                    options.Add(new EventOptionPayload
                    {
                        index = i,
                        text_key = SafeReadString(() => opt.TextKey),
                        title = SafeReadString(() => opt.Title?.GetFormattedText()),
                        description = SafeReadString(() => opt.Description?.GetFormattedText()),
                        is_locked = SafeReadBool(() => opt.IsLocked),
                        is_proceed = SafeReadBool(() => opt.IsProceed),
                        will_kill_player = GetEventOptionWillKillPlayer(eventModel, opt),
                        has_relic_preview = GetReflectedProperty(opt, "Relic") != null
                    });
                }
            }

            return new EventPayload
            {
                event_id = SafeReadString(() => eventModel.Id?.Entry, "unknown"),
                title = SafeReadString(() => eventModel.Title?.GetFormattedText()),
                description = SafeReadString(() => eventModel.Description?.GetFormattedText()),
                is_finished = SafeReadBool(() => eventModel.IsFinished),
                options = options.ToArray()
            };
        }
        catch (Exception ex)
        {
            // ★★★ 0.1.7 修复：这里**必须**空安全。
            //
            // 实机（2026-10-25 `godot.log`，`C:\STEAM` 那套安装，4 次）：
            //     Failed: System.NullReferenceException
            //        at System.Object.GetType()
            //        at NekoComm.Game.GameStateService.BuildEventPayload(IScreenContext currentScreen)
            //        at NekoComm.Game.GameStateService.BuildStatePayloadCore()
            //        at NekoComm.Game.GameActionService.ExecuteEmbarkAsync()
            // 前一行日志是 `[WARN] [NekoComm] Unhandled screen type , falling back to EVENT`
            // （类型名是空串 ⇒ `currentScreen == null`）。
            //
            // 病理：`IsEventScreen(null)` 会走"静态房间兜底"返回 true，于是进入 try；开局 `embark`
            // 那一瞬读不到事件 ⇒ 抛异常 ⇒ 掉进本 catch ⇒ **catch 自己又解引用 null** ⇒ 抛出
            // NRE ⇒ Router 回 HTTP 500，整次 `/action` 的响应被毁掉（本该只是"这一帧没有事件层"）。
            // 也就是说：一个纯诊断用的日志字符串，把一次可降级的读取变成了硬失败。
            var screenType = currentScreen?.GetType().FullName ?? "<null>";
            Log.Warn($"[NekoComm] Failed to build event payload on screen {screenType}: {ex}");
            return null;
        }
    }

    /// <summary>
    /// 水晶球"占卜"子屏（`NCrystalSphereScreen`）的**专用状态层**。
    ///
    /// 为什么必须单开一层：这一屏的屏名会被 `ResolveScreenFromStaticRoom()` 兜底成 "EVENT"，
    /// 但它**没有** `NEventRoom`，进小游戏后 `EventSynchronizer.GetLocalEvent()` 也不再有
    /// 可选项 —— 于是 `event` 层是 null（或只剩事件标题），插件无从下手。
    /// 而这一屏真正的可操作对象是 `CrystalSphereMinigame.cells[,]`（11x11）。
    ///
    /// 数据来源（全部 public，已反射核实，见 .dsh-artifacts/_t33_PROBE-SUMMARY.md）：
    ///   NCrystalSphereScreen._entity         : CrystalSphereMinigame
    ///   CrystalSphereMinigame.cells          : CrystalSphereCell[,]
    ///   CrystalSphereMinigame.DivinationCount / IsFinished / GridSize / CrystalSphereTool
    ///   CrystalSphereCell.IsHidden / X / Y / Item
    ///
    /// ★ 恢复力：`_entity` 是私有字段，走反射读取；任何一步失败都返回 null 而不是抛出，
    ///   让上层退回"至少不卡死"的行为，绝不让读状态把整局弄崩。
    /// </summary>
    private static CrystalSpherePayload? BuildCrystalSpherePayload(IScreenContext? currentScreen)
    {
        try
        {
            if (currentScreen is not Node root)
            {
                return null;
            }

            // 只有真的是水晶球屏才建这一层 —— 不做"猜"。
            if (!currentScreen.GetType().Name.Contains("CrystalSphere"))
            {
                return null;
            }

            var minigame = GetReflectedField(root, "_entity");
            if (minigame == null)
            {
                return null;
            }

            var cellsObj = GetReflectedProperty(minigame, "cells") ?? GetReflectedField(minigame, "cells");
            if (cellsObj is not Array cells)
            {
                return null;
            }

            var revealed = new List<CrystalSphereCellPayload>();
            var candidates = new List<CrystalSphereCellPayload>();
            var hiddenCount = 0;

            foreach (var c in cells)
            {
                if (c == null)
                {
                    continue;
                }

                var isHidden = SafeReadBool(() => GetReflectedProperty(c, "IsHidden") is true);
                var x = GetReflectedInt(c, "X");
                var y = GetReflectedInt(c, "Y");
                var item = GetReflectedProperty(c, "Item");

                if (isHidden)
                {
                    hiddenCount++;
                    // ★★★ 队长 ②「能力缺口」：隐藏格**必须给坐标**，不能只给一个数字。
                    //   理由与 CANON §23 同源：**枚举要给出"有哪些可能"，而不是"有多少"**。
                    //   只有 `cells_hidden = 37` 时，插件无从判断"该揭哪块"，只能瞎点 ——
                    //   而"瞎点"正是 `_advance_custom_event_cursor` 当初的问题。
                    //   ⇒ 这里输出**可点候选的坐标清单**，插件才真的能做决策。
                    candidates.Add(new CrystalSphereCellPayload
                    {
                        x = x,
                        y = y,
                        item_type = "",
                        item_name = ""
                    });
                    continue;
                }

                // 已揭开的格子：把 Item 类型说出来（CardReward/Curse/Gold/Potion/Relic）
                if (item == null)
                {
                    continue;
                }

                var itemType = item.GetType().Name;
                revealed.Add(new CrystalSphereCellPayload
                {
                    x = x,
                    y = y,
                    item_type = itemType,
                    item_name = SafeReadString(() => GetReflectedProperty(item, "Name")?.ToString(), itemType)
                });
                // ★ 队长 ③：原来这里有个 `occupiedHiddenCount++`，**从未被 return 使用**（死变量），
                //   而且名字与语义相反（它加在"未隐藏"分支）。已删除 ——
                //   不留"看起来实现了、实则没接到"的变量（CANON §22.4 判据 E1，
                //   与 t25 那个 `require_no_new_key` 死形参同形态）。
            }

            return new CrystalSpherePayload
            {
                active = true,
                grid_width = GetReflectedInt(minigame, "GridSize", gridAxis: 0),
                grid_height = GetReflectedInt(minigame, "GridSize", gridAxis: 1),
                divinations_left = GetReflectedInt(minigame, "DivinationCount"),
                is_finished = SafeReadBool(() => GetReflectedProperty(minigame, "IsFinished") is true),
                tool = (GetReflectedProperty(minigame, "CrystalSphereTool")?.ToString() ?? "None"),
                cells_total = cells.Length,
                cells_hidden = hiddenCount,
                cells_revealed = revealed.Count,
                revealed_cells = revealed.ToArray(),
                candidates = candidates.ToArray()
            };
        }
        catch (Exception ex)
        {
            Log.Warn($"[NekoComm] Failed to build crystal sphere payload: {ex}");
            return null;
        }
    }

    private static RestPayload? BuildRestPayload(IScreenContext? currentScreen, RunState? runState)
    {
        if (currentScreen is not NRestSiteRoom)
        {
            return null;
        }

        try
        {
            var options = RunManager.Instance.RestSiteSynchronizer.GetLocalOptions();
            var localPlayer = GetLocalPlayer(runState);
            if (options == null)
            {
                return new RestPayload
                {
                    options = Array.Empty<RestOptionPayload>()
                };
            }

            return new RestPayload
            {
                options = options.Select((opt, i) =>
                {
                    var requiresTarget = RestOptionRequiresTarget(opt, runState, localPlayer);
                    var validTargetIndices = requiresTarget
                        ? GetRestOptionTargetIndices(runState, localPlayer, allowSelf: false)
                        : Array.Empty<int>();

                    return new RestOptionPayload
                    {
                        index = i,
                        option_id = opt.OptionId ?? "unknown",
                        title = opt.Title?.GetFormattedText() ?? "",
                        description = opt.Description?.GetFormattedText() ?? "",
                        is_enabled = opt.IsEnabled,
                        requires_target = requiresTarget,
                        target_index_space = requiresTarget
                            ? GetRestOptionTargetIndexSpace(opt, runState, localPlayer)
                            : null,
                        valid_target_indices = validTargetIndices,
                        valid_target_player_ids = requiresTarget
                            ? validTargetIndices
                                .Select(index => ResolveRunPlayerTarget(runState, index))
                                .Where(player => player != null)
                                .Select(player => NetIdToString(player!.NetId))
                                .ToArray()
                            : Array.Empty<string>()
                    };
                }).ToArray()
            };
        }
        catch
        {
            return null;
        }
    }

    private static ShopPayload? BuildShopPayload(IScreenContext? currentScreen)
    {
        var merchantRoom = GetMerchantRoom(currentScreen);
        var inventoryScreen = GetMerchantInventoryScreen(currentScreen);
        var inventory = inventoryScreen?.Inventory ?? merchantRoom?.Inventory?.Inventory;

        if (merchantRoom == null && inventoryScreen == null)
        {
            return null;
        }

        if (inventory == null)
        {
            return new ShopPayload
            {
                is_open = inventoryScreen?.IsOpen ?? false,
                can_open = CanOpenShopInventory(currentScreen),
                can_close = CanCloseShopInventory(currentScreen),
                cards = Array.Empty<ShopCardPayload>(),
                relics = Array.Empty<ShopRelicPayload>(),
                potions = Array.Empty<ShopPotionPayload>(),
                card_removal = null
            };
        }

        var cards = inventory.CharacterCardEntries
            .Select((entry, index) => BuildShopCardPayload(entry, index, "character"))
            .Concat(inventory.ColorlessCardEntries.Select((entry, index) =>
                BuildShopCardPayload(entry, inventory.CharacterCardEntries.Count + index, "colorless")))
            .ToArray();

        return new ShopPayload
        {
            is_open = inventoryScreen?.IsOpen ?? false,
            can_open = CanOpenShopInventory(currentScreen),
            can_close = CanCloseShopInventory(currentScreen),
            cards = cards,
            relics = inventory.RelicEntries.Select((entry, index) => BuildShopRelicPayload(entry, index)).ToArray(),
            potions = inventory.PotionEntries.Select((entry, index) => BuildShopPotionPayload(entry, index, inventory.Player)).ToArray(),
            card_removal = BuildShopCardRemovalPayload(inventory.CardRemovalEntry)
        };
    }

    private static TimelinePayload? BuildTimelinePayload(IScreenContext? currentScreen)
    {
        var timelineScreen = GetTimelineScreen(currentScreen);
        if (timelineScreen == null)
        {
            return null;
        }

        var slots = GetTimelineSlots(currentScreen)
            .Select((slot, index) => new TimelineSlotPayload
            {
                index = index,
                epoch_id = slot.model.Id,
                title = slot.model.Title.GetFormattedText() ?? slot.model.Id,
                state = slot.State.ToString().ToLowerInvariant(),
                is_actionable = slot.State is EpochSlotState.Obtained or EpochSlotState.Complete
            })
            .ToArray();

        return new TimelinePayload
        {
            back_enabled = GetTimelineBackButton(currentScreen)?.IsEnabled == true,
            inspect_open = GetTimelineInspectScreen(currentScreen)?.Visible == true,
            unlock_screen_open = GetTimelineUnlockScreen(currentScreen) != null,
            can_choose_epoch = CanChooseTimelineEpoch(currentScreen),
            can_confirm_overlay = CanConfirmTimelineOverlay(currentScreen),
            slots = slots
        };
    }

    private static ChestPayload? BuildChestPayload(IScreenContext? currentScreen)
    {
        var relicCollection = GetTreasureRelicCollection(currentScreen);
        if (relicCollection != null)
        {
            var relics = RunManager.Instance.TreasureRoomRelicSynchronizer.CurrentRelics;
            var hasRelicBeenClaimed = GetProceedButton(currentScreen) != null;
            return new ChestPayload
            {
                is_opened = true,
                has_relic_been_claimed = hasRelicBeenClaimed,
                relic_options = BuildTreasureRelicOptions(relics)
            };
        }

        if (currentScreen is NTreasureRoom treasureRoom)
        {
            var chestButton = treasureRoom.GetNodeOrNull<NButton>("%Chest");
            var isOpened = chestButton == null || !GodotObject.IsInstanceValid(chestButton) || !chestButton.IsEnabled;
            var hasRelicBeenClaimed = GetProceedButton(currentScreen) != null;

            return new ChestPayload
            {
                is_opened = isOpened,
                has_relic_been_claimed = hasRelicBeenClaimed,
                relic_options = Array.Empty<ChestRelicOptionPayload>()
            };
        }

        return null;
    }

    private static ChestRelicOptionPayload[] BuildTreasureRelicOptions(IReadOnlyList<RelicModel>? relics)
    {
        if (relics == null || relics.Count == 0)
        {
            return Array.Empty<ChestRelicOptionPayload>();
        }

        return relics.Select((relic, index) => new ChestRelicOptionPayload
        {
            index = index,
            relic_id = relic.Id.Entry,
            name = relic.Title.GetFormattedText(),
            rarity = relic.Rarity.ToString()
        }).ToArray();
    }

    private static RewardPayload? BuildRewardPayload(IScreenContext? currentScreen)
    {
        if (currentScreen is NRewardsScreen)
        {
            var rewardButtons = GetRewardButtons(currentScreen);
            var proceedButton = GetRewardProceedButton(currentScreen);

            return new RewardPayload
            {
                pending_card_choice = false,
                can_proceed = proceedButton?.IsEnabled ?? false,
                rewards = rewardButtons.Select((button, index) => BuildRewardOptionPayload(button, index)).ToArray(),
                card_options = Array.Empty<RewardCardOptionPayload>()
            };
        }

        if (currentScreen is NCardRewardSelectionScreen)
        {
            var cardOptions = GetCardRewardOptions(currentScreen);
            var alternatives = GetCardRewardAlternativeButtons(currentScreen);

            return new RewardPayload
            {
                pending_card_choice = true,
                can_proceed = false,
                rewards = Array.Empty<RewardOptionPayload>(),
                card_options = cardOptions.Select((holder, index) => BuildRewardCardOptionPayload(holder, index)).ToArray(),
                alternatives = alternatives.Select((button, index) => BuildRewardAlternativePayload(button, index)).ToArray()
            };
        }

        return null;
    }

    private static BundlePayload[]? BuildBundlePayload(IScreenContext? currentScreen)
    {
        if (currentScreen is not NChooseABundleSelectionScreen bundleScreen)
        {
            return null;
        }

        var bundleNodes = GetBundleOptions(currentScreen);
        if (bundleNodes.Count == 0)
        {
            return null;
        }

        return bundleNodes.Select((bundleNode, bundleIndex) =>
        {
            // NCardBundle contains NCard children (not NCardHolder).
            // NCard exposes CardModel via the .Model property.
            var cards = FindDescendants<Node>((Node)bundleNode)
                .Where(n => GodotObject.IsInstanceValid(n) && n.GetType().Name == "NCard")
                .Select(n => n.GetType().GetProperty("Model")?.GetValue(n) as CardModel)
                .Where(cm => cm != null)
                .Select((card, cardIndex) => BuildBundleCardPayload(card!, cardIndex))
                .ToArray();

            return new BundlePayload
            {
                index = bundleIndex,
                cards = cards
            };
        }).ToArray();
    }

    /// <summary>
    /// Main-menu diagnostics: is a run resumable, is a submenu hiding it, is a new co-op room even allowed.
    /// Only populated on MAIN_MENU; null everywhere else. See MainMenuPayload for why this is exposed.
    /// </summary>
    private static MainMenuPayload? BuildMainMenuPayload(IScreenContext? currentScreen, RunState? runState)
    {
        if (currentScreen is not NMainMenu mainMenu || !mainMenu.IsVisibleInTree())
            return null;

        var submenuOpen = mainMenu.SubmenuStack?.SubmenusOpen == true;

        // ★★ t33 顺手修掉 D10 的 **mod 侧根因**（既有缺陷，非本轮引入）。
        //
        // 【现象】MAIN_MENU 出现约 9 秒后，`/state` **永久 500**；`/health` 与
        //   `/actions/available` 仍 200（它们不经过本方法）⇒ 自动游玩彻底失明。
        // 【栈帧】（godot.log，2026-10-20 07:27:30 那次启动）
        //   System.NullReferenceException
        //     at NekoComm.Game.GameStateService.BuildMainMenuPayload(IScreenContext, RunState)
        //     at NekoComm.Game.GameStateService.BuildStatePayloadCore()
        //     at NekoComm.Game.GameStateService.BuildStatePayload()
        //   ⇒ 交付 DLL 不带 PDB，栈帧**没有行号**，而这一行是
        //     `RunManager.Instance.NetService.Type.IsMultiplayer()` 三个裸解引用串在一起
        //     ⇒ 无法从栈帧判断断在哪一环。
        // 【定位】t33 加了一次性诊断直接打每一环的值（`_t33` 系列日志）：
        //     `RunManager.Instance null=False  NetService null=True  SubmenuStack null=False`
        //   ⇒ ★ **就是 `NetService`**。`RunManager.Instance` 与 `mainMenu.SubmenuStack` 都正常。
        //   ★ 注意 sts2.dll 的可空性标注把 `RunManager.NetService` 标成 `NotNull` —— **那是错的**：
        //     该字段只在 `InitializeShared` 里赋值，主菜单早期尚未跑 ⇒ 实际为 null。
        //     （教训：别信跨程序集的 `NotNull` 标注，要实测。）
        // 【修法】改用 `IsMultiplayerRunSafe()` 逐级判空（与同文件 :3690 的
        //   `netService?.GetRawLobbyIdentifier()` 同款自觉）。
        // 【验证】修复后 MAIN_MENU 下 `/state` 连续 6/6 返回 200、27 个顶层键（修前 0/20）。
        // 【旧 DLL 对照】回滚到昨晚原版 917C76F6C3F9E66A 同样复现（见 `_t33_state500.log`）
        //   ⇒ 确认是既有缺陷，t26 只做了插件侧降级绕过，mod 侧根因一直没修。
        return new MainMenuPayload
        {
            has_resumable_run = CanContinueRun(currentScreen),
            can_abandon = CanAbandonRun(currentScreen),
            submenu_open = submenuOpen,
            can_open_multiplayer_menu = CanOpenMultiplayerMenu(currentScreen),
            save_and_quit_available = CanSaveAndQuit(currentScreen, runState),
            is_multiplayer_run = IsMultiplayerRunSafe(),
        };
    }

    /// <summary>
    /// 安全判定"是否多人局"：`RunManager.Instance?.NetService?.Type` 逐级判空。
    /// 任一级缺失都当作单人（与 `NetService.Type.IsMultiplayer()` 的默认语义一致 ——
    /// 单人局的 `Type` 是单人枚举，不是 null）。
    /// </summary>
    private static bool IsMultiplayerRunSafe()
    {
        try
        {
            var netService = RunManager.Instance?.NetService;
            if (netService == null)
            {
                return false;
            }

            return netService.Type.IsMultiplayer();
        }
        catch
        {
            return false;
        }
    }

    private static ModalPayload? BuildModalPayload(IScreenContext? currentScreen)
    {
        var modal = GetOpenModal();
        if (modal is not Node modalNode)
        {
            return null;
        }

        var confirmButton = GetModalConfirmButton(currentScreen);
        var cancelButton = GetModalCancelButton(currentScreen);

        return new ModalPayload
        {
            type_name = modal.GetType().Name,
            underlying_screen = currentScreen is Node node && ReferenceEquals(node, modalNode)
                ? ResolveUnderlyingScreen(modalNode)
                : null,
            can_confirm = confirmButton != null,
            can_dismiss = cancelButton != null,
            confirm_label = GetButtonLabel(confirmButton),
            dismiss_label = GetButtonLabel(cancelButton)
        };
    }

    private static GameOverPayload? BuildGameOverPayload(IScreenContext? currentScreen, RunState? runState)
    {
        if (currentScreen is not NGameOverScreen screen)
        {
            return null;
        }

        var player = GetLocalPlayer(runState);
        var continueButton = screen.GetNodeOrNull<NButton>("%ContinueButton");
        var mainMenuButton = screen.GetNodeOrNull<NButton>("%MainMenuButton");
        var history = RunManager.Instance.History;

        return new GameOverPayload
        {
            is_victory = history?.Win ?? (runState?.CurrentRoom?.IsVictoryRoom ?? false),
            floor = runState?.TotalFloor,
            character_id = player?.Character.Id.Entry,
            can_continue = continueButton?.IsEnabled ?? false,
            can_return_to_main_menu = true,
            showing_summary = mainMenuButton?.Visible == true || mainMenuButton?.IsEnabled == true
        };
    }

}
