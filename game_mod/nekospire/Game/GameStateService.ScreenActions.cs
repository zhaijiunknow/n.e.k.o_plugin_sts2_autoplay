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

// ScreenActions implementation; shares the service API and state across partial files.
internal static partial class GameStateService
{
    public static bool CanChooseMapNode(IScreenContext? currentScreen, RunState? runState)
    {
        return GetAvailableMapNodes(currentScreen, runState).Count > 0;
    }

    public static bool CanCollectRewardsAndProceed(IScreenContext? currentScreen)
    {
        return currentScreen is NRewardsScreen || currentScreen is NCardRewardSelectionScreen;
    }

    public static bool CanClaimReward(IScreenContext? currentScreen)
    {
        return GetRewardButtons(currentScreen).Any(button => button.IsEnabled);
    }

    public static bool CanChooseRewardCard(IScreenContext? currentScreen)
    {
        return GetCardRewardOptions(currentScreen).Count > 0;
    }

    public static bool CanSkipRewardCards(IScreenContext? currentScreen)
    {
        return GetCardRewardAlternativeButtons(currentScreen).Count > 0;
    }

    public static bool CanSelectDeckCard(IScreenContext? currentScreen)
    {
        return GetDeckSelectionOptions(currentScreen).Count > 0;
    }

    public static bool CanCloseCardsView(IScreenContext? currentScreen)
    {
        return GetCardsViewBackButton(currentScreen) != null;
    }

    public static bool CanConfirmSelection(IScreenContext? currentScreen)
    {
        return TryGetCombatHandSelectionMetadata(currentScreen, out _, out var metadata) &&
            metadata.RequiresConfirmation &&
            metadata.CanConfirm;
    }

    public static bool CanProceed(IScreenContext? currentScreen)
    {
        if (currentScreen is NRewardsScreen or NCardRewardSelectionScreen)
        {
            return false;
        }

        return GetProceedButton(currentScreen) != null;
    }

    public static bool CanOpenChest(IScreenContext? currentScreen)
    {
        if (currentScreen is not NTreasureRoom treasureRoom)
        {
            return false;
        }

        var chestButton = treasureRoom.GetNodeOrNull<NButton>("%Chest");
        return chestButton != null && GodotObject.IsInstanceValid(chestButton) && chestButton.IsEnabled;
    }

    public static bool CanChooseTreasureRelic(IScreenContext? currentScreen)
    {
        if (GetTreasureRelicCollection(currentScreen) == null)
        {
            return false;
        }

        var relics = RunManager.Instance.TreasureRoomRelicSynchronizer.CurrentRelics;
        return relics != null && relics.Count > 0;
    }

    public static NTreasureRoomRelicCollection? GetTreasureRelicCollection(IScreenContext? currentScreen)
    {
        if (currentScreen is NTreasureRoomRelicCollection relicCollection)
        {
            return relicCollection;
        }

        if (currentScreen is NTreasureRoom treasureRoom)
        {
            var nestedCollection = treasureRoom.GetNodeOrNull<NTreasureRoomRelicCollection>("%RelicCollection");
            if (nestedCollection != null &&
                GodotObject.IsInstanceValid(nestedCollection) &&
                nestedCollection.Visible)
            {
                return nestedCollection;
            }
        }

        return null;
    }

    public static bool CanChooseEventOption(IScreenContext? currentScreen)
    {
        // ★ 与 BuildEventPayload 共用 IsEventScreen —— 两处必须同源（见该函数注释）。
        if (!IsEventScreen(currentScreen))
        {
            return false;
        }

        try
        {
            var eventModel = RunManager.Instance.EventSynchronizer.GetLocalEvent();
            if (eventModel == null)
            {
                return false;
            }

            // Finished events have a synthetic proceed option
            if (eventModel.IsFinished)
            {
                return true;
            }

            // Non-finished events need at least one non-locked option
            return eventModel.CurrentOptions.Any(o => !o.IsLocked);
        }
        catch
        {
            return false;
        }
    }

    public static bool CanChooseCapstoneOption(IScreenContext? currentScreen)
    {
        return GetCapstoneButtons(currentScreen).Count > 0;
    }

