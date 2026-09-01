// The forward engine over a pure SimState. Phase 0 covers the player turn for simple
// (field-driven) cards: start of turn, play a card, end player turn. Enemy AI / power hooks /
// OnPlay DSL plug in at Phase 2/3. Everything here is deterministic and game-type-free.
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    public static class SimResolver
    {
        public const int DefaultHandSize = 5;

        // Start a fresh player turn: clear block (BeforeTurnStart), refill energy/draw (setup), then
        // fire player-side TurnStart power effects (ritual/plating/demon-form).
        public static void NewTurn(SimState state)
        {
            state.Round++;
            state.Turn++;
            state.Side = SimSide.Player;
            foreach (var p in state.Players) p.Block = 0;
            foreach (var e in state.Enemies) e.Block = 0;

            // Combat-start relic effects apply once (start block / start strength).
            if (!state.CombatStartApplied)
            {
                state.CombatStartApplied = true;
                foreach (var p in state.Players)
                {
                    if (state.CombatStartBlock != 0) SimCommand.GainBlock(state, p, state.CombatStartBlock);
                    if (state.CombatStartStrength != 0) SimCommand.ApplyPower(p, "strength_power", state.CombatStartStrength);
                }
            }

            // "Next turn" effects (created by cards played last turn) apply once, then reset.
            if (state.NextTurnBlock != 0)
                foreach (var p in state.Players) SimCommand.GainBlock(state, p, state.NextTurnBlock);
            state.ActiveEnergy = state.MaxEnergy + state.TurnEnergyBonus + state.NextTurnEnergy;
            state.ActiveStars = state.NextTurnStars;
            SimCommand.Draw(state, SimHooks.ModifyHandDraw(state, 5) + state.TurnDrawBonus + state.NextTurnDraw);
            state.NextTurnBlock = state.NextTurnDraw = state.NextTurnEnergy = state.NextTurnStars = 0;
            TickTurnStart(state, state.Players);
        }

        public static bool IsPlayable(SimState state, SimCard card)
        {
            if (card == null) return false;
            if (card.CardType == "Status") return false;
            var cost = card.CostsX ? state.ActiveEnergy : card.Cost;
            if (state.ActiveEnergy < cost) return false;
            if (card.StarCost > state.ActiveStars) return false;
            return true;
        }

        public static List<string> PlayCard(SimState state, int handIndex, int? targetIndex)
        {
            if (handIndex < 0 || handIndex >= state.Hand.Count)
                throw new ArgumentOutOfRangeException(nameof(handIndex));
            var card = state.Hand[handIndex];
            var cost = card.CostsX ? state.ActiveEnergy : card.Cost;
            var notes = new List<string>();
            if (state.ActiveEnergy < cost) { notes.Add("not_enough_energy"); return notes; }

            SimCommand.LoseEnergy(state, cost);
            if (card.StarCost > 0) SimCommand.LoseStars(state, card.StarCost);
            var me = state.ActivePlayer;

            // The card leaves hand as soon as it is played (matches the game). This matters for cards
            // that inspect the hand (e.g. "exhaust non-Attack cards") — otherwise it would count itself.
            state.Hand.Remove(card);

            // Behaviour cards (Phase 3): run the script instead of the field-driven path. X resolves
            // to the energy spent.
            if (card.Script.Count > 0)
            {
                var x = card.CostsX ? cost : -1;
                SimOnPlay.Execute(state, card, targetIndex, x);
                SimCommand.MoveToDiscard(state, card);
                return notes;
            }

            // Non-numeric behaviour (OnPlay DSL) is a Phase 3 concern; Phase 0/1 is field-driven.
            var targets = ResolveTargets(state, card, targetIndex);

            // ExtraDamage may scale per card in the Exhaust pile (e.g. Ashen Strike).
            var totalDamage = card.Damage + (card.ExtraDamagePerExhaust ? card.ExtraDamage * state.ExhaustPile.Count : card.ExtraDamage);
            var isAttack = card.CardType == "Attack";
            if (totalDamage > 0)
                foreach (var t in targets) // empty targets => damage is wasted
                    SimCommand.DealDamage(state, me, t, totalDamage, isAttack);

            if (card.Block > 0) SimCommand.GainBlock(state, me, card.Block);
            if (card.HpLoss > 0) LoseHp(me, card.HpLoss);            // "lose HP" ignores block
            if (card.MaxHpGain > 0) SimCommand.GainMaxHp(me, card.MaxHpGain);
            if (card.Heal > 0) SimCommand.Heal(me, card.Heal);
            if (card.EnergyGain > 0) SimCommand.GainEnergy(state, card.EnergyGain);
            if (card.StarsGain > 0) SimCommand.GainStars(state, card.StarsGain);
            if (card.CardsDraw > 0) SimCommand.Draw(state, card.CardsDraw);

            foreach (var oa in card.OrbActions) SimOrbEngine.ApplyOrbAction(state, me, oa);

            if (!string.IsNullOrEmpty(card.SummonCardId))
                SimCommand.AddCardTo(state, PlaceholderCard(card.SummonCardId), "hand");

            if (card.Powers.Count > 0)
            {
                // Powers apply to enemy-typed targets if any, else to self (power cards).
                if (targets.Count > 0)
                    foreach (var t in targets)
                        foreach (var (id, amt) in card.Powers) SimCommand.ApplyPower(t, id, amt);
                else
                    foreach (var (id, amt) in card.Powers) SimCommand.ApplyPower(me, id, amt);
            }

            SimCommand.MoveToDiscard(state, card);
            return notes;
        }

        // Placeholder for a summoned/spawned card. Phase 3 builds the real card from data; for now it
        // just materialises the id so pile accounting is faithful.
        private static SimCard PlaceholderCard(string id) => new()
        {
            Id = id, Name = id, Cost = 0, CardType = "Status", Target = SimTargetKind.None,
        };

        public static void EndPlayerTurn(SimState state)
        {
            // Player-side end-of-turn power effects (poison/regen/doom on the player). Enemy powers
            // tick at the enemy turn end (SimEnemy.RunEnemyTurn), so no double-tick here.
            TickTurnEnd(state, state.Players);
            foreach (var p in state.Players) if (p.Alive) SimOrbEngine.PassiveOrbs(state, p);
            // Hand lifecycle: RETAIN stays in hand; ETHEREAL exhausts; everything else discards.
            var keep = new List<SimCard>();
            foreach (var c in state.Hand)
            {
                if (c.Retains) keep.Add(c);
                else if (c.Ethereal) state.ExhaustPile.Add(c);
                else state.DiscardPile.Add(c);
            }
            state.Hand.Clear();
            state.Hand.AddRange(keep);
            state.Side = SimSide.Enemy;
        }

        public static IEnumerable<SimCombatant> AllCreatures(SimState state)
        {
            foreach (var p in state.Players) yield return p;
            foreach (var e in state.Enemies) yield return e;
        }

        // Fire TurnStart hooks for the given creatures. Keys are snapshotted because a hook may add
        // strength to the same Powers dict it is being read from. Public so the enemy engine can fire
        // enemy-side turn-start effects at the enemy turn.
        public static void TickTurnStart(SimState state, IEnumerable<SimCombatant> creatures)
        {
            foreach (var c in creatures)
                if (c.Alive)
                    foreach (var id in new List<string>(c.Powers.Keys))
                        if (SimPower.TryGet(id, out var beh) && beh.TurnStart != null)
                            beh.TurnStart(state, c);
        }

        public static void TickTurnEnd(SimState state, IEnumerable<SimCombatant> creatures)
        {
            foreach (var c in creatures)
                if (c.Alive)
                    foreach (var id in new List<string>(c.Powers.Keys))
                        if (SimPower.TryGet(id, out var beh) && beh.TurnEnd != null)
                            beh.TurnEnd(state, c);
        }

        // ---- target resolution ------------------------------------------------

        public static List<SimCombatant> ResolveTargets(SimState state, SimCard card, int? targetIndex)
        {
            var res = new List<SimCombatant>();
            var me = state.ActivePlayer;
            switch (card.Target)
            {
                case SimTargetKind.None:
                case SimTargetKind.Self:
                    if (me != null) res.Add(me);
                    break;
                case SimTargetKind.AnyEnemy:
                {
                    var e = PickEnemy(state, targetIndex);
                    if (e != null) res.Add(e);
                    break;
                }
                case SimTargetKind.AllEnemies:
                    foreach (var e in state.Enemies) if (e.Alive) res.Add(e);
                    break;
                case SimTargetKind.RandomEnemy:
                {
                    var e = FirstAliveEnemy(state); // Phase 1: RNG pick
                    if (e != null) res.Add(e);
                    break;
                }
                case SimTargetKind.AnyAlly:
                {
                    var ally = PickAlly(state, targetIndex);
                    if (ally != null) res.Add(ally);
                    break;
                }
                case SimTargetKind.AllAllies:
                    foreach (var p in state.Players) if (p.Alive) res.Add(p);
                    break;
            }
            return res;
        }

        public static SimCombatant? PickEnemy(SimState state, int? targetIndex)
        {
            if (targetIndex.HasValue && targetIndex.Value >= 0 && targetIndex.Value < state.Enemies.Count
                && state.Enemies[targetIndex.Value].Alive)
                return state.Enemies[targetIndex.Value];
            return FirstAliveEnemy(state);
        }

        private static SimCombatant? FirstAliveEnemy(SimState state)
        {
            foreach (var e in state.Enemies) if (e.Alive) return e;
            return null;
        }

        private static SimCombatant? PickAlly(SimState state, int? targetIndex)
        {
            if (targetIndex.HasValue && targetIndex.Value >= 0 && targetIndex.Value < state.Players.Count
                && state.Players[targetIndex.Value].Alive)
                return state.Players[targetIndex.Value];
            foreach (var p in state.Players) if (p.Alive && p != state.ActivePlayer) return p;
            return state.ActivePlayer;
        }

        public static void LoseHp(SimCombatant c, int amount)
        {
            if (amount <= 0 || c == null) return;
            c.Hp = Math.Max(0, c.Hp - amount);
        }
    }
}
