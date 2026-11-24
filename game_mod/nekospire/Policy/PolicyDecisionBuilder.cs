// Builds the decision (candidate list) the AlphaDefect network scores, from the live game's logic layer.
//
// Every builder mirrors the matching method of upstream's RunSession (engine/Sts2Headless/Engine/
// RunSession.cs): same candidates, same order, same referenced game objects. That is what keeps the
// frames nekospire encodes equal to the frames the network was trained on, and it is what
// game_mod/PolicyFidelity checks against upstream's own engine.
//
// Nothing here touches Godot nodes: the builders take the logic objects (plus an optional resolver that
// turns a logic object into a nekospire action index), so they also run in upstream's headless host.
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Runs;
using Sts2Headless;

namespace NekoComm.Policy;

/// <summary>How nekospire executes one candidate. <see cref="Kind"/> is a /action name or a policy-only step.</summary>
internal sealed record PolicyAction(string Kind, int? CardIndex = null, int? TargetIndex = null, int? OptionIndex = null)
{
    /// <summary>The game object the action is about (a shop entry), so a failed attempt can be remembered.</summary>
    public object? Subject { get; init; }

    /// <summary>Policy-only: press the rewards screen's proceed button without collecting what is left.</summary>
    public const string LeaveRewards = "policy_leave_rewards";
    /// <summary>Policy-only: press the card-reward alternative button at OptionIndex (skip, reroll, ...).</summary>
    public const string RewardAlternative = "policy_reward_alternative";
}

/// <summary>A decision plus, per candidate, the nekospire action that carries it out (null: none known).</summary>
internal sealed class PolicyDecision(DecisionKind kind, int selected = 0)
{
    public Decision Decision { get; } = new(kind) { Selected = selected };
    public List<PolicyAction?> Actions { get; } = new();
    public PendingCardChoice? Selecting { get; init; }
    public IReadOnlyList<int> SelectedIndices { get; init; } = Array.Empty<int>();

    public void Add(ActionKind kind, string label, PolicyAction? action, object? a = null, object? b = null,
        string? key = null, float f0 = 0, float f1 = 0, float f2 = 0)
    {
        Decision.Add(kind, label, static () => { }, a, b, key, f0, f1, f2);
        Actions.Add(action);
    }
}

internal static class PolicyDecisionBuilder
{
    /// <summary>Event options upstream never offers (key: TextKey). TRIAL double-down only opens the abandon-run popup.</summary>
    static readonly HashSet<string> ExcludedEventOptions = new() { "TRIAL.pages.REJECT.options.DOUBLE_DOWN" };

    // ---------------------------------------------------------------- combat

    /// <summary>Top-level play decision: every playable card x valid target, usable potions, end turn.</summary>
    public static PolicyDecision Combat(Player me, CombatState combat)
    {
        var cs = me.Creature.CombatState!;
        var d = new PolicyDecision(DecisionKind.Combat);
        var targets = new List<Creature?> { null };
        targets.AddRange(cs.Creatures.Where(c => c.IsAlive));
        var hand = PileType.Hand.GetPile(me).Cards;
        for (int i = 0; i < hand.Count; i++)
        {
            CardModel c = hand[i];
            if (!c.CanPlay(out _, out _)) continue;
            foreach (var t in targets)
                if (c.CanPlayTargeting(t))
                    d.Add(ActionKind.PlayCard, $"play {c.Id.Entry}{TargetLabel(t)}", PlayAction(combat, me, i, c, t), a: c, b: t);
        }
        foreach (var p in UsablePotions(me, inCombat: true))
            foreach (var t in targets)
                if (p.IsValidTarget(t))
                    d.Add(ActionKind.UsePotion, $"use {p.Id.Entry}{TargetLabel(t)}", PotionAction(combat, me, p, t), a: p, b: t);
        d.Add(ActionKind.EndTurn, "end turn", new PolicyAction("end_turn"));
        return d;
    }

    static string TargetLabel(Creature? t) => t == null ? "" : t.IsPlayer ? " -> player" : $" -> {t.Monster?.Id.Entry}";

