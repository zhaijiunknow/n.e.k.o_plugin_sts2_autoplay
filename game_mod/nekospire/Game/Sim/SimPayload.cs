// Pure SimState <-> JSON payload (shared by the capture engine and the offline replay harness).
// Pure / game-type-free, so it can be round-trip tested in the standalone SlmTests project.
using System;
using System.Collections.Generic;
using System.Text.Json;

namespace NekoComm.Game.Sim
{
    public static class SimPayload
    {
        /// <summary>Full, JSON-clean representation of a SimState so replay can reconstruct it faithfully
        /// (incl. the card's CardType / keywords / Script / flags — otherwise behaviour cards like
        /// SECOND_WIND lose their script and replay diverges).</summary>
        public static object ToPayload(SimState s)
        {
            object Combatant(SimCombatant c) => new
            {
                hp = c.Hp, max_hp = c.MaxHp, block = c.Block,
                powers = new Dictionary<string, int>(c.Powers),
            };
            object Card(SimCard c) => new
            {
                id = c.Id, name = c.Name, cost = c.Cost,
                card_type = c.CardType, target = c.Target.ToString(),
                damage = c.Damage, block = c.Block, draw = c.CardsDraw,
                energy_gain = c.EnergyGain, hp_loss = c.HpLoss,
                max_hp_gain = c.MaxHpGain, heal = c.Heal, extra_damage = c.ExtraDamage,
                star_cost = c.StarCost, repeat = c.Repeat,
                exhausts_on_play = c.ExhaustsOnPlay, retains = c.Retains, ethereal = c.Ethereal, innate = c.Innate,
                approximate_effect = c.ApproximateEffect, behavior_unmodeled = c.BehaviorUnmodeled,
                powers = PowersDict(c.Powers),
                script = c.Script.Count > 0 ? c.Script.ConvertAll(OpPayload) : null,
            };
            return new
            {
                round = s.Round, turn = s.Turn, side = s.Side.ToString(),
                energy = s.ActiveEnergy, max_energy = s.MaxEnergy,
                players = s.Players.ConvertAll(Combatant),
                enemies = s.Enemies.ConvertAll(Combatant),
                hand = s.Hand.ConvertAll(Card),
                draw = s.DrawPile.ConvertAll(Card),
                discard = s.DiscardPile.ConvertAll(Card),
                exhaust = s.ExhaustPile.ConvertAll(Card),
            };
        }

        /// <summary>Reverse of ToPayload: rebuild a SimState from the capture JSON (full card surface incl.
        /// Script). Used by the replay harness to reproduce a captured transition.</summary>
        public static SimState FromPayload(string json)
        {
            using var doc = JsonDocument.Parse(json);
            var r = doc.RootElement;
            var s = new SimState
            {
                Round = r.GetProperty("round").GetInt32(),
                Turn = r.GetProperty("turn").GetInt32(),
                Side = r.GetProperty("side").GetString() == "Enemy" ? SimSide.Enemy : SimSide.Player,
                ActiveEnergy = r.GetProperty("energy").GetInt32(),
                MaxEnergy = r.GetProperty("max_energy").GetInt32(),
            };
            foreach (var e in r.GetProperty("players").EnumerateArray()) s.Players.Add(CombatantFromJson(e));
            foreach (var e in r.GetProperty("enemies").EnumerateArray()) s.Enemies.Add(CombatantFromJson(e));
            foreach (var c in r.GetProperty("hand").EnumerateArray()) s.Hand.Add(CardFromJson(c));
            foreach (var c in r.GetProperty("draw").EnumerateArray()) s.DrawPile.Add(CardFromJson(c));
            foreach (var c in r.GetProperty("discard").EnumerateArray()) s.DiscardPile.Add(CardFromJson(c));
            foreach (var c in r.GetProperty("exhaust").EnumerateArray()) s.ExhaustPile.Add(CardFromJson(c));
            return s;
        }

