// Pure-state combat model for the determinstic resolver.
// Deliberately free of any STS2 game-type dependency so it can be unit-tested standalone
// (linked into a plain net9.0 console test project) as well as compiled into the mod.
// Game types are bridged in SimBuild.cs. This mirrors the "SimState" design in the plan.
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    public enum SimSide { Player, Enemy }

    public enum SimTargetKind
    {
        None, Self, AnyEnemy, AllEnemies, AnyAlly, AllAllies, RandomEnemy
    }

    /// <summary>A single card action on an orb (channel/evoke/passive), kept minimal for Phase 0.</summary>
    public struct SimOrbAction
    {
        public string Action;   // "channel" | "evoke" | "passive"
        public string OrbId;
        public int Times;
    }

    /// <summary>One combatant — a player's creature or an enemy. Pure scalar state.</summary>
    public sealed class SimCombatant
    {
        public int Hp;
        public int MaxHp;
        public int Block;
        public double Ascension; // unused for now; kept so the schema is stable
        public string EnemyId = "";            // set for enemies (kind of monster)
        public readonly Dictionary<string, int> Powers = new(StringComparer.OrdinalIgnoreCase);

        // Enemy AI (Phase 2): this creature's move table + current pointer into it.
        public readonly List<SimMonsterMove> Moves = new();
        public int MoveIndex;

        // Orbs are a general combat mechanic (any character can channel them if a card provides the
        // action) — not Defect-specific.
        public readonly List<SimOrb> Orbs = new();
        public int OrbCapacity;
        public int Focus;

        public bool Alive => Hp > 0;

        public SimCombatant() { }

        public SimCombatant(int hp, int maxHp)
        {
            Hp = hp;
            MaxHp = maxHp;
        }

        public SimCombatant Clone()
        {
            var c = new SimCombatant(Hp, MaxHp) { Block = Block, Ascension = Ascension, EnemyId = EnemyId, MoveIndex = MoveIndex, OrbCapacity = OrbCapacity, Focus = Focus };
            foreach (var kv in Powers) c.Powers[kv.Key] = kv.Value;
            foreach (var m in Moves) c.Moves.Add(m.Clone());
            foreach (var o in Orbs) c.Orbs.Add(o.Clone());
            return c;
        }
    }

    /// <summary>A card instance in combat. For Phase 0 the effect surface is the numeric field set
    /// (Damage/Block/CardsDraw/EnergyGain/HpLoss/Powers) — the "simple card" path. OnPlayScript is a
    /// DSL handle used from Phase 3 for behaviour cards; null means field-driven.</summary>
    public sealed class SimCard
    {
        public string Id = "";
        public string Name = "";
        public int Cost;
        public bool CostsX;
        public bool CostsStarX;
        public string CardType = "";               // Attack / Skill / Power / Status / ...
        public SimTargetKind Target = SimTargetKind.None;
        public int Damage;
        public int Block;
        public int CardsDraw;
        public int EnergyGain;
        public int HpLoss;
        public int StarsGain;           // StarsVar (gain stars)
        // Additional DynamicVar classes bridged in Phase 1:
        public int MaxHpGain;          // MaxHpVar
        public int Heal;               // HealVar
        public int ExtraDamage;        // ExtraDamageVar
        public bool ExtraDamagePerExhaust; // extra adds per card in the Exhaust pile (Ashen Strike)
        public int StarCost;           // StarsVar (separate resource from energy)
        public int Repeat = 1;         // RepeatVar (multi-hit; interpretation refined in Phase 3 DSL)
        public string? SummonCardId;   // SummonVar / OstyDamageVar placeholder (card to spawn)
        public bool ExhaustsOnPlay;    // EXHAUST keyword
        public bool Retains;           // RETAIN keyword: stays in hand at end of turn
        public bool Ethereal;          // ETHEREAL keyword: exhausts at end of turn if in hand
        public bool Innate;            // INNATE keyword: drawn into hand at combat start
        public bool BehaviorUnmodeled;   // behaviour card, no table entry -> engine can't simulate it
        public bool ApproximateEffect;   // table entry is an approximation (an effect unrepresented)
        public string? OnPlayScript;     // Phase 3+: DSL handle; null => field-driven
        public readonly List<SimOp> Script = new();   // Phase 3: behaviour script (X-cost/多段/召唤/复制)
        public readonly List<(string powerId, int amount)> Powers = new();
        public readonly List<SimOrbAction> OrbActions = new();

        public SimCard Clone()
        {
            var c = new SimCard
            {
                Id = Id, Name = Name, Cost = Cost, CostsX = CostsX, CostsStarX = CostsStarX,
                CardType = CardType, Target = Target, Damage = Damage, Block = Block,
                CardsDraw = CardsDraw, EnergyGain = EnergyGain, HpLoss = HpLoss, StarsGain = StarsGain,
                MaxHpGain = MaxHpGain, Heal = Heal, ExtraDamage = ExtraDamage, ExtraDamagePerExhaust = ExtraDamagePerExhaust,
                StarCost = StarCost, Repeat = Repeat, SummonCardId = SummonCardId, ExhaustsOnPlay = ExhaustsOnPlay,
                Retains = Retains, Ethereal = Ethereal, Innate = Innate, BehaviorUnmodeled = BehaviorUnmodeled,
                ApproximateEffect = ApproximateEffect, OnPlayScript = OnPlayScript,
            };
            foreach (var s in Script) c.Script.Add(s.Clone());
            c.Powers.AddRange(Powers);
            c.OrbActions.AddRange(OrbActions);
            return c;
        }
    }

    /// <summary>The full pure combat state. Mutable; deep-cloned for search branches via Clone().</summary>
    public sealed class SimState
    {
        public int Round;
        public int Turn;
        public SimSide Side;
        public int RunSeed;          // wired to real RNG in Phase 1
        public int Ascension;
        public SimRngSet Rng = new("");   // deterministic streams; SimBuild sets a per-run seed

        // Active-player resources. Kept on the state for Phase 0; promoted to a proper
        // per-player resource object if/when co-op needs it.
        public int ActiveEnergy;
        public int MaxEnergy;
        public int ActiveStars;

        public readonly List<SimCombatant> Players = new();
        public readonly List<SimCombatant> Enemies = new();
        public readonly List<SimCard> DrawPile = new();
        public readonly List<SimCard> DiscardPile = new();
        public readonly List<SimCard> ExhaustPile = new();
        public readonly List<SimCard> Hand = new();
        public readonly List<string> Potions = new();
        public readonly List<string> Relics = new();

        // Relic-derived combat mods (Phase 2). TurnEnergyBonus/TurnDrawBonus apply every player turn;
        // CombatStartBlock/CombatStartStrength apply once at the first turn.
        public int TurnEnergyBonus;
        public int TurnDrawBonus;
        public int CombatStartBlock;
        public int CombatStartStrength;
        public bool CombatStartApplied;

        // "Next turn" effects (GUIDING_STAR draw, GLITTERSTREAM block, REFINE_BLADE energy, HIDDEN_CACHE
        // stars, DODGE_AND_ROLL block, ...). Applied once when the next player turn begins, then reset.
        public int NextTurnBlock;
        public int NextTurnDraw;
        public int NextTurnEnergy;
        public int NextTurnStars;

        public SimCombatant ActivePlayer => Players.Count > 0 ? Players[0] : throw new InvalidOperationException("no active player");

        public SimState Clone()
        {
            var s = new SimState
            {
                Round = Round, Turn = Turn, Side = Side, RunSeed = RunSeed, Ascension = Ascension,
                ActiveEnergy = ActiveEnergy, MaxEnergy = MaxEnergy, ActiveStars = ActiveStars,
                Rng = Rng.Clone(), TurnEnergyBonus = TurnEnergyBonus, TurnDrawBonus = TurnDrawBonus,
                CombatStartBlock = CombatStartBlock, CombatStartStrength = CombatStartStrength,
                CombatStartApplied = CombatStartApplied,
                NextTurnBlock = NextTurnBlock, NextTurnDraw = NextTurnDraw,
                NextTurnEnergy = NextTurnEnergy, NextTurnStars = NextTurnStars,
            };
            foreach (var p in Players) s.Players.Add(p.Clone());
            foreach (var e in Enemies) s.Enemies.Add(e.Clone());
            foreach (var c in DrawPile) s.DrawPile.Add(c.Clone());
            foreach (var c in DiscardPile) s.DiscardPile.Add(c.Clone());
            foreach (var c in ExhaustPile) s.ExhaustPile.Add(c.Clone());
            foreach (var c in Hand) s.Hand.Add(c.Clone());
            s.Potions.AddRange(Potions);
            s.Relics.AddRange(Relics);
            return s;
        }
    }
}
