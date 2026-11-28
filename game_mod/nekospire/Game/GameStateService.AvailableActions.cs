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

// AvailableActions implementation; shares the service API and state across partial files.
internal static partial class GameStateService
{
    public static AvailableActionsPayload BuildAvailableActionsPayload()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var combatState = CombatManager.Instance.DebugOnlyGetState();
        var runState = RunManager.Instance.DebugOnlyGetState();
        var descriptors = new List<ActionDescriptor>();

        if (GetOpenModal() != null)
        {
            if (CanConfirmModal(currentScreen))
            {
                descriptors.Add(new ActionDescriptor
                {
                    name = "confirm_modal",
                    requires_target = false,
                    requires_index = false
                });
            }

            if (CanDismissModal(currentScreen))
            {
                descriptors.Add(new ActionDescriptor
                {
                    name = "dismiss_modal",
                    requires_target = false,
                    requires_index = false
                });
            }

            return new AvailableActionsPayload
            {
                screen = ResolveScreen(currentScreen),
                actions = descriptors.ToArray()
            };
        }

        if (CanEndTurn(currentScreen, combatState))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "end_turn",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanPlayAnyCard(currentScreen, combatState))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "play_card",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanContinueRun(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "continue_run",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanAbandonRun(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "abandon_run",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanSaveAndQuit(currentScreen, runState))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "save_and_quit",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanOpenCharacterSelect(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "open_character_select",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanOpenTimeline(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "open_timeline",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanCloseMainMenuSubmenu(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "close_main_menu_submenu",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanChooseTimelineEpoch(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "choose_timeline_epoch",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanConfirmTimelineOverlay(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "confirm_timeline_overlay",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanChooseMapNode(currentScreen, runState))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "choose_map_node",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanCollectRewardsAndProceed(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "resolve_rewards",
                requires_target = false,
                requires_index = true
            });

            descriptors.Add(new ActionDescriptor
            {
                name = "collect_rewards_and_proceed",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanClaimReward(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "claim_reward",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanChooseRewardCard(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "choose_reward_card",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanSkipRewardCards(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "skip_reward_cards",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanSelectDeckCard(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "select_deck_card",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanCloseCardsView(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "close_cards_view",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanConfirmSelection(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "confirm_selection",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanProceed(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "proceed",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanOpenChest(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "open_chest",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanChooseTreasureRelic(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "choose_treasure_relic",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanChooseEventOption(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "choose_event_option",
                requires_target = false,
                requires_index = true
            });
        }

        // 水晶球"占卜"子屏：屏名是 EVENT，但不是 NEventRoom，choose_event_option 不管用。
        // 单独给一个揭格子的动作，否则这一屏可用动作会退化成只剩 save_and_quit。
        var crystalSphereHere = BuildCrystalSpherePayload(currentScreen);
        if (crystalSphereHere != null && crystalSphereHere.active && !crystalSphereHere.is_finished)
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "divine_crystal_sphere",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanChooseCapstoneOption(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "choose_capstone_option",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanChooseBundle(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "choose_bundle",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanConfirmBundle(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "confirm_bundle",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanChooseRestOption(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "choose_rest_option",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanOpenShopInventory(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "open_shop_inventory",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanCloseShopInventory(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "close_shop_inventory",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanBuyShopCard(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "buy_card",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanBuyShopRelic(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "buy_relic",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanBuyShopPotion(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "buy_potion",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanRemoveCardAtShop(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "remove_card_at_shop",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanSelectCharacter(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "select_character",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanEmbark(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "embark",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanUnready(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "unready",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanHostMultiplayerLobby(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "host_multiplayer_lobby",
                requires_target = false,
                requires_index = false
            });
        }

        // Production co-op path (same-machine ENet): open_multiplayer_menu -> start_multiplayer_host /
        // join_multiplayer_direct, the same three actions NekoAutoplayDriver.DecideMainMenu uses. Keep these
        // in sync with BuildAvailableActionNames below: this endpoint used to advertise only the debug
        // "multiplayer test" scene actions (host/join/ready_multiplayer_lobby) and omit these three, so any
        // client reading /actions/available could not see the path that actually works.
        if (CanOpenMultiplayerMenu(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "open_multiplayer_menu",
                requires_target = false,
                requires_index = false
            });
        }

        if (IsMultiplayerSubmenu(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "start_multiplayer_host",
                requires_target = false,
                requires_index = false
            });

            descriptors.Add(new ActionDescriptor
            {
                name = "join_multiplayer_direct",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanStartCoopSession(currentScreen))
        {
            descriptors.Add(new ActionDescriptor { name = "continue_coop_session", requires_target = false, requires_index = false });
            descriptors.Add(new ActionDescriptor
            {
                name = "start_coop_session",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanJoinMultiplayerLobby(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "join_multiplayer_lobby",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanReadyMultiplayerLobby(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "ready_multiplayer_lobby",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanDisconnectMultiplayerLobby(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "disconnect_multiplayer_lobby",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanIncreaseAscension(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "increase_ascension",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanDecreaseAscension(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "decrease_ascension",
                requires_target = false,
                requires_index = false
            });
        }

        if (CanUsePotion(currentScreen, combatState, runState))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "use_potion",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanDiscardPotion(currentScreen, runState))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "discard_potion",
                requires_target = false,
                requires_index = true
            });
        }

        if (CanReturnToMainMenu(currentScreen))
        {
            descriptors.Add(new ActionDescriptor
            {
                name = "return_to_main_menu",
                requires_target = false,
                requires_index = false
            });
        }

        return new AvailableActionsPayload
        {
            screen = ResolveScreen(currentScreen),
            actions = descriptors.ToArray()
        };
    }

    private static string[] BuildAvailableActionNames(
        IScreenContext? currentScreen,
        CombatState? combatState,
        RunState? runState,
        CrystalSpherePayload? crystalSphere = null)
    {
        var names = new List<string>();

        if (GetOpenModal() != null)
        {
            if (CanConfirmModal(currentScreen))
            {
                names.Add("confirm_modal");
            }

            if (CanDismissModal(currentScreen))
            {
                names.Add("dismiss_modal");
            }

            return names.ToArray();
        }

        if (CanEndTurn(currentScreen, combatState))
        {
            names.Add("end_turn");
        }

        if (CanPlayAnyCard(currentScreen, combatState))
        {
            names.Add("play_card");
        }

        if (CanContinueRun(currentScreen))
        {
            names.Add("continue_run");
        }

        if (CanAbandonRun(currentScreen))
        {
            names.Add("abandon_run");
        }

        if (CanSaveAndQuit(currentScreen, runState))
        {
            names.Add("save_and_quit");
        }

        if (CanOpenCharacterSelect(currentScreen))
        {
            names.Add("open_character_select");
        }

        if (CanOpenTimeline(currentScreen))
        {
            names.Add("open_timeline");
        }

        if (CanCloseMainMenuSubmenu(currentScreen))
        {
            names.Add("close_main_menu_submenu");
        }

        if (CanChooseTimelineEpoch(currentScreen))
        {
            names.Add("choose_timeline_epoch");
        }

        if (CanConfirmTimelineOverlay(currentScreen))
        {
            names.Add("confirm_timeline_overlay");
        }

        if (CanChooseMapNode(currentScreen, runState))
        {
            names.Add("choose_map_node");
        }

        if (CanCollectRewardsAndProceed(currentScreen))
        {
            names.Add("resolve_rewards");
            names.Add("collect_rewards_and_proceed");
        }

        if (CanClaimReward(currentScreen))
        {
            names.Add("claim_reward");
        }

        if (CanChooseRewardCard(currentScreen))
        {
            names.Add("choose_reward_card");
        }

        if (CanSkipRewardCards(currentScreen))
        {
            names.Add("skip_reward_cards");
        }

        if (CanSelectDeckCard(currentScreen))
        {
            names.Add("select_deck_card");
        }

        if (CanCloseCardsView(currentScreen))
        {
            names.Add("close_cards_view");
        }

        if (CanConfirmSelection(currentScreen))
        {
            names.Add("confirm_selection");
        }

        if (CanProceed(currentScreen))
        {
            names.Add("proceed");
        }

        if (CanOpenChest(currentScreen))
        {
            names.Add("open_chest");
        }

        if (CanChooseTreasureRelic(currentScreen))
        {
            names.Add("choose_treasure_relic");
        }

        if (CanChooseEventOption(currentScreen))
        {
            names.Add("choose_event_option");
        }

        // 水晶球"占卜"子屏：屏名是 EVENT 但不是 NEventRoom，choose_event_option 不管用。
        if (crystalSphere != null && crystalSphere.active && !crystalSphere.is_finished)
        {
            names.Add("divine_crystal_sphere");
        }

        if (CanChooseCapstoneOption(currentScreen))
        {
            names.Add("choose_capstone_option");
        }

        if (CanChooseBundle(currentScreen))
        {
            names.Add("choose_bundle");
        }

        if (CanConfirmBundle(currentScreen))
        {
            names.Add("confirm_bundle");
        }

        if (CanChooseRestOption(currentScreen))
        {
            names.Add("choose_rest_option");
        }

        if (CanOpenShopInventory(currentScreen))
        {
            names.Add("open_shop_inventory");
        }

        if (CanCloseShopInventory(currentScreen))
        {
            names.Add("close_shop_inventory");
        }

        if (CanBuyShopCard(currentScreen))
        {
            names.Add("buy_card");
        }

        if (CanBuyShopRelic(currentScreen))
        {
            names.Add("buy_relic");
        }

        if (CanBuyShopPotion(currentScreen))
        {
            names.Add("buy_potion");
        }

        if (CanRemoveCardAtShop(currentScreen))
        {
            names.Add("remove_card_at_shop");
        }

        if (CanSelectCharacter(currentScreen))
        {
            names.Add("select_character");
        }

        if (CanEmbark(currentScreen))
        {
            names.Add("embark");
        }

        if (CanUnready(currentScreen))
        {
            names.Add("unready");
        }

        if (CanHostMultiplayerLobby(currentScreen))
        {
            names.Add("host_multiplayer_lobby");
        }

        if (CanOpenMultiplayerMenu(currentScreen))
        {
            names.Add("open_multiplayer_menu");
        }

        if (IsMultiplayerSubmenu(currentScreen))
        {
            names.Add("start_multiplayer_host");
            names.Add("join_multiplayer_direct");
        }

        if (CanStartCoopSession(currentScreen))
        {
            names.Add("start_coop_session");
            names.Add("continue_coop_session");
        }

        if (CanJoinMultiplayerLobby(currentScreen))
        {
            names.Add("join_multiplayer_lobby");
        }

        if (CanReadyMultiplayerLobby(currentScreen))
        {
            names.Add("ready_multiplayer_lobby");
        }

        if (CanDisconnectMultiplayerLobby(currentScreen))
        {
            names.Add("disconnect_multiplayer_lobby");
        }

        if (CanIncreaseAscension(currentScreen))
        {
            names.Add("increase_ascension");
        }

        if (CanDecreaseAscension(currentScreen))
        {
            names.Add("decrease_ascension");
        }

        if (CanUsePotion(currentScreen, combatState, runState))
        {
            names.Add("use_potion");
        }

        if (CanDiscardPotion(currentScreen, runState))
        {
            names.Add("discard_potion");
        }

        if (CanReturnToMainMenu(currentScreen))
        {
            names.Add("return_to_main_menu");
        }

        return names.ToArray();
    }

}
