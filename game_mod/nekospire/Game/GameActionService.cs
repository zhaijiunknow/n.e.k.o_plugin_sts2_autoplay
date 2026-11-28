using Godot;
using System.Reflection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using NekoComm.Game.Sim;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Multiplayer;
using MegaCrit.Sts2.Core.Multiplayer;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.DevConsole;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Debug;
using MegaCrit.Sts2.Core.Nodes.Debug.Multiplayer;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.CharacterSelect;
using MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.PauseMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Nodes.Screens.Timeline;
using MegaCrit.Sts2.Core.Nodes.Screens.Timeline.UnlockScreens;
using MegaCrit.Sts2.Core.Nodes.Screens.TreasureRoomRelic;
using MegaCrit.Sts2.Core.Nodes.Events.Custom.CrystalSphere;
using MegaCrit.Sts2.Core.Nodes.TopBar;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Multiplayer.Connection;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Multiplayer.Game.Lobby;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Timeline;
using NekoComm.Server;
namespace NekoComm.Game;

internal static partial class GameActionService
{
    /// <summary>
    /// Tracks whether the agent explicitly skipped the card reward via skip_reward_cards.
    /// When set, DrainRewardFlowAsync will not auto-claim card rewards.
    /// Reset when leaving the reward screen.
    /// </summary>
    private static bool _cardRewardSkipped;

    /// <summary>
    /// Mid-turn card play counters. Maintained by the mod since the game's
    /// internal counters are not accessible via reflection. Synchronized to
    /// the current combat round when state is read and incremented by play_card.
    /// </summary>
    internal static int CardsPlayedThisTurn { get; private set; }
    internal static int AttacksPlayedThisTurn { get; private set; }
    internal static int SkillsPlayedThisTurn { get; private set; }
    internal static int LastTurnNumber { get; private set; }

    /// <summary>
    /// When set by resolve_rewards, TryResolveCardRewardAsync picks this
    /// card index instead of the first option. -2 means skip.
    /// -1 means no pending choice (use default behavior).
    /// </summary>
    private static int _pendingCardRewardChoice = -1;

    internal static void SyncCardPlayCounters(int currentTurn)
    {
        if (currentTurn == LastTurnNumber)
        {
            return;
        }

        CardsPlayedThisTurn = 0;
        AttacksPlayedThisTurn = 0;
        SkillsPlayedThisTurn = 0;
        LastTurnNumber = currentTurn;
    }