    public static IReadOnlyList<NButton> GetCapstoneButtons(IScreenContext? currentScreen)
    {
        if (currentScreen is not NCapstoneSubmenuStack capstoneScreen)
        {
            return Array.Empty<NButton>();
        }

        return FindDescendants<NButton>((Node)capstoneScreen)
            .Where(b => GodotObject.IsInstanceValid(b) && b.IsVisibleInTree() && b.IsEnabled)
            .ToArray();
    }

    public static bool CanChooseBundle(IScreenContext? currentScreen)
    {
        return GetBundleOptions(currentScreen).Count > 0;
    }

    public static bool CanConfirmBundle(IScreenContext? currentScreen)
    {
        return GetBundleConfirmButtons(currentScreen).Count > 0;
    }

    public static IReadOnlyList<NButton> GetBundleConfirmButtons(IScreenContext? currentScreen)
    {
        if (currentScreen is not NChooseABundleSelectionScreen bundleScreen)
        {
            return Array.Empty<NButton>();
        }

        // Look specifically for NConfirmButton or button named "Confirm"
        return FindDescendants<NButton>((Node)bundleScreen)
            .Where(b => GodotObject.IsInstanceValid(b) && b.IsVisibleInTree() && b.IsEnabled
                && (b.GetType().Name == "NConfirmButton" || b.Name == "Confirm"))
            .ToArray();
    }

    public static IReadOnlyList<Control> GetBundleOptions(IScreenContext? currentScreen)
    {
        if (currentScreen is not NChooseABundleSelectionScreen bundleScreen)
        {
            return Array.Empty<Control>();
        }

        // NCardBundle nodes represent the selectable card packs
        return FindDescendants<Control>((Node)bundleScreen)
            .Where(n => GodotObject.IsInstanceValid(n) && n.IsVisibleInTree() && n.GetType().Name == "NCardBundle")
            .ToArray();
    }

    public static bool CanChooseRestOption(IScreenContext? currentScreen)
    {
        if (currentScreen is not NRestSiteRoom)
        {
            return false;
        }

        try
        {
            var options = RunManager.Instance.RestSiteSynchronizer.GetLocalOptions();
            return options != null && options.Any(o => o.IsEnabled);
        }
        catch
        {
            return false;
        }
    }

    public static bool CanOpenShopInventory(IScreenContext? currentScreen)
    {
        var room = GetMerchantRoom(currentScreen);
        return room != null && room.Inventory != null && !room.Inventory.IsOpen && currentScreen is NMerchantRoom;
    }

    public static bool CanCloseShopInventory(IScreenContext? currentScreen)
    {
        return currentScreen is NMerchantInventory inventory && inventory.IsOpen;
    }

    public static bool CanBuyShopCard(IScreenContext? currentScreen)
    {
        var inventoryScreen = GetMerchantInventoryScreen(currentScreen);
        return inventoryScreen != null && inventoryScreen.IsOpen &&
            GetMerchantCardEntries(currentScreen).Any(entry => entry.IsStocked && entry.EnoughGold);
    }

    public static bool CanBuyShopRelic(IScreenContext? currentScreen)
    {
        var inventoryScreen = GetMerchantInventoryScreen(currentScreen);
        return inventoryScreen != null && inventoryScreen.IsOpen &&
            GetMerchantRelicEntries(currentScreen).Any(entry => entry.IsStocked && entry.EnoughGold);
    }

    public static bool CanBuyShopPotion(IScreenContext? currentScreen)
    {
        var inventoryScreen = GetMerchantInventoryScreen(currentScreen);
        var inventory = GetMerchantInventory(currentScreen);
        return inventoryScreen != null && inventoryScreen.IsOpen &&
            GetMerchantPotionEntries(currentScreen).Any(entry => CanPurchaseShopPotion(inventory?.Player, entry));
    }

    public static bool CanRemoveCardAtShop(IScreenContext? currentScreen)
    {
        var inventoryScreen = GetMerchantInventoryScreen(currentScreen);
        var entry = GetMerchantCardRemovalEntry(currentScreen);
        return inventoryScreen != null && inventoryScreen.IsOpen &&
            entry?.IsStocked == true && entry.EnoughGold;
    }

