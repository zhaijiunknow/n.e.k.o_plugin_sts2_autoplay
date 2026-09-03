// Relic combat mods, aggregated data-driven from a relic's declared DynamicVar keys. Pure / game-free.
// A relic that declares an EnergyVar/CardsVar/BlockVar/StrengthPower contributes a flat turn/start-o-
// combat modifier. This captures the common numeric relics; on-play triggers (AfterAttack, AfterCard
// Played, ...) are per-relic code and are not modelled — SimBuild flags them honestly.
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    public static class SimRelic
    {
        /// <summary>Map a relic's var map to combat mods: (energy/turn, draw/turn, start block, start strength).</summary>
        public static (int energy, int draw, int block, int strength) Aggregate(IReadOnlyDictionary<string, int> vars)
        {
            int G(string k) => vars.TryGetValue(k, out var v) ? v : 0;
            return (G("Energy"), G("Cards"), G("Block"), G("Strength") + G("StrengthPower"));
        }
    }
}
