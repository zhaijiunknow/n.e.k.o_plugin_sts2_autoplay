// Canonicalise + diff two pure SimStates. Pure / game-type-free so it is unit-testable.
// Used by the replay harness to assert that re-applying a captured action reproduces the
// captured post-state. Canonicalisation normalises order-only differences (pile shuffle order,
// card insertion order) so replay does not fail on non-meaningful reordering.
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    public static class SimDiff
    {
        // Normalise order-only differences in-place so structural equality holds.
        public static void Canonicalize(SimState s)
        {
            SortPile(s.DrawPile);
            SortPile(s.DiscardPile);
            SortPile(s.ExhaustPile);
            // Hand order is computationally meaningful (cards are indexed for play), so leave it.
        }

        private static void SortPile(List<SimCard> pile)
        {
            pile.Sort((x, y) =>
            {
                var c = string.CompareOrdinal(x.Id, y.Id);
                if (c != 0) return c;
                c = x.Cost.CompareTo(y.Cost);
                if (c != 0) return c;
                return x.Damage.CompareTo(y.Damage);
            });
        }

        /// <summary>Returns a list of human-readable mismatch descriptions (empty => identical).</summary>
        public static List<string> Diff(SimState a, SimState b)
        {
            var out_ = new List<string>();
            if (a.Round != b.Round) out_.Add($"round: {a.Round} != {b.Round}");
            if (a.Turn != b.Turn) out_.Add($"turn: {a.Turn} != {b.Turn}");
            if (a.Side != b.Side) out_.Add($"side: {a.Side} != {b.Side}");
            if (a.ActiveEnergy != b.ActiveEnergy) out_.Add($"energy: {a.ActiveEnergy} != {b.ActiveEnergy}");
            if (a.MaxEnergy != b.MaxEnergy) out_.Add($"max_energy: {a.MaxEnergy} != {b.MaxEnergy}");

            DiffCombatants(out_, "player", a.Players, b.Players);
            DiffCombatants(out_, "enemy", a.Enemies, b.Enemies);
            DiffCards(out_, "hand", a.Hand, b.Hand);
            DiffPile(out_, "draw", a.DrawPile, b.DrawPile);
            DiffPile(out_, "discard", a.DiscardPile, b.DiscardPile);
            DiffPile(out_, "exhaust", a.ExhaustPile, b.ExhaustPile);

            if (string.Join(",", a.Potions) != string.Join(",", b.Potions))
                out_.Add("potions differ");
            if (string.Join(",", a.Relics) != string.Join(",", b.Relics))
                out_.Add("relics differ");
            return out_;
        }

        private static void DiffCombatants(List<string> out_, string label, List<SimCombatant> a, List<SimCombatant> b)
        {
            if (a.Count != b.Count) { out_.Add($"{label} count: {a.Count} != {b.Count}"); return; }
            for (var i = 0; i < a.Count; i++)
            {
                var x = a[i]; var y = b[i];
                if (x.Hp != y.Hp) out_.Add($"{label}[{i}].hp: {x.Hp} != {y.Hp}");
                if (x.Block != y.Block) out_.Add($"{label}[{i}].block: {x.Block} != {y.Block}");
                if (x.MaxHp != y.MaxHp) out_.Add($"{label}[{i}].max_hp: {x.MaxHp} != {y.MaxHp}");
                foreach (var k in Keys(x.Powers).Union(Keys(y.Powers)))
                {
                    var px = x.Powers.TryGetValue(k, out var vx) ? vx : 0;
                    var py = y.Powers.TryGetValue(k, out var vy) ? vy : 0;
                    if (px != py) out_.Add($"{label}[{i}].power[{k}]: {px} != {py}");
                }
            }
        }

        private static void DiffCards(List<string> out_, string label, List<SimCard> a, List<SimCard> b)
        {
            if (a.Count != b.Count) { out_.Add($"{label} count: {a.Count} != {b.Count}"); return; }
            for (var i = 0; i < a.Count; i++)
            {
                var x = a[i]; var y = b[i];
                if (x.Id != y.Id) out_.Add($"{label}[{i}].id: {x.Id} != {y.Id}");
                if (x.Cost != y.Cost) out_.Add($"{label}[{i}].cost: {x.Cost} != {y.Cost}");
                if (x.Damage != y.Damage) out_.Add($"{label}[{i}].damage: {x.Damage} != {y.Damage}");
                if (x.Block != y.Block) out_.Add($"{label}[{i}].block: {x.Block} != {y.Block}");
            }
        }

        private static void DiffPile(List<string> out_, string label, List<SimCard> a, List<SimCard> b)
        {
            if (a.Count != b.Count) { out_.Add($"{label} count: {a.Count} != {b.Count}"); return; }
            for (var i = 0; i < a.Count; i++)
            {
                if (a[i].Id != b[i].Id) out_.Add($"{label}[{i}].id: {a[i].Id} != {b[i].Id}");
                if (a[i].Cost != b[i].Cost) out_.Add($"{label}[{i}].cost: {a[i].Cost} != {b[i].Cost}");
            }
        }

        private static IEnumerable<string> Keys(Dictionary<string, int> d) => d.Keys;
    }
}
