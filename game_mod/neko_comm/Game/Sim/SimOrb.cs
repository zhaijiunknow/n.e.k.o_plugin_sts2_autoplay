// Orb mechanic — a general combat resource any character can use if a card provides the action (not
// Defect-specific). Pure / game-type-free. Channel pushes an orb onto a capped queue; Evoke pops the
// front orb and resolves it; Passives fire for each orb at the owner's turn end (Dark grows, Glass
// decays). Focus adds to both passive and evoke amounts.
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    public sealed class SimOrb
    {
        public string OrbId = "";
        public int Passive;
        public int Evoke;
        public int Value;    // Dark grows by Passive; Glass decays by 1 after its passive.

        public SimOrb Clone() => (SimOrb)MemberwiseClone();
    }

    public static class SimOrbData
    {
        // (passive, evoke) per orb — mirrors the game's orb tuning.
        private static readonly Dictionary<string, (int passive, int evoke)> Base = new(StringComparer.OrdinalIgnoreCase)
        {
            ["LIGHTNING"] = (3, 8),
            ["FROST"] = (2, 5),
            ["PLASMA"] = (0, 2),
            ["DARK"] = (0, 6),
            ["GLASS"] = (4, 8),
        };

        public static SimOrb Create(string id)
        {
            var (p, e) = Base.TryGetValue(id, out var v) ? v : (0, 0);
            // Dark accumulates damage from 0; Glass starts at its passive and decays.
            var value = id.Equals("DARK", StringComparison.OrdinalIgnoreCase) ? 0 : p;
            return new SimOrb { OrbId = id, Passive = p, Evoke = e, Value = value };
        }

        public static int CapacityFor => 3; // default orb slots
    }

    public static class SimOrbEngine
    {
        public static void ChannelOrb(SimState state, SimCombatant owner, string orbId, int times = 1)
        {
            for (var i = 0; i < Math.Max(1, times); i++)
            {
                if (owner.Orbs.Count >= Math.Max(0, owner.OrbCapacity)) return;
                owner.Orbs.Add(SimOrbData.Create(orbId));
            }
        }

        public static void EvokeOrb(SimState state, SimCombatant owner, int times = 1)
        {
            for (var i = 0; i < Math.Max(1, times); i++)
            {
                if (owner.Orbs.Count == 0) return;
                var orb = owner.Orbs[0];
                owner.Orbs.RemoveAt(0);
                ResolveEvoke(state, owner, orb);
            }
        }

        public static void PassiveOrbs(SimState state, SimCombatant owner)
        {
            foreach (var orb in new List<SimOrb>(owner.Orbs))
                ResolvePassive(state, owner, orb);
        }

        /// <summary>Interpret a card/potion orb action (channel/evoke/passive) for the owner.</summary>
        public static void ApplyOrbAction(SimState state, SimCombatant owner, SimOrbAction action)
        {
            switch (action.Action?.ToLowerInvariant())
            {
                case "channel": ChannelOrb(state, owner, action.OrbId, action.Times); break;
                case "evoke": EvokeOrb(state, owner, action.Times); break;
                case "passive": PassiveOrbs(state, owner); break;
            }
        }

        // ---- resolution ------------------------------------------------------

        private static void ResolveEvoke(SimState state, SimCombatant owner, SimOrb orb)
        {
            var f = owner.Focus;
            switch (orb.OrbId.ToUpperInvariant())
            {
                case "LIGHTNING": HitLowest(state, owner, orb.Evoke + f); break;
                case "FROST": SimCommand.GainBlock(state, owner, orb.Evoke + f); break;
                case "PLASMA": SimCommand.GainEnergy(state, orb.Evoke + f); break;
                case "DARK": HitLowest(state, owner, orb.Value + f); break;
                case "GLASS": HitAll(state, owner, orb.Evoke + f); break;
            }
        }

        private static void ResolvePassive(SimState state, SimCombatant owner, SimOrb orb)
        {
            var f = owner.Focus;
            switch (orb.OrbId.ToUpperInvariant())
            {
                case "LIGHTNING": HitLowest(state, owner, orb.Passive + f); break;
                case "FROST": SimCommand.GainBlock(state, owner, orb.Passive + f); break;
                case "PLASMA": SimCommand.GainEnergy(state, orb.Passive + f); break;
                case "DARK": orb.Value += orb.Passive; break;   // grows
                case "GLASS": HitAll(state, owner, orb.Passive + f); orb.Value = Math.Max(0, orb.Value - 1); break; // decays
            }
        }

        private static void HitLowest(SimState state, SimCombatant owner, int damage)
        {
            var target = LowestHpEnemy(state);
            if (target != null) SimCommand.DealDamage(state, owner, target, damage, isAttack: false);
        }

        private static void HitAll(SimState state, SimCombatant owner, int damage)
        {
            foreach (var e in state.Enemies)
                if (e.Alive) SimCommand.DealDamage(state, owner, e, damage, isAttack: false);
        }

        private static SimCombatant? LowestHpEnemy(SimState state)
        {
            SimCombatant? best = null;
            foreach (var e in state.Enemies)
                if (e.Alive && (best == null || e.Hp < best.Hp)) best = e;
            return best;
        }
    }
}
