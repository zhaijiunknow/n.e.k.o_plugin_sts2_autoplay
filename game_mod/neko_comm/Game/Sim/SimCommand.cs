// Primitive state mutations for the deterministic resolver.
// Each command is a pure-state operation — no game globals, no async, no VFX.
// Damage/block mods go through a small hook seam (SimHooks) that Phase 2 fleshes out with the
// real modifier chain (strength/weak/vulnerable/frail/dexterity/...). For Phase 0 the seam is
// identity so simple cards are exact.
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    /// <summary>Modifier chain over the SimPower behaviour registry. Strength/dexterity/weak/vulnerable/
    /// frail/intangible are applied here from each creature's *current* powers (the resolver state, not a
    /// stale snapshot), which is what makes combat math faithful to mid-combat power changes.</summary>
    public static class SimHooks
    {
        public static int ModifyAttackBase(SimState state, SimCombatant? attacker, int baseDamage, bool isAttack)
        {
            if (attacker == null || !isAttack) return baseDamage;
            var v = baseDamage;
            foreach (var (id, _) in attacker.Powers)
                if (SimPower.TryGet(id, out var beh) && beh.AttackBase != null)
                    v = beh.AttackBase(attacker, v);
            return v;
        }

        public static double ModifyDamage(SimState state, SimCombatant? attacker, SimCombatant? target, double baseDamage)
        {
            var v = baseDamage;
            if (attacker != null)
                foreach (var (id, _) in attacker.Powers)
                    if (SimPower.TryGet(id, out var beh) && beh.DamageDealt != null)
                        v = beh.DamageDealt(attacker, v);
            if (target != null)
                foreach (var (id, _) in target.Powers)
                    if (SimPower.TryGet(id, out var beh) && beh.DamageTaken != null)
                        v = beh.DamageTaken(target, v);
            return v;
        }

        public static int ModifyBlockGained(SimState state, SimCombatant gainer, int block)
        {
            if (gainer == null) return block;
            double v = block;
            foreach (var (id, _) in gainer.Powers)
                if (SimPower.TryGet(id, out var beh) && beh.BlockBase != null)
                    v = beh.BlockBase(gainer, (int)v);
            foreach (var (id, _) in gainer.Powers)
                if (SimPower.TryGet(id, out var beh) && beh.BlockMult != null)
                    v = beh.BlockMult(gainer, v);
            return (int)v; // truncate after frail multiplier
        }

        public static int ModifyHandDraw(SimState state, int count) => count;
    }

    public static class SimCommand
    {
        // Damage/block/heal are modelled as TRANSIENT powers (the power-centric view): applying
        // "damage_power"/"block_power" to a target resolves it via the modifier chain (SimHooks) then it
        // disappears. DealDamage/GainBlock are thin wrappers over the same power dispatch — one source of
        // truth, so a card's effect is "apply these powers to these targets" and the resolver is uniform.
        public static int DealDamage(SimState state, SimCombatant source, SimCombatant target, int baseDamage, bool isAttack = true)
        {
            if (target == null || !target.Alive || baseDamage <= 0) return 0;
            var hpBefore = target.Hp;
            ApplyPower(state, source, target, "damage_power", baseDamage, isAttack);
            return hpBefore - target.Hp;
        }

        public static void GainBlock(SimState state, SimCombatant gainer, int block)
            => ApplyPower(state, null, gainer, "block_power", block, false);

        // Dispatch: a transient power resolves immediately; otherwise it stacks (persistent).
        public static void ApplyPower(SimState state, SimCombatant? source, SimCombatant target, string powerId, int amount, bool isAttack = false)
        {
            if (target == null) return;
            if (SimPower.TryGet(powerId, out var beh) && beh.ResolveTransient != null)
            {
                beh.ResolveTransient(state, source, target, amount, isAttack);
                return;
            }
            ApplyPower(target, powerId, amount);   // persistent stack
        }

        public static void Heal(SimCombatant target, int amount)
        {
            if (amount <= 0 || target == null) return;
            target.Hp = Math.Min(target.MaxHp, target.Hp + amount);
        }

        public static void GainMaxHp(SimCombatant target, int amount)
        {
            if (amount <= 0 || target == null) return;
            target.MaxHp += amount;
            target.Hp += amount;
        }

        public static void ApplyPower(SimCombatant target, string powerId, int amount)
        {
            if (target == null || string.IsNullOrEmpty(powerId) || amount == 0) return;
            target.Powers[powerId] = target.Powers.TryGetValue(powerId, out var cur) ? cur + amount : amount;
        }

        // Draw n cards from the draw pile (reshuffling discard deterministically if empty).
        public static void Draw(SimState state, int n)
        {
            for (var i = 0; i < n; i++)
            {
                if (state.DrawPile.Count == 0)
                    ReshuffleDiscardIntoDraw(state);
                if (state.DrawPile.Count == 0) return;
                var card = state.DrawPile[0];
                state.DrawPile.RemoveAt(0);
                state.Hand.Add(card);
            }
        }

        public static void ReshuffleDiscardIntoDraw(SimState state)
        {
            if (state.DiscardPile.Count == 0) return;
            // Deterministic reshuffle (draw pile order = discard order). Randomised in Phase 1.
            foreach (var c in state.DiscardPile) state.DrawPile.Add(c);
            state.DiscardPile.Clear();
        }

        public static void MoveToDiscard(SimState state, SimCard card)
        {
            state.Hand.Remove(card);
            if (card.ExhaustsOnPlay) state.ExhaustPile.Add(card);
            else state.DiscardPile.Add(card);
        }

        // Exhaust a card and fire on-exhaust hooks (e.g. Feel No Pain gains block).
        public static void ExhaustCard(SimState state, SimCombatant owner, SimCard card)
        {
            state.Hand.Remove(card);
            state.ExhaustPile.Add(card);
            if (owner != null)
                foreach (var (id, _) in owner.Powers)
                    if (SimPower.TryGet(id, out var beh) && beh.OnExhaust != null)
                        beh.OnExhaust(state, owner);
        }

        public static void GainEnergy(SimState state, int amount)
        {
            if (amount == 0) return;
            state.ActiveEnergy += amount;
        }

        public static void LoseEnergy(SimState state, int amount)
        {
            if (amount == 0) return;
            state.ActiveEnergy = Math.Max(0, state.ActiveEnergy - amount);
        }

        public static void GainStars(SimState state, int amount)
        {
            if (amount == 0) return;
            state.ActiveStars = Math.Max(0, state.ActiveStars + amount);
        }

        public static void LoseStars(SimState state, int amount)
        {
            if (amount == 0) return;
            state.ActiveStars = Math.Max(0, state.ActiveStars - amount);
        }

        public static void LoseBlock(SimState state, SimCombatant c, int amount)
        {
            if (amount <= 0 || c == null) return;
            c.Block = Math.Max(0, c.Block - amount);
        }

        public static void SetCurrentHp(SimCombatant c, int hp)
        {
            if (c == null) return;
            c.Hp = Math.Clamp(hp, 0, c.MaxHp);
        }

        // "Lose HP" / poison: bypasses block entirely (unlike DealDamage).
        public static void LoseMaxHp(SimCombatant c, int amount)
        {
            if (amount <= 0 || c == null) return;
            c.MaxHp = Math.Max(0, c.MaxHp - amount);
            if (c.Hp > c.MaxHp) c.Hp = c.MaxHp;
        }

        public static void LoseHp(SimCombatant c, int amount)
        {
            if (amount <= 0 || c == null) return;
            c.Hp = Math.Max(0, c.Hp - amount);
        }

        public static void RemovePower(SimCombatant c, string powerId)
        {
            if (c == null || string.IsNullOrEmpty(powerId)) return;
            c.Powers.Remove(powerId);
        }

        public static void DecrementPower(SimCombatant c, string powerId, int delta)
        {
            if (c == null || !c.Powers.TryGetValue(powerId, out var cur)) return;
            var v = cur - delta;
            if (v <= 0) c.Powers.Remove(powerId);
            else c.Powers[powerId] = v;
        }

        /// <summary>Add a card into a concrete pile (used by summon / status-card / X-value generation).</summary>
        public static void AddCardTo(SimState state, SimCard card, string pile)
        {
            switch (pile)
            {
                case "hand": state.Hand.Add(card); break;
                case "draw": state.DrawPile.Add(card); break;
                case "discard": state.DiscardPile.Add(card); break;
                case "exhaust": state.ExhaustPile.Add(card); break;
            }
        }
    }
}
