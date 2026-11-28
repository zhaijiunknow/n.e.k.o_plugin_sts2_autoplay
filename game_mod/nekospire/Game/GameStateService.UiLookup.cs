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

// UiLookup implementation; shares the service API and state across partial files.
internal static partial class GameStateService
{
    private static NMerchantRoom? GetMerchantRoom(IScreenContext? currentScreen)
    {
        return currentScreen switch
        {
            NMerchantRoom room => room,
            NMerchantInventory => NMerchantRoom.Instance,
            _ => null
        };
    }

    internal static NMerchantInventory? GetMerchantInventoryScreen(IScreenContext? currentScreen)
    {
        return currentScreen switch
        {
            NMerchantInventory inventory => inventory,
            NMerchantRoom room when room.Inventory != null => room.Inventory,
            _ => null
        };
    }

    public static MerchantInventory? GetMerchantInventory(IScreenContext? currentScreen)
    {
        return GetMerchantInventoryScreen(currentScreen)?.Inventory ?? GetMerchantRoom(currentScreen)?.Inventory?.Inventory;
    }

    public static IReadOnlyList<MerchantCardEntry> GetMerchantCardEntries(IScreenContext? currentScreen)
    {
        var inventory = GetMerchantInventory(currentScreen);
        if (inventory == null)
        {
            return Array.Empty<MerchantCardEntry>();
        }

        return inventory.CharacterCardEntries.Concat(inventory.ColorlessCardEntries).ToArray();
    }

    public static IReadOnlyList<MerchantRelicEntry> GetMerchantRelicEntries(IScreenContext? currentScreen)
    {
        return GetMerchantInventory(currentScreen)?.RelicEntries?.ToArray() ?? Array.Empty<MerchantRelicEntry>();
    }

    public static IReadOnlyList<MerchantPotionEntry> GetMerchantPotionEntries(IScreenContext? currentScreen)
    {
        return GetMerchantInventory(currentScreen)?.PotionEntries?.ToArray() ?? Array.Empty<MerchantPotionEntry>();
    }

    public static MerchantCardRemovalEntry? GetMerchantCardRemovalEntry(IScreenContext? currentScreen)
    {
        return GetMerchantInventory(currentScreen)?.CardRemovalEntry;
    }

    public static NCharacterSelectScreen? GetCharacterSelectScreen(IScreenContext? currentScreen)
    {
        return currentScreen as NCharacterSelectScreen;
    }

    public static NMultiplayerTest? GetMultiplayerTestScene()
    {
        var currentScene = NGame.Instance?.RootSceneContainer?.CurrentScene;
        return currentScene is NMultiplayerTest multiplayerTest && multiplayerTest.IsVisibleInTree()
            ? multiplayerTest
            : null;
    }

