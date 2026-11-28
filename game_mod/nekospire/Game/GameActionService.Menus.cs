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

// Menus implementation; shares the service API and state across partial files.
internal static partial class GameActionService
{
    private static async Task<ActionResponsePayload> ExecuteOpenCharacterSelectAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanOpenCharacterSelect(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "open_character_select",
                screen
            });
        }

        if (currentScreen is NSingleplayerSubmenu openSubmenu)
        {
            ClickSingleplayerStandardButton(openSubmenu);
        }
        else if (currentScreen is NMainMenu mainMenu)
        {
            var singleplayerSubmenu = mainMenu.SubmenuStack.GetSubmenuType<NSingleplayerSubmenu>();
            if (singleplayerSubmenu != null)
            {
                mainMenu.SubmenuStack.Push(singleplayerSubmenu);
                await WaitForMainMenuSubmenuOpenAsync<NSingleplayerSubmenu>(mainMenu, TimeSpan.FromSeconds(5));
                ClickSingleplayerStandardButton(singleplayerSubmenu);
            }
            else
            {
                var characterSelectScreen = mainMenu.SubmenuStack.GetSubmenuType<NCharacterSelectScreen>();
                characterSelectScreen.InitializeSingleplayer();
                mainMenu.SubmenuStack.Push(characterSelectScreen);
            }
        }
        else
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "open_character_select",
                screen
            });
        }

        var stable = await WaitForCharacterSelectOpenAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "open_character_select",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteOpenTimelineAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is not NMainMenu mainMenu || !GameStateService.CanOpenTimeline(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "open_timeline",
                screen
            });
        }

        mainMenu.SubmenuStack.PushSubmenuType<NTimelineScreen>();
        var stable = await WaitForMainMenuSubmenuOpenAsync<NTimelineScreen>(mainMenu, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "open_timeline",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteCloseMainMenuSubmenuAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is not NSubmenu submenu || !GameStateService.CanCloseMainMenuSubmenu(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "close_main_menu_submenu",
                screen
            });
        }

        var submenuStack = GameStateService.GetMainMenuSubmenuStack(submenu)
            ?? throw new ApiException(503, "state_unavailable", "Main menu submenu stack is unavailable.", new
            {
                action = "close_main_menu_submenu",
                screen
            }, retryable: true);

        submenuStack.Pop();
        var stable = await WaitForMainMenuSubmenuCloseAsync(submenuStack, submenu, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "close_main_menu_submenu",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteChooseTimelineEpochAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanChooseTimelineEpoch(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "choose_timeline_epoch",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "option_index is required.", new
            {
                action = "choose_timeline_epoch"
            });
        }

        var slot = ResolveTimelineSlot(currentScreen, request.option_index.Value);
        var previousState = slot.State;

        slot.ForceClick();
        var stable = await WaitForTimelineEpochTransitionAsync(slot, previousState, TimeSpan.FromSeconds(15));

        return new ActionResponsePayload
        {
            action = "choose_timeline_epoch",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteConfirmTimelineOverlayAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanConfirmTimelineOverlay(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "confirm_timeline_overlay",
                screen
            });
        }

        var unlockScreen = GameStateService.GetTimelineUnlockScreen(currentScreen);
        if (unlockScreen != null)
        {
            var confirmButton = GameStateService.GetTimelineUnlockConfirmButton(currentScreen)
                ?? throw new ApiException(503, "state_unavailable", "Timeline unlock confirm button is unavailable.", new
                {
                    action = "confirm_timeline_overlay",
                    screen
                }, retryable: true);

            confirmButton.ForceClick();
            var unlockType = unlockScreen.GetType();
            var stable = await WaitForTimelineUnlockTransitionAsync(unlockType, TimeSpan.FromSeconds(10));

            return new ActionResponsePayload
            {
                action = "confirm_timeline_overlay",
                status = stable ? "completed" : "pending",
                stable = stable,
                message = stable ? "Action completed." : "Action queued but state is still transitioning.",
                state = GameStateService.BuildStatePayload()
            };
        }

        var closeButton = GameStateService.GetTimelineInspectCloseButton(currentScreen)
            ?? throw new ApiException(503, "state_unavailable", "Timeline inspect close button is unavailable.", new
            {
                action = "confirm_timeline_overlay",
                screen
            }, retryable: true);

        closeButton.ForceClick();
        var inspectScreen = GameStateService.GetTimelineInspectScreen(currentScreen);
        var stableInspect = await WaitForTimelineInspectCloseAsync(inspectScreen, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "confirm_timeline_overlay",
            status = stableInspect ? "completed" : "pending",
            stable = stableInspect,
            message = stableInspect ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteContinueRunAsync()
    {
        // Pop any open main-menu submenu first: CanContinueRun requires SubmenuStack.SubmenusOpen == false, so
        // a leftover submenu (the mods/config submenu, or one the player opened) used to make the Continue
        // button unreachable and this action 409 — which is one of the ways a player "could not get back to
        // the save". start_coop_session already did this; continue_run did not.
        await CloseMainMenuSubmenusAsync();

        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is not NMainMenu mainMenu || !GameStateService.CanContinueRun(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "continue_run",
                screen
            });
        }

        var continueButton = GameStateService.GetMainMenuContinueButton(mainMenu)
            ?? throw new ApiException(503, "state_unavailable", "Continue button is unavailable.", new
            {
                action = "continue_run",
                screen
            }, retryable: true);

        continueButton.ForceClick();
        var stable = await WaitForMainMenuExitAsync(mainMenu, TimeSpan.FromSeconds(15));

        var landingScreen = GameStateService.ResolveScreen(ActiveScreenContext.Instance.GetCurrentScreen());
        return new ActionResponsePayload
        {
            action = "continue_run",
            status = stable ? "completed" : "pending",
            stable = stable,
            // Report the landing screen: callers need to tell "resumed the save into MAP/COMBAT" apart from
            // "the button was clicked but we are still on the main menu".
            message = stable ? $"Action completed (screen={landingScreen})." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteAbandonRunAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is not NMainMenu mainMenu || !GameStateService.CanAbandonRun(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "abandon_run",
                screen
            });
        }

        var abandonButton = GameStateService.GetMainMenuAbandonRunButton(mainMenu)
            ?? throw new ApiException(503, "state_unavailable", "Abandon run button is unavailable.", new
            {
                action = "abandon_run",
                screen
            }, retryable: true);

        abandonButton.ForceClick();
        var stable = await WaitForMainMenuModalAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "abandon_run",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteSaveAndQuitAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var runState = RunManager.Instance.DebugOnlyGetState();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanSaveAndQuit(currentScreen, runState))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "save_and_quit",
                screen
            });
        }

        var pauseMenu = FindPauseMenu();
        if (pauseMenu == null)
        {
            var pauseButton = FindFirstInGame<NTopBarPauseButton>();
            if (pauseButton == null || !pauseButton.IsVisibleInTree())
            {
                throw new ApiException(503, "state_unavailable", "Pause button is unavailable.", new
                {
                    action = "save_and_quit",
                    screen
                }, retryable: true);
            }

            pauseButton.ForceClick();
            pauseMenu = await WaitForPauseMenuAsync(TimeSpan.FromSeconds(5));
        }

        if (pauseMenu == null)
        {
            throw new ApiException(503, "state_unavailable", "Pause menu did not open.", new
            {
                action = "save_and_quit",
                screen
            }, retryable: true);
        }

        var closeTask = InvokePrivateTask(pauseMenu, "CloseToMenu");
        if (closeTask != null)
        {
            await closeTask;
        }
        else
        {
            var saveAndQuitButton = GetPrivateField<NButton>(pauseMenu, "_saveAndQuitButton");
            if (saveAndQuitButton == null || !saveAndQuitButton.IsVisibleInTree() || !saveAndQuitButton.IsEnabled)
            {
                throw new ApiException(503, "state_unavailable", "Save and Quit button is unavailable.", new
                {
                    action = "save_and_quit",
                    screen
                }, retryable: true);
            }

            saveAndQuitButton.ForceClick();
        }

        var stable = await WaitForMainMenuAfterSaveAndQuitAsync(TimeSpan.FromSeconds(20));

        return new ActionResponsePayload
        {
            action = "save_and_quit",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteSelectCharacterAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);
        var multiplayerTestScene = GameStateService.GetMultiplayerTestScene();

        if (multiplayerTestScene != null)
        {
            return await ExecuteSelectMultiplayerLobbyCharacterAsync(request, multiplayerTestScene, screen);
        }

        if (currentScreen is not NCharacterSelectScreen characterSelectScreen || !GameStateService.CanSelectCharacter(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "select_character",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "select_character requires option_index.", new
            {
                action = "select_character"
            });
        }

        var buttons = GameStateService.GetCharacterSelectButtons(currentScreen);
        if (request.option_index < 0 || request.option_index >= buttons.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "select_character",
                option_index = request.option_index,
                option_count = buttons.Count
            });
        }

        var button = buttons[request.option_index.Value];
        if (button.IsLocked)
        {
            throw new ApiException(409, "invalid_target", "The selected character is locked.", new
            {
                action = "select_character",
                option_index = request.option_index,
                character_id = button.Character.Id.Entry
            });
        }

        if (!button.IsEnabled || !button.IsVisibleInTree())
        {
            throw new ApiException(409, "invalid_target", "The selected character cannot be chosen right now.", new
            {
                action = "select_character",
                option_index = request.option_index,
                character_id = button.Character.Id.Entry
            });
        }

        var previousCharacterId = characterSelectScreen.Lobby.LocalPlayer.character.Id.Entry;
        button.Select();
        var stable = await WaitForCharacterSelectionTransitionAsync(characterSelectScreen, button.Character.Id.Entry, previousCharacterId, TimeSpan.FromSeconds(5));

        return new ActionResponsePayload
        {
            action = "select_character",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteSelectMultiplayerLobbyCharacterAsync(ActionRequest request, NMultiplayerTest scene, string screen)
    {
        if (!GameStateService.CanSelectCharacter(ActiveScreenContext.Instance.GetCurrentScreen()))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "select_character",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "select_character requires option_index.", new
            {
                action = "select_character"
            });
        }

        var characters = GameStateService.GetMultiplayerLobbyCharacters();
        if (request.option_index < 0 || request.option_index >= characters.Length)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "select_character",
                option_index = request.option_index,
                option_count = characters.Length
            });
        }

        var paginator = GameStateService.GetMultiplayerTestCharacterPaginator(scene)
            ?? throw new ApiException(503, "state_unavailable", "Multiplayer character selector is unavailable.", new
            {
                action = "select_character",
                screen
            }, retryable: true);

        var lobby = GameStateService.GetMultiplayerTestLobby(scene)
            ?? throw new ApiException(503, "state_unavailable", "Multiplayer lobby is unavailable.", new
            {
                action = "select_character",
                screen
            }, retryable: true);

        var previousCharacterId = lobby.LocalPlayer.character.Id.Entry;
        var currentCharacterId = characters[request.option_index.Value].Id.Entry;
        paginator.SetIndex(request.option_index.Value);
        var stable = await WaitForMultiplayerLobbyCharacterSelectionTransitionAsync(scene, currentCharacterId, previousCharacterId, TimeSpan.FromSeconds(5));

        return new ActionResponsePayload
        {
            action = "select_character",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteEmbarkAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is NMultiplayerLoadGameScreen && GameStateService.CanEmbark(currentScreen))
        {
            GameStateService.GetCharacterEmbarkButton(currentScreen)!.ForceClick();
            return new ActionResponsePayload
            {
                action = "embark", status = "completed", stable = true,
                message = "联机读档已准备，等待其他玩家。", state = GameStateService.BuildStatePayload()
            };
        }
        if (!GameStateService.CanEmbark(currentScreen) || currentScreen is not NCharacterSelectScreen characterSelectScreen)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "embark",
                screen
            });
        }

        var embarkButton = GameStateService.GetCharacterEmbarkButton(currentScreen)
            ?? throw new ApiException(503, "state_unavailable", "Embark button is unavailable.", new
            {
                action = "embark",
                screen
            }, retryable: true);

        embarkButton.ForceClick();
        var stable = await WaitForEmbarkTransitionAsync(characterSelectScreen, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "embark",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteUnreadyAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);
        var multiplayerTestScene = GameStateService.GetMultiplayerTestScene();

        if (multiplayerTestScene != null)
        {
            var multiplayerLobby = GameStateService.GetMultiplayerTestLobby(multiplayerTestScene)
                ?? throw new ApiException(503, "state_unavailable", "Multiplayer lobby is unavailable.", new
                {
                    action = "unready",
                    screen
                }, retryable: true);

            if (!GameStateService.CanUnready(currentScreen))
            {
                throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
                {
                    action = "unready",
                    screen
                });
            }

            multiplayerLobby.SetReady(ready: false);
            var multiplayerStable = await WaitForMultiplayerLobbyReadyTransitionAsync(multiplayerTestScene, ready: false, expectRunStart: false, TimeSpan.FromSeconds(5));

            return new ActionResponsePayload
            {
                action = "unready",
                status = multiplayerStable ? "completed" : "pending",
                stable = multiplayerStable,
                message = multiplayerStable ? "Action completed." : "Action queued but state is still transitioning.",
                state = GameStateService.BuildStatePayload()
            };
        }

        if (!GameStateService.CanUnready(currentScreen) || currentScreen is not NCharacterSelectScreen characterSelectScreen)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "unready",
                screen
            });
        }

        characterSelectScreen.Lobby.SetReady(ready: false);
        var stable = await WaitForLobbyReadyTransitionAsync(characterSelectScreen, ready: false, TimeSpan.FromSeconds(5));

        return new ActionResponsePayload
        {
            action = "unready",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteOpenMultiplayerMenuAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is not NMainMenu mainMenu || !GameStateService.CanOpenMultiplayerMenu(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "open_multiplayer_menu",
                screen
            });
        }

        mainMenu.SubmenuStack.PushSubmenuType<NMultiplayerSubmenu>();
        var stable = await WaitForMainMenuSubmenuOpenAsync<NMultiplayerSubmenu>(mainMenu, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "open_multiplayer_menu",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteStartMultiplayerHostAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is not NMultiplayerSubmenu submenu)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "start_multiplayer_host",
                screen
            });
        }
        var submenuStack = GameStateService.GetMainMenuSubmenuStack(submenu)
            ?? throw new ApiException(503, "state_unavailable", "Main menu submenu stack is unavailable.", new
            {
                action = "start_multiplayer_host",
                screen
            }, retryable: true);

        // R2 same-machine direct connect: force a plain ENet host on 33771 (bypass the Steam-host
        // decision) so the catgirl client can connect directly to 127.0.0.1:33771.
        var netService = new NetHostGameService(PeerVersionInfo.LocalDefault());
        var netError = netService.StartENetHost(33771, 4);
        if (netError.HasValue)
        {
            throw new ApiException(409, "invalid_action", $"Failed to start ENet host: {netError}", new
            {
                action = "start_multiplayer_host",
                screen
            });
        }

        var characterSelect = submenuStack.GetSubmenuType<NCharacterSelectScreen>();
        characterSelect.InitializeMultiplayerAsHost(netService, 4);
        submenuStack.Push(characterSelect);

        var stable = await WaitForCharacterSelectOpenAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "start_multiplayer_host",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    /// <summary>
    /// One-shot host-side co-op bootstrap, mirroring the config window's "start co-op room" button
    /// (NekoConfigWindow.StartCoopSessionAsync): enable coop, pop back to the real main menu, open the
    /// multiplayer submenu, start the ENet host on 33771, then launch the catgirl game process.
    ///
    /// The HTTP surface previously had no way to reach that button, so a remote caller could open a room but
    /// never get the second player — spawning the catgirl process is the half that was missing.
    ///
    /// Composing the executors directly (rather than going back through ExecuteAsync) is safe: Router already
    /// dispatches actions through GameThread.InvokeAsync, and GameThread.InvokeAsync runs inline when it is
    /// re-entered from the game thread, so there is no nested post and no deadlock.
    /// </summary>
    private static Task<ActionResponsePayload> ExecuteStartCoopSessionAsync() =>
        RunCoopTransitionAsync(ExecuteStartCoopSessionCoreAsync);

    private static async Task<ActionResponsePayload> ExecuteStartCoopSessionCoreAsync()
    {
        // The catgirl process reads the same settings.json and gates its autoplay on this flag
        // (NekoAutoplayDriver.IsClientByPort -> NekoConfig.Current.coop_enabled), so it must be persisted
        // before the process is spawned or the catgirl will sit idle.
        CoopSaveSession.RequireHost();
        var peer = CoopClientPeer.Existing(NekoConfig.Current.coop_client_port);
        if (peer != null) await peer.PrepareAsync();
        await CloseMainMenuSubmenusAsync();

        var openResult = await ExecuteOpenMultiplayerMenuAsync();
        var hostResult = await ExecuteStartMultiplayerHostAsync();
        var cfg = NekoConfig.Current;
        cfg.coop_enabled = true;
        cfg.Save();
        if (peer != null) await peer.RejoinAsync(CoopSaveIdentity.NewClientId);
        else NekoConfigWindow.LaunchCatgirlProcess(CoopSaveIdentity.NewClientId);

        var stable = openResult.stable && hostResult.stable;
        return new ActionResponsePayload
        {
            action = "start_coop_session",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable
                ? "Co-op room opened and the catgirl process launched or rejoined."
                : "Co-op room opened but the game is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    /// <summary>
    /// Pop the main-menu submenu stack back to NMainMenu before open_multiplayer_menu, which only works when
    /// currentScreen is NMainMenu (any open submenu makes it 409). Loops defensively for nested submenus.
    /// </summary>
    private static async Task CloseMainMenuSubmenusAsync()
    {
        for (var i = 0; i < 6; i++)
        {
            if (!GameStateService.CanCloseMainMenuSubmenu(ActiveScreenContext.Instance.GetCurrentScreen()))
            {
                return;
            }

            await ExecuteCloseMainMenuSubmenuAsync();
            await Task.Delay(150);
        }
    }

    private static async Task<ActionResponsePayload> ExecuteJoinMultiplayerDirectAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is not NMultiplayerSubmenu submenu)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "join_multiplayer_direct",
                screen
            });
        }
        var submenuStack = GameStateService.GetMainMenuSubmenuStack(submenu)
            ?? throw new ApiException(503, "state_unavailable", "Main menu submenu stack is unavailable.", new
            {
                action = "join_multiplayer_direct",
                screen
            }, retryable: true);

        var netId = CoopSaveIdentity.CurrentClientId;
        var initializer = new ENetClientConnectionInitializer(netId, "127.0.0.1", 33771);
        var netService = new NetClientGameService(PeerVersionInfo.LocalDefault());
        var joinFlow = new JoinFlow(netService, initializer);
        var joinResult = await joinFlow.Begin(NGame.Instance?.GetTree());

        if (joinResult.sessionState.GetValueOrDefault() == RunSessionState.InLobby && joinResult.joinResponse.HasValue)
        {
            var characterSelect = submenuStack.GetSubmenuType<NCharacterSelectScreen>();
            characterSelect.InitializeMultiplayerAsClient(joinFlow.NetService, joinResult.joinResponse.Value);
            submenuStack.Push(characterSelect);

            var stable = await WaitForCharacterSelectOpenAsync(TimeSpan.FromSeconds(10));

            return new ActionResponsePayload
            {
                action = "join_multiplayer_direct",
                status = stable ? "completed" : "pending",
                stable = stable,
                message = stable ? "Action completed." : "Action queued but state is still transitioning.",
                state = GameStateService.BuildStatePayload()
            };
        }

        if (joinResult.sessionState == RunSessionState.InLoadedLobby && joinResult.loadJoinResponse.HasValue)
        {
            var load = submenuStack.GetSubmenuType<NMultiplayerLoadGameScreen>();
            try
            {
                load.InitializeAsClient(joinFlow.NetService, joinResult.loadJoinResponse.Value);
                submenuStack.Push(load);
            }
            catch
            {
                netService.Disconnect(NetError.InternalError);
                throw;
            }
            return new ActionResponsePayload
            {
                action = "join_multiplayer_direct", status = "completed", stable = true,
                message = "已使用原玩家身份加入联机读档大厅。", state = GameStateService.BuildStatePayload()
            };
        }
        netService.Disconnect(NetError.Quit);
        throw new ApiException(409, "invalid_action", "Failed to join the direct-connect lobby.", new
        {
            action = "join_multiplayer_direct",
            session_state = joinResult.sessionState?.ToString(),
            screen
        });
    }

    private static async Task<ActionResponsePayload> ExecuteHostMultiplayerLobbyAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);
        var scene = GameStateService.GetMultiplayerTestScene();

        if (!GameStateService.CanHostMultiplayerLobby(currentScreen) || scene == null)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "host_multiplayer_lobby",
                screen
            });
        }

        var startHostTask = InvokePrivateTask<bool>(scene, "StartHost", false)
            ?? throw new ApiException(503, "state_unavailable", "Multiplayer host entry point is unavailable.", new
            {
                action = "host_multiplayer_lobby",
                screen
            }, retryable: true);

        var hostStarted = await startHostTask;
        if (!hostStarted)
        {
            throw new ApiException(409, "invalid_action", "Failed to host the multiplayer lobby.", new
            {
                action = "host_multiplayer_lobby",
                screen
            });
        }

        var stable = await WaitForMultiplayerLobbyHostTransitionAsync(scene, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "host_multiplayer_lobby",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteJoinMultiplayerLobbyAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);
        var scene = GameStateService.GetMultiplayerTestScene();

        if (!GameStateService.CanJoinMultiplayerLobby(currentScreen) || scene == null)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "join_multiplayer_lobby",
                screen
            });
        }

        var joinHost = GameStateService.GetMultiplayerLobbyJoinHost();
        var joinPort = (ushort)GameStateService.GetMultiplayerLobbyJoinPort();
        var joinNetId = GameStateService.GetMultiplayerLobbyJoinNetIdHint();
        var initializer = new ENetClientConnectionInitializer(joinNetId, joinHost, joinPort);
        await scene.JoinToHost(initializer);

        if (GameStateService.GetMultiplayerTestLobby(scene) == null)
        {
            throw new ApiException(409, "invalid_action", "Failed to join the multiplayer lobby.", new
            {
                action = "join_multiplayer_lobby",
                screen,
                join_host = joinHost,
                join_port = joinPort,
                net_id = joinNetId
            });
        }

        var stable = await WaitForMultiplayerLobbyJoinTransitionAsync(scene, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "join_multiplayer_lobby",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteReadyMultiplayerLobbyAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);
        var scene = GameStateService.GetMultiplayerTestScene();

        if (!GameStateService.CanReadyMultiplayerLobby(currentScreen) || scene == null)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "ready_multiplayer_lobby",
                screen
            });
        }

        var lobby = GameStateService.GetMultiplayerTestLobby(scene)
            ?? throw new ApiException(503, "state_unavailable", "Multiplayer lobby is unavailable.", new
            {
                action = "ready_multiplayer_lobby",
                screen
            }, retryable: true);
        var expectRunStart = lobby.Players.Count > 1 &&
            lobby.Players
                .Where(player => player.id != lobby.LocalPlayer.id)
                .All(player => player.isReady);

        InvokePrivateVoid(scene, "ReadyButtonPressed");
        var stable = await WaitForMultiplayerLobbyReadyTransitionAsync(scene, ready: true, expectRunStart, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "ready_multiplayer_lobby",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteDisconnectMultiplayerLobbyAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);
        var scene = GameStateService.GetMultiplayerTestScene();

        if (!GameStateService.CanDisconnectMultiplayerLobby(currentScreen) || scene == null)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "disconnect_multiplayer_lobby",
                screen
            });
        }

        InvokePrivateVoid(scene, "Disconnect", NetError.Quit);
        var stable = await WaitForMultiplayerLobbyDisconnectTransitionAsync(scene, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "disconnect_multiplayer_lobby",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteAdjustAscensionAsync(int delta, string actionName)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);
        var canAdjust = delta > 0
            ? GameStateService.CanIncreaseAscension(currentScreen)
            : GameStateService.CanDecreaseAscension(currentScreen);

        if (!canAdjust || currentScreen is not NCharacterSelectScreen characterSelectScreen)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = actionName,
                screen
            });
        }

        var targetAscension = characterSelectScreen.Lobby.Ascension + delta;
        characterSelectScreen.Lobby.SyncAscensionChange(targetAscension);
        var stable = await WaitForLobbyAscensionTransitionAsync(characterSelectScreen, targetAscension, TimeSpan.FromSeconds(5));

        return new ActionResponsePayload
        {
            action = actionName,
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

}
