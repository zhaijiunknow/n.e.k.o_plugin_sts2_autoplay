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

// Combat implementation; shares the service API and state across partial files.
internal static partial class GameStateService
{
    public static bool CanEndTurn(IScreenContext? currentScreen, CombatState? combatState, bool requireButtonReady = true)
    {
        if (!CanUseCombatActions(currentScreen, combatState, out _, out var combatRoom))
        {
            return false;
        }

        if (CombatManager.Instance.IsPlayerReadyToEndTurn(GetLocalPlayer(combatState)!))
        {
            return false;
        }

        return !requireButtonReady || IsEndTurnButtonReady(GetEndTurnButton(combatRoom));
    }

    public static bool CanPlayAnyCard(IScreenContext? currentScreen, CombatState? combatState)
    {
        if (!CanUseCombatActions(currentScreen, combatState, out var me, out _))
        {
            return false;
        }

        return me!.PlayerCombatState!.Hand.Cards.Any(IsCardPlayable);
    }

    public static Player? GetLocalPlayer(CombatState? combatState)
    {
        return combatState == null ? null : LocalContext.GetMe((ICombatState)combatState);
    }

    public static Player? GetLocalPlayer(RunState? runState)
    {
        return runState == null ? null : LocalContext.GetMe((IPlayerCollection)runState);
    }

    public static bool IsPlayerActionPhase(CombatState? combatState)
    {
        var me = GetLocalPlayer(combatState);
        return IsPlayerActionPhase(combatState, me);
    }

    private static bool IsPlayerActionPhase(CombatState? combatState, Player? me)
    {
        if (combatState == null ||
            me == null ||
            combatState.CurrentSide != CombatSide.Player)
        {
            return false;
        }

        return CombatManager.Instance.IsPartOfPlayerTurn(me);
    }

    /// <summary>
    /// Co-op: returns the combat player at the given ordered slot index (0-based).
    /// The slot order matches <see cref="GetOrderedCombatPlayers"/>, i.e. the same
    /// "combat.players[]" target space — slot 0 is the local player, slot 1 the partner.
    /// </summary>
    public static Player? GetCombatPlayerAtSlot(CombatState combatState, int slotIndex)
    {
        return GetOrderedCombatPlayers(combatState).ElementAtOrDefault(slotIndex);
    }

    /// <summary>
    /// Co-op: whether a specific combat player is in its own player action phase.
    /// Unlike <see cref="IsPlayerActionPhase(CombatState)"/>, this checks the *given* player
    /// instead of always the local "me", so it can tell when the partner (slot 1) has the turn.
    /// </summary>
    public static bool IsPlayerActionPhaseFor(CombatState? combatState, Player? player)
    {
        if (combatState == null || player == null || combatState.CurrentSide != CombatSide.Player)
        {
            return false;
        }

        return CombatManager.Instance.IsPartOfPlayerTurn(player);
    }

    public static Creature? ResolveEnemyTarget(CombatState combatState, int targetIndex)
    {
        var enemies = combatState.Enemies.ToList();
        if (targetIndex < 0 || targetIndex >= enemies.Count)
        {
            return null;
        }

        var enemy = enemies[targetIndex];
        return enemy.IsAlive && enemy.IsHittable ? enemy : null;
    }

    public static Creature? ResolvePlayerTarget(CombatState combatState, int targetIndex)
    {
        var players = GetOrderedCombatPlayers(combatState);
        if (targetIndex < 0 || targetIndex >= players.Count)
        {
            return null;
        }

        var player = players[targetIndex];
        return player.Creature.IsAlive ? player.Creature : null;
    }

    public static bool CardRequiresTarget(CardModel card)
    {
        return RequiresIndexedCardTarget(card.TargetType);
    }

    public static bool RestOptionRequiresTarget(RestSiteOption option, RunState? runState, Player? localPlayer)
    {
        return runState != null &&
            localPlayer != null &&
            string.Equals(option.OptionId, "MEND", StringComparison.OrdinalIgnoreCase) &&
            GetRestOptionTargetIndices(runState, localPlayer, allowSelf: false).Length > 0;
    }

    public static string? GetRestOptionTargetIndexSpace(RestSiteOption option, RunState? runState, Player? localPlayer)
    {
        return RestOptionRequiresTarget(option, runState, localPlayer) ? "run.players" : null;
    }