        private static SimCombatant CombatantFromJson(JsonElement e)
        {
            var c = new SimCombatant(e.GetProperty("hp").GetInt32(), e.GetProperty("max_hp").GetInt32())
            { Block = e.GetProperty("block").GetInt32() };
            foreach (var p in e.GetProperty("powers").EnumerateObject()) c.Powers[p.Name] = p.Value.GetInt32();
            return c;
        }

        private static SimCard CardFromJson(JsonElement e)
        {
            var c = new SimCard
            {
                Id = e.GetProperty("id").GetString() ?? "",
                Name = e.TryGetProperty("name", out var nm) ? nm.GetString() ?? "" : "",
                Cost = e.GetProperty("cost").GetInt32(),
                CardType = e.TryGetProperty("card_type", out var ct) ? ct.GetString() ?? "" : "",
                Target = Enum.TryParse<SimTargetKind>(e.GetProperty("target").GetString(), out var t) ? t : SimTargetKind.None,
                Damage = e.GetProperty("damage").GetInt32(), Block = e.GetProperty("block").GetInt32(),
                CardsDraw = e.GetProperty("draw").GetInt32(), EnergyGain = e.GetProperty("energy_gain").GetInt32(),
                HpLoss = e.GetProperty("hp_loss").GetInt32(), Repeat = e.TryGetProperty("repeat", out var rp) ? rp.GetInt32() : 1,
                ExhaustsOnPlay = e.TryGetProperty("exhausts_on_play", out var ex) && ex.GetBoolean(),
                Retains = e.TryGetProperty("retains", out var rt) && rt.GetBoolean(),
                Ethereal = e.TryGetProperty("ethereal", out var et) && et.GetBoolean(),
                Innate = e.TryGetProperty("innate", out var inn) && inn.GetBoolean(),
                ApproximateEffect = e.TryGetProperty("approximate_effect", out var ap) && ap.GetBoolean(),
                BehaviorUnmodeled = e.TryGetProperty("behavior_unmodeled", out var bu) && bu.GetBoolean(),
            };
            foreach (var p in e.GetProperty("powers").EnumerateObject()) c.Powers.Add((p.Name, p.Value.GetInt32()));
            if (e.TryGetProperty("script", out var sc) && sc.ValueKind == JsonValueKind.Array)
                foreach (var o in sc.EnumerateArray()) c.Script.Add(OpFromJson(o));
            return c;
        }

        private static SimOp OpFromJson(JsonElement e) => new()
        {
            Kind = Enum.TryParse<SimOpKind>(e.GetProperty("kind").GetString(), out var kind) ? kind : SimOpKind.Damage,
            Target = Enum.TryParse<SimTargetSel>(e.GetProperty("target").GetString(), out var sel) ? sel : SimTargetSel.Self,
            Amount = e.GetProperty("amount").GetInt32(),
            AmountIsX = e.TryGetProperty("amount_is_x", out var ax) && ax.GetBoolean(),
            PowerId = e.TryGetProperty("power_id", out var pi) ? pi.GetString() ?? "" : "",
            SummonCardId = e.TryGetProperty("summon_card_id", out var si) ? si.GetString() ?? "" : "",
            Times = e.GetProperty("times").GetInt32(),
            Per = e.TryGetProperty("per", out var pe) ? pe.GetInt32() : 0,
            Condition = e.TryGetProperty("condition", out var co) && Enum.TryParse<SimPerCondition>(co.GetString(), out var cond) ? cond : SimPerCondition.None,
            Pile = e.TryGetProperty("pile", out var pl) && Enum.TryParse<SimPile>(pl.GetString(), out var pile) ? pile : SimPile.Hand,
        };

        private static object OpPayload(SimOp op) => new
        {
            kind = op.Kind.ToString(), target = op.Target.ToString(),
            amount = op.Amount, amount_is_x = op.AmountIsX,
            power_id = op.PowerId, summon_card_id = op.SummonCardId,
            times = op.Times, per = op.Per, condition = op.Condition.ToString(), pile = op.Pile.ToString(),
        };

        private static Dictionary<string, int> PowersDict(List<(string id, int amt)> powers)
        {
            var d = new Dictionary<string, int>();
            foreach (var (id, amt) in powers) d[id] = amt;
            return d;
        }
    }
}
