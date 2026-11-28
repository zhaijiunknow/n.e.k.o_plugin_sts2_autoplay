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

// MenuTransitions implementation; shares the service API and state across partial files.
internal static partial class GameActionService
{
    private static NEpochSlot ResolveTimelineSlot(IScreenContext? currentScreen, int optionIndex)
    {
        var slots = GameStateService.GetTimelineSlots(currentScreen)
            .Where(slot => slot.State is EpochSlotState.Obtained or EpochSlotState.Complete)
            .ToArray();

        if (optionIndex < 0 || optionIndex >= slots.Length)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "choose_timeline_epoch",
                option_index = optionIndex
            });
        }

        return slots[optionIndex];
    }

    private static async Task<bool> WaitForCharacterSelectOpenAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();
            if (IsCharacterSelectOpenOrActionableModal())
            {
                return true;
            }
        }

        return IsCharacterSelectOpenOrActionableModal();
    }

    private static bool IsCharacterSelectOpenOrActionableModal()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        if (currentScreen is NCharacterSelectScreen)
        {
            return true;
        }

        return GameStateService.CanConfirmModal(currentScreen) || GameStateService.CanDismissModal(currentScreen);
    }

    private static void ClickSingleplayerStandardButton(NSingleplayerSubmenu submenu)
    {
        var standardButton = GameStateService.GetSingleplayerStandardButton(submenu);
        if (standardButton != null)
        {
            standardButton.ForceClick();
            return;
        }

        submenu.Call(NSingleplayerSubmenu.MethodName.OpenCharacterSelect);
    }

    private static async Task<bool> WaitForTimelineEpochTransitionAsync(
        NEpochSlot slot,
        EpochSlotState previousState,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is not NTimelineScreen)
            {
                return true;
            }

            if (GameStateService.CanConfirmTimelineOverlay(currentScreen))
            {
                return true;
            }

            if (GameStateService.GetTimelineInspectScreen(currentScreen) != null ||
                GameStateService.GetTimelineUnlockScreen(currentScreen) != null)
            {
                continue;
            }

            if (!GodotObject.IsInstanceValid(slot) || slot.State != previousState)
            {
                return true;
            }
        }

        return false;
    }

    private static async Task<bool> WaitForMainMenuSubmenuOpenAsync<TSubmenu>(NMainMenu screen, TimeSpan timeout)
        where TSubmenu : NSubmenu
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is TSubmenu)
            {
                return true;
            }

            if (!GodotObject.IsInstanceValid(screen))
            {
                return true;
            }
        }

        return ActiveScreenContext.Instance.GetCurrentScreen() is TSubmenu;
    }

    private static async Task<bool> WaitForMainMenuExitAsync(NMainMenu screen, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (GameStateService.GetOpenModal() != null)
            {
                return true;
            }

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (!ReferenceEquals(currentScreen, screen) &&
                GameStateService.ResolveScreen(currentScreen) != "UNKNOWN")
            {
                return true;
            }
        }

        var finalScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        return !ReferenceEquals(finalScreen, screen) &&
               GameStateService.ResolveScreen(finalScreen) != "UNKNOWN";
    }

    private static async Task<bool> WaitForMainMenuModalAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (GameStateService.GetOpenModal() != null)
            {
                return true;
            }
        }

        return GameStateService.GetOpenModal() != null;
    }

    private static async Task<bool> WaitForTimelineInspectCloseAsync(
        NEpochInspectScreen? inspectScreen,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is not NTimelineScreen)
            {
                return true;
            }

            var currentInspect = GameStateService.GetTimelineInspectScreen(currentScreen);
            if (currentInspect == null || (inspectScreen != null && !ReferenceEquals(currentInspect, inspectScreen)))
            {
                return true;
            }

            if (GameStateService.GetTimelineUnlockScreen(currentScreen) != null)
            {
                return true;
            }
        }

        return false;
    }

    private static async Task<bool> WaitForTimelineUnlockTransitionAsync(Type unlockScreenType, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is not NTimelineScreen)
            {
                return true;
            }

            var unlockScreen = GameStateService.GetTimelineUnlockScreen(currentScreen);
            if (unlockScreen == null || unlockScreen.GetType() != unlockScreenType)
            {
                return true;
            }
        }

        return false;
    }

    private static async Task<bool> WaitForMainMenuSubmenuCloseAsync(
        NMainMenuSubmenuStack submenuStack,
        NSubmenu submenu,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (!ReferenceEquals(currentScreen, submenu) || !submenuStack.SubmenusOpen)
            {
                return true;
            }
        }

        var finalScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        return !ReferenceEquals(finalScreen, submenu) || !submenuStack.SubmenusOpen;
    }

    private static async Task<bool> WaitForCharacterSelectionTransitionAsync(
        NCharacterSelectScreen screen,
        string currentCharacterId,
        string previousCharacterId,
        TimeSpan timeout)
    {
        if (currentCharacterId == previousCharacterId)
        {
            return true;
        }

        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (!GodotObject.IsInstanceValid(screen))
            {
                return true;
            }

            if (screen.Lobby.LocalPlayer.character.Id.Entry == currentCharacterId)
            {
                return true;
            }
        }

        return screen.Lobby.LocalPlayer.character.Id.Entry == currentCharacterId;
    }

    private static async Task<bool> WaitForEmbarkTransitionAsync(NCharacterSelectScreen screen, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (GameStateService.GetOpenModal() != null)
            {
                return true;
            }

            if (screen.Lobby.NetService.Type.IsMultiplayer() && screen.Lobby.LocalPlayer.isReady)
            {
                return true;
            }

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (!ReferenceEquals(currentScreen, screen) &&
                GameStateService.ResolveScreen(currentScreen) != "UNKNOWN")
            {
                return true;
            }
        }

        var finalScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        if (screen.Lobby.NetService.Type.IsMultiplayer() && screen.Lobby.LocalPlayer.isReady)
        {
            return true;
        }

        return !ReferenceEquals(finalScreen, screen) &&
               GameStateService.ResolveScreen(finalScreen) != "UNKNOWN";
    }

    private static async Task<bool> WaitForLobbyReadyTransitionAsync(NCharacterSelectScreen screen, bool ready, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (!GodotObject.IsInstanceValid(screen))
            {
                return ready;
            }

            if (screen.Lobby.LocalPlayer.isReady == ready)
            {
                return true;
            }
        }

        return GodotObject.IsInstanceValid(screen) && screen.Lobby.LocalPlayer.isReady == ready;
    }

    private static async Task<bool> WaitForLobbyAscensionTransitionAsync(NCharacterSelectScreen screen, int targetAscension, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (!GodotObject.IsInstanceValid(screen))
            {
                return false;
            }

            if (screen.Lobby.Ascension == targetAscension)
            {
                return true;
            }
        }

        return GodotObject.IsInstanceValid(screen) && screen.Lobby.Ascension == targetAscension;
    }

    private static async Task<bool> WaitForMultiplayerLobbyCharacterSelectionTransitionAsync(
        NMultiplayerTest scene,
        string currentCharacterId,
        string previousCharacterId,
        TimeSpan timeout)
    {
        if (currentCharacterId == previousCharacterId)
        {
            return true;
        }

        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScene = GameStateService.GetMultiplayerTestScene();
            if (!ReferenceEquals(currentScene, scene))
            {
                return false;
            }

            var lobby = GameStateService.GetMultiplayerTestLobby(scene);
            if (lobby?.LocalPlayer.character?.Id.Entry == currentCharacterId)
            {
                return true;
            }
        }

        return GameStateService.GetMultiplayerTestLobby(scene)?.LocalPlayer.character?.Id.Entry == currentCharacterId;
    }

    private static async Task<bool> WaitForMultiplayerLobbyHostTransitionAsync(NMultiplayerTest scene, TimeSpan timeout)
    {
        return await WaitForMultiplayerLobbyTransitionAsync(scene, timeout, lobby =>
            lobby != null &&
            lobby.NetService.Type == NetGameType.Host &&
            lobby.Players.Count >= 1);
    }

    private static async Task<bool> WaitForMultiplayerLobbyJoinTransitionAsync(NMultiplayerTest scene, TimeSpan timeout)
    {
        return await WaitForMultiplayerLobbyTransitionAsync(scene, timeout, lobby =>
            lobby != null &&
            lobby.NetService.Type == NetGameType.Client &&
            lobby.Players.Count >= 2);
    }

    private static async Task<bool> WaitForMultiplayerLobbyTransitionAsync(
        NMultiplayerTest scene,
        TimeSpan timeout,
        Func<StartRunLobby?, bool> predicate)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScene = GameStateService.GetMultiplayerTestScene();
            if (!ReferenceEquals(currentScene, scene))
            {
                return false;
            }

            var lobby = GameStateService.GetMultiplayerTestLobby(scene);
            if (predicate(lobby))
            {
                return true;
            }
        }

        return predicate(GameStateService.GetMultiplayerTestLobby(scene));
    }

    private static async Task<bool> WaitForMultiplayerLobbyReadyTransitionAsync(NMultiplayerTest scene, bool ready, bool expectRunStart, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScene = GameStateService.GetMultiplayerTestScene();
            if (!ReferenceEquals(currentScene, scene))
            {
                return ready && expectRunStart;
            }

            var lobby = GameStateService.GetMultiplayerTestLobby(scene);
            if (ready && expectRunStart && lobby != null && lobby.LocalPlayer.isReady)
            {
                continue;
            }

            if (lobby != null && lobby.LocalPlayer.isReady == ready)
            {
                return true;
            }
        }

        var finalScene = GameStateService.GetMultiplayerTestScene();
        if (!ReferenceEquals(finalScene, scene))
        {
            return ready && expectRunStart;
        }

        return GameStateService.GetMultiplayerTestLobby(scene)?.LocalPlayer.isReady == ready;
    }

    private static async Task<bool> WaitForMultiplayerLobbyDisconnectTransitionAsync(NMultiplayerTest scene, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScene = GameStateService.GetMultiplayerTestScene();
            if (currentScene == null)
            {
                return true;
            }

            if (ReferenceEquals(currentScene, scene) && GameStateService.GetMultiplayerTestLobby(scene) == null)
            {
                return true;
            }
        }

        var finalScene = GameStateService.GetMultiplayerTestScene();
        return finalScene == null || (ReferenceEquals(finalScene, scene) && GameStateService.GetMultiplayerTestLobby(scene) == null);
    }

}