    public static int[] GetRestOptionTargetIndices(RunState? runState, Player? localPlayer, bool allowSelf)
    {
        if (runState == null || localPlayer == null)
        {
            return Array.Empty<int>();
        }

        return runState.Players
            .OrderBy(runState.GetPlayerSlotIndex)
            .Select((player, index) => new { player, index })
            .Where(entry => entry.player.Creature.IsAlive && (allowSelf || entry.player.NetId != localPlayer.NetId))
            .Select(entry => entry.index)
            .ToArray();
    }

    public static Player? ResolveRunPlayerTarget(RunState? runState, int targetIndex)
    {
        if (runState == null || targetIndex < 0)
        {
            return null;
        }

        var players = runState.Players
            .OrderBy(runState.GetPlayerSlotIndex)
            .ToArray();
        if (targetIndex >= players.Length)
        {
            return null;
        }

        var player = players[targetIndex];
        return player.Creature.IsAlive ? player : null;
    }

    public static bool IsCardPlayable(CardModel card)
    {
        return card.CanPlay(out _, out _) && IsCardTargetSupported(card);
    }

    public static bool IsCardTargetSupported(CardModel card)
    {
        return card.TargetType switch
        {
            TargetType.None => true,
            TargetType.Self => true,
            TargetType.AnyEnemy => true,
            TargetType.AllEnemies => true,
            TargetType.RandomEnemy => true,
            TargetType.AnyAlly => true,
            TargetType.AllAllies => true,
            _ => false
        };
    }

    public static string? GetUnplayableReasonCode(CardModel card)
    {
        card.CanPlay(out var reason, out _);
        return GetUnplayableReasonCode(reason);
    }

    public static string? GetUnplayableReasonCode(UnplayableReason reason)
    {
        if (reason == UnplayableReason.None)
        {
            return null;
        }

        if (reason.HasFlag(UnplayableReason.EnergyCostTooHigh))
        {
            return "not_enough_energy";
        }

        if (reason.HasFlag(UnplayableReason.StarCostTooHigh))
        {
            return "not_enough_stars";
        }

        if (reason.HasFlag(UnplayableReason.NoLivingAllies))
        {
            return "no_living_allies";
        }

        if (reason.HasFlag(UnplayableReason.BlockedByHook))
        {
            return "blocked_by_hook";
        }

        if (reason.HasFlag(UnplayableReason.HasUnplayableKeyword) || reason.HasFlag(UnplayableReason.BlockedByCardLogic))
        {
            return "unplayable";
        }

        return reason.ToString();
    }

    private static bool CanUseCombatActions(IScreenContext? currentScreen, CombatState? combatState, out Player? me, out NCombatRoom? combatRoom)
    {
        me = null;
        combatRoom = null;

        if (combatState == null || currentScreen is not NCombatRoom room)
        {
            ResetCombatActionReadiness();
            return false;
        }

        combatRoom = room;

        if (!CombatManager.Instance.IsInProgress ||
            CombatManager.Instance.IsOverOrEnding ||
            CombatManager.Instance.PlayerActionsDisabled)
        {
            ResetCombatActionReadiness();
            return false;
        }

        if (combatRoom.Mode != CombatRoomMode.ActiveCombat)
        {
            ResetCombatActionReadiness();
            return false;
        }

        var hand = combatRoom.Ui?.Hand;
        if (hand == null || hand.InCardPlay || hand.IsInCardSelection || hand.CurrentMode != MegaCrit.Sts2.Core.Nodes.Combat.NPlayerHand.Mode.Play)
        {
            ResetCombatActionReadiness();
            return false;
        }

        me = GetLocalPlayer(combatState);
        if (me == null || !me.Creature.IsAlive)
        {
            ResetCombatActionReadiness();
            return false;
        }

        GameActionService.SyncCardPlayCounters(combatState.RoundNumber);
        if (!IsLocalCombatTurnReady(me))
        {
            ResetCombatActionReadiness();
            return false;
        }

        if (!IsCombatActionSnapshotStable(combatState, me))
        {
            return false;
        }

        if (!IsPlayerActionPhase(combatState, me))
        {
            ResetCombatActionReadiness();
            return false;
        }

        return true;
    }