    public static Task<ActionResponsePayload> ExecuteAsync(ActionRequest request)
    {
        var actionName = request.action?.Trim().ToLowerInvariant();

        return actionName switch
        {
            Policy.PolicyService.ActionName => Policy.PolicyService.ExecuteAsync(request),
            "resolve_rewards" => ExecuteResolveRewardsAsync(request),
            "end_turn" => ExecuteEndTurnAsync(request),
            "coop_end_turn" => ExecuteEndTurnAsync(request),
            "play_card" => ExecutePlayCardAsync(request),
            "coop_play_card" => ExecutePlayCardAsync(request),
            "continue_run" => ExecuteContinueRunAsync(),
            "abandon_run" => ExecuteAbandonRunAsync(),
            "save_and_quit" => ExecuteSaveAndQuitAsync(),
            "open_character_select" => ExecuteOpenCharacterSelectAsync(),
            "open_timeline" => ExecuteOpenTimelineAsync(),
            "close_main_menu_submenu" => ExecuteCloseMainMenuSubmenuAsync(),
            "choose_timeline_epoch" => ExecuteChooseTimelineEpochAsync(request),
            "confirm_timeline_overlay" => ExecuteConfirmTimelineOverlayAsync(),
            "choose_map_node" => ExecuteChooseMapNodeAsync(request),
            "collect_rewards_and_proceed" => ExecuteCollectRewardsAndProceedAsync(),
            "claim_reward" => ExecuteClaimRewardAsync(request),
            "choose_reward_card" => ExecuteChooseRewardCardAsync(request),
            "skip_reward_cards" => ExecuteSkipRewardCardsAsync(),
            "select_deck_card" => ExecuteSelectDeckCardAsync(request),
            "close_cards_view" => ExecuteCloseCardsViewAsync(),
            "confirm_selection" => ExecuteConfirmSelectionAsync(),
            "proceed" => ExecuteProceedAsync(),
            "open_chest" => ExecuteOpenChestAsync(),
            "choose_treasure_relic" => ExecuteChooseTreasureRelicAsync(request),
            "choose_event_option" => ExecuteChooseEventOptionAsync(request),
            "divine_crystal_sphere" => ExecuteDivineCrystalSphereAsync(request),
            "choose_capstone_option" => ExecuteChooseCapstoneOptionAsync(request),
            "choose_bundle" => ExecuteChooseBundleAsync(request),
            "confirm_bundle" => ExecuteConfirmBundleAsync(),
            "choose_rest_option" => ExecuteChooseRestOptionAsync(request),
            "open_shop_inventory" => ExecuteOpenShopInventoryAsync(),
            "close_shop_inventory" => ExecuteCloseShopInventoryAsync(),
            "buy_card" => ExecuteBuyCardAsync(request),
            "buy_relic" => ExecuteBuyRelicAsync(request),
            "buy_potion" => ExecuteBuyPotionAsync(request),
            "remove_card_at_shop" => ExecuteRemoveCardAtShopAsync(),
            "select_character" => ExecuteSelectCharacterAsync(request),
            "embark" => ExecuteEmbarkAsync(),
            "unready" => ExecuteUnreadyAsync(),
            "host_multiplayer_lobby" => ExecuteHostMultiplayerLobbyAsync(),
            "open_multiplayer_menu" => ExecuteOpenMultiplayerMenuAsync(),
            "start_multiplayer_host" => ExecuteStartMultiplayerHostAsync(),
            "start_coop_session" => ExecuteStartCoopSessionAsync(),
            "continue_coop_session" => ExecuteContinueCoopSessionAsync(),
            "prepare_coop_rejoin" => RunCoopTransitionAsync(ExecutePrepareCoopRejoinAsync),
            "rejoin_coop_session" => RunCoopTransitionAsync(() => ExecuteRejoinCoopSessionAsync(request)),
            "join_multiplayer_direct" => ExecuteJoinMultiplayerDirectAsync(),
            "join_multiplayer_lobby" => ExecuteJoinMultiplayerLobbyAsync(),
            "ready_multiplayer_lobby" => ExecuteReadyMultiplayerLobbyAsync(),
            "disconnect_multiplayer_lobby" => ExecuteDisconnectMultiplayerLobbyAsync(),
            "increase_ascension" => ExecuteAdjustAscensionAsync(1, "increase_ascension"),
            "decrease_ascension" => ExecuteAdjustAscensionAsync(-1, "decrease_ascension"),
            "use_potion" => ExecuteUsePotionAsync(request),
            "discard_potion" => ExecuteDiscardPotionAsync(request),
            "run_console_command" => ExecuteRunConsoleCommandAsync(request),
            "confirm_modal" => ExecuteConfirmModalAsync(),
            "dismiss_modal" => ExecuteDismissModalAsync(),
            "return_to_main_menu" => ExecuteReturnToMainMenuAsync(),
            _ => throw new ApiException(409, "invalid_action", "Action is not supported yet.", new
            {
                action = request.action
            })
        };
    }

    private static async Task<ActionResponsePayload> ExecuteRunConsoleCommandAsync(ActionRequest request)
    {
        if (!AreDebugActionsEnabled())
        {
            throw new ApiException(409, "invalid_action", "run_console_command is disabled. Set STS2_ENABLE_DEBUG_ACTIONS=1 for development use.", new
            {
                action = "run_console_command"
            });
        }

        var command = request.command?.Trim();
        if (string.IsNullOrWhiteSpace(command))
        {
            throw new ApiException(400, "invalid_request", "command is required.", new
            {
                action = "run_console_command"
            });
        }

        NDevConsole console;
        try
        {
            console = NDevConsole.Instance;
        }
        catch (Exception ex)
        {
            throw new ApiException(503, "state_unavailable", $"Dev console is unavailable: {ex.Message}", new
            {
                action = "run_console_command",
                command
            }, retryable: true);
        }

        var devConsole = GetDevConsoleCore(console)
            ?? throw new ApiException(503, "state_unavailable", "Dev console backend is unavailable.", new
            {
                action = "run_console_command",
                command
            }, retryable: true);

        var runState = RunManager.Instance.DebugOnlyGetState();
        var player = GameStateService.GetLocalPlayer(runState);
        var result = devConsole.ProcessNetCommand(player, command);
        if (!result.success)
        {
            throw new ApiException(409, "invalid_action", string.IsNullOrWhiteSpace(result.msg) ? "Console command failed." : result.msg, new
            {
                action = "run_console_command",
                command
            });
        }

        if (result.task != null)
        {
            await result.task;
        }

        var stable = await WaitForConsoleCommandStabilityAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "run_console_command",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable
                ? string.IsNullOrWhiteSpace(result.msg) ? "Console command executed." : result.msg
                : "Console command executed but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<bool> WaitForConsoleCommandStabilityAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (IsStableScreenState(ActiveScreenContext.Instance.GetCurrentScreen(), allowMapScreen: true))
            {
                return true;
            }
        }

