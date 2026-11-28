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

// Rewards implementation; shares the service API and state across partial files.
internal static partial class GameActionService
{
    private static async Task<ActionResponsePayload> ExecuteResolveRewardsAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanCollectRewardsAndProceed(currentScreen) &&
            currentScreen is not NCardRewardSelectionScreen)
        {
            throw new ApiException(409, "invalid_action", "Not on reward screen.", new
            {
                action = "resolve_rewards",
                screen
            });
        }

        // option_index: -1 = skip card, 0/1/2 = pick that card, absent = auto (first card)
        // card_index is accepted as a backwards-compatible alias for picking a card.
        if (request.option_index.HasValue)
        {
            if (request.option_index.Value == -1)
            {
                _pendingCardRewardChoice = -2;
                _cardRewardSkipped = true;
            }
            else
            {
                _pendingCardRewardChoice = request.option_index.Value;
                _cardRewardSkipped = false;
            }
        }
        else if (request.card_index.HasValue)
        {
            _pendingCardRewardChoice = request.card_index.Value;
            _cardRewardSkipped = false;
        }
        else
        {
            _pendingCardRewardChoice = -1;
            _cardRewardSkipped = false;
        }

        var stable = await DrainRewardFlowAsync(TimeSpan.FromSeconds(20));

        return new ActionResponsePayload
        {
            action = "resolve_rewards",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "All rewards resolved." : "Reward flow still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteCollectRewardsAndProceedAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanCollectRewardsAndProceed(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "collect_rewards_and_proceed",
                screen
            });
        }

        var stable = await DrainRewardFlowAsync(TimeSpan.FromSeconds(20));

        return new ActionResponsePayload
        {
            action = "collect_rewards_and_proceed",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Reward flow is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteClaimRewardAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanClaimReward(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "claim_reward",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "claim_reward requires option_index.", new
            {
                action = "claim_reward"
            });
        }

        var rewardButtons = GameStateService.GetRewardButtons(currentScreen);

        if (request.option_index < 0 || request.option_index >= rewardButtons.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "claim_reward",
                option_index = request.option_index,
                option_count = rewardButtons.Count
            });
        }

        var selectedReward = rewardButtons[request.option_index.Value];
        if (!selectedReward.IsEnabled)
        {
            throw new ApiException(409, "invalid_action", "The selected reward is not claimable in the current state.", new
            {
                action = "claim_reward",
                option_index = request.option_index
            });
        }

        var previousRewardCount = rewardButtons.Count(button => button.IsEnabled);
        selectedReward.ForceClick();
        var stable = await WaitForRewardButtonResolutionAsync(currentScreen, previousRewardCount, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "claim_reward",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteChooseRewardCardAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanChooseRewardCard(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "choose_reward_card",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "choose_reward_card requires option_index.", new
            {
                action = "choose_reward_card"
            });
        }

        var options = GameStateService.GetCardRewardOptions(currentScreen);
        if (request.option_index < 0 || request.option_index >= options.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "choose_reward_card",
                option_index = request.option_index,
                option_count = options.Count
            });
        }

        var selected = options[request.option_index.Value];
        var previousOptionCount = options.Count;
        selected.EmitSignal(NCardHolder.SignalName.Pressed, selected);
        _cardRewardSkipped = false; // Card was taken, clear any prior skip
        var stable = await WaitForRewardCardResolutionAsync(currentScreen, previousOptionCount, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "choose_reward_card",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteSkipRewardCardsAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanSkipRewardCards(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "skip_reward_cards",
                screen
            });
        }

        var alternatives = GameStateService.GetCardRewardAlternativeButtons(currentScreen);
        var selected = alternatives.First();
        selected.ForceClick();
        _cardRewardSkipped = true;
        var stable = await WaitForRewardCardResolutionAsync(currentScreen, GameStateService.GetCardRewardOptions(currentScreen).Count, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "skip_reward_cards",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteSelectDeckCardAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanSelectDeckCard(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "select_deck_card",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "select_deck_card requires option_index.", new
            {
                action = "select_deck_card"
            });
        }

        var options = GameStateService.GetDeckSelectionOptions(currentScreen);
        if (request.option_index < 0 || request.option_index >= options.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "select_deck_card",
                option_index = request.option_index,
                option_count = options.Count
            });
        }

        var isCombatHandSelection = GameStateService.TryGetCombatHandSelectionMetadata(currentScreen, out var combatHand, out var combatHandSelection);
        var selected = options[request.option_index.Value];
        if (isCombatHandSelection)
        {
            if (selected is not NHandCardHolder handHolder)
            {
                throw new ApiException(503, "state_unavailable", "Combat hand selection holder is unavailable.", new
                {
                    action = "select_deck_card",
                    screen
                }, retryable: true);
            }

            combatHand!.Call(
                combatHand.CurrentMode == NPlayerHand.Mode.UpgradeSelect
                    ? NPlayerHand.MethodName.SelectCardInUpgradeMode
                    : NPlayerHand.MethodName.SelectCardInSimpleMode,
                handHolder);
            combatHand.Call(NPlayerHand.MethodName.CheckIfSelectionComplete);
        }
        else
        {
            selected.EmitSignal(NCardHolder.SignalName.Pressed, selected);
        }

        var stable = currentScreen switch
        {
            NCardGridSelectionScreen cardSelectScreen => await ConfirmDeckSelectionAsync(cardSelectScreen, TimeSpan.FromSeconds(10)),
            NChooseACardSelectionScreen chooseCardScreen => await WaitForChooseCardSelectionResolutionAsync(chooseCardScreen, TimeSpan.FromSeconds(10)),
            _ when isCombatHandSelection => await WaitForCombatHandSelectionStepAsync(combatHandSelection, TimeSpan.FromSeconds(10)),
            _ => false
        };

        return new ActionResponsePayload
        {
            action = "select_deck_card",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteConfirmSelectionAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanConfirmSelection(currentScreen) ||
            !GameStateService.TryGetCombatHandSelection(currentScreen, out var combatHand) ||
            combatHand == null ||
            !TryGetCombatHandConfirmButton(combatHand, out var confirmButton) ||
            confirmButton == null)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "confirm_selection",
                screen
            });
        }

        confirmButton.ForceClick();
        var stable = await WaitForCombatHandSelectionResolutionAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "confirm_selection",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteCloseCardsViewAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanCloseCardsView(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "close_cards_view",
                screen
            });
        }

        var backButton = GameStateService.GetCardsViewBackButton(currentScreen)
            ?? throw new ApiException(503, "state_unavailable", "Cards view back button is unavailable.", new
            {
                action = "close_cards_view",
                screen
            }, retryable: true);

        backButton.ForceClick();
        var stable = await WaitForCardsViewCloseAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "close_cards_view",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<bool> WaitForChooseCardSelectionResolutionAsync(
        NChooseACardSelectionScreen selectionScreen,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is not NChooseACardSelectionScreen || !GodotObject.IsInstanceValid(selectionScreen))
            {
                return true;
            }
        }

        return ActiveScreenContext.Instance.GetCurrentScreen() is not NChooseACardSelectionScreen;
    }

    private static async Task<bool> WaitForCardsViewCloseAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (ActiveScreenContext.Instance.GetCurrentScreen() is not NCardsViewScreen)
            {
                return true;
            }
        }

        return ActiveScreenContext.Instance.GetCurrentScreen() is not NCardsViewScreen;
    }

    private static async Task<bool> WaitForCombatHandSelectionResolutionAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (!GameStateService.TryGetCombatHandSelection(currentScreen, out var currentHand) ||
                currentHand == null ||
                !GodotObject.IsInstanceValid(currentHand))
            {
                return true;
            }
        }

        return !GameStateService.TryGetCombatHandSelection(ActiveScreenContext.Instance.GetCurrentScreen(), out _);
    }

    private static async Task<bool> WaitForCombatHandSelectionStepAsync(
        CombatHandSelectionMetadata previousSelection,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (!GameStateService.TryGetCombatHandSelectionMetadata(currentScreen, out _, out var currentSelection))
            {
                return true;
            }

            if (currentSelection.SelectedCount != previousSelection.SelectedCount)
            {
                if (!currentSelection.RequiresConfirmation &&
                    currentSelection.SelectedCount >= currentSelection.MaxSelect)
                {
                    continue;
                }

                return false;
            }
        }

        return !GameStateService.TryGetCombatHandSelection(ActiveScreenContext.Instance.GetCurrentScreen(), out _);
    }

    private static bool TryGetCombatHandConfirmButton(NPlayerHand hand, out NConfirmButton? confirmButton)
    {
        confirmButton = hand.GetNodeOrNull<NConfirmButton>("%SelectModeConfirmButton")
            ?? hand.GetNodeOrNull<NConfirmButton>("SelectModeConfirmButton");
        return confirmButton != null && GodotObject.IsInstanceValid(confirmButton);
    }

    private static async Task<bool> DrainRewardFlowAsync(TimeSpan timeout)
    {
        if (NGame.Instance == null)
        {
            return false;
        }

        var deadline = DateTime.UtcNow + timeout;
        var attemptedRewardButtons = new HashSet<ulong>();

        while (DateTime.UtcNow < deadline)
        {
            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();

            if (currentScreen is NCardRewardSelectionScreen cardRewardScreen)
            {
                if (!await TryResolveCardRewardAsync(cardRewardScreen, deadline))
                {
                    return false;
                }

                continue;
            }

            if (currentScreen is not NRewardsScreen rewardsScreen)
            {
                _cardRewardSkipped = false;
                return true;
            }

            if (TryGetNextClaimableRewardButton(rewardsScreen, attemptedRewardButtons, out var rewardButton))
            {
                attemptedRewardButtons.Add(rewardButton!.GetInstanceId());
                await ClickRewardButtonAsync(rewardButton, deadline);
                continue;
            }

            var proceedButton = GameStateService.GetRewardProceedButton(rewardsScreen);
            if (proceedButton != null && proceedButton.IsEnabled)
            {
                proceedButton.ForceClick();
                return await WaitForRewardFlowExitAsync(rewardsScreen, deadline);
            }

            return IsRewardFlowStable();
        }

        return IsRewardFlowStable();
    }

    private static bool TryGetNextClaimableRewardButton(
        NRewardsScreen rewardsScreen,
        HashSet<ulong> attemptedRewardButtons,
        out NRewardButton? rewardButton)
    {
        var hasPotionSlots = GameStateService.GetLocalPlayer(RunManager.Instance.DebugOnlyGetState())?.HasOpenPotionSlots ?? false;
        rewardButton = GameStateService
            .GetRewardButtons(rewardsScreen)
            .FirstOrDefault(button =>
                button.IsEnabled &&
                !attemptedRewardButtons.Contains(button.GetInstanceId()) &&
                (button.Reward is not PotionReward || hasPotionSlots) &&
                (!_cardRewardSkipped || button.Reward is not CardReward));

        return rewardButton != null;
    }

    private static async Task ClickRewardButtonAsync(NRewardButton rewardButton, DateTime deadline)
    {
        var previousRewardCount = GameStateService.GetRewardButtons(ActiveScreenContext.Instance.GetCurrentScreen()).Count;
        rewardButton.ForceClick();

        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is NCardRewardSelectionScreen)
            {
                return;
            }

            var rewardButtons = GameStateService.GetRewardButtons(currentScreen);
            if (!GodotObject.IsInstanceValid(rewardButton) || rewardButtons.Count != previousRewardCount)
            {
                return;
            }
        }
    }

    private static async Task<bool> TryResolveCardRewardAsync(NCardRewardSelectionScreen cardRewardScreen, DateTime deadline)
    {
        for (var i = 0; i < 24 && DateTime.UtcNow < deadline; i++)
        {
            await WaitForNextFrameAsync();
        }

        // If resolve_rewards requested a skip, click the skip alternative
        if (_pendingCardRewardChoice == -2)
        {
            _pendingCardRewardChoice = -1;
            var alternatives = GameStateService.GetCardRewardAlternativeButtons(cardRewardScreen);
            if (alternatives.Count > 0)
            {
                alternatives.First().ForceClick();
                _cardRewardSkipped = true;
            }
            while (DateTime.UtcNow < deadline)
            {
                await WaitForNextFrameAsync();
                if (!GodotObject.IsInstanceValid(cardRewardScreen) ||
                    ActiveScreenContext.Instance.GetCurrentScreen() is not NCardRewardSelectionScreen)
                    return true;
            }
            return false;
        }

        var options = GameStateService.GetCardRewardOptions(cardRewardScreen);

        // If resolve_rewards specified a card index, use it
        NCardHolder? selected;
        if (_pendingCardRewardChoice >= 0 && _pendingCardRewardChoice < options.Count)
        {
            selected = options[_pendingCardRewardChoice];
            _pendingCardRewardChoice = -1;
        }
        else
        {
            selected = options.FirstOrDefault();
        }
        if (selected == null)
        {
            return false;
        }

        selected.EmitSignal(NCardHolder.SignalName.Pressed, selected);
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (!GodotObject.IsInstanceValid(cardRewardScreen) ||
                ActiveScreenContext.Instance.GetCurrentScreen() is not NCardRewardSelectionScreen)
            {
                return true;
            }
        }

        return false;
    }

    private static async Task<bool> WaitForRewardFlowExitAsync(NRewardsScreen rewardsScreen, DateTime deadline)
    {
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (!GodotObject.IsInstanceValid(rewardsScreen))
            {
                return true;
            }

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen != rewardsScreen)
            {
                return true;
            }

            if (NOverlayStack.Instance?.Peek() != rewardsScreen)
            {
                return true;
            }
        }

        return IsRewardFlowStable();
    }

    private static bool IsRewardFlowStable()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        return currentScreen is not NRewardsScreen && currentScreen is not NCardRewardSelectionScreen;
    }

    private static async Task<bool> WaitForRewardCardResolutionAsync(
        IScreenContext? previousScreen,
        int previousOptionCount,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (!ReferenceEquals(currentScreen, previousScreen))
            {
                return true;
            }

            if (GameStateService.GetCardRewardOptions(currentScreen).Count != previousOptionCount)
            {
                return true;
            }
        }

        return false;
    }

    private static async Task<bool> WaitForRewardButtonResolutionAsync(
        IScreenContext? previousScreen,
        int previousRewardCount,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (!ReferenceEquals(currentScreen, previousScreen))
            {
                return true;
            }

            var currentRewardCount = GameStateService.GetRewardButtons(currentScreen).Count(button => button.IsEnabled);
            if (currentRewardCount != previousRewardCount)
            {
                return true;
            }
        }

        return false;
    }

    private static async Task<bool> ConfirmDeckSelectionAsync(NCardGridSelectionScreen screen, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;

        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (!GodotObject.IsInstanceValid(screen))
            {
                return true;
            }

            var previewContainer = screen.GetNodeOrNull<Control>("%PreviewContainer");
            var previewConfirm = screen.GetNodeOrNull<NConfirmButton>("%PreviewConfirm")
                ?? previewContainer?.GetNodeOrNull<NConfirmButton>("Confirm");
            if (previewContainer?.Visible == true && previewConfirm?.IsEnabled == true)
            {
                previewConfirm.ForceClick();
                return await WaitForDeckSelectionResolutionAsync(screen, deadline);
            }

            if (screen is NDeckTransformSelectScreen transformScreen &&
                TryGetDeckTransformConfirmButton(transformScreen, out var transformConfirm))
            {
                transformConfirm!.ForceClick();
                return await WaitForDeckSelectionResolutionAsync(screen, deadline);
            }

            if (screen is NDeckEnchantSelectScreen enchantScreen &&
                TryGetDeckEnchantConfirmButton(enchantScreen, out var enchantConfirm))
            {
                enchantConfirm!.ForceClick();
                return await WaitForDeckSelectionResolutionAsync(screen, deadline);
            }

            if (screen is NDeckUpgradeSelectScreen upgradeScreen &&
                TryGetDeckUpgradeConfirmButton(upgradeScreen, out var upgradeConfirm))
            {
                upgradeConfirm!.ForceClick();
                return await WaitForDeckSelectionResolutionAsync(screen, deadline);
            }

            var confirmButton = screen.GetNodeOrNull<NConfirmButton>("%Confirm")
                ?? screen.GetNodeOrNull<NConfirmButton>("Confirm");
            if (confirmButton?.IsEnabled == true)
            {
                confirmButton.ForceClick();
            }
        }

        return false;
    }

    private static bool TryGetDeckUpgradeConfirmButton(
        NDeckUpgradeSelectScreen screen,
        out NConfirmButton? confirmButton)
    {
        var singlePreview = screen.GetNodeOrNull<Control>("%UpgradeSinglePreviewContainer");
        if (singlePreview?.Visible == true)
        {
            confirmButton = singlePreview.GetNodeOrNull<NConfirmButton>("Confirm");
            return confirmButton?.IsEnabled == true;
        }

        var multiPreview = screen.GetNodeOrNull<Control>("%UpgradeMultiPreviewContainer");
        if (multiPreview?.Visible == true)
        {
            confirmButton = multiPreview.GetNodeOrNull<NConfirmButton>("Confirm");
            return confirmButton?.IsEnabled == true;
        }

        confirmButton = null;
        return false;
    }

    private static bool TryGetDeckTransformConfirmButton(
        NDeckTransformSelectScreen screen,
        out NConfirmButton? confirmButton)
    {
        var previewContainer = screen.GetNodeOrNull<Control>("%PreviewContainer");
        if (previewContainer?.Visible == true)
        {
            confirmButton = previewContainer.GetNodeOrNull<NConfirmButton>("Confirm");
            return confirmButton?.IsEnabled == true;
        }

        confirmButton = null;
        return false;
    }

    private static bool TryGetDeckEnchantConfirmButton(
        NDeckEnchantSelectScreen screen,
        out NConfirmButton? confirmButton)
    {
        var singlePreview = screen.GetNodeOrNull<Control>("%EnchantSinglePreviewContainer");
        if (singlePreview?.Visible == true)
        {
            confirmButton = singlePreview.GetNodeOrNull<NConfirmButton>("Confirm");
            return confirmButton?.IsEnabled == true;
        }

        var multiPreview = screen.GetNodeOrNull<Control>("%EnchantMultiPreviewContainer");
        if (multiPreview?.Visible == true)
        {
            confirmButton = multiPreview.GetNodeOrNull<NConfirmButton>("Confirm");
            return confirmButton?.IsEnabled == true;
        }

        confirmButton = null;
        return false;
    }

    private static async Task<bool> WaitForDeckSelectionResolutionAsync(NCardGridSelectionScreen screen, DateTime deadline)
    {
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (!GodotObject.IsInstanceValid(screen) ||
                ActiveScreenContext.Instance.GetCurrentScreen() is not NCardGridSelectionScreen)
            {
                return true;
            }
        }

        return false;
    }

    private static async Task<ActionResponsePayload> ExecuteOpenChestAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is not NTreasureRoom treasureRoom || !GameStateService.CanOpenChest(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "open_chest",
                screen
            });
        }

        var chestButton = treasureRoom.GetNodeOrNull<NButton>("%Chest")
            ?? throw new ApiException(503, "state_unavailable", "Chest button not found.", new
            {
                action = "open_chest",
                screen
            }, retryable: true);

        chestButton.ForceClick();
        var stable = await WaitForChestOpenTransitionAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "open_chest",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<bool> WaitForChestOpenTransitionAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (GameStateService.GetTreasureRelicCollection(currentScreen) != null)
            {
                return true;
            }
        }

        return false;
    }

    private static async Task<ActionResponsePayload> ExecuteChooseTreasureRelicAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanChooseTreasureRelic(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "choose_treasure_relic",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "choose_treasure_relic requires option_index.", new
            {
                action = "choose_treasure_relic"
            });
        }

        var relics = RunManager.Instance.TreasureRoomRelicSynchronizer.CurrentRelics;
        if (relics == null || request.option_index < 0 || request.option_index >= relics.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "choose_treasure_relic",
                option_index = request.option_index,
                relic_count = relics?.Count ?? 0
            });
        }

        RunManager.Instance.TreasureRoomRelicSynchronizer.PickRelicLocally(request.option_index.Value);
        var stable = await WaitForRelicPickTransitionAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "choose_treasure_relic",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

}
