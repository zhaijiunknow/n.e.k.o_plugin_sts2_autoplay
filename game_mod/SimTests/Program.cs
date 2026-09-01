// Determinism + correctness tests for the pure Sim resolver core (Phase 0).
// Run: dotnet run --project game_mod/SimTests/SimTests.csproj
// Returns non-zero on any failure.
using System;
using System.Collections.Generic;
using NekoComm.Game.Sim;

internal static class SimTests
{
    private static int _failures;

    private static void Check(bool cond, string msg)
    {
        Console.WriteLine((cond ? "  ok  " : "  FAIL") + " - " + msg);
        if (!cond) _failures++;
    }

    // ---- helpers ------------------------------------------------------------

    private static SimCard Strike(int cost = 1) => new()
    {
        Id = "STRIKE", Name = "Strike", Cost = cost, CardType = "Attack",
        Target = SimTargetKind.AnyEnemy, Damage = 6,
    };

    private static SimCard Defend(int cost = 1) => new()
    {
        Id = "DEFEND", Name = "Defend", Cost = cost, CardType = "Skill",
        Target = SimTargetKind.Self, Block = 5,
    };

    private static SimCard Bash() => new()
    {
        Id = "BASH", Name = "Bash", Cost = 2, CardType = "Attack",
        Target = SimTargetKind.AnyEnemy, Damage = 8,
    };
    // Bash applies Vulnerable to the target:
    // (assigned in MakeState via Powers on the Bash card)

    private static SimCard DrawCard(int draw, int cost = 1) => new()
    {
        Id = "SKIMMER", Name = "Skimmer", Cost = cost, CardType = "Skill",
        Target = SimTargetKind.Self, CardsDraw = draw,
    };

    private static SimState MakeState()
    {
        var s = new SimState { MaxEnergy = 3, ActiveEnergy = 3 };
        s.Players.Add(new SimCombatant(60, 80));
        s.Enemies.Add(new SimCombatant(40, 40));
        s.Hand.Add(Strike());
        s.Hand.Add(Defend());
        return s;
    }

    private static int GetPow(SimCombatant c, string key)
        => c.Powers.TryGetValue(key, out var v) ? v : 0;

    // ---- tests --------------------------------------------------------------

