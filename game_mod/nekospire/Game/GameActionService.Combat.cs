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

// Combat implementation; shares the service API and state across partial files.
internal static partial class GameActionService
{
    private static async Task<ActionResponsePayload> ExecuteEndTurnAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var combatState = CombatManager.Instance.DebugOnlyGetState();
        var screen = GameStateService.ResolveScreen(currentScreen);

        // Co-op: an explicit player_index slot, OR simply a >1-player combat. In two-process
        // co-op each client drives its OWN local player, so a plain end_turn here is also co-op.
        // But ending a co-op player's turn may NOT advance the round — it hands the action phase
        // to the other player — so we must wait on the player's action phase, not the round.
        var isCoop = (request.player_index is int) || (combatState != null && combatState.Players.Count > 1);
        var me = request.player_index is int coopSlot
            ? GameStateService.GetCombatPlayerAtSlot(
                combatState ?? throw new ApiException(503, "state_unavailable", "Combat state is unavailable.", new { action = request.action, screen }, retryable: true),
                coopSlot)
            : GameStateService.GetLocalPlayer(combatState);

        if (me == null)
        {
            throw new ApiException(503, "state_unavailable", "Player is unavailable.", new
            {
                action = request.action,
                player_index = request.player_index,
                screen
            }, retryable: true);
        }