    public static StartRunLobby? GetMultiplayerTestLobby(NMultiplayerTest scene)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var field = typeof(NMultiplayerTest).GetField("_lobby", flags);
        return field?.GetValue(scene) as StartRunLobby;
    }

    private static int GetStartRunLobbyMaxPlayers(StartRunLobby lobby)
    {
        var maxPlayers = StartRunLobbyMaxPlayersField?.GetValue(lobby) is int parsed ? parsed : 0;
        return maxPlayers > 0 ? maxPlayers : lobby.Players.Count;
    }

    public static NMultiplayerTestCharacterPaginator? GetMultiplayerTestCharacterPaginator(NMultiplayerTest scene)
    {
        return scene.GetNodeOrNull<NMultiplayerTestCharacterPaginator>("CharacterChooser");
    }

    public static string GetMultiplayerLobbyJoinHost()
    {
        var raw = System.Environment.GetEnvironmentVariable("STS2_MULTIPLAYER_HOST_IP");
        return string.IsNullOrWhiteSpace(raw) ? "127.0.0.1" : raw.Trim();
    }

    public static int GetMultiplayerLobbyJoinPort()
    {
        return 33771;
    }

    public static ulong GetMultiplayerLobbyJoinNetIdHint()
    {
        var raw = System.Environment.GetEnvironmentVariable("STS2_MULTIPLAYER_NET_ID");
        if (!string.IsNullOrWhiteSpace(raw) && ulong.TryParse(raw.Trim(), out var parsed))
        {
            return parsed;
        }

        return (ulong)System.Environment.ProcessId;
    }

    public static CharacterModel[] GetMultiplayerLobbyCharacters()
    {
        return
        [
            ModelDb.Character<Ironclad>(),
            ModelDb.Character<Silent>(),
            ModelDb.Character<Regent>(),
            ModelDb.Character<Necrobinder>(),
            ModelDb.Character<Defect>()
        ];
    }

    public static IReadOnlyList<NCharacterSelectButton> GetCharacterSelectButtons(IScreenContext? currentScreen)
    {
        var screen = GetCharacterSelectScreen(currentScreen);
        if (screen == null)
        {
            return Array.Empty<NCharacterSelectButton>();
        }

        return FindDescendants<NCharacterSelectButton>(screen)
            .Where(node => GodotObject.IsInstanceValid(node))
            .OrderBy(node => node.GlobalPosition.Y)
            .ThenBy(node => node.GlobalPosition.X)
            .ToArray();
    }

    public static NConfirmButton? GetCharacterEmbarkButton(IScreenContext? currentScreen)
    {
        if (currentScreen is NMultiplayerLoadGameScreen load)
            return load.GetNodeOrNull<NConfirmButton>("ConfirmButton");
        return GetCharacterSelectScreen(currentScreen)?.GetNodeOrNull<NConfirmButton>("ConfirmButton");
    }

    public static NBackButton? GetCharacterUnreadyButton(IScreenContext? currentScreen)
    {
        return GetCharacterSelectScreen(currentScreen)?.GetNodeOrNull<NBackButton>("UnreadyButton");
    }

    public static NMainMenuTextButton? GetMainMenuContinueButton(NMainMenu mainMenu)
    {
        return mainMenu.GetNodeOrNull<NMainMenuTextButton>("MainMenuTextButtons/ContinueButton");
    }

    public static NMainMenuTextButton? GetMainMenuAbandonRunButton(NMainMenu mainMenu)
    {
        return mainMenu.GetNodeOrNull<NMainMenuTextButton>("MainMenuTextButtons/AbandonRunButton");
    }

    public static NMainMenuTextButton? GetMainMenuSingleplayerButton(NMainMenu mainMenu)
    {
        return mainMenu.GetNodeOrNull<NMainMenuTextButton>("MainMenuTextButtons/SingleplayerButton");
    }

    public static NButton? GetSingleplayerStandardButton(NSingleplayerSubmenu submenu)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
        if (submenu.GetType().GetField("_standardButton", flags)?.GetValue(submenu) is NButton fieldButton &&
            IsUsableModalButton(fieldButton))
        {
            return fieldButton;
        }

        foreach (var path in new[] { "%StandardButton", "StandardButton", "Buttons/StandardButton" })
        {
            var button = submenu.GetNodeOrNull<NButton>(path);
            if (IsUsableModalButton(button))
            {
                return button;
            }
        }

        return FindDescendants<NButton>(submenu).FirstOrDefault(button =>
            IsUsableModalButton(button) &&
            ModalButtonNameContains(button, "Standard") &&
            !ModalButtonNameContains(button, "Daily", "Custom"));
    }

    public static NMainMenuTextButton? GetMainMenuTimelineButton(NMainMenu mainMenu)
    {
        return mainMenu.GetNodeOrNull<NMainMenuTextButton>("MainMenuTextButtons/TimelineButton");
    }

    public static NTimelineScreen? GetTimelineScreen(IScreenContext? currentScreen)
    {
        if (currentScreen is NTimelineScreen timelineScreen && timelineScreen.IsVisibleInTree())
        {
            return timelineScreen;
        }

        return null;
    }

    public static IReadOnlyList<NEpochSlot> GetTimelineSlots(IScreenContext? currentScreen)
    {
        var timelineScreen = GetTimelineScreen(currentScreen);
        if (timelineScreen == null)
        {
            return Array.Empty<NEpochSlot>();
        }

        return FindDescendants<NEpochSlot>(timelineScreen)
            .Where(slot => slot.IsVisibleInTree() && slot.model != null && slot.State != EpochSlotState.NotObtained)
            .OrderBy(slot => slot.GlobalPosition.X)
            .ThenBy(slot => slot.GlobalPosition.Y)
            .ToArray();
    }

    public static NEpochInspectScreen? GetTimelineInspectScreen(IScreenContext? currentScreen)
    {
        var timelineScreen = GetTimelineScreen(currentScreen);
        var inspectScreen = timelineScreen?.GetNodeOrNull<NEpochInspectScreen>("%EpochInspectScreen");
        return inspectScreen?.Visible == true ? inspectScreen : null;
    }

    public static NUnlockScreen? GetTimelineUnlockScreen(IScreenContext? currentScreen)
    {
        var timelineScreen = GetTimelineScreen(currentScreen);
        if (timelineScreen == null)
        {
            return null;
        }

        return FindDescendants<NUnlockScreen>(timelineScreen)
            .FirstOrDefault(screen => screen.IsVisibleInTree());
    }

    public static NButton? GetTimelineBackButton(IScreenContext? currentScreen)
    {
        return GetTimelineScreen(currentScreen)?.GetNodeOrNull<NButton>("BackButton");
    }

    public static NButton? GetTimelineInspectCloseButton(IScreenContext? currentScreen)
    {
        return GetTimelineInspectScreen(currentScreen)?.GetNodeOrNull<NButton>("%CloseButton");
    }

    public static NButton? GetTimelineUnlockConfirmButton(IScreenContext? currentScreen)
    {
        return GetTimelineUnlockScreen(currentScreen)?.GetNodeOrNull<NButton>("ConfirmButton");
    }

    public static NMainMenuSubmenuStack? GetMainMenuSubmenuStack(Node? node)
    {
        var current = node;
        while (current != null)
        {
            if (current is NMainMenuSubmenuStack submenuStack)
            {
                return submenuStack;
            }

            current = current.GetParent();
        }

        return null;
    }

    public static IScreenContext? GetOpenModal()
    {
        return NModalContainer.Instance?.OpenModal;
    }

    public static NButton? GetModalConfirmButton(IScreenContext? currentScreen)
    {
        return FindModalButton(
            isConfirm: true,
            "VerticalPopup/YesButton",
            "YesButton",
            "%YesButton",
            "ConfirmButton",
            "%ConfirmButton",
            "%Confirm",
            "%AcknowledgeButton",
            "OkButton",
            "%OkButton",
            "OKButton");
    }

    public static NButton? GetModalCancelButton(IScreenContext? currentScreen)
    {
        return FindModalButton(
            isConfirm: false,
            "VerticalPopup/NoButton",
            "NoButton",
            "%NoButton",
            "CancelButton",
            "%CancelButton",
            "%BackButton",
            "BackButton");
    }

    private static NButton? FindModalButton(bool isConfirm, params string[] paths)
    {
        if (GetOpenModal() is not Node modalNode)
        {
            return null;
        }

        if (modalNode is NVerticalPopup verticalPopup)
        {
            var typedButton = isConfirm ? verticalPopup.YesButton : verticalPopup.NoButton;
            if (IsUsableModalButton(typedButton))
            {
                return typedButton;
            }
        }

        foreach (var path in paths)
        {
            var button = modalNode.GetNodeOrNull<NButton>(path);
            if (IsUsableModalButton(button))
            {
                return button;
            }
        }

        var usableButtons = FindDescendants<NButton>(modalNode).Where(IsUsableModalButton).ToArray();
        if (isConfirm)
        {
            var ftueConfirm = usableButtons.OfType<NFtueConfirmButton>().FirstOrDefault();
            if (ftueConfirm != null)
            {
                return ftueConfirm;
            }

            var namedConfirm = usableButtons.FirstOrDefault(IsConfirmNamedModalButton);
            if (namedConfirm != null)
            {
                return namedConfirm;
            }

            if (usableButtons.Length == 1 && !IsDismissNamedModalButton(usableButtons[0]))
            {
                return usableButtons[0];
            }
        }
        else
        {
            var namedDismiss = usableButtons.FirstOrDefault(IsDismissNamedModalButton);
            if (namedDismiss != null)
            {
                return namedDismiss;
            }
        }

        return null;
    }

    private static bool IsUsableModalButton(NButton? button)
    {
        return button != null &&
               GodotObject.IsInstanceValid(button) &&
               button.IsEnabled &&
               button.IsVisibleInTree();
    }

    private static bool IsConfirmNamedModalButton(NButton button)
    {
        return ModalButtonNameContains(button, "Yes", "Confirm", "Acknowledge", "Ok", "Okay", "Continue", "Accept");
    }

    private static bool IsDismissNamedModalButton(NButton button)
    {
        return ModalButtonNameContains(button, "No", "Cancel", "Back", "Dismiss", "Close");
    }

    private static bool ModalButtonNameContains(NButton button, params string[] tokens)
    {
        var name = button.Name.ToString();
        return tokens.Any(token => name.Contains(token, StringComparison.OrdinalIgnoreCase));
    }

    private static string? ResolveUnderlyingScreen(Node modalNode)
    {
        var parent = modalNode.GetParent();
        while (parent != null)
        {
            if (parent is IScreenContext screenContext && !ReferenceEquals(parent, modalNode))
            {
                return ResolveNonModalScreen(screenContext);
            }

            parent = parent.GetParent();
        }

        return null;
    }

    private static string ResolveNonModalScreen(IScreenContext? currentScreen)
    {
        if (currentScreen != null &&
            TryGetCombatHandSelection(currentScreen, out _))
        {
            return "CARD_SELECTION";
        }

        if (currentScreen is NCardsViewScreen)
        {
            return "CARDS_VIEW";
        }

        // Generic grid-holder screens collapse to CARD_SELECTION, but the reward / transform / remove
        // card choosers must NOT be swallowed here — fall through to the switch so they get a specific
        // scene (CARD_SELECTION_REWARD / _TRANSFORM / _REMOVE) for MCP consumers.
        if (currentScreen is Node rootNode &&
            currentScreen is not NChooseABundleSelectionScreen &&
            currentScreen is not NCardRewardSelectionScreen &&
            currentScreen is not NDeckTransformSelectScreen &&
            currentScreen is not NDeckCardSelectScreen &&
            GetVisibleGridCardHolders(rootNode).Count > 0)
        {
            return "CARD_SELECTION";
        }

        if (GetMultiplayerTestScene() != null)
        {
            return "MULTIPLAYER_LOBBY";
        }

        return currentScreen switch
        {
            NGameOverScreen => "GAME_OVER",
            NCardRewardSelectionScreen => "CARD_SELECTION_REWARD",
            NChooseACardSelectionScreen => "CARD_SELECTION",
            NDeckCardSelectScreen => "CARD_SELECTION_REMOVE",
            NDeckTransformSelectScreen => "CARD_SELECTION_TRANSFORM",
            NDeckUpgradeSelectScreen or NDeckEnchantSelectScreen => "CARD_SELECTION",
            NCardGridSelectionScreen => "CARD_SELECTION",
            NRewardsScreen => "REWARD",
            NTreasureRoom or NTreasureRoomRelicCollection => "CHEST",
            NRestSiteRoom => "REST",
            NMerchantRoom or NMerchantInventory => "SHOP",
            NEventRoom => "EVENT",
            NCombatRoom => "COMBAT",
            NMapScreen or NMapRoom => "MAP",
            NCharacterSelectScreen or NMultiplayerLoadGameScreen => "CHARACTER_SELECT",
            NChooseABundleSelectionScreen => "BUNDLE_SELECTION",
            NCapstoneSubmenuStack => "CAPSTONE_SELECTION",
            NPatchNotesScreen => "MAIN_MENU",
            NSubmenu => "MAIN_MENU",
            NLogoAnimation => "MAIN_MENU",
            NMainMenu => "MAIN_MENU",
            _ => "UNKNOWN"
        };
    }

    private static string? GetButtonLabel(NButton? button)
    {
        if (button == null)
        {
            return null;
        }

        return button.GetNodeOrNull<MegaLabel>("Label")?.Text ?? button.Name.ToString();
    }

    private static bool TryGetMapScreen(IScreenContext? currentScreen, RunState? runState, out NMapScreen? mapScreen)
    {
        mapScreen = currentScreen as NMapScreen ?? NMapScreen.Instance;
        if (runState == null || currentScreen is not (NMapScreen or NMapRoom))
        {
            return false;
        }

        if (mapScreen == null || !GodotObject.IsInstanceValid(mapScreen))
        {
            return false;
        }

        return mapScreen.IsVisibleInTree() && mapScreen.IsOpen;
    }

    internal static List<T> FindDescendants<T>(Node root) where T : Node
    {
        var found = new List<T>();
        FindDescendantsRecursive(root, found);
        return found;
    }

    private static IReadOnlyList<NGridCardHolder> GetVisibleGridCardHolders(Node root)
    {
        return FindDescendants<NGridCardHolder>(root)
            .Where(node => GodotObject.IsInstanceValid(node) && node.IsVisibleInTree() && node.CardModel != null)
            .OrderBy(node => node.GlobalPosition.Y)
            .ThenBy(node => node.GlobalPosition.X)
            .ToArray();
    }

    private static void FindDescendantsRecursive<T>(Node node, List<T> found) where T : Node
    {
        if (!GodotObject.IsInstanceValid(node))
        {
            return;
        }

        if (node is T typedNode)
        {
            found.Add(typedNode);
        }

        foreach (Node child in node.GetChildren())
        {
            FindDescendantsRecursive(child, found);
        }
    }

    private static bool CanAdjustAscension(IScreenContext? currentScreen, int delta)
    {
        var screen = GetCharacterSelectScreen(currentScreen);
        if (screen == null)
        {
            return false;
        }

        var lobby = screen.Lobby;
        if (lobby.NetService.Type == NetGameType.Client || lobby.LocalPlayer.isReady)
        {
            return false;
        }

        var nextAscension = lobby.Ascension + delta;
        return nextAscension >= 0 && nextAscension <= lobby.MaxAscension;
    }

    private static IReadOnlyCollection<ulong> GetConnectedPlayerIds(RunState? runState)
    {
        if (runState == null)
        {
            return Array.Empty<ulong>();
        }

        var connectedPlayerIds = RunManager.Instance.RunLobby?.PlayerIds?.ToArray();
        if (connectedPlayerIds != null && connectedPlayerIds.Length > 0)
        {
            return connectedPlayerIds;
        }

        return runState.Players.Select(player => player.NetId).ToArray();
    }

    private static string NetIdToString(ulong netId)
    {
        return netId.ToString();
    }
}