    public static bool CanSelectCharacter(IScreenContext? currentScreen)
    {
        var multiplayerTestScene = GetMultiplayerTestScene();
        if (multiplayerTestScene != null)
        {
            return GetMultiplayerTestLobby(multiplayerTestScene) != null && GetMultiplayerLobbyCharacters().Length > 0;
        }

        return GetCharacterSelectButtons(currentScreen)
            .Any(button => !button.IsLocked && button.IsEnabled && button.IsVisibleInTree());
    }

    public static bool CanContinueRun(IScreenContext? currentScreen)
    {
        if (currentScreen is not NMainMenu mainMenu || !mainMenu.IsVisibleInTree())
        {
            return false;
        }

        if (mainMenu.SubmenuStack?.SubmenusOpen == true)
        {
            return false;
        }

        var continueButton = GetMainMenuContinueButton(mainMenu);
        return continueButton != null && continueButton.IsVisibleInTree() && continueButton.IsEnabled;
    }

    public static bool CanAbandonRun(IScreenContext? currentScreen)
    {
        if (currentScreen is not NMainMenu mainMenu || !mainMenu.IsVisibleInTree())
        {
            return false;
        }

        if (mainMenu.SubmenuStack?.SubmenusOpen == true)
        {
            return false;
        }

        var abandonButton = GetMainMenuAbandonRunButton(mainMenu);
        return abandonButton != null && abandonButton.IsVisibleInTree() && abandonButton.IsEnabled;
    }

    public static bool CanSaveAndQuit(IScreenContext? currentScreen, RunState? runState)
    {
        if (currentScreen == null || runState == null)
        {
            return false;
        }

        if (NGame.Instance == null || !GodotObject.IsInstanceValid(NGame.Instance))
        {
            return false;
        }

        if (RunManager.Instance.NetService.Type.IsMultiplayer())
        {
            return false;
        }

        return currentScreen is not (NMainMenu or NGameOverScreen or NCharacterSelectScreen or NMultiplayerTest);
    }

    public static bool CanOpenCharacterSelect(IScreenContext? currentScreen)
    {
        if (currentScreen is NSingleplayerSubmenu singleplayerSubmenu && singleplayerSubmenu.IsVisibleInTree())
        {
            return true;
        }

        if (currentScreen is not NMainMenu mainMenu || !mainMenu.IsVisibleInTree())
        {
            return false;
        }

        if (mainMenu.SubmenuStack?.SubmenusOpen == true)
        {
            return false;
        }

        var singleplayerButton = GetMainMenuSingleplayerButton(mainMenu);
        if (singleplayerButton != null && singleplayerButton.IsVisibleInTree() && singleplayerButton.IsEnabled)
        {
            return true;
        }

        // Some main-menu states still allow the singleplayer submenu to open even when the
        // button has not become visible in the scene tree. If there is no active run flow to
        // continue or abandon, prefer exposing character select instead of hard-blocking.
        return !CanContinueRun(currentScreen) && !CanAbandonRun(currentScreen);
    }

    public static bool CanOpenTimeline(IScreenContext? currentScreen)
    {
        if (currentScreen is not NMainMenu mainMenu || !mainMenu.IsVisibleInTree())
        {
            return false;
        }

        if (mainMenu.SubmenuStack?.SubmenusOpen == true)
        {
            return false;
        }

        var timelineButton = GetMainMenuTimelineButton(mainMenu);
        return timelineButton != null && timelineButton.IsVisibleInTree() && timelineButton.IsEnabled;
    }

    public static bool CanCloseMainMenuSubmenu(IScreenContext? currentScreen)
    {
        if (currentScreen is not NSubmenu submenu || !submenu.IsVisibleInTree())
        {
            return false;
        }

        var submenuStack = GetMainMenuSubmenuStack(submenu);
        return submenuStack != null && submenuStack.SubmenusOpen;
    }

    public static bool CanEmbark(IScreenContext? currentScreen)
    {
        var embarkButton = GetCharacterEmbarkButton(currentScreen);
        return embarkButton != null && embarkButton.IsEnabled && embarkButton.IsVisibleInTree();
    }