        if (isCoop)
        {
            if (!GameStateService.IsPlayerActionPhaseFor(combatState, me))
            {
                throw new ApiException(409, "invalid_action", "This player cannot end its turn in the current state.", new
                {
                    action = request.action,
                    player_index = request.player_index,
                    screen
                });
            }
        }
        else if (!GameStateService.CanEndTurn(currentScreen, combatState, requireButtonReady: false))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = request.action,
                screen
            });
        }

        var playerCombatState = me.Creature.CombatState
            ?? throw new ApiException(503, "state_unavailable", "Combat state is unavailable.", new
            {
                action = request.action,
                screen
            }, retryable: true);

        var endTurnButton = GameStateService.GetEndTurnButton(currentScreen as NCombatRoom);
        if (!GameStateService.IsEndTurnButtonReady(endTurnButton))
        {
            throw new ApiException(503, "state_unavailable", "End turn button is unavailable.", new
            {
                action = request.action,
                screen
            }, retryable: true);
        }

        var roundNumber = playerCombatState.RoundNumber;
        SimCapture.Begin("end_turn", combatState, me, new { player_index = request.player_index });
        endTurnButton!.ForceClick();

        // Co-op ending a player's turn does NOT necessarily advance the round — it hands the
        // action phase to the other player. So watch the target player's action phase, not the round.
        var stable = isCoop
            ? await WaitForPlayerActionPhaseEndAsync(me, roundNumber, TimeSpan.FromSeconds(5))
            : await WaitForEndTurnTransitionAsync(roundNumber, TimeSpan.FromSeconds(5));
        SimCapture.End(combatState, me);

        return new ActionResponsePayload
        {
            action = request.action ?? (isCoop ? "coop_end_turn" : "end_turn"),
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    /// <summary>
    /// Co-op end-turn wait: returns true once the given player is no longer the one whose
    /// action phase is active (turn passed away) — regardless of whether the round advanced.
    /// </summary>
    private static async Task<bool> WaitForPlayerActionPhaseEndAsync(Player player, int roundNumber, TimeSpan timeout)
    {
        if (NGame.Instance == null)
        {
            return false;
        }

        var deadline = DateTime.UtcNow + timeout;

        while (DateTime.UtcNow < deadline)
        {
            await NGame.Instance.ToSignal(NGame.Instance.GetTree(), SceneTree.SignalName.ProcessFrame);

            var combatState = CombatManager.Instance.DebugOnlyGetState();
            if (combatState == null || !CombatManager.Instance.IsInProgress)
            {
                return true;
            }

            if (combatState.RoundNumber != roundNumber)
            {
                return true; // round advanced (combat progressed past this player's turn)
            }

            if (!GameStateService.IsPlayerActionPhaseFor(combatState, player))
            {
                return true; // this player's action phase ended (turn handed off)
            }
        }

        var finalCombatState = CombatManager.Instance.DebugOnlyGetState();
        return finalCombatState == null
            || !CombatManager.Instance.IsInProgress
            || !GameStateService.IsPlayerActionPhaseFor(finalCombatState, player);
    }

    private static async Task<bool> WaitForEndTurnTransitionAsync(int previousRound, TimeSpan timeout)
    {
        if (NGame.Instance == null)
        {
            return false;
        }

        var deadline = DateTime.UtcNow + timeout;

        while (DateTime.UtcNow < deadline)
        {
            await NGame.Instance.ToSignal(NGame.Instance.GetTree(), SceneTree.SignalName.ProcessFrame);

            if (IsEndTurnStable(previousRound))
            {
                return true;
            }
        }

        return IsEndTurnStable(previousRound);
    }

    private static bool IsEndTurnStable(int previousRound)
    {
        if (!CombatManager.Instance.IsInProgress)
        {
            return true;
        }

        var combatState = CombatManager.Instance.DebugOnlyGetState();
        if (combatState == null)
        {
            return true;
        }

        if (combatState.RoundNumber != previousRound)
        {
            return true;
        }

        if (combatState.CurrentSide != CombatSide.Player)
        {
            return true;
        }

        return !GameStateService.IsPlayerActionPhase(combatState);
    }

    private static async Task<ActionResponsePayload> ExecutePlayCardAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var combatState = CombatManager.Instance.DebugOnlyGetState();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (request.card_index == null)
        {
            throw new ApiException(400, "invalid_request", "play_card requires card_index.", new
            {
                action = request.action
            });
        }

        // Co-op: player_index is a 0-based slot into combat.players[] (slot 0 local, slot 1 partner).
        var isCoop = request.player_index is int;
        var me = isCoop
            ? GameStateService.GetCombatPlayerAtSlot(
                combatState ?? throw new ApiException(503, "state_unavailable", "Combat state is unavailable.", new { action = request.action, screen }, retryable: true),
                request.player_index!.Value)
            : GameStateService.GetLocalPlayer(combatState)
                ?? throw new ApiException(503, "state_unavailable", "Local player is unavailable.", new
                {
                    action = request.action,
                    screen
                }, retryable: true);

        if (me == null)
        {
            throw new ApiException(503, "state_unavailable", "Player is unavailable.", new
            {
                action = request.action,
                player_index = request.player_index,
                screen
            }, retryable: true);
        }

        if (isCoop)
        {
            if (!GameStateService.IsPlayerActionPhaseFor(combatState, me))
            {
                throw new ApiException(409, "invalid_action", "This player cannot play cards in the current state.", new
                {
                    action = request.action,
                    player_index = request.player_index,
                    screen
                });
            }
        }
        else if (!GameStateService.CanPlayAnyCard(currentScreen, combatState))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = request.action,
                screen
            });
        }

        var hand = me.PlayerCombatState?.Hand.Cards.ToList()
            ?? throw new ApiException(503, "state_unavailable", "Hand is unavailable.", new
            {
                action = "play_card",
                screen
            }, retryable: true);

        if (request.card_index < 0 || request.card_index >= hand.Count)
        {
            throw new ApiException(409, "invalid_target", "card_index is out of range.", new
            {
                action = "play_card",
                card_index = request.card_index,
                hand_count = hand.Count
            });
        }

        var card = hand[request.card_index.Value];
        if (!GameStateService.IsCardTargetSupported(card))
        {
            throw new ApiException(409, "invalid_action", "This target type is not supported by the API.", new
            {
                action = "play_card",
                card_index = request.card_index,
                card_id = card.Id.Entry,
                target_type = card.TargetType.ToString(),
                screen
            });
        }

        var target = ResolveCardTarget(request, combatState, card);

        // Capture (when enabled): snapshot the pre-action state so a later replay can diff.
        SimCapture.Begin("play_card", combatState, me, new
        {
            card_index = request.card_index,
            card_id = card.Id.Entry,
            target_index = request.target_index,
        });

        if (!card.TryManualPlay(target))
        {
            throw new ApiException(409, "invalid_action", "Card cannot be played in the current state.", new
            {
                action = "play_card",
                card_index = request.card_index,
                target_index = request.target_index,
                card_id = card.Id.Entry,
                screen
            });
        }

        var currentTurn = combatState?.RoundNumber ?? 0;
        SyncCardPlayCounters(currentTurn);
        CardsPlayedThisTurn++;
        var cardType = card.Type.ToString();
        if (cardType == "Attack") AttacksPlayedThisTurn++;
        else if (cardType == "Skill") SkillsPlayedThisTurn++;

        var stable = await WaitForPlayCardTransitionAsync(card, TimeSpan.FromSeconds(5));

        // Capture: after the action settles, snapshot post-state and write the event.
        SimCapture.End(combatState, me);

        return new ActionResponsePayload
        {
            action = request.action ?? "play_card",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static Creature? ResolveCardTarget(ActionRequest request, CombatState? combatState, CardModel card)
    {
        if (!GameStateService.CardRequiresTarget(card))
        {
            return null;
        }

        if (combatState == null)
        {
            throw new ApiException(503, "state_unavailable", "Combat state is unavailable.", new
            {
                action = "play_card",
                card_id = card.Id.Entry
            }, retryable: true);
        }

        if (request.target_index == null)
        {
            throw new ApiException(409, "invalid_target", "This card requires target_index.", new
            {
                action = "play_card",
                card_id = card.Id.Entry,
                target_type = card.TargetType.ToString(),
                target_index_space = card.TargetType == TargetType.AnyEnemy ? "enemies" : "players"
            });
        }

        if (card.TargetType == TargetType.AnyEnemy)
        {
            var enemy = GameStateService.ResolveEnemyTarget(combatState, request.target_index.Value);
            if (enemy == null)
            {
                throw new ApiException(409, "invalid_target", "target_index is out of range for combat.enemies[].", new
                {
                    action = "play_card",
                    card_id = card.Id.Entry,
                    target_index = request.target_index,
                    target_index_space = "enemies"
                });
            }

            return enemy;
        }

        if (card.TargetType == TargetType.AnyAlly)
        {
            var allyTargetIndices = GameStateService.GetTargetablePlayerIndices(combatState, card.Owner, allowSelf: false);
            if (!allyTargetIndices.Contains(request.target_index.Value))
            {
                throw new ApiException(409, "invalid_target", "target_index is out of range for combat.players[].", new
                {
                    action = "play_card",
                    card_id = card.Id.Entry,
                    target_index = request.target_index,
                    target_index_space = "players"
                });
            }

            return GameStateService.ResolvePlayerTarget(combatState, request.target_index.Value);
        }

        throw new ApiException(409, "invalid_action", "This target type is not supported yet.", new
        {
            action = "play_card",
            card_id = card.Id.Entry,
            target_type = card.TargetType.ToString()
        });
    }

    private static async Task<bool> WaitForPlayCardTransitionAsync(CardModel card, TimeSpan timeout)
    {
        if (NGame.Instance == null)
        {
            return false;
        }

        var deadline = DateTime.UtcNow + timeout;

        while (DateTime.UtcNow < deadline)
        {
            await NGame.Instance.ToSignal(NGame.Instance.GetTree(), SceneTree.SignalName.ProcessFrame);

            if (IsPlayCardStable(card))
            {
                return true;
            }

            if (IsPlayCardAwaitingPlayerInput())
            {
                return false;
            }
        }

        return IsPlayCardStable(card);
    }

    private static bool IsPlayCardStable(CardModel card)
    {
        if (!CombatManager.Instance.IsInProgress)
        {
            return true;
        }

        if (card.Pile?.Type == PileType.Hand)
        {
            return false;
        }

        return ArePlayerDrivenActionsSettled();
    }

    private static bool IsPlayCardAwaitingPlayerInput()
    {
        if (!CombatManager.Instance.IsInProgress)
        {
            return false;
        }

        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        return currentScreen != null && GameStateService.ResolveScreen(currentScreen) == "CARD_SELECTION";
    }

    private static bool ArePlayerDrivenActionsSettled()
    {
        var runningAction = RunManager.Instance.ActionExecutor.CurrentlyRunningAction;
        if (runningAction != null && ActionQueueSet.IsGameActionPlayerDriven(runningAction))
        {
            return false;
        }

        var readyAction = RunManager.Instance.ActionQueueSet.GetReadyAction();
        if (readyAction != null && ActionQueueSet.IsGameActionPlayerDriven(readyAction))
        {
            return false;
        }

        return true;
    }

    internal static bool AreGameActionsSettled()
    {
        if (RunManager.Instance.ActionExecutor.CurrentlyRunningAction != null)
        {
            return false;
        }

        return RunManager.Instance.ActionQueueSet.GetReadyAction() == null;
    }

    private static async Task<ActionResponsePayload> ExecuteUsePotionAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var combatState = CombatManager.Instance.DebugOnlyGetState();
        var runState = RunManager.Instance.DebugOnlyGetState();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "use_potion requires option_index.", new
            {
                action = "use_potion"
            });
        }

        if (!GameStateService.CanUsePotionAtIndex(currentScreen, combatState, runState, request.option_index.Value))
        {
            throw new ApiException(409, "invalid_action", "The selected potion cannot be used in the current state.", new
            {
                action = "use_potion",
                screen,
                option_index = request.option_index
            });
        }

        var player = GameStateService.GetLocalPlayer(runState)
            ?? throw new ApiException(503, "state_unavailable", "Local player is unavailable.", new
            {
                action = "use_potion",
                screen
            }, retryable: true);

        if (request.option_index < 0 || request.option_index >= player.PotionSlots.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "use_potion",
                option_index = request.option_index,
                option_count = player.PotionSlots.Count
            });
        }

        var potion = player.PotionSlots[request.option_index.Value]
            ?? throw new ApiException(409, "invalid_target", "The selected potion slot is empty.", new
            {
                action = "use_potion",
                option_index = request.option_index
            });

        var target = ResolvePotionTarget(request, combatState, potion);
        potion.EnqueueManualUse(target);
        var stable = await WaitForPotionUseTransitionAsync(player, request.option_index.Value, potion, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "use_potion",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteDiscardPotionAsync(ActionRequest request)
    {
        var runState = RunManager.Instance.DebugOnlyGetState();
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "discard_potion requires option_index.", new
            {
                action = "discard_potion"
            });
        }

        if (!GameStateService.CanDiscardPotionAtIndex(currentScreen, runState, request.option_index.Value))
        {
            throw new ApiException(409, "invalid_action", "The selected potion cannot be discarded in the current state.", new
            {
                action = "discard_potion",
                screen,
                option_index = request.option_index
            });
        }

        var player = GameStateService.GetLocalPlayer(runState)
            ?? throw new ApiException(503, "state_unavailable", "Local player is unavailable.", new
            {
                action = "discard_potion",
                screen
            }, retryable: true);

        if (request.option_index < 0 || request.option_index >= player.PotionSlots.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "discard_potion",
                option_index = request.option_index,
                option_count = player.PotionSlots.Count
            });
        }

        var potion = player.PotionSlots[request.option_index.Value]
            ?? throw new ApiException(409, "invalid_target", "The selected potion slot is empty.", new
            {
                action = "discard_potion",
                option_index = request.option_index
            });

        RunManager.Instance.ActionQueueSynchronizer.RequestEnqueue(new DiscardPotionGameAction(
            player,
            (uint)request.option_index.Value,
            CombatManager.Instance.IsInProgress));
        var stable = await WaitForPotionDiscardTransitionAsync(player, request.option_index.Value, potion, TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "discard_potion",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static Creature? ResolvePotionTarget(ActionRequest request, CombatState? combatState, PotionModel potion)
    {
        return potion.TargetType switch
        {
            TargetType.AnyEnemy => ResolvePotionEnemyTarget(request, combatState, potion),
            TargetType.AnyPlayer when GameStateService.PotionRequiresTarget(combatState, potion) => ResolvePotionPlayerTarget(request, combatState, potion),
            TargetType.TargetedNoCreature => null,
            // ★ D9 修复：非单目标（AllEnemies / RandomEnemy / AllAllies / None / …）不需要生物目标。
            //   `Self` 也归此类 —— 游戏 IsValidTarget(null) 对非单目标返回 true，
            //   而 OnUse 内部自带 Owner.Creature，不看这个参数。
            _ when !potion.TargetType.IsSingleTarget() => null,
            // 单目标但未在上面覆盖（AnyPlayer 走显式选择、AnyAlly 等）⇒ 交给自愈逻辑。
            _ => ResolvePotionFallbackTarget(combatState, potion),
        };
    }

    /// <summary>
    /// 剩下这类「单目标但本服务没有显式解析分支」的药水（<c>AnyPlayer</c> 不需要显式选择、
    /// <c>AnyAlly</c>、<c>Osty</c> …）。**只在自己确实是合法目标时才回填自己**，
    /// 否则明确报错 —— 不允许再静默塞一个非法 target 让游戏侧 Cancel。
    /// </summary>
    private static Creature? ResolvePotionFallbackTarget(CombatState? combatState, PotionModel potion)
    {
        Creature owner = potion.Owner.Creature;
        if (potion.IsValidTarget(owner))
        {
            return owner;
        }

        if (potion.IsValidTarget(null))
        {
            return null;
        }

        throw new ApiException(409, "invalid_target", "No valid target is available for the selected potion.", new
        {
            action = "use_potion",
            potion_id = potion.Id.Entry,
            target_type = potion.TargetType.ToString(),
            in_combat = combatState != null
        });
    }

    private static Creature ResolvePotionEnemyTarget(ActionRequest request, CombatState? combatState, PotionModel potion)
    {
        if (combatState == null)
        {
            throw new ApiException(503, "state_unavailable", "Combat state is unavailable.", new
            {
                action = "use_potion",
                potion_id = potion.Id.Entry
            }, retryable: true);
        }

        if (request.target_index == null)
        {
            throw new ApiException(409, "invalid_target", "This potion requires target_index.", new
            {
                action = "use_potion",
                potion_id = potion.Id.Entry,
                target_type = potion.TargetType.ToString(),
                target_index_space = "enemies"
            });
        }

        var enemy = GameStateService.ResolveEnemyTarget(combatState, request.target_index.Value);
        if (enemy == null)
        {
            throw new ApiException(409, "invalid_target", "target_index is out of range for combat.enemies[].", new
            {
                action = "use_potion",
                potion_id = potion.Id.Entry,
                target_index = request.target_index,
                target_index_space = "enemies"
            });
        }

        return enemy;
    }

    private static Creature ResolvePotionPlayerTarget(ActionRequest request, CombatState? combatState, PotionModel potion)
    {
        if (combatState == null)
        {
            throw new ApiException(503, "state_unavailable", "Combat state is unavailable.", new
            {
                action = "use_potion",
                potion_id = potion.Id.Entry
            }, retryable: true);
        }

        if (request.target_index == null)
        {
            throw new ApiException(409, "invalid_target", "This potion requires target_index.", new
            {
                action = "use_potion",
                potion_id = potion.Id.Entry,
                target_type = potion.TargetType.ToString(),
                target_index_space = "players"
            });
        }

        var playerTargetIndices = GameStateService.GetTargetablePlayerIndices(combatState, potion.Owner, allowSelf: true);
        if (!playerTargetIndices.Contains(request.target_index.Value))
        {
            throw new ApiException(409, "invalid_target", "target_index is out of range for combat.players[].", new
            {
                action = "use_potion",
                potion_id = potion.Id.Entry,
                target_index = request.target_index,
                target_index_space = "players"
            });
        }

        return GameStateService.ResolvePlayerTarget(combatState, request.target_index.Value)
            ?? throw new ApiException(409, "invalid_target", "target_index is out of range for combat.players[].", new
            {
                action = "use_potion",
                potion_id = potion.Id.Entry,
                target_index = request.target_index,
                target_index_space = "players"
            });
    }

    private static async Task<bool> WaitForPotionUseTransitionAsync(Player player, int potionIndex, PotionModel potion, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (IsPotionUseAwaitingPlayerInput())
            {
                return false;
            }

            if (HasPotionUseSettled(player, potionIndex, potion))
            {
                return true;
            }
        }

        return HasPotionUseSettled(player, potionIndex, potion);
    }

    private static async Task<bool> WaitForPotionDiscardTransitionAsync(Player player, int potionIndex, PotionModel potion, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            if (potion.HasBeenRemovedFromState)
            {
                return true;
            }

            if (potionIndex >= player.PotionSlots.Count)
            {
                return true;
            }

            if (!ReferenceEquals(player.PotionSlots[potionIndex], potion))
            {
                return true;
            }
        }

        return potion.HasBeenRemovedFromState || !ReferenceEquals(player.PotionSlots[potionIndex], potion);
    }

    private static bool HasPotionUseSettled(Player player, int potionIndex, PotionModel potion)
    {
        if (!HasPotionSlotTransitioned(player, potionIndex, potion))
        {
            return false;
        }

        return ArePlayerDrivenActionsSettled();
    }

    private static bool HasPotionSlotTransitioned(Player player, int potionIndex, PotionModel potion)
    {
        if (potion.HasBeenRemovedFromState)
        {
            return true;
        }

        if (potionIndex >= player.PotionSlots.Count)
        {
            return true;
        }

        return !ReferenceEquals(player.PotionSlots[potionIndex], potion);
    }

    private static bool IsPotionUseAwaitingPlayerInput()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        if (currentScreen is NCardGridSelectionScreen or NChooseACardSelectionScreen)
        {
            return true;
        }

        return GameStateService.TryGetCombatHandSelection(currentScreen, out _);
    }

}
