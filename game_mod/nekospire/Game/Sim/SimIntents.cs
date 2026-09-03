// Enemy action model: intents (the 16 IntentType classes collapsed to the ones that matter for
// state), monster move tables, RollMove (via the MonsterAi RNG stream) and the enemy turn driver.
// Pure / game-type-free and testable with hand-built monsters; the monsters.json bridge (SimMonster)
// is a later data pass.
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    // Collapsed intent kinds (mirrors the game's IntentType surface for state-affecting ones).
    // "Hidden"/"Unknown" are skipped (no state effect the model can compute). VFX-only intents (Talk/
    // Think/Sfx) are not modeled — the resolver is a pure machine, not a presenter.
    public enum SimIntentKind
    {
        Attack,      // deal Damage (base, before strength/weak/vuln), Times hits
        Buff,        // add PowerAmount of PowerId to self
        Debuff,      // add PowerAmount of PowerId to the player
        Defend,      // gain Block to self
        Heal,        // heal self
        Summon,      // spawn SummonEnemyId enemy
        Status,      // put StatusCardId into the player's hand
        Stun,        // player cannot act next turn
        Sleep,       // skip attack (no effect in the pure model beyond a no-op)
        Escape,      // enemy leaves
    }

    public sealed class SimIntent
    {
        public SimIntentKind Kind;
        public int Damage;
        public int Times = 1;
        public string PowerId = "";
        public int PowerAmount;
        public int Block;
        public int Heal;
        public string? SummonEnemyId;
        public string? StatusCardId;

        public SimIntent Clone() => (SimIntent)MemberwiseClone();
    }

    /// <summary>One entry in an enemy's move table.</summary>
    public sealed class SimMonsterMove
    {
        public string MoveId = "";
        public int Weight = 100;          // selection weight (for random-branch moves)
        public readonly List<SimIntent> Intents = new();

        public SimMonsterMove Clone()
        {
            var m = new SimMonsterMove { MoveId = MoveId, Weight = Weight };
            foreach (var i in Intents) m.Intents.Add(i.Clone());
            return m;
        }
    }

    public static class SimEnemy
    {
        // The enemy follows its move table as a deterministic cycle (mirrors the game's MoveState
        // FollowUpState chain, which is deterministic for the common non-random-branch case). Weighted
        // random branches are a later refinement. A single/idle move simply repeats.
        public static SimMonsterMove RollMove(SimState state, SimCombatant enemy)
        {
            if (enemy.Moves.Count == 0)
                return new SimMonsterMove { MoveId = "idle" };
            var move = enemy.Moves[enemy.MoveIndex % enemy.Moves.Count];
            enemy.MoveIndex = (enemy.MoveIndex + 1) % enemy.Moves.Count;
            return move;
        }

        /// <summary>Execute one enemy turn: enemy turn-start power effects, each surviving enemy rolls
        /// a move and performs its intents, then enemy end-of-turn power effects (poison/regen).</summary>
        public static void RunEnemyTurn(SimState state)
        {
            state.Side = SimSide.Enemy;
            // Snapshot survival so a newly summoned enemy does not act in the same turn.
            var turnEnemies = new List<SimCombatant>();
            foreach (var e in state.Enemies) if (e.Alive) turnEnemies.Add(e);

            SimResolver.TickTurnStart(state, turnEnemies);
            foreach (var e in turnEnemies)
            {
                if (!e.Alive) continue;
                var move = RollMove(state, e);
                PerformIntents(state, e, move);
            }
            SimResolver.TickTurnEnd(state, turnEnemies);   // enemy poison/regen/doom tick here
            state.Side = SimSide.Player;
        }

        private static void PerformIntents(SimState state, SimCombatant self, SimMonsterMove move)
        {
            var player = state.ActivePlayer;
            foreach (var intent in move.Intents)
            {
                switch (intent.Kind)
                {
                    case SimIntentKind.Attack:
                        for (var t = 0; t < intent.Times; t++)
                            for (var i = 0; i < state.Players.Count; i++)
                                if (state.Players[i].Alive)
                                    SimCommand.DealDamage(state, self, state.Players[i], intent.Damage, isAttack: true);
                        break;
                    case SimIntentKind.Buff:
                        SimCommand.ApplyPower(self, intent.PowerId, intent.PowerAmount);
                        break;
                    case SimIntentKind.Debuff:
                        if (player != null) SimCommand.ApplyPower(player, intent.PowerId, intent.PowerAmount);
                        break;
                    case SimIntentKind.Defend:
                        SimCommand.GainBlock(state, self, intent.Block);
                        break;
                    case SimIntentKind.Heal:
                        SimCommand.Heal(self, intent.Heal);
                        break;
                    case SimIntentKind.Summon:
                        if (player != null) SimCommand.AddCardTo(state, new SimCard { Id = intent.SummonEnemyId ?? "", CardType = "Status" }, "hand");
                        break;
                    case SimIntentKind.Status:
                        if (player != null) SimCommand.AddCardTo(state, new SimCard { Id = intent.StatusCardId ?? "WOUND", CardType = "Status" }, "hand");
                        break;
                    case SimIntentKind.Stun:
                        if (player != null) SimCommand.ApplyPower(player, "stun_power", 1);
                        break;
                    case SimIntentKind.Sleep:
                    case SimIntentKind.Escape:
                        // No state change the pure model computes (Escape could remove the enemy later).
                        break;
                }
            }
        }
    }
}