    public static bool CanUnready(IScreenContext? currentScreen)
    {
        var multiplayerTestScene = GetMultiplayerTestScene();
        var multiplayerLobby = multiplayerTestScene != null ? GetMultiplayerTestLobby(multiplayerTestScene) : null;
        if (multiplayerLobby != null)
        {
            return multiplayerLobby.LocalPlayer.isReady;
        }

        var unreadyButton = GetCharacterUnreadyButton(currentScreen);
        return unreadyButton != null && unreadyButton.IsEnabled && unreadyButton.IsVisibleInTree();
    }

    public static bool CanHostMultiplayerLobby(IScreenContext? currentScreen)
    {
        var scene = GetMultiplayerTestScene();
        return scene != null && GetMultiplayerTestLobby(scene) == null;
    }

    public static bool CanJoinMultiplayerLobby(IScreenContext? currentScreen)
    {
        var scene = GetMultiplayerTestScene();
        return scene != null && GetMultiplayerTestLobby(scene) == null;
    }

    /// <summary>
    /// R2 real menu: whether the live multiplayer submenu can be opened from the main menu.
    /// </summary>
    public static bool CanOpenMultiplayerMenu(IScreenContext? currentScreen)
    {
        return currentScreen is NMainMenu;
    }

    /// <summary>
    /// Host-side one-shot co-op bootstrap (GameActionService.ExecuteStartCoopSessionAsync). Unlike
    /// open_multiplayer_menu it pops back to the main menu itself, so it is offered anywhere around the main
    /// menu, including from inside one of its submenus.
    /// </summary>
    public static bool CanStartCoopSession(IScreenContext? currentScreen)
    {
        return currentScreen is NMainMenu or NSubmenu;
    }

    public static bool IsMultiplayerSubmenu(IScreenContext? currentScreen)
    {
        return currentScreen is NMultiplayerSubmenu;
    }

    public static bool CanReadyMultiplayerLobby(IScreenContext? currentScreen)
    {
        var scene = GetMultiplayerTestScene();
        var lobby = scene != null ? GetMultiplayerTestLobby(scene) : null;
        return lobby != null && !lobby.LocalPlayer.isReady;
    }

    public static bool CanDisconnectMultiplayerLobby(IScreenContext? currentScreen)
    {
        var scene = GetMultiplayerTestScene();
        return scene != null && GetMultiplayerTestLobby(scene) != null;
    }

    public static bool CanIncreaseAscension(IScreenContext? currentScreen)
    {
        return CanAdjustAscension(currentScreen, delta: 1);
    }

    public static bool CanDecreaseAscension(IScreenContext? currentScreen)
    {
        return CanAdjustAscension(currentScreen, delta: -1);
    }

    public static bool CanChooseTimelineEpoch(IScreenContext? currentScreen)
    {
        return GetTimelineSlots(currentScreen).Any(slot => slot.State is EpochSlotState.Obtained or EpochSlotState.Complete);
    }

    public static bool CanConfirmTimelineOverlay(IScreenContext? currentScreen)
    {
        var unlockConfirmButton = GetTimelineUnlockConfirmButton(currentScreen);
        if (unlockConfirmButton != null && unlockConfirmButton.IsVisibleInTree() && unlockConfirmButton.IsEnabled)
        {
            return true;
        }

        var inspectCloseButton = GetTimelineInspectCloseButton(currentScreen);
        return inspectCloseButton != null && inspectCloseButton.IsVisibleInTree() && inspectCloseButton.IsEnabled;
    }

    public static bool CanUsePotion(IScreenContext? currentScreen, CombatState? combatState, RunState? runState)
    {
        var player = GetLocalPlayer(runState);
        if (player == null)
        {
            return false;
        }

        return player.PotionSlots.Any(potion => IsPotionUsable(currentScreen, combatState, player, potion));
    }

    public static bool CanUsePotionAtIndex(IScreenContext? currentScreen, CombatState? combatState, RunState? runState, int optionIndex)
    {
        var player = GetLocalPlayer(runState);
        if (player == null || optionIndex < 0 || optionIndex >= player.PotionSlots.Count)
        {
            return false;
        }

        return IsPotionUsable(currentScreen, combatState, player, player.PotionSlots[optionIndex]);
    }

    public static bool CanDiscardPotion(IScreenContext? currentScreen, RunState? runState)
    {
        var player = GetLocalPlayer(runState);
        if (player == null || !CanDiscardPotionsInCurrentScreen(currentScreen))
        {
            return false;
        }

        return player.PotionSlots.Any(potion => IsPotionDiscardable(player, potion));
    }