    private static bool IsLocalCombatTurnReady(Player me)
    {
        var playerCombatState = me.PlayerCombatState;
        if (playerCombatState == null || playerCombatState.TurnNumber <= 0)
        {
            return false;
        }

        if (playerCombatState.Hand.Cards.Count == 0 &&
            GameActionService.CardsPlayedThisTurn == 0)
        {
            return false;
        }

        return true;
    }

    private static bool IsCombatActionSnapshotStable(CombatState combatState, Player me)
    {
        if (!GameActionService.AreGameActionsSettled())
        {
            ResetCombatActionReadiness();
            return false;
        }

        var signature = BuildCombatActionReadinessSignature(combatState, me);
        var now = DateTime.UtcNow;

        if (!string.Equals(signature, _lastCombatActionReadinessSignature, StringComparison.Ordinal))
        {
            _lastCombatActionReadinessSignature = signature;
            _lastCombatActionReadinessSinceUtc = now;
            return false;
        }

        return now - _lastCombatActionReadinessSinceUtc >= CombatActionSnapshotStableDelay;
    }

    private static string BuildCombatActionReadinessSignature(CombatState combatState, Player me)
    {
        var playerCombatState = me.PlayerCombatState;
        var handCards = playerCombatState?.Hand.Cards.ToList() ?? new List<CardModel>();
        var handSignature = string.Join(
            ",",
            handCards.Select(card => $"{card.Id.Entry}:{card.Pile?.Type.ToString() ?? "Unknown"}"));

        return string.Join(
            "|",
            combatState.RoundNumber,
            playerCombatState?.TurnNumber ?? 0,
            playerCombatState?.Energy ?? 0,
            playerCombatState?.Stars ?? 0,
            handCards.Count,
            handSignature);
    }

    private static void ResetCombatActionReadiness()
    {
        _lastCombatActionReadinessSignature = null;
        _lastCombatActionReadinessSinceUtc = DateTime.MinValue;
    }

    public static NEndTurnButton? GetEndTurnButton(NCombatRoom? combatRoom)
    {
        if (combatRoom == null || !GodotObject.IsInstanceValid(combatRoom))
        {
            return null;
        }

        return combatRoom.Ui?.EndTurnButton
            ?? FindDescendants<NEndTurnButton>(combatRoom).FirstOrDefault(GodotObject.IsInstanceValid);
    }

    public static bool IsEndTurnButtonReady(NEndTurnButton? button)
    {
        if (button == null || !GodotObject.IsInstanceValid(button) || !button.IsVisibleInTree() || !button.IsEnabled)
        {
            return false;
        }

        const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
        var property = button.GetType().GetProperty("CanTurnBeEnded", flags);
        return property?.GetValue(button) is not bool canTurnBeEnded || canTurnBeEnded;
    }

    private static CombatPayload? BuildCombatPayload(CombatState? combatState)
    {
        var me = GetLocalPlayer(combatState);
        if (combatState == null || me?.PlayerCombatState == null)
        {
            return null;
        }

        var hand = me.PlayerCombatState.Hand.Cards.ToList();
        var enemies = combatState.Enemies.ToList();
        var orbQueue = me.PlayerCombatState.OrbQueue;
        var orbs = orbQueue.Orbs.ToList();
        var connectedPlayerIds = GetConnectedPlayerIds(combatState.RunState as RunState);
        GameActionService.SyncCardPlayCounters(combatState.RoundNumber);

        var playerPayload = new CombatPlayerPayload
        {
            current_hp = me.Creature.CurrentHp,
            max_hp = me.Creature.MaxHp,
            block = me.Creature.Block,
            energy = me.PlayerCombatState.Energy,
            stars = me.PlayerCombatState.Stars,
            focus = me.Creature.GetPowerAmount<FocusPower>(),
            powers = BuildCreaturePowerPayloads(me.Creature),
            base_orb_slots = me.BaseOrbSlotCount,
            orb_capacity = orbQueue.Capacity,
            empty_orb_slots = Math.Max(0, orbQueue.Capacity - orbs.Count),
            orbs = orbs.Select((orb, index) => BuildCombatOrbPayload(orb, index)).ToArray(),
            cards_played_this_turn = GameActionService.CardsPlayedThisTurn,
            attacks_played_this_turn = GameActionService.AttacksPlayedThisTurn,
            skills_played_this_turn = GameActionService.SkillsPlayedThisTurn
        };
        var enemyPayloads = enemies.Select((enemy, index) => BuildEnemyPayload(enemy, index)).ToArray();
        var lethalRisks = BuildCombatLethalRiskPayloads(playerPayload, enemyPayloads);

        return new CombatPayload
        {
            player = playerPayload,
            players = GetOrderedCombatPlayers(combatState)
                .Select(player => BuildCombatPlayerSummaryPayload(player, combatState, connectedPlayerIds, me.NetId))
                .ToArray(),
            hand = hand.Select((card, index) => BuildHandCardPayload(combatState, card, index)).ToArray(),
            enemies = enemyPayloads,
            end_turn_will_kill_player = lethalRisks.Any(risk => risk.will_kill_player),
            lethal_risks = lethalRisks
        };
    }