    /// <summary>play_card indices as GameActionService resolves them: hand position; enemies[] or players[] for a target.</summary>
    static PolicyAction? PlayAction(CombatState combat, Player me, int handIndex, CardModel card, Creature? target)
    {
        switch (card.TargetType)
        {
            case TargetType.AnyEnemy:
                int enemy = target == null ? -1 : combat.Enemies.ToList().IndexOf(target);
                return enemy >= 0 && target!.IsHittable ? new PolicyAction("play_card", handIndex, enemy) : null;
            case TargetType.AnyAlly:
                int ally = target == null ? -1 : PlayerIndex(combat, target);
                return ally >= 0 && target != me.Creature ? new PolicyAction("play_card", handIndex, ally) : null;
            case TargetType.None or TargetType.Self or TargetType.AllEnemies or TargetType.RandomEnemy or TargetType.AllAllies:
                return new PolicyAction("play_card", handIndex);
            default:
                return null;
        }
    }

    /// <summary>use_potion indices as GameActionService resolves them: potion slot; enemies[] or players[] for a target.</summary>
    static PolicyAction? PotionAction(CombatState? combat, Player me, PotionModel potion, Creature? target)
    {
        int slot = me.PotionSlots.ToList().IndexOf(potion);
        if (slot < 0) return null;
        switch (potion.TargetType)
        {
            case TargetType.AnyEnemy:
                int enemy = target == null || combat == null ? -1 : combat.Enemies.ToList().IndexOf(target);
                return enemy >= 0 && target!.IsHittable ? new PolicyAction("use_potion", TargetIndex: enemy, OptionIndex: slot) : null;
            case TargetType.AnyPlayer:
                bool explicitTarget = combat != null && CombatManager.Instance.IsInProgress && me.RunState.Players.Count > 1
                    && combat.PlayerCreatures.Count(c => c.IsAlive) > 1;
                if (!explicitTarget) return target == null || target == me.Creature ? new PolicyAction("use_potion", OptionIndex: slot) : null;
                int player = target == null ? -1 : PlayerIndex(combat!, target);
                return player >= 0 ? new PolicyAction("use_potion", TargetIndex: player, OptionIndex: slot) : null;
            case TargetType.AnyAlly:
                return null;
            default:
                return target == null || target == me.Creature ? new PolicyAction("use_potion", OptionIndex: slot) : null;
        }
    }

    static int PlayerIndex(CombatState combat, Creature target)
    {
        var players = combat.Players
            .OrderBy(p => combat.RunState is RunState run ? run.GetPlayerSlotIndex(p) : 0).ToList();
        return players.FindIndex(p => p.Creature == target);
    }

    /// <summary>Same condition as NPotionPopup (upstream RunSession.UsablePotions).</summary>
    static IEnumerable<PotionModel> UsablePotions(Player me, bool inCombat) =>
        me.CanUseOrRemovePotions && !me.Creature.IsDead
            ? me.Potions.Where(p => !p.IsQueued && p.PassesCustomUsabilityCheck &&
                                    (p.Usage == PotionUsage.AnyTime || (inCombat && p.Usage == PotionUsage.CombatOnly)))
            : Enumerable.Empty<PotionModel>();

    /// <summary>Out-of-combat use of any-time potions; offered on rewards, events, rest sites, shops, chests and the map.</summary>
    static void AddPotionActions(PolicyDecision d, Player me)
    {
        foreach (var p in UsablePotions(me, inCombat: false))
            if (p.IsValidTarget(me.Creature) || p.IsValidTarget(null))
                d.Add(ActionKind.UsePotion, "use " + p.Id.Entry, PotionAction(null, me, p, null), a: p);
    }

    static void AddPotionDiscards(PolicyDecision d, Player me, bool discardSupported)
    {
        if (!me.CanUseOrRemovePotions) return;
        var slots = me.PotionSlots;
        for (int i = 0; i < slots.Count; i++)
            if (slots[i] is { IsQueued: false } p)
                d.Add(ActionKind.DiscardPotion, "discard " + p.Id.Entry, discardSupported ? new PolicyAction("discard_potion", OptionIndex: i) : null, a: p);
    }