    public static bool CanDiscardPotionAtIndex(IScreenContext? currentScreen, RunState? runState, int optionIndex)
    {
        var player = GetLocalPlayer(runState);
        if (player == null || !CanDiscardPotionsInCurrentScreen(currentScreen) || optionIndex < 0 || optionIndex >= player.PotionSlots.Count)
        {
            return false;
        }

        return IsPotionDiscardable(player, player.PotionSlots[optionIndex]);
    }

    public static bool CanConfirmModal(IScreenContext? currentScreen)
    {
        return GetModalConfirmButton(currentScreen) != null;
    }

    public static bool CanDismissModal(IScreenContext? currentScreen)
    {
        return GetModalCancelButton(currentScreen) != null;
    }

    public static bool CanReturnToMainMenu(IScreenContext? currentScreen)
    {
        return currentScreen is NGameOverScreen;
    }

    public static IReadOnlyList<NMapPoint> GetAvailableMapNodes(IScreenContext? currentScreen, RunState? runState)
    {
        if (!TryGetMapScreen(currentScreen, runState, out var mapScreen))
        {
            return Array.Empty<NMapPoint>();
        }

        return FindDescendants<NMapPoint>(mapScreen!)
            .Where(node => GodotObject.IsInstanceValid(node) && node.IsEnabled)
            .OrderBy(node => node.Point.coord.row)
            .ThenBy(node => node.Point.coord.col)
            .ToArray();
    }

    public static IReadOnlyList<NRewardButton> GetRewardButtons(IScreenContext? currentScreen)
    {
        if (currentScreen is not NRewardsScreen rewardScreen)
        {
            return Array.Empty<NRewardButton>();
        }

        return FindDescendants<NRewardButton>(rewardScreen)
            .Where(node => GodotObject.IsInstanceValid(node))
            .OrderBy(node => node.GlobalPosition.Y)
            .ThenBy(node => node.GlobalPosition.X)
            .ToArray();
    }

    public static NProceedButton? GetRewardProceedButton(IScreenContext? currentScreen)
    {
        if (currentScreen is not NRewardsScreen rewardScreen)
        {
            return null;
        }

        return FindDescendants<NProceedButton>(rewardScreen)
            .FirstOrDefault(node => GodotObject.IsInstanceValid(node));
    }

    public static IReadOnlyList<NCardHolder> GetCardRewardOptions(IScreenContext? currentScreen)
    {
        if (currentScreen is not NCardRewardSelectionScreen cardRewardScreen)
        {
            return Array.Empty<NCardHolder>();
        }

        return FindDescendants<NCardHolder>(cardRewardScreen)
            .Where(node => GodotObject.IsInstanceValid(node) && node.CardModel != null)
            .OrderBy(node => node.GlobalPosition.Y)
            .ThenBy(node => node.GlobalPosition.X)
            .ToArray();
    }

    public static IReadOnlyList<NCardRewardAlternativeButton> GetCardRewardAlternativeButtons(IScreenContext? currentScreen)
    {
        if (currentScreen is not NCardRewardSelectionScreen cardRewardScreen)
        {
            return Array.Empty<NCardRewardAlternativeButton>();
        }

        return FindDescendants<NCardRewardAlternativeButton>(cardRewardScreen)
            .Where(node => GodotObject.IsInstanceValid(node) && node.IsVisibleInTree())
            .OrderBy(node => node.GlobalPosition.Y)
            .ThenBy(node => node.GlobalPosition.X)
            .ToArray();
    }

    public static IReadOnlyList<NCardHolder> GetDeckSelectionOptions(IScreenContext? currentScreen)
    {
        if (currentScreen is NCardsViewScreen)
        {
            return Array.Empty<NCardHolder>();
        }

        if (currentScreen is NCardGridSelectionScreen cardSelectScreen)
        {
            return GetVisibleGridCardHolders(cardSelectScreen)
                .Cast<NCardHolder>()
                .ToArray();
        }

        if (currentScreen is NChooseACardSelectionScreen chooseCardScreen)
        {
            return GetVisibleGridCardHolders(chooseCardScreen)
                .Cast<NCardHolder>()
                .ToArray();
        }

        if (TryGetCombatHandSelection(currentScreen, out var hand))
        {
            return hand!.ActiveHolders
                .Where(node => GodotObject.IsInstanceValid(node) && node.Visible && node.CardModel != null)
                .OrderBy(node => node.GetIndex())
                .Cast<NCardHolder>()
                .ToArray();
        }

        if (currentScreen is Node rootNode)
        {
            return GetVisibleGridCardHolders(rootNode)
                .Cast<NCardHolder>()
                .ToArray();
        }

        return Array.Empty<NCardHolder>();
    }