        return IsStableScreenState(ActiveScreenContext.Instance.GetCurrentScreen(), allowMapScreen: true);
    }

    private static DevConsole? GetDevConsoleCore(NDevConsole console)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var field = typeof(NDevConsole).GetField("_devConsole", flags);
        return field?.GetValue(console) as DevConsole;
    }

    private static bool AreDebugActionsEnabled()
    {
        var raw = ReadEnvironmentVariable("STS2_ENABLE_DEBUG_ACTIONS");
        if (string.IsNullOrWhiteSpace(raw))
        {
            return false;
        }

        raw = raw.Trim();

        return raw.Equals("1", StringComparison.OrdinalIgnoreCase) ||
               raw.Equals("true", StringComparison.OrdinalIgnoreCase) ||
               raw.Equals("yes", StringComparison.OrdinalIgnoreCase) ||
               raw.Equals("on", StringComparison.OrdinalIgnoreCase);
    }

    private static string? ReadEnvironmentVariable(string name)
    {
        var processValue = System.Environment.GetEnvironmentVariable(name);
        if (!string.IsNullOrWhiteSpace(processValue))
        {
            return processValue;
        }

        try
        {
            var godotValue = OS.GetEnvironment(name);
            if (!string.IsNullOrWhiteSpace(godotValue))
            {
                return godotValue;
            }
        }
        catch
        {
        }

        var userValue = System.Environment.GetEnvironmentVariable(name, System.EnvironmentVariableTarget.User);
        if (!string.IsNullOrWhiteSpace(userValue))
        {
            return userValue;
        }

        return System.Environment.GetEnvironmentVariable(name, System.EnvironmentVariableTarget.Machine);
    }

    private static async Task<ActionResponsePayload> ExecuteConfirmModalAsync()
    {
        return await ExecuteModalButtonAsync("confirm_modal", GameStateService.GetModalConfirmButton);
    }

    private static async Task<ActionResponsePayload> ExecuteDismissModalAsync()
    {
        return await ExecuteModalButtonAsync("dismiss_modal", GameStateService.GetModalCancelButton);
    }

    private static async Task<ActionResponsePayload> ExecuteReturnToMainMenuAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is not NGameOverScreen gameOverScreen || !GameStateService.CanReturnToMainMenu(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "return_to_main_menu",
                screen
            });
        }

        gameOverScreen.Call(NGameOverScreen.MethodName.ReturnToMainMenu);
        var stable = await WaitForGameOverExitAsync(TimeSpan.FromSeconds(15));

        return new ActionResponsePayload
        {
            action = "return_to_main_menu",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteModalButtonAsync(
        string actionName,
        Func<IScreenContext?, NButton?> buttonResolver)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);
        var previousModal = GameStateService.GetOpenModal();
        var button = buttonResolver(currentScreen);

        if (previousModal == null || button == null)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = actionName,
                screen
            });
        }

        button.ForceClick();
        var stable = await WaitForModalTransitionAsync(previousModal, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = actionName,
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<bool> WaitForModalTransitionAsync(IScreenContext previousModal, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentModal = GameStateService.GetOpenModal();
            if (currentModal == null || !ReferenceEquals(currentModal, previousModal))
            {
                return true;
            }
        }

        var finalModal = GameStateService.GetOpenModal();
        return finalModal == null || !ReferenceEquals(finalModal, previousModal);
    }

    private static async Task<bool> WaitForGameOverExitAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (ActiveScreenContext.Instance.GetCurrentScreen() is not NGameOverScreen)
            {
                return true;
            }
        }

        return ActiveScreenContext.Instance.GetCurrentScreen() is not NGameOverScreen;
    }

    private static async Task<NPauseMenu?> WaitForPauseMenuAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var pauseMenu = FindPauseMenu();
            if (pauseMenu != null && pauseMenu.IsVisibleInTree())
            {
                return pauseMenu;
            }
        }

        return FindPauseMenu();
    }

    private static async Task<bool> WaitForMainMenuAfterSaveAndQuitAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is NMainMenu)
            {
                return true;
            }
        }

        return ActiveScreenContext.Instance.GetCurrentScreen() is NMainMenu;
    }

    private static NPauseMenu? FindPauseMenu()
    {
        return FindFirstInGame<NPauseMenu>();
    }

    private static T? FindFirstInGame<T>() where T : Node
    {
        var game = NGame.Instance;
        if (game == null || !GodotObject.IsInstanceValid(game))
        {
            return null;
        }

        Node? root = game.GetTree()?.Root;
        root ??= game;
        return FindFirstDescendant<T>(root);
    }

    private static T? FindFirstDescendant<T>(Node? node) where T : Node
    {
        if (node == null || !GodotObject.IsInstanceValid(node))
        {
            return null;
        }

        if (node is T typedNode)
        {
            return typedNode;
        }

        foreach (var child in node.GetChildren())
        {
            var found = FindFirstDescendant<T>(child);
            if (found != null)
            {
                return found;
            }
        }

        return null;
    }

    private static string BuildEventOptionSignature(EventModel eventModel)
    {
        return string.Join(
            "|",
            eventModel.CurrentOptions.Select(option =>
                $"{option.TextKey}:{option.IsLocked}:{option.IsProceed}:{option.Title?.GetFormattedText()}:{option.Description?.GetFormattedText()}"));
    }

    private static void ObserveBackgroundResult(Task<bool> task, string actionName)
    {
        _ = ObserveBackgroundResultCore(task, actionName);
    }

    private static Task<T>? InvokePrivateTask<T>(object target, string methodName, params object?[] args)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var method = target.GetType().GetMethod(methodName, flags);
        return method?.Invoke(target, args) as Task<T>;
    }

    private static Task? InvokePrivateTask(object target, string methodName, params object?[] args)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var method = target.GetType().GetMethod(methodName, flags);
        return method?.Invoke(target, args) as Task;
    }

    private static T? GetPrivateField<T>(object target, string fieldName) where T : class
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var field = target.GetType().GetField(fieldName, flags);
        return field?.GetValue(target) as T;
    }

    private static void InvokePrivateVoid(object target, string methodName, params object?[] args)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var method = target.GetType().GetMethod(methodName, flags)
            ?? throw new InvalidOperationException($"Method '{methodName}' was not found on {target.GetType().FullName}.");
        method.Invoke(target, args);
    }

    private static async Task ObserveBackgroundResultCore(Task<bool> task, string actionName)
    {
        try
        {
            var success = await task;
            if (!success)
            {
                Log.Warn($"[NekoComm] Background action {actionName} returned false.");
            }
        }
        catch (Exception ex)
        {
            Log.Error($"[NekoComm] Background action {actionName} failed: {ex}");
        }
    }

    private static async Task<bool> WaitForRelicPickTransitionAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is NTreasureRoomRelicCollection)
            {
                continue;
            }

            if (currentScreen is NTreasureRoom)
            {
                if (GameStateService.GetProceedButton(currentScreen) != null)
                {
                    await WaitForNextFrameAsync();

                    var confirmedScreen = ActiveScreenContext.Instance.GetCurrentScreen();
                    return confirmedScreen is NTreasureRoom && GameStateService.GetProceedButton(confirmedScreen) != null;
                }

                continue;
            }

            if (IsStableScreenState(currentScreen, allowMapScreen: true))
            {
                return true;
            }
        }

        var screen = ActiveScreenContext.Instance.GetCurrentScreen();
        return screen is NTreasureRoom && GameStateService.GetProceedButton(screen) != null;
    }

    /// <summary>
    /// Waits for the next game frame via Godot's ProcessFrame signal.
    /// When NGame or SceneTree is unavailable (e.g. during shutdown),
    /// falls back to Task.Delay WITHOUT ConfigureAwait(false) to preserve
    /// the game thread's SynchronizationContext. This is critical 闂?using
    /// ConfigureAwait(false) would cause subsequent loop iterations to run
    /// on a thread-pool thread, breaking Godot object access safety.
    /// </summary>
    private static async Task WaitForNextFrameAsync()
    {
        var game = NGame.Instance;
        if (game == null || !GodotObject.IsInstanceValid(game))
        {
            await Task.Delay(TimeSpan.FromMilliseconds(16));
            return;
        }

        var tree = game.GetTree();
        if (tree == null || !GodotObject.IsInstanceValid(tree))
        {
            await Task.Delay(TimeSpan.FromMilliseconds(16));
            return;
        }

        await game.ToSignal(tree, SceneTree.SignalName.ProcessFrame);
    }
}