    // ---------------------------------------------------------------- map

    public static PolicyDecision? Map(RunState s, Player me, Func<MapPoint, int?>? nodeIndex)
    {
        var d = new PolicyDecision(DecisionKind.Map);
        foreach (var p in RunSessionMap.Travelable(s).OrderBy(p => p.coord.col))
        {
            int? index = nodeIndex?.Invoke(p);
            d.Add(ActionKind.MapMove, $"-> ({p.coord.col},{p.coord.row}) {p.PointType}",
                index is int i ? new PolicyAction("choose_map_node", OptionIndex: i) : null, a: p);
        }
        if (d.Decision.Choices.Count == 0) return null;
        AddPotionActions(d, me);
        return d;
    }

    // ---------------------------------------------------------------- rest site

    /// <param name="alreadyChose">An option was already taken in this room, so leaving is allowed.</param>
    public static PolicyDecision? RestSite(IReadOnlyList<RestSiteOption> opts, Player me, bool alreadyChose, Func<RestSiteOption, bool>? needsTarget)
    {
        var d = new PolicyDecision(DecisionKind.RestSite);
        bool canSmith = opts.Any(o => o is SmithRestSiteOption && o.IsEnabled);
        for (int i = 0; i < opts.Count; i++)
        {
            if (!opts[i].IsEnabled) continue;
            if (opts[i] is HealRestSiteOption && canSmith && RunSession.RestHealInfo(me) is { actual: 0, extra: 0 }) continue;
            bool target = needsTarget?.Invoke(opts[i]) ?? false;
            d.Add(ActionKind.RestOption, opts[i].OptionId, target ? null : new PolicyAction("choose_rest_option", OptionIndex: i), a: opts[i]);
        }
        if (d.Decision.Choices.Count == 0) return null;
        if (alreadyChose) d.Add(ActionKind.Leave, "leave", new PolicyAction("proceed"));
        if (alreadyChose && d.Decision.Choices.Count == 1) return null;
        AddPotionActions(d, me);
        return d;
    }

    // ---------------------------------------------------------------- event

    public static PolicyDecision? Event(EventModel ev, Player me)
    {
        var d = new PolicyDecision(DecisionKind.Event);
        var opts = ev.CurrentOptions;
        for (int i = 0; i < opts.Count; i++)
        {
            if (opts[i].IsLocked || ExcludedEventOptions.Contains(opts[i].TextKey)) continue;
            d.Add(ActionKind.EventOption, opts[i].TextKey, new PolicyAction("choose_event_option", OptionIndex: i), a: opts[i], b: HoverObject(opts[i]));
        }
        if (d.Decision.Choices.Count == 0) return null;
        AddPotionActions(d, me);
        return d;
    }

    /// <summary>First card, relic or potion in the option's hover tips (the option's own relic excluded).</summary>
    static object? HoverObject(EventOption opt)
    {
        if (opt.HoverTips == null) return null;
        foreach (var t in opt.HoverTips)
        {
            object? m = t is MegaCrit.Sts2.Core.HoverTips.CardHoverTip ct ? ct.Card
                : t is MegaCrit.Sts2.Core.HoverTips.HoverTip ht ? ht.CanonicalModel : null;
            if (m is CardModel or PotionModel || (m is RelicModel r && r.Id != opt.Relic?.Id)) return m;
        }
        return null;
    }

    // ---------------------------------------------------------------- shop