    public static string? GetDeckSelectionPrompt(IScreenContext? currentScreen)
    {
        if (currentScreen is NCardsViewScreen)
        {
            return null;
        }

        if (currentScreen is NCardGridSelectionScreen cardSelectScreen)
        {
            return cardSelectScreen.GetNodeOrNull<MegaRichTextLabel>("%BottomLabel")?.Text;
        }

        if (currentScreen is NChooseACardSelectionScreen chooseCardScreen)
        {
            return SafeReadString(() => chooseCardScreen.GetNodeOrNull<NCommonBanner>("Banner")?.label.Text);
        }

        if (TryGetCombatHandSelection(currentScreen, out var hand))
        {
            return SafeReadString(() => hand!.GetNodeOrNull<MegaRichTextLabel>("%SelectionHeader")?.Text);
        }

        if (currentScreen is Node rootNode)
        {
            return SafeReadString(() =>
                rootNode.GetNodeOrNull<MegaRichTextLabel>("%BottomLabel")?.Text ??
                FindDescendants<MegaRichTextLabel>(rootNode)
                    .FirstOrDefault(label => label.IsVisibleInTree() && !string.IsNullOrWhiteSpace(label.Text))?.Text);
        }

        return null;
    }

    public static bool TryGetCombatHandSelection(IScreenContext? currentScreen, out NPlayerHand? hand)
    {
        hand = null;

        if (currentScreen is not NCombatRoom combatRoom)
        {
            return false;
        }

        hand = combatRoom.Ui?.Hand;
        return hand != null &&
            GodotObject.IsInstanceValid(hand) &&
            hand.IsInCardSelection &&
            hand.CurrentMode is NPlayerHand.Mode.SimpleSelect or NPlayerHand.Mode.UpgradeSelect;
    }

    private static CardSelectorPrefs? TryGetCombatHandSelectionPrefs(NPlayerHand hand)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var field = typeof(NPlayerHand).GetField("_prefs", flags);
        if (field?.GetValue(hand) is CardSelectorPrefs prefs)
        {
            return prefs;
        }

        return null;
    }

    public static bool TryGetCombatHandSelectionMetadata(
        IScreenContext? currentScreen,
        out NPlayerHand? hand,
        out CombatHandSelectionMetadata metadata)
    {
        metadata = default;
        if (!TryGetCombatHandSelection(currentScreen, out hand) || hand == null)
        {
            return false;
        }

        var prefs = TryGetCombatHandSelectionPrefs(hand);
        var requiresConfirmation = prefs?.RequireManualConfirmation ?? false;
        var canConfirm = requiresConfirmation &&
            TryGetCombatHandConfirmButton(hand, out var confirmButton) &&
            confirmButton!.Visible &&
            confirmButton.IsEnabled;

        metadata = new CombatHandSelectionMetadata(
            prefs?.MinSelect ?? 1,
            prefs?.MaxSelect ?? 1,
            GetCombatHandSelectedCount(hand),
            requiresConfirmation,
            canConfirm);
        return true;
    }

    private static int GetCombatHandSelectedCount(NPlayerHand hand)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var field = typeof(NPlayerHand).GetField("_selectedCards", flags);
        return field?.GetValue(hand) is System.Collections.ICollection collection ? collection.Count : 0;
    }

    private static bool TryGetCombatHandConfirmButton(NPlayerHand hand, out NConfirmButton? confirmButton)
    {
        confirmButton = hand.GetNodeOrNull<NConfirmButton>("%SelectModeConfirmButton")
            ?? hand.GetNodeOrNull<NConfirmButton>("SelectModeConfirmButton");
        return confirmButton != null && GodotObject.IsInstanceValid(confirmButton);
    }

}