    private static CombatLethalRiskPayload[] BuildCombatLethalRiskPayloads(
        CombatPlayerPayload player,
        CombatEnemyPayload[] enemies)
    {
        var risks = new List<CombatLethalRiskPayload>();
        var incomingDamage = enemies
            .Where(enemy => enemy.is_alive)
            .SelectMany(enemy => enemy.intents)
            .Sum(intent => Math.Max(0, intent.total_damage.GetValueOrDefault()));

        if (incomingDamage > 0)
        {
            var damageAfterBlock = Math.Max(0, incomingDamage - Math.Max(0, player.block));
            if (damageAfterBlock >= player.current_hp)
            {
                risks.Add(new CombatLethalRiskPayload
                {
                    risk_id = "incoming_damage",
                    source = "enemy_intents",
                    will_kill_player = true,
                    reason = "Enemy intent damage after current block is at least current HP.",
                    incoming_damage = incomingDamage,
                    damage_after_block = damageAfterBlock,
                    player_hp = player.current_hp,
                    player_block = player.block
                });
            }
        }

        foreach (var power in player.powers)
        {
            if (!IsSandpitPower(power) || power.amount is not int amount || amount > 1)
            {
                continue;
            }

            risks.Add(new CombatLethalRiskPayload
            {
                risk_id = "sandpit_countdown",
                source = "player_power",
                will_kill_player = true,
                reason = "SANDPIT_POWER is at or below 1; ending turn is treated as lethal unless the boss dies first or Frantic Escape has already raised the counter.",
                player_hp = player.current_hp,
                player_block = player.block,
                power_id = power.power_id,
                power_amount = amount
            });
        }

        return risks.ToArray();
    }

    private static bool IsSandpitPower(CombatPowerPayload power)
    {
        return string.Equals(power.power_id, "SANDPIT_POWER", StringComparison.OrdinalIgnoreCase)
            || string.Equals(power.name, "Sandpit", StringComparison.OrdinalIgnoreCase)
            || string.Equals(power.name, "沙坑", StringComparison.OrdinalIgnoreCase);
    }

    private static RunPayload? BuildRunPayload(IScreenContext? currentScreen, CombatState? combatState, RunState? runState)
    {
        if (runState == null)
        {
            return null;
        }

        var player = GetLocalPlayer(runState);
        if (player == null)
        {
            return null;
        }

        var connectedPlayerIds = GetConnectedPlayerIds(runState);

        return new RunPayload
        {
            character_id = player.Character.Id.Entry,
            character_name = player.Character.Title.GetFormattedText(),
            ascension = runState.AscensionLevel,
            ascension_effects = BuildAscensionEffectPayloads(runState.AscensionLevel, runState.GameMode),
            floor = runState.TotalFloor,
            current_hp = player.Creature.CurrentHp,
            max_hp = player.Creature.MaxHp,
            gold = player.Gold,
            max_energy = player.MaxEnergy,
            base_orb_slots = player.BaseOrbSlotCount,
            act_id = TryGetMemberValue(runState, "CurrentActIndex")?.ToString()
                ?? TryGetMemberValue(runState, "ActId")?.ToString(),
            boss_id = ResolveBossId(runState),
            deck = player.Deck.Cards.Select((card, index) => BuildDeckCardPayload(card, index)).ToArray(),
            relics = player.Relics.Select((relic, index) => BuildRunRelicPayload(relic, index)).ToArray(),
            players = runState!.Players
                .OrderBy(runState.GetPlayerSlotIndex)
                .Select(otherPlayer => BuildRunPlayerSummaryPayload(runState, otherPlayer, connectedPlayerIds, player.NetId))
                .ToArray(),
            potions = player.PotionSlots.Select((potion, index) =>
                BuildRunPotionPayload(currentScreen, combatState, player, potion, index)).ToArray()
        };
    }