    /// <param name="entryAction">Maps a purchasable entry to its nekospire action (null: not executable).</param>
    /// <param name="failed">
    /// Entries whose purchase already failed in this room. A failed purchase changes nothing, so retrying gives
    /// the same result; upstream stops offering them (RunSession._failed) and so must we, or the network picks
    /// the same purchase forever.
    /// </param>
    public static PolicyDecision Shop(MerchantInventory inv, Player me, Func<MerchantEntry, PolicyAction?>? entryAction, PolicyAction? leave,
        Func<MerchantEntry, bool>? failed = null)
    {
        var d = new PolicyDecision(DecisionKind.Shop);
        bool Failed(MerchantEntry e) => failed?.Invoke(e) ?? false;
        foreach (MerchantEntry e in inv.AllEntries)
        {
            if (e.IsStocked && !e.EnoughGold && !Failed(e) && e is not MerchantCardRemovalEntry { Used: true }) d.Decision.Locked.Add((EntryObject(e), e.Cost));
            if (!e.IsStocked || !e.EnoughGold || Failed(e)) continue;
            if (e is MerchantPotionEntry && !me.HasOpenPotionSlots) continue;
            if (e is MerchantCardRemovalEntry { Used: true }) continue;
            d.Add(ActionKind.Buy, $"buy {EntryLabel(e)} ({e.Cost})", entryAction?.Invoke(e), a: EntryObject(e), f0: e.Cost);
        }
        d.Add(ActionKind.Leave, "leave", leave);
        AddPotionActions(d, me);
        if (!me.HasOpenPotionSlots && inv.AllEntries.Any(e => e is MerchantPotionEntry && e.IsStocked && e.EnoughGold && !Failed(e)))
            AddPotionDiscards(d, me, discardSupported: true);
        if (!me.HasOpenPotionSlots)
            foreach (var e in inv.AllEntries)
                if (e is MerchantPotionEntry { Model: { } p } && e.IsStocked) d.Decision.Shown.Add((p, Zone.Offer, e.Cost));
        return d;
    }

    static object EntryObject(MerchantEntry e) => e switch
    {
        MerchantCardEntry c => c.CreationResult.Card,
        MerchantRelicEntry { Model: { } r } => r,
        MerchantPotionEntry { Model: { } p } => p,
        _ => e,
    };

    static string EntryLabel(MerchantEntry e) => e switch
    {
        MerchantCardEntry c => c.CreationResult.Card.Id.Entry,
        MerchantRelicEntry r => r.Model?.Id.Entry ?? "relic",
        MerchantPotionEntry p => p.Model?.Id.Entry ?? "potion",
        MerchantCardRemovalEntry => "card removal",
        _ => e.GetType().Name,
    };

    // ---------------------------------------------------------------- treasure

    public static PolicyDecision Treasure(IReadOnlyList<RelicModel> relics, Player me, PolicyAction? skip)
    {
        var d = new PolicyDecision(DecisionKind.Treasure);
        for (int i = 0; i < relics.Count; i++)
            d.Add(ActionKind.PickRelic, "take " + relics[i].Id.Entry, new PolicyAction("choose_treasure_relic", OptionIndex: i), a: relics[i]);
        d.Add(ActionKind.SkipRelic, "skip", skip);
        AddPotionActions(d, me);
        return d;
    }

    // ---------------------------------------------------------------- rewards

    /// <param name="rewardIndex">Maps a reward to its claim_reward index (null: its button is gone or disabled).</param>
    public static PolicyDecision Rewards(PendingRewards pr, Player me, Func<Reward, int?>? rewardIndex, bool canLeave)
    {
        var d = new PolicyDecision(DecisionKind.Rewards);
        var available = pr.Available;
        foreach (var r in available)
        {
            int? index = rewardIndex?.Invoke(r);
            d.Add(ActionKind.TakeReward, "take " + RewardLabel(r), index is int i ? new PolicyAction("claim_reward", OptionIndex: i) : null, a: r);
        }
        foreach (var r in available) if (r is CardReward cr) ShowRewardCards(d.Decision, pr.Set, cr);
        if (!pr.Set.DisallowSkipping || available.Count == 0)
            d.Add(ActionKind.Leave, "leave", canLeave ? new PolicyAction(PolicyAction.LeaveRewards) : null);
        AddPotionActions(d, me);
        if (!me.HasOpenPotionSlots && pr.Set.Rewards.Any(r => r is PotionReward && !r.SuccessfullySelected))
        {
            // nekospire does not discard potions while a rewards screen is open, so these stay unexecutable.
            AddPotionDiscards(d, me, discardSupported: false);
            foreach (var r in pr.Set.Rewards) if (r is PotionReward { Potion: not null } p && !p.SuccessfullySelected) d.Decision.Shown.Add((p, Zone.Offer, 0));
        }
        return d;
    }

