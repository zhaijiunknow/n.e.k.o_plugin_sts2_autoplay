// Data-driven power behaviour registry. Each power id maps to a behaviour implementing the hook
// slots it participates in. SimHooks consults the active powers of the combatant in play against
// this registry, so adding a new power is one registry entry (matching powers.json hook signatures
// — Phase 2 starts with the combat-critical set; expanded to 268 in later passes).
//
// Pure / game-type-free. This is where "忠实" for combat math lives: strength/dexterity flat adds,
// weak/vulnerable/frail multipliers, intangible damage cap, buffer/thorns (handled in DealDamage),
// and turn-start/turn-end tickers (ritual/plating/demon-form/poison/regen/doom).
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    // A transient power resolves immediately and disappears (damage/block/heal) rather than stacking
    // on the creature. Given the running state, the source, target, amount and whether it is an attack.
    public delegate void SimPowerResolve(SimState state, SimCombatant? source, SimCombatant target, int amount, bool isAttack);

    public sealed class SimPowerBehavior
    {
        // A transient effect (damage_power / block_power / heal_power): applied to a target, resolved via
        // the modifier chain, then gone. Set => ApplyPower dispatches to it instead of stacking.
        public SimPowerResolve? ResolveTransient;

        // Owner = the creature holding the power.
        public Func<SimCombatant, int, int>? AttackBase;       // flat add to outgoing damage base (strength, accuracy)
        public Func<SimCombatant, double, double>? DamageDealt; // multiplier on outgoing damage (weak, intangible)
        public Func<SimCombatant, double, double>? DamageTaken; // multiplier on incoming damage (vulnerable, intangible)
        public Func<SimCombatant, int, int>? BlockBase;         // flat add to block gained (dexterity)
        public Func<SimCombatant, double, double>? BlockMult;   // multiplier on block gained (frail)
        public Action<SimState, SimCombatant>? TurnStart;       // at the creature's turn start
        public Action<SimState, SimCombatant>? TurnEnd;         // at the creature's turn end
        public Action<SimState, SimCombatant>? OnExhaust;       // when the owner exhausts a card
    }

    public static class SimPower
    {
        // Common combat-critical powers (Phase 2 set). Expanded toward all 268 in later passes.
        private static readonly Dictionary<string, SimPowerBehavior> Registry = new(StringComparer.OrdinalIgnoreCase)
        {
            // Transient powers: applied to a target, resolved via the modifier chain, then gone.
            ["damage_power"] = new SimPowerBehavior
            {
                ResolveTransient = (s, src, tgt, amount, isAtk) =>
                {
                    if (amount <= 0 || tgt == null || !tgt.Alive) return;
                    var attackBase = SimHooks.ModifyAttackBase(s, src, amount, isAtk);
                    var dmg = SimHooks.ModifyDamage(s, src, tgt, attackBase);
                    var amt = (int)dmg; // truncate after multipliers (STS2)
                    if (amt <= 0) return;
                    if (tgt.Powers.TryGetValue("buffer_power", out var buf) && buf > 0)
                    {
                        tgt.Powers["buffer_power"] = buf - 1;
                        return;
                    }
                    var absorbed = Math.Min(tgt.Block, amt);
                    tgt.Block -= absorbed;
                    var rem = amt - absorbed;
                    tgt.Hp -= rem;
                    if (tgt.Hp < 0) tgt.Hp = 0;
                    if (src != null && tgt.Powers.TryGetValue("thorns_power", out var thorns) && thorns > 0)
                        src.Hp = Math.Max(0, src.Hp - thorns);
                },
            },
            ["block_power"] = new SimPowerBehavior
            {
                ResolveTransient = (s, src, tgt, amount, isAtk) =>
                {
                    if (amount <= 0 || tgt == null) return;
                    tgt.Block += SimHooks.ModifyBlockGained(s, tgt, amount);
                },
            },
            ["heal_power"] = new SimPowerBehavior
            {
                ResolveTransient = (s, src, tgt, amount, isAtk) =>
                {
                    if (amount <= 0 || tgt == null) return;
                    tgt.Hp = Math.Min(tgt.MaxHp, tgt.Hp + amount);
                },
            },

            ["strength_power"] = new SimPowerBehavior { AttackBase = (o, b) => b + Pow(o.Powers, "strength_power") },
            ["dexterity_power"] = new SimPowerBehavior { BlockBase = (o, b) => b + Pow(o.Powers, "dexterity_power") },
            ["weak_power"] = new SimPowerBehavior { DamageDealt = (o, d) => d * 0.75 },
            ["vulnerable_power"] = new SimPowerBehavior { DamageTaken = (o, d) => d * 1.5 },
            ["frail_power"] = new SimPowerBehavior { BlockMult = (o, b) => b * 0.75 },
            ["intangible_power"] = new SimPowerBehavior
            {
                DamageDealt = (o, d) => Math.Min(d, 1),
                DamageTaken = (o, d) => Math.Min(d, 1),
            },
            // Turn tickers.
            ["ritual_power"] = new SimPowerBehavior { TurnStart = (s, c) => Apply(c, "strength_power", Pow(c.Powers, "ritual_power")) },
            ["plating_power"] = new SimPowerBehavior { TurnStart = (s, c) => SimCommand.GainBlock(s, c, Pow(c.Powers, "plating_power")) },
            ["plated_armor_power"] = new SimPowerBehavior { TurnStart = (s, c) => SimCommand.GainBlock(s, c, Pow(c.Powers, "plated_armor_power")) },
            ["demon_form_power"] = new SimPowerBehavior { TurnStart = (s, c) => Apply(c, "strength_power", 1) },
            ["regen_power"] = new SimPowerBehavior { TurnEnd = (s, c) => SimCommand.Heal(c, Pow(c.Powers, "regen_power")) },
            ["poison_power"] = new SimPowerBehavior
            {
                TurnEnd = (s, c) =>
                {
                    var poison = Pow(c.Powers, "poison_power");
                    if (poison > 0)
                    {
                        SimCommand.LoseHp(c, poison);
                        SimCommand.DecrementPower(c, "poison_power", 1);
                    }
                },
            },
            ["doom_power"] = new SimPowerBehavior
            {
                // At the end of the creature's turn, take damage equal to Doom stacks, then -1.
                TurnEnd = (s, c) =>
                {
                    var doom = Pow(c.Powers, "doom_power");
                    if (doom > 0)
                    {
                        SimCommand.LoseHp(c, doom);
                        SimCommand.DecrementPower(c, "doom_power", 1);
                    }
                },
            },
            // On-exhaust hook: Feel No Pain gains block per exhausted card.
            ["feel_no_pain_power"] = new SimPowerBehavior
            {
                OnExhaust = (s, c) => SimCommand.GainBlock(s, c, Pow(c.Powers, "feel_no_pain_power")),
            },
            // Wraith Form: lose 1 Dexterity at the start of each turn.
            ["wraith_form_power"] = new SimPowerBehavior
            {
                TurnStart = (s, c) => SimCommand.DecrementPower(c, "dexterity_power", Pow(c.Powers, "wraith_form_power")),
            },
            // Biased Cognition: lose Focus each turn (Focus is the orb stat on the creature).
            ["biased_cognition_power"] = new SimPowerBehavior
            {
                TurnStart = (s, c) =>
                {
                    var amt = Pow(c.Powers, "biased_cognition_power");
                    if (amt > 0) c.Focus = Math.Max(0, c.Focus - amt);
                },
            },
        };

        public static bool TryGet(string powerId, out SimPowerBehavior behavior)
            => Registry.TryGetValue(powerId, out behavior!);

        public static IEnumerable<string> KnownPowerIds => Registry.Keys;

        private static int Pow(IReadOnlyDictionary<string, int> d, string key)
            => d.TryGetValue(key, out var v) ? v : 0;

        private static void Apply(SimCombatant c, string powerId, int amount)
        {
            if (amount == 0) return;
            var cur = Pow(c.Powers, powerId);
            c.Powers[powerId] = cur + amount;
        }
    }
}