    private static string? ResolveBossId(RunState runState)
    {
        if (runState.Act?.BossEncounter?.Id.Entry is { Length: > 0 } bossId)
        {
            return bossId;
        }

        return TryGetMemberValue(runState, "BossId")?.ToString();
    }

    private static CombatHandCardPayload BuildHandCardPayload(CombatState combatState, CardModel card, int index)
    {
        card.CanPlay(out var reason, out _);
        var targetSupported = IsCardTargetSupported(card);
        var targetIndexSpace = GetCardTargetIndexSpace(card);
        var validTargetIndices = GetCardTargetIndices(combatState, card);
        var resolvedRulesText = GetResolvedCardRulesText(card);
        var dynamicValues = BuildCardDynamicValuePayloads(card);

        return new CombatHandCardPayload
        {
            index = index,
            card_id = card.Id.Entry,
            name = card.Title,
            upgraded = card.IsUpgraded,
            target_type = card.TargetType.ToString(),
            requires_target = CardRequiresTarget(card),
            target_index_space = targetIndexSpace,
            valid_target_indices = validTargetIndices,
            costs_x = card.EnergyCost.CostsX,
            star_costs_x = card.HasStarCostX,
            energy_cost = card.EnergyCost.GetWithModifiers(CostModifiers.All),
            star_cost = Math.Max(0, card.GetStarCostWithModifiers()),
            rules_text = GetCardRulesText(card),
            resolved_rules_text = resolvedRulesText,
            dynamic_values = dynamicValues,
            playable = targetSupported && reason == UnplayableReason.None,
            unplayable_reason = targetSupported
                ? GetUnplayableReasonCode(reason)
                : "unsupported_target_type"
        };
    }

    private static CombatEnemyPayload BuildEnemyPayload(Creature enemy, int index)
    {
        var moveId = enemy.Monster?.NextMove?.Id;
        var intents = BuildEnemyIntentPayloads(enemy);

        return new CombatEnemyPayload
        {
            index = index,
            enemy_id = enemy.ModelId.Entry,
            name = enemy.Name,
            current_hp = enemy.CurrentHp,
            max_hp = enemy.MaxHp,
            block = enemy.Block,
            is_alive = enemy.IsAlive,
            is_hittable = enemy.IsHittable,
            powers = BuildCreaturePowerPayloads(enemy),
            intent = moveId,
            move_id = moveId,
            intents = intents
        };
    }

    private static CombatPowerPayload[] BuildCreaturePowerPayloads(Creature creature)
    {
        var powersValue = creature.GetType().GetProperty("Powers")?.GetValue(creature);
        if (powersValue is not System.Collections.IEnumerable powersEnumerable)
        {
            return Array.Empty<CombatPowerPayload>();
        }

        var result = new List<CombatPowerPayload>();
        var index = 0;

        foreach (var power in powersEnumerable)
        {
            if (power == null)
            {
                continue;
            }

            var powerType = power.GetType();
            var idEntry = SafeReadString(() =>
            {
                var idValue = powerType.GetProperty("Id")?.GetValue(power);
                if (idValue == null)
                {
                    return string.Empty;
                }

                return idValue.GetType().GetProperty("Entry")?.GetValue(idValue)?.ToString();
            });

            var title = SafeReadString(() =>
            {
                var titleValue = powerType.GetProperty("Title")?.GetValue(power);
                if (titleValue == null)
                {
                    return string.Empty;
                }

                return titleValue.GetType().GetMethod("GetFormattedText")?.Invoke(titleValue, null)?.ToString();
            });

            var amount = GetReflectedNullableIntProperty(power, "Amount");

            var isDebuff = string.Equals(
                GetReflectedProperty(power, "TypeForCurrentAmount")?.ToString()
                    ?? GetReflectedProperty(power, "Type")?.ToString(),
                "Debuff",
                StringComparison.Ordinal);

            result.Add(new CombatPowerPayload
            {
                index = index,
                power_id = string.IsNullOrWhiteSpace(idEntry) ? "unknown_power" : idEntry,
                name = string.IsNullOrWhiteSpace(title) ? idEntry : title,
                amount = amount,
                is_debuff = isDebuff
            });
            index += 1;
        }

        return result.ToArray();
    }