    static string RewardLabel(Reward r) => r switch
    {
        GoldReward => "gold",
        PotionReward p => "potion " + p.Potion?.Id.Entry,
        RelicReward rr => "relic " + rr.Relic?.Id.Entry,
        CardReward => "card reward",
        _ => r.GetType().Name,
    };

    static void ShowRewardCards(Decision d, RewardsSet set, CardReward cr)
    {
        var z = Observation.RewardZone(set, cr);
        foreach (var c in cr.Cards) d.Shown.Add((c, z, 0));
    }

    // ---------------------------------------------------------------- card reward

    /// <summary>
    /// One open card reward. RewardsFlow.ActiveSet / ActiveChoice / ActiveAttempted must already describe the
    /// rewards screen underneath (the encoder shows the rest of that set).
    /// </summary>
    public static PolicyDecision CardRewardChoice(IReadOnlyList<CardModel> cards, IReadOnlyList<string> alternativeIds,
        Func<CardModel, int?>? cardIndex)
    {
        var d = new PolicyDecision(DecisionKind.CardReward);
        if (RewardsFlow.ActiveSet is { } set)
            foreach (var r in RewardsFlow.Unclaimed(set, RewardsFlow.ActiveAttempted, RewardsFlow.ActiveChoice))
            {
                d.Decision.Shown.Add((r, Zone.Offer, 0));
                if (r is CardReward other) ShowRewardCards(d.Decision, set, other);
            }
        foreach (var c in cards)
        {
            int? index = cardIndex?.Invoke(c);
            d.Add(ActionKind.PickCardReward, "take " + c.Id.Entry, index is int i ? new PolicyAction("choose_reward_card", OptionIndex: i) : null, a: c);
        }
        for (int i = 0; i < alternativeIds.Count; i++)
            d.Add(ActionKind.CardRewardAlt, alternativeIds[i], new PolicyAction(PolicyAction.RewardAlternative, OptionIndex: i), key: Vocab.Alt(alternativeIds[i]));
        return d;
    }

    // ---------------------------------------------------------------- card selection

    /// <summary>
    /// "Pick one, repeat, confirm". Returns null where upstream's engine resolves the screen without asking
    /// (nothing to choose) — the live UI still needs a click there, which the caller leaves to its fallback.
    /// </summary>
    /// <param name="allowCancel">False once a selection was already cancelled in this room (upstream offers one cancel per room).</param>
    public static PolicyDecision? CardSelect(PendingCardChoice pc, IReadOnlyList<int> selected, bool allowCancel,
        Func<CardModel, int?>? cardIndex, PolicyAction? confirm, PolicyAction? cancel)
    {
        int max = Math.Min(pc.Max, pc.Options.Count);
        int min = Math.Min(pc.Min, max);
        bool upgrade = pc.Info?.Method == "FromDeckForUpgrade" && max > 0;
        if (upgrade) min = Math.Max(min, 1);
        bool canCancel = pc.Cancelable && !upgrade && selected.Count == 0 && allowCancel;
        if (max == 0 || (min == max && max == pc.Options.Count && !canCancel)) return null;
        var d = new PolicyDecision(DecisionKind.CardSelect, selected.Count) { Selecting = pc, SelectedIndices = selected };
        bool upgradeAny = pc.Info?.Method is { } meth && meth.Contains("ForUpgrade");
        for (int i = 0; i < pc.Options.Count; i++)
        {
            if (selected.Contains(i)) continue;
            int? index = cardIndex?.Invoke(pc.Options[i]);
            d.Add(ActionKind.SelectCard, "select " + pc.Options[i].Id.Entry,
                index is int k ? new PolicyAction("select_deck_card", OptionIndex: k) : null,
                a: pc.Options[i], b: upgradeAny ? Observation.UpgradedCopy(pc.Options[i]) : null);
        }
        if (selected.Count >= min) d.Add(ActionKind.ConfirmSelection, "confirm", confirm);
        if (canCancel) d.Add(ActionKind.Leave, "cancel", cancel);
        return d;
    }
}