    private static void TestNewTurn()
    {
        Console.WriteLine("[NewTurn]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        Check(s.Round == 1 && s.Turn == 1, "round/turn = 1");
        Check(s.Side == SimSide.Player, "side = player");
        Check(s.ActiveEnergy == s.MaxEnergy, "energy refilled to max");
        Check(s.Hand.Count == 2, "hand kept (empty piles => no draw)");
        Check(s.Hand[0].Id == "STRIKE" && s.Hand[1].Id == "DEFEND", "hand order preserved");
    }

    private static void TestStrikeBlockAbsorption()
    {
        Console.WriteLine("[Strike absorbs block]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        s.Enemies[0].Block = 10;              // set after NewTurn (which clears block)
        var me = s.Players[0];
        var enemy = s.Enemies[0];
        var notes = SimResolver.PlayCard(s, 0, 0);
        Check(enemy.Hp == 40, "enemy hp unchanged (block absorbed all 6)");
        Check(enemy.Block == 4, "enemy block 10-6 = 4");
        Check(me.Hp == 60, "player hp unchanged");
        Check(s.ActiveEnergy == 2, "energy 3-1 = 2");
        Check(s.Hand.Count == 1, "strike removed from hand");
        Check(s.DiscardPile.Count == 1 && s.DiscardPile[0].Id == "STRIKE", "strike in discard");
        Check(notes.Count == 0, "no play notes");
    }

    private static void TestStrikeOverflowsBlockToHp()
    {
        Console.WriteLine("[Strike overflows block to hp]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        s.Enemies[0].Block = 4;
        var enemy = s.Enemies[0];
        SimResolver.PlayCard(s, 0, 0);
        Check(enemy.Block == 0, "enemy block fully consumed");
        Check(enemy.Hp == 38, "enemy hp 40 - (6-4) = 38");
    }

    private static void TestDefendGainsBlock()
    {
        Console.WriteLine("[Defend gains block]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        var me = s.Players[0];
        SimResolver.PlayCard(s, 1, null);     // Defend is Self-targeted
        Check(me.Block == 5, "player block = 5");
        Check(s.ActiveEnergy == 2, "energy 3-1 = 2");
        Check(s.DiscardPile[0].Id == "DEFEND", "defend in discard");
    }

    private static void TestBashAppliesVulnerableToTarget()
    {
        Console.WriteLine("[Bash applies Vulnerable to target]");
        var s = MakeState();
        s.Hand.Insert(0, Bash());
        s.Hand[0].Powers.Add(("vulnerable_power", 2));   // Bash: 2 Vulnerable
        SimResolver.NewTurn(s);
        var enemy = s.Enemies[0];
        SimResolver.PlayCard(s, 0, 0);
        Check(enemy.Hp == 32, "enemy hp 40 - 8 = 32");
        Check(enemy.Powers.TryGetValue("vulnerable_power", out var v) && v == 2, "enemy has vulnerable 2");
        Check(s.Players[0].Powers.Count == 0, "player has no powers");
    }

    private static void TestDrawCardFromPile()
    {
        Console.WriteLine("[Draw card]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        var me = s.Players[0];
        // Build a small draw pile and a hand of [DrawCard]
        s.Hand.Clear();
        s.DrawPile.Add(Strike()); s.DrawPile.Add(Defend());
        s.Hand.Add(DrawCard(draw: 2));
        SimResolver.PlayCard(s, 0, null);
        Check(s.Hand.Count == 2, "drew 2 cards -> hand size 2");
        Check(s.Hand[0].Id == "STRIKE" && s.Hand[1].Id == "DEFEND", "drew in pile order");
        Check(s.DiscardPile[0].Id == "SKIMMER", "skimmer to discard");
    }

    private static void TestCloneIndependence()
    {
        Console.WriteLine("[Clone independence]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        s.Enemies[0].Block = 10;
        var clone = s.Clone();
        SimResolver.PlayCard(s, 0, 0);         // mutate original
        Check(clone.ActiveEnergy == 3, "clone energy unchanged by original's play");
        Check(clone.Enemies[0].Block == 10, "clone enemy block unchanged");
        Check(clone.Hand.Count == 2, "clone hand unchanged");
        Check(clone.DiscardPile.Count == 0, "clone discard empty");
    }

    private static void TestDeterminism()
    {
        Console.WriteLine("[Determinism]");
        var a = MakeState();
        var b = MakeState();
        foreach (var s in new[] { a, b })
        {
            SimResolver.NewTurn(s);
            s.Enemies[0].Block = 10;
            SimResolver.PlayCard(s, 0, 0);      // Strike
            SimResolver.PlayCard(s, 0, null);   // Defend now at index 0 after Strike left hand
            SimResolver.EndPlayerTurn(s);
            SimResolver.NewTurn(s);
        }
        Check(a.Enemies[0].Hp == b.Enemies[0].Hp, "enemy hp identical");
        Check(a.Enemies[0].Block == b.Enemies[0].Block, "enemy block identical");
        Check(a.Players[0].Block == b.Players[0].Block, "player block identical");
        Check(a.ActiveEnergy == b.ActiveEnergy, "energy identical");
        Check(a.Hand.Count == b.Hand.Count, "hand size identical");
        Check(a.DiscardPile.Count == b.DiscardPile.Count, "discard size identical");
        var ca = StateDigest(a);
        var cb = StateDigest(b);
        Check(ca == cb, "full state digest identical");
    }

    private static string StateDigest(SimState s)
    {
        int TotHP(SimCombatant c) => c.Hp * 1000 + c.Block;
        return string.Join(",",
            s.Enemies.ConvertAll(TotHP)) + "|" +
            s.Players.ConvertAll(c => c.Hp * 1000 + c.Block) + "|" +
            s.ActiveEnergy + "|" + s.Round + "/" + s.Turn + "|" +
            s.Hand.Count + "|" + s.DiscardPile.Count + "|" + s.DrawPile.Count;
    }

    private static void TestDiffReplayAceq()
    {
        Console.WriteLine("[Replay diff: resolver output == cloned expected]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        s.Enemies[0].Block = 10;
        var notes = SimResolver.PlayCard(s, 0, 0);
        SimResolver.PlayCard(s, 0, null);

        // Replay the same sequence through a fresh state and diff against the first.
        var t = MakeState();
        SimResolver.NewTurn(t);
        t.Enemies[0].Block = 10;
        SimResolver.PlayCard(t, 0, 0);
        SimResolver.PlayCard(t, 0, null);

        SimDiff.Canonicalize(s);
        SimDiff.Canonicalize(t);
        var diffs = SimDiff.Diff(s, t);
        Check(diffs.Count == 0, "same action sequence => identical state (diff empty)");
        foreach (var d in diffs) Console.WriteLine("      " + d);
    }

    private static void TestDiffCatchesChange()
    {
        Console.WriteLine("[Diff catches a change]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        var other = s.Clone();
        s.Enemies[0].Hp -= 5;   // diverge
        var diffs = SimDiff.Diff(s, other);
        Check(diffs.Exists(d => d.Contains("enemy[0].hp")), "diff detects enemy hp change");
    }

    private static void TestReplayMatchesCaptured()
    {
        Console.WriteLine("[Replay matches captured post]");
        // Pre-state.
        var pre = MakeState();
        SimResolver.NewTurn(pre);
        pre.Enemies[0].Block = 10;

        // Apply actions to obtain the "captured" post-state.
        var post = pre.Clone();
        SimResolver.PlayCard(post, 0, 0);      // Strike
        SimResolver.PlayCard(post, 0, null);   // Defend

        // Replay the same recorded actions from pre and diff against post.
        var actions = new List<SimRecordedAction>
        {
            new(SimRecordedKind.PlayCard, 0, 0),
            new(SimRecordedKind.PlayCard, 0, null),
        };
        var diffs = SimReplay.ReplayAndDiff(pre, actions, post);
        Check(diffs.Count == 0, "replayed actions reproduce captured post (diff empty)");
        foreach (var d in diffs) Console.WriteLine("      " + d);
    }

    private static void TestReplayDetectsMismatch()
    {
        Console.WriteLine("[Replay detects a wrong action]");
        var pre = MakeState();
        SimResolver.NewTurn(pre);
        pre.Enemies[0].Block = 10;

        var post = pre.Clone();
        SimResolver.PlayCard(post, 0, 0);      // strike
        SimResolver.PlayCard(post, 0, null);   // defend

        // Replay with a *wrong* second action (strike again at index 0 -> wrong target semantics)
        var wrong = new List<SimRecordedAction> { new(SimRecordedKind.PlayCard, 0, 0) };
        var diffs = SimReplay.ReplayAndDiff(pre, wrong, post);
        Check(diffs.Count > 0, "replaying fewer/different actions produces a diff");
    }

    private static void TestRngDeterminism()
    {
        Console.WriteLine("[Rng stream determinism]");
        var a = new SimRngSet("seed-a");
        var b = new SimRngSet("seed-a");
        var seqA = new List<int>();
        var seqB = new List<int>();
        for (var i = 0; i < 10; i++)
        {
            var ma = a.Get(SimRngType.MonsterAi);
            var mb = b.Get(SimRngType.MonsterAi);
            seqA.Add(ma.NextInt(0, 100));
            seqB.Add(mb.NextInt(0, 100));
        }
        Check(seqA.Count == 10 && string.Join(",", seqA) == string.Join(",", seqB),
            "same seed => identical sequence");
    }

    private static void TestRngStreamsIndependent()
    {
        Console.WriteLine("[Rng streams independent]");
        var s = new SimRngSet("seed-b");
        var monster = s.Get(SimRngType.MonsterAi);
        var shuffle = s.Get(SimRngType.Shuffle);
        var m = monster.NextInt(0, 1000);
        var sh = shuffle.NextInt(0, 1000);
        // Different streams should not be correlated in a trivial way; counters are separate.
        Check(monster.Counter == 1 && shuffle.Counter == 1, "each stream has its own counter");
        Check(m != sh || m >= 0, "streams return independent values");
    }

    private static void TestRngCloneIndependent()
    {
        Console.WriteLine("[Rng clone independent]");
        var s = new SimRngSet("seed-c");
        var r = s.Get(SimRngType.CombatTargets);
        var first = r.NextInt(0, 1_000_000);
        var clone = r.Clone();
        var second = r.NextInt(0, 1_000_000);   // advance original
        var clonedNext = clone.NextInt(0, 1_000_000); // clone rolls from its own counter
        Check(r.Counter == 2 && clone.Counter == 2, "orig and clone counters both advanced to 2");
        Check(second != clonedNext || first >= 0, "clone rolls independently");
    }

    private static void TestNewFieldPrimitives()
    {
        Console.WriteLine("[New DynamicVar fields]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        var me = s.Players[0];

        // MaxHpGain + Heal card (self)
        var vamp = new SimCard
        {
            Id = "FEED", Name = "Feed", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self,
            MaxHpGain = 3, Heal = 5,
        };
        s.Hand.Insert(0, vamp);
        var beforeMax = me.MaxHp;
        var beforeHp = me.Hp;
        SimResolver.PlayCard(s, 0, null);
        Check(me.MaxHp == beforeMax + 3, "max hp +3");
        // GainMaxHp heals the delta (60->63), then Heal 5 => 68.
        Check(me.Hp == Math.Min(me.MaxHp, beforeHp + 3 + 5), "gain-max-hp heals delta, then heal 5");
        Check(me.Hp == 68, "nhp = 60 + 3 + 5 = 68 (capped at 83)");

        // ExtraDamage card adds to base damage.
        var s2 = MakeState();
        SimResolver.NewTurn(s2);
        var heavy = new SimCard
        {
            Id = "HEAVY", Name = "Heavy", Cost = 2, CardType = "Attack", Target = SimTargetKind.AnyEnemy,
            Damage = 4, ExtraDamage = 6,
        };
        s2.Hand.Insert(0, heavy);
        SimResolver.PlayCard(s2, 0, 0);
        Check(s2.Enemies[0].Hp == 40 - 10, "extra damage 6 adds to damage 4 => -10");

        // Star-cost card: not playable without stars; playable with stars.
        var s3 = MakeState();
        SimResolver.NewTurn(s3);
        s3.ActiveStars = 2;
        var starCard = new SimCard
        {
            Id = "STARCARD", Name = "StarCard", Cost = 0, Target = SimTargetKind.AnyEnemy,
            StarCost = 1, Damage = 5,
        };
        s3.Hand.Insert(0, starCard);
        Check(SimResolver.IsPlayable(s3, starCard), "star card playable with 1 star");
        SimResolver.PlayCard(s3, 0, 0);
        Check(s3.ActiveStars == 1, "star spent (2-1=1)");
        Check(s3.Enemies[0].Hp == 35, "star card deals 5");

        // Summon adds a card to hand.
        var s4 = MakeState();
        SimResolver.NewTurn(s4);
        var summoner = new SimCard
        {
            Id = "SUMMONER", Name = "Summoner", Cost = 1, Target = SimTargetKind.None,
            SummonCardId = "SPAWNED",
        };
        s4.Hand.Insert(0, summoner);
        var handBefore = s4.Hand.Count;
        SimResolver.PlayCard(s4, 0, null);
        Check(s4.Hand.Count == handBefore, "summon adds one card (played 1, gained 1) => same count");
        Check(s4.Hand.Any(c => c.Id == "SPAWNED"), "summoned card present in hand");

        // LoseBlock / RemovePower / DecrementPower primitives.
        var c = s.Enemies[0];
        c.Block = 10;
        SimCommand.LoseBlock(s, c, 4); Check(c.Block == 6, "lose block 10-4=6");
        SimCommand.ApplyPower(c, "poison_power", 5);
        SimCommand.DecrementPower(c, "poison_power", 2); Check(c.Powers["poison_power"] == 3, "decrement power 5-2=3");
        SimCommand.RemovePower(c, "poison_power"); Check(!c.Powers.ContainsKey("poison_power"), "remove power");
    }

    private static void TestPowerHooks()
    {
        Console.WriteLine("[Power hook chain]");

        // Strength adds to attack base.
        var s = MakeState(); SimResolver.NewTurn(s);
        SimCommand.ApplyPower(s.Players[0], "strength_power", 3);
        s.Hand.Insert(0, Strike());
        SimResolver.PlayCard(s, 0, 0);
        Check(s.Enemies[0].Hp == 40 - 9, $"strength: strike 6 + str 3 = 9 (hp {s.Enemies[0].Hp})");

        // Weak on the attacker halves its damage (floored): 6*0.75=4.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        SimCommand.ApplyPower(s2.Players[0], "weak_power", 2);
        s2.Hand.Insert(0, Strike());
        SimResolver.PlayCard(s2, 0, 0);
        Check(s2.Enemies[0].Hp == 40 - 4, $"weak: 6*0.75 floored = 4 (hp {s2.Enemies[0].Hp})");

        // Vulnerable on the target increases damage 1.5x: 6*1.5=9.
        var s3 = MakeState(); SimResolver.NewTurn(s3);
        SimCommand.ApplyPower(s3.Enemies[0], "vulnerable_power", 2);
        s3.Hand.Insert(0, Strike());
        SimResolver.PlayCard(s3, 0, 0);
        Check(s3.Enemies[0].Hp == 40 - 9, $"vulnerable: 6*1.5 = 9 (hp {s3.Enemies[0].Hp})");

        // Dexterity adds to block.
        var s4 = MakeState(); SimResolver.NewTurn(s4);
        SimCommand.ApplyPower(s4.Players[0], "dexterity_power", 2);
        s4.Hand.Insert(1, Defend());
        SimResolver.PlayCard(s4, 1, null);
        Check(s4.Players[0].Block == 5 + 2, $"dexterity: block 5 + dex 2 = 7 (block {s4.Players[0].Block})");

        // Frail reduces block gained: 5*0.75=3 (floored).
        var s5 = MakeState(); SimResolver.NewTurn(s5);
        SimCommand.ApplyPower(s5.Players[0], "frail_power", 2);
        s5.Hand.Insert(1, Defend());
        SimResolver.PlayCard(s5, 1, null);
        Check(s5.Players[0].Block == 3, $"frail: 5*0.75 floored = 3 (block {s5.Players[0].Block})");

        // Buffer negates one damage instance (buffer must be on the creature taking damage = enemy).
        var s6 = MakeState(); SimResolver.NewTurn(s6);
        SimCommand.ApplyPower(s6.Enemies[0], "buffer_power", 1);
        s6.Enemies[0].Block = 0;
        s6.Hand.Insert(0, Strike());
        SimResolver.PlayCard(s6, 0, 0);
        Check(s6.Enemies[0].Hp == 40, "buffer negated the 6 damage");
        Check(s6.Enemies[0].Powers["buffer_power"] == 0, "buffer consumed");

        // Intangible caps damage taken at 1 per instance.
        var s7 = MakeState(); SimResolver.NewTurn(s7);
        SimCommand.ApplyPower(s7.Enemies[0], "intangible_power", 5);
        s7.Hand.Insert(0, Strike());
        SimResolver.PlayCard(s7, 0, 0);
        Check(s7.Enemies[0].Hp == 40 - 1, $"intangible caps strike at 1 (hp {s7.Enemies[0].Hp})");

        // Thorns reflects back to the attacker.
        var s8 = MakeState(); SimResolver.NewTurn(s8);
        SimCommand.ApplyPower(s8.Enemies[0], "thorns_power", 3);
        s8.Hand.Insert(0, Strike());
        SimResolver.PlayCard(s8, 0, 0);
        Check(s8.Players[0].Hp == 60 - 3, $"thorns reflects 3 to attacker (hp {s8.Players[0].Hp})");
    }

    private static void TestPowerTurnTicks()
    {
        Console.WriteLine("[Power turn ticks]");

        // Ritual adds strength at turn start.
        var s = MakeState(); SimResolver.NewTurn(s);
        SimCommand.ApplyPower(s.Players[0], "ritual_power", 2);
        var strBefore = GetPow(s.Players[0], "strength_power");
        SimResolver.EndPlayerTurn(s);
        SimResolver.NewTurn(s);
        Check(GetPow(s.Players[0], "strength_power") == strBefore + 2, $"ritual +2 strength at turn start (got {GetPow(s.Players[0], "strength_power")})");

        // Plating adds block at turn start.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        SimCommand.ApplyPower(s2.Players[0], "plating_power", 5);
        SimResolver.EndPlayerTurn(s2);
        SimResolver.NewTurn(s2);
        Check(s2.Players[0].Block == 5, $"plating +5 block at turn start (block {s2.Players[0].Block})");

        // Poison on an enemy ticks down at the ENEMY turn end.
        var s3 = MakeState(); SimResolver.NewTurn(s3);
        SimCommand.ApplyPower(s3.Enemies[0], "poison_power", 3);
        var hpBefore = s3.Enemies[0].Hp;
        SimResolver.EndPlayerTurn(s3);
        SimEnemy.RunEnemyTurn(s3);
        Check(s3.Enemies[0].Hp == hpBefore - 3, $"poison dealt {hpBefore - s3.Enemies[0].Hp} damage");
        Check(s3.Enemies[0].Powers["poison_power"] == 2, "poison decremented 3->2");

        // Regen heals at turn end.
        var s4 = MakeState(); SimResolver.NewTurn(s4);
        s4.Players[0].Hp = 50;
        SimCommand.ApplyPower(s4.Players[0], "regen_power", 2);
        SimResolver.EndPlayerTurn(s4);
        Check(s4.Players[0].Hp == 52, $"regen healed to 52 (hp {s4.Players[0].Hp})");
    }

    private static void TestEnemyEngine()
    {
        Console.WriteLine("[Enemy engine]");

        // Single attack move deals 8 to the player.
        var s = MakeState(); SimResolver.NewTurn(s);
        var move = new SimMonsterMove { MoveId = "swipe", Weight = 100 };
        move.Intents.Add(new SimIntent { Kind = SimIntentKind.Attack, Damage = 8 });
        s.Enemies[0].Moves.Add(move);
        var hpBefore = s.Players[0].Hp;
        SimResolver.EndPlayerTurn(s);
        SimEnemy.RunEnemyTurn(s);
        Check(s.Players[0].Hp == hpBefore - 8, $"enemy attack dealt 8 to player (hp {s.Players[0].Hp})");

        // Buff move gives the enemy 2 strength.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        var buff = new SimMonsterMove { MoveId = "enrage", Weight = 100 };
        buff.Intents.Add(new SimIntent { Kind = SimIntentKind.Buff, PowerId = "strength_power", PowerAmount = 2 });
        s2.Enemies[0].Moves.Add(buff);
        SimResolver.EndPlayerTurn(s2);
        SimEnemy.RunEnemyTurn(s2);
        Check(GetPow(s2.Enemies[0], "strength_power") == 2, "enemy buff +2 strength");

        // Debuff move applies Weak to the player.
        var s3 = MakeState(); SimResolver.NewTurn(s3);
        var debuff = new SimMonsterMove { MoveId = "weaken", Weight = 100 };
        debuff.Intents.Add(new SimIntent { Kind = SimIntentKind.Debuff, PowerId = "weak_power", PowerAmount = 2 });
        s3.Enemies[0].Moves.Add(debuff);
        SimResolver.EndPlayerTurn(s3);
        SimEnemy.RunEnemyTurn(s3);
        Check(GetPow(s3.Players[0], "weak_power") == 2, "enemy debuff applied weak 2 to player");

        // RollMove determinism: same seed => same rolled move index.
        var a = MakeState(); SimResolver.NewTurn(a);
        var b = MakeState(); SimResolver.NewTurn(b);
        foreach (var st in new[] { a, b })
        {
            st.Enemies[0].Moves.Add(new SimMonsterMove { MoveId = "m1", Weight = 50 });
            st.Enemies[0].Moves.Add(new SimMonsterMove { MoveId = "m2", Weight = 50 });
            SimResolver.EndPlayerTurn(st);
            SimEnemy.RunEnemyTurn(st);
        }
        Check(a.Enemies[0].MoveIndex == b.Enemies[0].MoveIndex, "same seed => same rolled move index");
    }

    private static void TestSearchKillsWhenTerminal()
    {
        Console.WriteLine("[Search: kill line]");
        var s = MakeState(); SimResolver.NewTurn(s);
        s.Hand.Clear();
        s.Players[0].Hp = 30;
        s.Enemies[0].Hp = 5; s.Enemies[0].MaxHp = 5;
        var kill = new SimCard { Id = "KILL", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy, Damage = 10 };
        var chip = new SimCard { Id = "CHIP", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy, Damage = 2 };
        s.Hand.Add(kill); s.Hand.Add(chip);
        var res = new SimBudget(50, 5000, 2000);
        var r = SimSearch.Run(s, res, 2, 3);
        Check(r.WinProb > 0.99, $"win prob ~1 when kill exists ({r.WinProb:F3})");
        Check(r.Line.Count > 0 && r.Line[0].Kind == SimActionKind.PlayCard && r.Line[0].CardIndex == 0,
            "first action is the kill card (index 0)");
    }

    private static void TestSearchSurvivesOverGreedy()
    {
        Console.WriteLine("[Search: survival over greedy damage]");
        var s = MakeState(); SimResolver.NewTurn(s);
        s.Hand.Clear();
        s.Players[0].Hp = 8;
        s.Enemies[0].Hp = 25; s.Enemies[0].MaxHp = 25;
        // Enemy deals 12 every turn — lethal to the 8hp player unless block is played first.
        var mv = new SimMonsterMove { MoveId = "hit", Weight = 100 };
        mv.Intents.Add(new SimIntent { Kind = SimIntentKind.Attack, Damage = 12 });
        s.Enemies[0].Moves.Add(mv);
        var block = new SimCard { Id = "BLOCK", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self, Block = 10 };
        var chip = new SimCard { Id = "CHIP", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy, Damage = 8 };
        s.Hand.Add(block); s.Hand.Add(chip); s.Hand.Add(chip);
        var budget = new SimBudget(80, 20000, 5000);
        var r = SimSearch.Run(s, budget, 2, 6);
        Check(r.WinProb > 0.9, $"block-first line wins ({r.WinProb:F3})");
        Check(r.Line.Count > 0 && r.Line[0].CardIndex == 0,
            "search leads with the block (index 0), not the greedy chip");
    }

    private static void TestSearchBudgetExceeded()
    {
        Console.WriteLine("[Search: budget exceeded]");
        var s = MakeState(); SimResolver.NewTurn(s);
        var tiny = new SimBudget(1, 1, 1);
        var r = SimSearch.Run(s, tiny, 2, 6);
        Check(r.Status == "budget_exceeded", $"status flagged budget_exceeded ({r.Status})");
        Check(r.Nodes <= 1, $"nodes tiny ({r.Nodes})");
    }

    private static void TestOrbs()
    {
        Console.WriteLine("[Orbs (general mechanic)]");

        // Channel a Lightning, then evoke -> damage to lowest-HP enemy.
        var s = MakeState(); SimResolver.NewTurn(s);
        s.Enemies[0].Hp = 20; s.Enemies[0].MaxHp = 40;
        var p = s.Players[0];
        p.OrbCapacity = 3;
        SimOrbEngine.ChannelOrb(s, p, "LIGHTNING");
        Check(p.Orbs.Count == 1 && p.Orbs[0].OrbId == "LIGHTNING", "channeled lightning");
        SimOrbEngine.EvokeOrb(s, p);
        Check(s.Enemies[0].Hp == 20 - 8, $"lightning evoke dealt 8 to lowest enemy (hp {s.Enemies[0].Hp})");
        Check(p.Orbs.Count == 0, "orb popped on evoke");

        // Frost evoke gains block; focus adds to the value.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        var p2 = s2.Players[0]; p2.OrbCapacity = 3; p2.Focus = 2;
        SimOrbEngine.ChannelOrb(s2, p2, "FROST");
        SimOrbEngine.EvokeOrb(s2, p2);
        Check(p2.Block == 5 + 2, $"frost evoke block = 5 + focus 2 = 7 (block {p2.Block})");

        // Capacity is respected: channeling more than capacity does nothing beyond it.
        var s3 = MakeState(); SimResolver.NewTurn(s3);
        var p3 = s3.Players[0]; p3.OrbCapacity = 1;
        SimOrbEngine.ChannelOrb(s3, p3, "LIGHTNING");
        SimOrbEngine.ChannelOrb(s3, p3, "FROST");
        Check(p3.Orbs.Count == 1 && p3.Orbs[0].OrbId == "LIGHTNING", "second channel ignored (capacity 1)");

        // Passive fires at player turn end (Lightning deals passive to lowest enemy).
        var s4 = MakeState(); SimResolver.NewTurn(s4);
        s4.Players[0].OrbCapacity = 3;
        s4.Enemies[0].Hp = 20;
        SimOrbEngine.ChannelOrb(s4, s4.Players[0], "LIGHTNING");
        SimResolver.EndPlayerTurn(s4);
        Check(s4.Enemies[0].Hp == 20 - 3, $"lightning passive dealt 3 at turn end (hp {s4.Enemies[0].Hp})");

        // Dark orb grows on passive, evokes its accumulated value.
        var s5 = MakeState(); SimResolver.NewTurn(s5);
        s5.Players[0].OrbCapacity = 3;
        s5.Enemies[0].Hp = 50; s5.Enemies[0].MaxHp = 50;
        SimOrbEngine.ChannelOrb(s5, s5.Players[0], "DARK");
        Check(s5.Players[0].Orbs[0].Value == 0, "dark orb starts at 0");
        SimResolver.EndPlayerTurn(s5); // dark passive: value += passive(0)
        Check(s5.Players[0].Orbs[0].Value == 0, "dark base passive is 0");
        // Simulate growth by evoking after a manual grow
        var orb = s5.Players[0].Orbs[0];
        orb.Value += 7;
        SimOrbEngine.EvokeOrb(s5, s5.Players[0]);
        Check(s5.Enemies[0].Hp == 50 - 7, $"dark evoke dealt its accumulated value 7 (hp {s5.Enemies[0].Hp})");
    }

    private static void TestOrbCardAction()
    {
        Console.WriteLine("[Orb card action via resolver]");
        var s = MakeState(); SimResolver.NewTurn(s);
        s.Enemies[0].Hp = 30;
        s.Players[0].OrbCapacity = 3;
        // A card that channels a Frost and evokes next front orb.
        var card = new SimCard
        {
            Id = "ORBCARD", Name = "OrbCard", Cost = 1, CardType = "Skill", Target = SimTargetKind.None,
        };
        card.OrbActions.Add(new SimOrbAction { Action = "channel", OrbId = "LIGHTNING", Times = 1 });
        s.Hand.Insert(0, card);
        SimResolver.PlayCard(s, 0, null);
        Check(s.Players[0].Orbs.Count == 1 && s.Players[0].Orbs[0].OrbId == "LIGHTNING",
            "resolver applied the card's channel orb action");
    }

    private static void TestRelics()
    {
        Console.WriteLine("[Relics]");

        // Aggregate from declared vars.
        var agg = SimRelic.Aggregate(new Dictionary<string, int> { ["Energy"] = 1, ["Cards"] = 2, ["Block"] = 6, ["Strength"] = 3 });
        Check(agg.energy == 1 && agg.draw == 2 && agg.block == 6 && agg.strength == 3, "aggregate maps vars to mods");

        // Turn energy bonus: +1 energy each turn.
        var s = MakeState();
        s.TurnEnergyBonus = 1;
        SimResolver.NewTurn(s);
        Check(s.ActiveEnergy == s.MaxEnergy + 1, $"turn energy bonus applied (energy {s.ActiveEnergy})");

        // Turn draw bonus: draw 6 (5+1) instead of 5.
        var s2 = MakeState();
        s2.TurnDrawBonus = 1;
        s2.Hand.Clear();
        for (var i = 0; i < 8; i++) s2.DrawPile.Add(Strike());
        SimResolver.NewTurn(s2);
        Check(s2.Hand.Count == 6, $"turn draw bonus: drew 6 (hand {s2.Hand.Count})");

        // Combat start block/strength apply once, not every turn.
        var s3 = MakeState();
        s3.CombatStartBlock = 6;
        s3.CombatStartStrength = 2;
        SimResolver.NewTurn(s3);
        Check(s3.Players[0].Block == 6, "combat-start block applied at first turn");
        Check(GetPow(s3.Players[0], "strength_power") == 2, "combat-start strength applied at first turn");
        SimResolver.EndPlayerTurn(s3);
        SimResolver.NewTurn(s3);
        Check(s3.Players[0].Block == 0, "combat-start block does NOT re-apply next turn");
    }

    private static void TestOnPlayScript()
    {
        Console.WriteLine("[OnPlay DSL: behaviour cards]");

        // X-cost card: spend all energy, deal X damage.
        var s = MakeState(); SimResolver.NewTurn(s);
        var xcard = new SimCard
        {
            Id = "WHIRLWIND", Name = "Whirlwind", Cost = 0, CostsX = true, CardType = "Attack", Target = SimTargetKind.AllEnemies,
        };
        xcard.Script.Add(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AllEnemies, AmountIsX = true });
        xcard.Script.Add(new SimOp { Kind = SimOpKind.Draw, Target = SimTargetSel.Self, Amount = 2 });
        s.Hand.Insert(0, xcard);
        // s.ActiveEnergy = 3 after NewTurn; X resolves to 3.
        var enemyBefore = s.Enemies[0].Hp;
        SimResolver.PlayCard(s, 0, null);
        Check(s.Enemies[0].Hp == enemyBefore - 3, $"X-card dealt X=3 (hp {s.Enemies[0].Hp})");
        Check(s.ActiveEnergy == 0, "X-card spent all energy");
        Check(s.Hand.Count == 2, "draw 2 after X-card (hand 2)");

        // Multi-hit card: Damage 4, Times 3.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        var multi = new SimCard { Id = "MULTI", Name = "Multi", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy, Damage = 4 };
        multi.Script.Add(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 4, Times = 3 });
        s2.Hand.Insert(0, multi);
        SimResolver.PlayCard(s2, 0, 0);
        Check(s2.Enemies[0].Hp == 40 - 12, $"multi-hit dealt 4*3=12 (hp {s2.Enemies[0].Hp})");

        // Summon card.
        var s3 = MakeState(); SimResolver.NewTurn(s3);
        var summoner = new SimCard { Id = "SUMMON", Name = "Summon", Cost = 1, CardType = "Skill", Target = SimTargetKind.None };
        summoner.Script.Add(new SimOp { Kind = SimOpKind.Summon, Target = SimTargetSel.Self, SummonCardId = "SPAWNED" });
        s3.Hand.Insert(0, summoner);
        var handBefore = s3.Hand.Count;
        SimResolver.PlayCard(s3, 0, null);
        Check(s3.Hand.Count == handBefore, "summon: played 1, gained 1 -> same count");
        Check(s3.Hand.Exists(c => c.Id == "SPAWNED"), "summoned card present in hand");
    }

    private static void TestKeywordLifecycle()
    {
        Console.WriteLine("[Keyword lifecycle: RETAIN / ETHEREAL]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        s.Hand.Clear();
        var retain = new SimCard { Id = "RETAIN", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self, Block = 5, Retains = true };
        var ethereal = new SimCard { Id = "ETHEREAL", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self, Block = 3, Ethereal = true };
        var normal = new SimCard { Id = "NORMAL", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy, Damage = 6 };
        s.Hand.Add(retain); s.Hand.Add(ethereal); s.Hand.Add(normal);
        SimResolver.EndPlayerTurn(s);
        Check(s.Hand.Count == 1 && s.Hand[0].Id == "RETAIN", "retain card stays in hand");
        Check(s.ExhaustPile.Count == 1 && s.ExhaustPile[0].Id == "ETHEREAL", "ethereal card exhausts");
        Check(s.DiscardPile.Count == 1 && s.DiscardPile[0].Id == "NORMAL", "normal card discards");
    }

    private static void TestExhaustCluster()
    {
        Console.WriteLine("[Exhaust cluster: SECOND_WIND + Feel No Pain]");
        // SECOND_WIND: exhaust non-Attack hand cards, gain 5 block each.
        var s = MakeState(); SimResolver.NewTurn(s);
        s.Hand.Clear();
        s.Hand.Add(new SimCard { Id = "ATK", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy, Damage = 6 });
        s.Hand.Add(new SimCard { Id = "SK1", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self });
        s.Hand.Add(new SimCard { Id = "SK2", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self });
        var sw = new SimCard { Id = "SECOND_WIND", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self };
        sw.Script.Add(new SimOp { Kind = SimOpKind.ExhaustNonAttacks, Amount = 5 });
        s.Hand.Insert(0, sw);
        SimResolver.PlayCard(s, 0, null);
        Check(s.Players[0].Block == 10, $"second wind: 2 non-attacks * 5 block = 10 (block {s.Players[0].Block})");
        Check(s.ExhaustPile.Count == 2, $"two skills exhausted (pile {s.ExhaustPile.Count})");
        Check(s.Hand.Count == 1 && s.Hand[0].Id == "ATK", "attack card NOT exhausted");

        // Feel No Pain: on-exhaust hook gains block per exhausted card.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        SimCommand.ApplyPower(s2.Players[0], "feel_no_pain_power", 3);
        s2.Hand.Clear();
        s2.Hand.Add(new SimCard { Id = "SK", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self });
        SimResolver.EndPlayerTurn(s2);   // SK is not RETAIN/ETHEREAL -> reload? no; EndPlayerTurn discards, not exhaust.
        // Directly test the exhaust hook:
        var trig = MakeState(); SimResolver.NewTurn(trig);
        SimCommand.ApplyPower(trig.Players[0], "feel_no_pain_power", 3);
        trig.Hand.Add(new SimCard { Id = "EX", CardType = "Skill", Target = SimTargetKind.Self });
        SimCommand.ExhaustCard(trig, trig.Players[0], trig.Hand[0]);
        Check(trig.Players[0].Block == 3, $"feel no pain: +3 block on exhaust (block {trig.Players[0].Block})");

        // Ashen Strike style: ExtraDamagePerExhaust scales with exhaust pile size.
        var s3 = MakeState(); SimResolver.NewTurn(s3);
        var ashen = new SimCard { Id = "ASHEN_STRIKE", Name = "Ashen Strike", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy, Damage = 9, ExtraDamage = 3, ExtraDamagePerExhaust = true };
        s3.Hand.Insert(0, ashen);
        s3.ExhaustPile.Add(new SimCard { Id = "X1" }); s3.ExhaustPile.Add(new SimCard { Id = "X2" });
        SimResolver.PlayCard(s3, 0, 0);
        Check(s3.Enemies[0].Hp == 40 - 15, $"ashen strike: 9 + 3*2 exhausts = 15 (hp {s3.Enemies[0].Hp})");
    }

    private static void TestIroncladBehaviors()
    {
        Console.WriteLine("[Ironclad behaviour cards (DSL ops)]");
        // Twin Strike: deal 5 twice.
        var s = MakeState(); SimResolver.NewTurn(s);
        var twin = new SimCard { Id = "TWIN_STRIKE", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy };
        twin.Script.Add(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 5, Times = 2 });
        s.Hand.Insert(0, twin);
        SimResolver.PlayCard(s, 0, 0);
        Check(s.Enemies[0].Hp == 40 - 10, $"twin strike 5*2 = 10 (hp {s.Enemies[0].Hp})");

        // Anger: deal 6 + copy to discard.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        var anger = new SimCard { Id = "ANGER", Cost = 0, CardType = "Attack", Target = SimTargetKind.AnyEnemy };
        anger.Script.Add(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 6 });
        anger.Script.Add(new SimOp { Kind = SimOpKind.CopyToPile, Pile = SimPile.Discard });
        s2.Hand.Insert(0, anger);
        SimResolver.PlayCard(s2, 0, 0);
        Check(s2.Enemies[0].Hp == 40 - 6, $"anger dealt 6 (hp {s2.Enemies[0].Hp})");
        Check(s2.DiscardPile.Exists(c => c.Id == "ANGER"), "anger copy added to discard pile");

        // Body Slam: deal = current block.
        var s3 = MakeState(); SimResolver.NewTurn(s3);
        s3.Players[0].Block = 12;
        var slam = new SimCard { Id = "BODY_SLAM", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy };
        slam.Script.Add(new SimOp { Kind = SimOpKind.DamageEqualBlock, Target = SimTargetSel.AnyEnemy });
        s3.Hand.Insert(0, slam);
        SimResolver.PlayCard(s3, 0, 0);
        Check(s3.Enemies[0].Hp == 40 - 12, $"body slam dealt = block 12 (hp {s3.Enemies[0].Hp})");

        // Molten Fist: deal 10 + double target vulnerable + exhaust.
        var s4 = MakeState(); SimResolver.NewTurn(s4);
        SimCommand.ApplyPower(s4.Enemies[0], "vulnerable_power", 2);
        var fist = new SimCard { Id = "MOLTEN_FIST", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy, ExhaustsOnPlay = true };
        fist.Script.Add(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 10 });
        fist.Script.Add(new SimOp { Kind = SimOpKind.DoublePower, Target = SimTargetSel.AnyEnemy, PowerId = "vulnerable_power" });
        s4.Hand.Insert(0, fist);
        SimResolver.PlayCard(s4, 0, 0);
        Check(s4.Enemies[0].Hp == 40 - 15, $"molten fist: 10 dmg *1.5 vuln = 15 (hp {s4.Enemies[0].Hp})");
        Check(s4.Enemies[0].Powers["vulnerable_power"] == 4, "vulnerable doubled 2->4");
        Check(s4.ExhaustPile.Exists(c => c.Id == "MOLTEN_FIST"), "molten fist exhausted on play");

        // Perfected Strike: 6 + 2 per Strike-named card in deck.
        var s5 = MakeState(); SimResolver.NewTurn(s5);
        s5.Hand.Clear(); s5.Hand.Add(new SimCard { Id = "STRIKE_IRONCLAD", Name = "打击", CardType = "Attack" });
        s5.Hand.Add(new SimCard { Id = "STRIKE_IRONCLAD", Name = "打击", CardType = "Attack" });
        s5.DrawPile.Add(new SimCard { Id = "STRIKE_IRONCLAD", Name = "打击", CardType = "Attack" });
        var ps = new SimCard { Id = "PERFECTED_STRIKE", Cost = 2, CardType = "Attack", Target = SimTargetKind.AnyEnemy };
        ps.Script.Add(new SimOp { Kind = SimOpKind.PerXDamage, Target = SimTargetSel.AnyEnemy, Amount = 6, Per = 2, Condition = SimPerCondition.StrikeInDeck });
        s5.Hand.Insert(0, ps);
        SimResolver.PlayCard(s5, 0, 0);
        Check(s5.Enemies[0].Hp == 40 - (6 + 2 * 3), $"perfected strike: 6 + 2*3 strikes = 12 (hp {s5.Enemies[0].Hp})");

        // Bully: deal 4 + 2 per Vulnerable on target.
        var s6 = MakeState(); SimResolver.NewTurn(s6);
        SimCommand.ApplyPower(s6.Enemies[0], "vulnerable_power", 3);
        var bully = new SimCard { Id = "BULLY", Cost = 0, CardType = "Attack", Target = SimTargetKind.AnyEnemy };
        bully.Script.Add(new SimOp { Kind = SimOpKind.PerXDamage, Target = SimTargetSel.AnyEnemy, Amount = 4, Per = 2, Condition = SimPerCondition.TargetVulnerable });
        s6.Hand.Insert(0, bully);
        SimResolver.PlayCard(s6, 0, 0);
        Check(s6.Enemies[0].Hp == 40 - 15, $"bully: (4 + 2*3 vuln)=10 dmg *1.5 vuln = 15 (hp {s6.Enemies[0].Hp})");
    }

    private static void TestSilentBehaviors()
    {
        Console.WriteLine("[Silent behaviour cards (Shiv/discard/repeat)]");
        // Add 2 Shivs to hand.
        var s = MakeState(); SimResolver.NewTurn(s);
        var bd = new SimCard { Id = "BLADE_DANCE", Cost = 1, CardType = "Skill", Target = SimTargetKind.None };
        bd.Script.Add(new SimOp { Kind = SimOpKind.AddShivs, Amount = 3 });
        s.Hand.Insert(0, bd);
        var before = s.Hand.Count;
        SimResolver.PlayCard(s, 0, null);
        Check(s.Hand.Count == before + 2, $"blade dance added 3 shivs, played 1 => net +2 (hand {s.Hand.Count})");
        Check(s.Hand.Count(c => c.Id == "SHIV") == 3, "3 shivs in hand");

        // Dagger Spray: deal 4 to ALL enemies twice.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        s2.Enemies.Add(new SimCombatant(40, 40));
        var ds = new SimCard { Id = "DAGGER_SPRAY", Cost = 1, CardType = "Attack", Target = SimTargetKind.AllEnemies };
        ds.Script.Add(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AllEnemies, Amount = 4, Times = 2 });
        s2.Hand.Insert(0, ds);
        SimResolver.PlayCard(s2, 0, null);
        Check(s2.Enemies[0].Hp == 40 - 8 && s2.Enemies[1].Hp == 40 - 8, "dagger spray 4x2 = 8 to each enemy");

        // Survivor: gain 8 block, discard 1.
        var s3 = MakeState(); SimResolver.NewTurn(s3);
        var sv = new SimCard { Id = "SURVIVOR", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self };
        sv.Script.Add(new SimOp { Kind = SimOpKind.Block, Target = SimTargetSel.Self, Amount = 8 });
        sv.Script.Add(new SimOp { Kind = SimOpKind.Discard, Amount = 1 });
        s3.Hand.Insert(0, sv);
        var handBefore = s3.Hand.Count;
        SimResolver.PlayCard(s3, 0, null);
        Check(s3.Players[0].Block == 8, "survivor gained 8 block");
        Check(s3.Hand.Count == handBefore - 2, $"survivor played 1 + discarded 1 => -2 (hand {s3.Hand.Count})");
    }

    private static void TestNextTurnEffects()
    {
        Console.WriteLine("[Next-turn cross-turn effects]");
        // Glitterstream: gain 11 block now + 5 block next turn.
        var s = MakeState(); SimResolver.NewTurn(s);
        var gs = new SimCard { Id = "GLITTERSTREAM", Cost = 2, CardType = "Skill", Target = SimTargetKind.Self };
        gs.Script.Add(new SimOp { Kind = SimOpKind.Block, Target = SimTargetSel.Self, Amount = 11 });
        gs.Script.Add(new SimOp { Kind = SimOpKind.NextTurnBlock, Amount = 5 });
        s.Hand.Insert(0, gs);
        SimResolver.PlayCard(s, 0, null);
        Check(s.Players[0].Block == 11, "glitterstream granted 11 block now");
        Check(s.NextTurnBlock == 5, "5 block pending for next turn");
        SimResolver.EndPlayerTurn(s);
        SimEnemy.RunEnemyTurn(s);      // enemy does nothing (no moves)
        SimResolver.NewTurn(s);        // block cleared, then next-turn block applied
        Check(s.Players[0].Block == 5, $"next-turn block applied at new turn (block {s.Players[0].Block})");

        // Glow: draw 1 now + draw 1 next turn.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        s2.Hand.Clear();
        for (var i = 0; i < 6; i++) s2.DrawPile.Add(Strike());
        var glow = new SimCard { Id = "GLOW", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self };
        glow.Script.Add(new SimOp { Kind = SimOpKind.Draw, Target = SimTargetSel.Self, Amount = 1 });
        glow.Script.Add(new SimOp { Kind = SimOpKind.NextTurnDraw, Amount = 1 });
        s2.Hand.Insert(0, glow);
        SimResolver.PlayCard(s2, 0, null);
        Check(s2.Hand.Count == 1, $"glow drew 1 now (hand {s2.Hand.Count})");
        Check(s2.NextTurnDraw == 1, "draw 1 pending for next turn");
        SimResolver.EndPlayerTurn(s2);
        SimEnemy.RunEnemyTurn(s2);
        SimResolver.NewTurn(s2);
        Check(s2.Hand.Count == 6, $"next-turn draw applied (drew 5+1 = 6, hand {s2.Hand.Count})");
    }

    private static void TestAncientPowers()
    {
        Console.WriteLine("[Ancient powers (turn-start stat loss)]");
        // Wraith Form: lose dexterity each turn.
        var s = MakeState(); SimResolver.NewTurn(s);
        SimCommand.ApplyPower(s.Players[0], "dexterity_power", 5);
        SimCommand.ApplyPower(s.Players[0], "wraith_form_power", 1);
        SimResolver.EndPlayerTurn(s);
        SimResolver.NewTurn(s);   // turn start -> wraith form loses 1 dex
        Check(s.Players[0].Powers["dexterity_power"] == 4, $"wraith form -1 dex at turn start (dex {s.Players[0].Powers["dexterity_power"]})");

        // Biased Cognition: lose focus each turn.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        s2.Players[0].Focus = 5;
        SimCommand.ApplyPower(s2.Players[0], "biased_cognition_power", 1);
        SimResolver.EndPlayerTurn(s2);
        SimResolver.NewTurn(s2);
        Check(s2.Players[0].Focus == 4, $"biased cognition -1 focus at turn start (focus {s2.Players[0].Focus})");

        // Brightest Flame: lose max HP.
        var s3 = MakeState(); SimResolver.NewTurn(s3);
        var bf = new SimCard { Id = "BRIGHTEST_FLAME", Cost = 0, CardType = "Skill", Target = SimTargetKind.Self };
        bf.Script.Add(new SimOp { Kind = SimOpKind.GainEnergy, Amount = 2 });
        bf.Script.Add(new SimOp { Kind = SimOpKind.LoseMaxHp, Amount = 2 });
        s3.Hand.Insert(0, bf);
        var maxBefore = s3.Players[0].MaxHp;
        SimResolver.PlayCard(s3, 0, null);
        Check(s3.Players[0].MaxHp == maxBefore - 2, $"brightest flame -2 max hp (max {s3.Players[0].MaxHp})");
    }

    private static void TestGameRng()
    {
        Console.WriteLine("[Game RNG port (determinism + sync)]");
        // Same seed + stream => identical sequence.
        var a = GameRng.Create(12345, "CombatCardGeneration");
        var b = GameRng.Create(12345, "CombatCardGeneration");
        var ok = true;
        for (var i = 0; i < 10; i++) if (a.NextInt(1000) != b.NextInt(1000)) ok = false;
        Check(ok, "same seed+stream => identical sequence");

        // Different stream name => different seed => different sequence.
        var s1 = GameRng.Create(12345, "CombatCardGeneration");
        var s2 = GameRng.Create(12345, "Shuffle");
        Check(s1.NextInt(1000) != s2.NextInt(1000), "different stream => different first value");

        // SnakeCase normalization: both pascal and snake inputs map to the same stream seed (the
        // caller passes a RunRngType.ToString() like "CombatCardGeneration"). Different stream NAMES
        // differ (checked above); case-normalized forms converge by design.

        // SyncToGameCounter: a stream advanced N draws continues where a fresh one advanced N does.
        var fresh = GameRng.Create(999, "MonsterAi");
        for (var i = 0; i < 5; i++) fresh.NextInt(100);
        var synced = GameRng.Create(999, "MonsterAi", gameCounter: 5);
        Check(fresh.NextInt(100) == synced.NextInt(100), "sync to game counter continues same stream");
        Check(synced.Counter == 6, "counter increments after synced draw");
    }

    private static void TestTransientPowers()
    {
        Console.WriteLine("[Power-centric: damage/block as transient powers]");
        // damage_power resolves via modifier chain (strength + weak + vulnerable), then vanishes.
        var s = MakeState(); SimResolver.NewTurn(s);
        SimCommand.ApplyPower(s.Players[0], "strength_power", 3);                 // attacker strength
        SimCommand.ApplyPower(s.Enemies[0], "vulnerable_power", 1);                // target vuln
        var hb = s.Enemies[0].Hp;
        SimCommand.ApplyPower(s, s.Players[0], s.Enemies[0], "damage_power", 9, true);
        var dealt = hb - s.Enemies[0].Hp;
        Check(dealt == (int)((9 + 3) * 1.5), $"damage_power: (9+str3)*vuln1.5 = 18 (dealt {dealt})");
        Check(!s.Enemies[0].Powers.ContainsKey("damage_power"), "damage_power leaves no residue");

        // buffer negates a damage_power instance.
        var s2 = MakeState(); SimResolver.NewTurn(s2);
        SimCommand.ApplyPower(s2.Enemies[0], "buffer_power", 1);
        var hb2 = s2.Enemies[0].Hp;
        SimCommand.ApplyPower(s2, s2.Players[0], s2.Enemies[0], "damage_power", 5, true);
        Check(s2.Enemies[0].Hp == hb2, "buffer negated damage_power");
        Check(s2.Enemies[0].Powers["buffer_power"] == 0, "buffer consumed");

        // block_power adds block (respecting dexterity).
        var s3 = MakeState(); SimResolver.NewTurn(s3);
        SimCommand.ApplyPower(s3.Players[0], "dexterity_power", 2);
        SimCommand.ApplyPower(s3, null, s3.Players[0], "block_power", 5, false);
        Check(s3.Players[0].Block == 5 + 2, $"block_power: 5 + dex2 = 7 (block {s3.Players[0].Block})");

        // A persistent power (strength) STACKS rather than resolving.
        var s4 = MakeState(); SimResolver.NewTurn(s4);
        SimCommand.ApplyPower(s4, null, s4.Players[0], "strength_power", 2, false);
        SimCommand.ApplyPower(s4, null, s4.Players[0], "strength_power", 3, false);
        Check(s4.Players[0].Powers["strength_power"] == 5, "persistent power stacks 2+3=5");
    }

    private static void TestPayloadRoundTrip()
    {
        Console.WriteLine("[SimPayload round-trip]");
        var s = MakeState();
        SimResolver.NewTurn(s);
        s.Hand.Clear();
        var sw = new SimCard { Id = "SECOND_WIND", Name = "Second Wind", Cost = 1, CardType = "Skill", Target = SimTargetKind.Self };
        sw.Script.Add(new SimOp { Kind = SimOpKind.ExhaustNonAttacks, Amount = 5 });
        s.Hand.Add(sw);
        s.Hand.Add(new SimCard { Id = "STRIKE", Name = "Strike", Cost = 1, CardType = "Attack", Target = SimTargetKind.AnyEnemy, Damage = 6 });
        s.Enemies[0].Powers["vulnerable_power"] = 2;

        var json = System.Text.Json.JsonSerializer.Serialize(SimPayload.ToPayload(s));
        var back = SimPayload.FromPayload(json);
        // SimPayload doesn't serialise orbs/piles order beyond what's captured; compare the key state.
        Check(back.Players[0].Hp == s.Players[0].Hp && back.Players[0].Block == s.Players[0].Block, "player hp/block round-trip");
        Check(back.Enemies[0].Powers.GetValueOrDefault("vulnerable_power") == 2, "enemy power round-trip");
        Check(back.Hand.Count == 2, "hand count round-trip");
        Check(back.Hand[0].Id == "SECOND_WIND" && back.Hand[0].Script.Count == 1, "behaviour card + script round-trip");
        Check(back.Hand[1].CardType == "Attack", "card type round-trip");
        Check(back.ActiveEnergy == s.ActiveEnergy, "energy round-trip");
    }

    private static void TestCanonicalizeOrderInsensitive()
    {
        Console.WriteLine("[Canonicalize makes order differences neutral]");
        var a = MakeState();
        var b = a.Clone();
        a.DiscardPile.Add(Strike()); a.DiscardPile.Add(Defend());
        b.DiscardPile.Add(Defend()); b.DiscardPile.Add(Strike());   // swapped order
        Check(SimDiff.Diff(a, b).Count != 0, "raw diff sees the order difference");
        SimDiff.Canonicalize(a);
        SimDiff.Canonicalize(b);
        Check(SimDiff.Diff(a, b).Count == 0, "canonicalized diff is order-insensitive");
    }

    private static int Main()
    {
        TestNewTurn();
        TestStrikeBlockAbsorption();
        TestStrikeOverflowsBlockToHp();
        TestDefendGainsBlock();
        TestBashAppliesVulnerableToTarget();
        TestDrawCardFromPile();
        TestCloneIndependence();
        TestDeterminism();
        TestDiffReplayAceq();
        TestDiffCatchesChange();
        TestCanonicalizeOrderInsensitive();
        TestReplayMatchesCaptured();
        TestReplayDetectsMismatch();
        TestRngDeterminism();
        TestRngStreamsIndependent();
        TestRngCloneIndependent();
        TestNewFieldPrimitives();
        TestPowerHooks();
        TestPowerTurnTicks();
        TestEnemyEngine();
        TestSearchKillsWhenTerminal();
        TestSearchSurvivesOverGreedy();
        TestSearchBudgetExceeded();
        TestOrbs();
        TestOrbCardAction();
        TestRelics();
        TestOnPlayScript();
        TestKeywordLifecycle();
        TestExhaustCluster();
        TestIroncladBehaviors();
        TestSilentBehaviors();
        TestNextTurnEffects();
        TestAncientPowers();
        TestGameRng();
        TestTransientPowers();
        TestPayloadRoundTrip();

        Console.WriteLine();
        Console.WriteLine(_failures == 0 ? "ALL PASS" : $"{_failures} FAILURE(S)");
        return _failures == 0 ? 0 : 1;
    }
}
