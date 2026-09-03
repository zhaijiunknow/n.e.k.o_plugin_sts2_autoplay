// OnPlay DSL: a bounded IR for behaviour cards whose effects cannot be expressed as a flat field set
// (X-cost, multi-hit, summon, copy, transform, per-target differences). A card carries a small script;
// when present the resolver runs it instead of the field-driven path. Pure / game-type-free.
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    public enum SimOpKind
    {
        Damage, Block, ApplyPower, Draw, GainEnergy, Heal, Summon, ExhaustNonAttacks,
        CopyToPile, DamageEqualBlock, DoublePower, PerXDamage, AddShivs, Discard,
        GainStars, NextTurnBlock, NextTurnDraw, NextTurnEnergy, NextTurnStars, LoseMaxHp,
    }
    public enum SimTargetSel { Self, AnyEnemy, AllEnemies, RandomEnemy }
    public enum SimPerCondition { None, StrikeInDeck, TargetVulnerable, ExhaustPile }
    public enum SimPile { Hand, Draw, Discard, Exhaust }

    public sealed class SimOp
    {
        public SimOpKind Kind;
        public SimTargetSel Target = SimTargetSel.Self;
        public int Amount;          // damage / block / draw / energy / heal / power amount; PerX base
        public bool AmountIsX;      // true => Amount == resolved X value
        public string PowerId = ""; // ApplyPower / DoublePower
        public string SummonCardId = ""; // Summon
        public int Times = 1;       // repeat hits
        public int Per;             // PerXDamage: damage per unit
        public SimPerCondition Condition = SimPerCondition.None;
        public SimPile Pile;        // CopyToPile target pile

        public SimOp Clone() => (SimOp)MemberwiseClone();
    }

    public static class SimOnPlay
    {
        /// <summary>Execute a card's behaviour script. Returns notes (e.g. unresolved X).</summary>
        public static List<string> Execute(SimState state, SimCard card, int? targetIndex, int xValue)
        {
            var notes = new List<string>();
            SimCombatant me = state.ActivePlayer;   // ActivePlayer throws if there is no player
            foreach (var op in card.Script)
            {
                var amount = op.AmountIsX ? xValue : op.Amount;
                if (op.AmountIsX && xValue < 0) continue; // X unresolved -> skip (honest)

                var targets = ResolveTargets(state, op.Target, targetIndex, me!);
                switch (op.Kind)
                {
                    case SimOpKind.Damage:
                        var isAttack = card.CardType == "Attack";
                        for (var t = 0; t < Math.Max(1, op.Times); t++)
                            foreach (var d in targets) SimCommand.DealDamage(state, me!, d, amount, isAttack);
                        break;
                    case SimOpKind.Block:
                        SimCommand.GainBlock(state, me!,amount * Math.Max(1, op.Times));
                        break;
                    case SimOpKind.ApplyPower:
                        foreach (var d in targets) SimCommand.ApplyPower(d, op.PowerId, amount);
                        break;
                    case SimOpKind.Draw:
                        SimCommand.Draw(state, amount);
                        break;
                    case SimOpKind.GainEnergy:
                        SimCommand.GainEnergy(state, amount);
                        break;
                    case SimOpKind.Heal:
                        SimCommand.Heal(me!, amount);
                        break;
                    case SimOpKind.Summon:
                        SimCommand.AddCardTo(state, new SimCard { Id = op.SummonCardId, CardType = "Status" }, "hand");
                        break;
                    case SimOpKind.ExhaustNonAttacks:
                    {
                        // Exhaust all non-Attack cards in hand; gain `amount` Block per card.
                        var toExhaust = new List<SimCard>();
                        foreach (var h in state.Hand)
                            if (!string.Equals(h.CardType, "Attack", StringComparison.OrdinalIgnoreCase))
                                toExhaust.Add(h);
                        foreach (var h in toExhaust) SimCommand.ExhaustCard(state, me!,h);
                        if (toExhaust.Count > 0) SimCommand.GainBlock(state, me!,amount * toExhaust.Count);
                        break;
                    }
                    case SimOpKind.CopyToPile:
                    {
                        SimCommand.AddCardTo(state, card.Clone(), op.Pile.ToString().ToLowerInvariant());
                        break;
                    }
                    case SimOpKind.DamageEqualBlock:
                    {
                        foreach (var d in targets)
                            SimCommand.DealDamage(state, me!,d, me != null ? me.Block : 0, card.CardType == "Attack");
                        break;
                    }
                    case SimOpKind.DoublePower:
                    {
                        foreach (var d in targets)
                            if (d.Powers.TryGetValue(op.PowerId, out var amt) && amt > 0)
                                d.Powers[op.PowerId] = amt * 2;
                        break;
                    }
                    case SimOpKind.PerXDamage:
                    {
                        var perX = op.Amount + op.Per * PerCount(state, op.Condition, targets.Count > 0 ? targets[0] : null);
                        foreach (var d in targets)
                            SimCommand.DealDamage(state, me!,d, perX, card.CardType == "Attack");
                        break;
                    }
                    case SimOpKind.AddShivs:
                    {
                        for (var i = 0; i < Math.Max(0, amount); i++)
                            SimCommand.AddCardTo(state, ShivCard(), "hand");
                        break;
                    }
                    case SimOpKind.Discard:
                    {
                        // Player-choice discard approximated: drop the first `amount` cards from hand.
                        for (var i = 0; i < Math.Max(0, amount) && state.Hand.Count > 0; i++)
                        {
                            var c = state.Hand[0];
                            state.Hand.RemoveAt(0);
                            state.DiscardPile.Add(c);
                        }
                        break;
                    }
                    case SimOpKind.GainStars:
                        SimCommand.GainStars(state, amount);
                        break;
                    case SimOpKind.NextTurnBlock:
                        state.NextTurnBlock += Math.Max(0, amount);
                        break;
                    case SimOpKind.NextTurnDraw:
                        state.NextTurnDraw += Math.Max(0, amount);
                        break;
                    case SimOpKind.NextTurnEnergy:
                        state.NextTurnEnergy += Math.Max(0, amount);
                        break;
                    case SimOpKind.NextTurnStars:
                        state.NextTurnStars += Math.Max(0, amount);
                        break;
                    case SimOpKind.LoseMaxHp:
                        SimCommand.LoseMaxHp(me!, Math.Max(0, amount));
                        break;
                }
            }
            return notes;
        }

        private static int PerCount(SimState state, SimPerCondition cond, SimCombatant? target)
        {
            switch (cond)
            {
                case SimPerCondition.StrikeInDeck:
                {
                    var n = 0;
                    foreach (var pile in new[] { state.Hand, state.DrawPile, state.DiscardPile, state.ExhaustPile })
                        foreach (var c in pile) if (ContainsStrike(c)) n++;
                    return n;
                }
                case SimPerCondition.TargetVulnerable:
                    return target != null && target.Powers.TryGetValue("vulnerable_power", out var v) ? v : 0;
                case SimPerCondition.ExhaustPile:
                    return state.ExhaustPile.Count;
            }
            return 0;
        }

        private static bool ContainsStrike(SimCard c)
            => (c.Id + " " + c.Name).IndexOf("STRIKE", StringComparison.OrdinalIgnoreCase) >= 0;

        // The Silent's generated Shiv card: 0-cost, deal 4, exhaust.
        private static SimCard ShivCard() => new()
        {
            Id = "SHIV", Name = "Shiv", Cost = 0, CardType = "Attack",
            Target = SimTargetKind.AnyEnemy, Damage = 4, ExhaustsOnPlay = true,
        };

        private static List<SimCombatant> ResolveTargets(SimState state, SimTargetSel sel, int? targetIndex, SimCombatant me)
        {
            var res = new List<SimCombatant>();
            switch (sel)
            {
                case SimTargetSel.Self:
                    if (me != null) res.Add(me);
                    break;
                case SimTargetSel.AnyEnemy:
                {
                    var e = SimResolver.PickEnemy(state, targetIndex);
                    if (e != null) res.Add(e);
                    break;
                }
                case SimTargetSel.AllEnemies:
                    foreach (var e in state.Enemies) if (e.Alive) res.Add(e);
                    break;
                case SimTargetSel.RandomEnemy:
                {
                    var r = state.Rng.Get(SimRngType.CombatTargets);
                    var alive = state.Enemies.FindAll(e => e.Alive);
                    if (alive.Count > 0) res.Add(alive[r.NextInt(0, alive.Count)]);
                    break;
                }
            }
            return res;
        }
    }
}