    private static CombatEnemyIntentPayload[] BuildEnemyIntentPayloads(Creature enemy)
    {
        var nextMove = enemy.Monster?.NextMove;
        if (nextMove == null)
        {
            return Array.Empty<CombatEnemyIntentPayload>();
        }

        var targets = enemy.CombatState?.Players
            .Select(player => player.Creature)
            .ToArray() ?? Array.Empty<Creature>();

        return nextMove.Intents
            .Select((intent, index) => BuildEnemyIntentPayload(intent, enemy, targets, index))
            .ToArray();
    }

    private static CombatEnemyIntentPayload BuildEnemyIntentPayload(
        AbstractIntent intent,
        Creature owner,
        Creature[] targets,
        int index)
    {
        int? damage = null;
        int? hits = null;
        int? totalDamage = null;
        int? statusCardCount = null;

        if (intent is AttackIntent attackIntent)
        {
            damage = SafeReadNullableInt(() => attackIntent.GetSingleDamage(targets, owner));
            hits = SafeReadNullableInt(() => Math.Max(1, attackIntent.Repeats));
            totalDamage = SafeReadNullableInt(() => attackIntent.GetTotalDamage(targets, owner));
        }

        if (intent is StatusIntent statusIntent)
        {
            statusCardCount = SafeReadNullableInt(() => statusIntent.CardCount);
        }

        var label = SafeReadString(() => intent.GetIntentLabel(targets, owner).GetFormattedText(), string.Empty);

        return new CombatEnemyIntentPayload
        {
            index = index,
            intent_type = intent.IntentType.ToString(),
            label = string.IsNullOrWhiteSpace(label) ? null : label,
            damage = damage,
            hits = hits,
            total_damage = totalDamage,
            status_card_count = statusCardCount
        };
    }

    private static int? SafeReadNullableInt(Func<int> getter)
    {
        try
        {
            return getter();
        }
        catch
        {
            return null;
        }
    }

    private static CombatOrbPayload BuildCombatOrbPayload(OrbModel orb, int slotIndex)
    {
        return new CombatOrbPayload
        {
            slot_index = slotIndex,
            orb_id = orb.Id.Entry,
            name = orb.Title.GetFormattedText(),
            passive_value = orb.PassiveVal,
            evoke_value = orb.EvokeVal,
            is_front = slotIndex == 0
        };
    }

    private static string? GetCardTargetIndexSpace(CardModel card)
    {
        return card.TargetType switch
        {
            TargetType.AnyEnemy => "enemies",
            TargetType.AnyAlly => "players",
            _ => null
        };
    }

    private static string? GetPotionTargetIndexSpace(CombatState? combatState, PotionModel potion)
    {
        return potion.TargetType switch
        {
            TargetType.AnyEnemy => "enemies",
            TargetType.AnyPlayer when PotionRequiresExplicitPlayerSelection(combatState, potion) => "players",
            TargetType.AnyAlly => "players",
            _ => null
        };
    }

    private static int[] GetCardTargetIndices(CombatState combatState, CardModel card)
    {
        return card.TargetType switch
        {
            TargetType.AnyEnemy => GetTargetableEnemyIndices(combatState),
            TargetType.AnyAlly => GetTargetablePlayerIndices(combatState, card.Owner, allowSelf: false),
            _ => Array.Empty<int>()
        };
    }

    private static int[] GetPotionTargetIndices(CombatState? combatState, PotionModel potion)
    {
        if (combatState == null)
        {
            return Array.Empty<int>();
        }

        return potion.TargetType switch
        {
            TargetType.AnyEnemy => GetTargetableEnemyIndices(combatState),
            TargetType.AnyPlayer when PotionRequiresExplicitPlayerSelection(combatState, potion) => GetTargetablePlayerIndices(combatState, potion.Owner, allowSelf: true),
            TargetType.AnyAlly => GetTargetablePlayerIndices(combatState, potion.Owner, allowSelf: false),
            _ => Array.Empty<int>()
        };
    }

    private static bool IsPotionUsable(IScreenContext? currentScreen, CombatState? combatState, Player player, PotionModel? potion)
    {
        if (potion == null || !IsPotionDiscardable(player, potion))
        {
            return false;
        }

        if (!potion.PassesCustomUsabilityCheck || !IsPotionTargetSupported(combatState, potion))
        {
            return false;
        }

        return potion.Usage switch
        {
            PotionUsage.AnyTime => true,
            PotionUsage.CombatOnly => CanUseCombatActions(currentScreen, combatState, out _, out _),
            _ => false
        };
    }

    private static bool CanDiscardPotionsInCurrentScreen(IScreenContext? currentScreen)
    {
        return currentScreen is not (NRewardsScreen or NCardRewardSelectionScreen);
    }

    private static bool IsPotionDiscardable(Player player, PotionModel? potion)
    {
        return potion != null &&
            !potion.IsQueued &&
            !potion.Owner.Creature.IsDead &&
            player.CanUseOrRemovePotions;
    }

    public static bool PotionRequiresTarget(CombatState? combatState, PotionModel potion)
    {
        return potion.TargetType switch
        {
            TargetType.AnyEnemy => true,
            TargetType.AnyPlayer => PotionRequiresExplicitPlayerSelection(combatState, potion),
            TargetType.AnyAlly => combatState != null && GetTargetablePlayerIndices(combatState, potion.Owner, allowSelf: false).Length > 0,
            _ => false
        };
    }

    private static bool IsPotionTargetSupported(CombatState? combatState, PotionModel potion)
    {
        return potion.TargetType switch
        {
            TargetType.AnyEnemy => GetTargetableEnemyIndices(combatState).Length > 0,
            TargetType.AnyPlayer => PotionRequiresExplicitPlayerSelection(combatState, potion)
                ? GetTargetablePlayerIndices(combatState, potion.Owner, allowSelf: true).Length > 0
                : true,
            TargetType.AnyAlly => GetTargetablePlayerIndices(combatState, potion.Owner, allowSelf: false).Length > 0,
            TargetType.TargetedNoCreature => true,
            _ => true
        };
    }

    private static bool PotionRequiresExplicitPlayerSelection(CombatState? combatState, PotionModel potion)
    {
        return combatState != null &&
            CombatManager.Instance.IsInProgress &&
            potion.Owner.RunState.Players.Count > 1 &&
            combatState.PlayerCreatures.Count(creature => creature.IsAlive) > 1;
    }

    private static bool RequiresIndexedCardTarget(TargetType targetType)
    {
        return targetType == TargetType.AnyEnemy || targetType == TargetType.AnyAlly;
    }

    public static int[] GetTargetableEnemyIndices(CombatState? combatState)
    {
        if (combatState == null)
        {
            return Array.Empty<int>();
        }

        return combatState.Enemies
            .Select((enemy, index) => new { enemy, index })
            .Where(entry => entry.enemy.IsAlive && entry.enemy.IsHittable)
            .Select(entry => entry.index)
            .ToArray();
    }

    public static int[] GetTargetablePlayerIndices(CombatState? combatState, Player owner, bool allowSelf)
    {
        if (combatState == null)
        {
            return Array.Empty<int>();
        }

        return GetOrderedCombatPlayers(combatState)
            .Select((player, index) => new { player, index })
            .Where(entry => entry.player.Creature.IsAlive)
            .Where(entry => allowSelf || entry.player.NetId != owner.NetId)
            .Select(entry => entry.index)
            .ToArray();
    }

    private static IReadOnlyList<Player> GetOrderedCombatPlayers(CombatState combatState)
    {
        return combatState.Players
            .OrderBy(player => combatState.RunState is RunState runState ? runState.GetPlayerSlotIndex(player) : 0)
            .ToArray();
    }

}
