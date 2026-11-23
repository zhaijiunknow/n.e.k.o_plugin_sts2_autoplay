"""nekospire-only behaviour patches on top of the vendored CombatSolver engine.

Applied by vendor-combat-solver.py after the upstream closure is copied in. Upstream is NOT modified;
these exist only in nekospire's embedded copy. Every (old, new) pair must match exactly once — when
upstream rewrites the surrounding code the sync fails instead of silently dropping the patch.

## multiplayer-local-player

Upstream models one player and never searches a multiplayer combat. nekospire's co-op catgirl needs a
plan, so the embedded kernel plans for the LOCAL player in a multiplayer combat:

  * the player count is frozen into HotCombatRulesSnapshot at root capture, together with the two vanilla
    scaling factors (Creature.ScaleHpForMultiplayer, MultiplayerScalingModel.ModifyBlockMultiplicative);
  * monsters created / hatched / revived mid-combat get the scaled HP, enemy move block gets the scaled
    block; with one player every factor is 1 and the state text is unchanged;
  * two "the only player" assumptions take the local player instead.

Teammates are still not simulated: shared RNG streams they consume (shuffles, random targets) and their
future plays are outside the model. The facade flags every multiplayer plan accordingly.
"""

LOCAL_PATCHES: dict[str, list[tuple[str, str]]] = {
    "HotKernel/Rules/HotCombatRulesSnapshot.cs": [
        (r'''{
    public bool IsDread => GameMode == GameMode.Dread;
''', r'''{
    /// <summary>
    /// Number of players in the combat. The kernel simulates the LOCAL player only; teammates are not
    /// simulated (their future actions are unknowable), but the player count changes enemy rules.
    /// </summary>
    public int PlayerCount { get; init; } = 1;

    /// <summary>
    /// STS2 0.113.1 <c>Creature.ScaleHpForMultiplayer</c>: HP of every monster created, hatched or revived
    /// mid-combat is multiplied by <c>playerCount * MultiplayerScalingModel.GetMultiplayerScaling</c>
    /// (1 in single-player). Frozen at the root because it depends on the encounter and the act.
    /// </summary>
    public decimal MultiplayerHpScale { get; init; } = 1m;

    /// <summary>
    /// STS2 0.113.1 <c>MultiplayerScalingModel.ModifyBlockMultiplicative</c>: powered monster-move block
    /// gained by an enemy is multiplied by <c>playerCount</c> (two players) or
    /// <c>playerCount * GetMultiplayerScaling</c> (three or more); 1 in single-player.
    /// </summary>
    public decimal MultiplayerEnemyBlockScale { get; init; } = 1m;

    public bool IsMultiplayer => PlayerCount > 1;

    public int ScaleMonsterHp(int hp)
        => MultiplayerHpScale == 1m ? hp : (int)Math.Min(hp * MultiplayerHpScale, 999_999_999m);

    public bool IsDread => GameMode == GameMode.Dread;
'''),
        (r'''    public static HotCombatRulesSnapshot Capture(CombatState combat)
        => Capture(combat.RunState, combat.Dominion?.Id.Entry);
''', r'''    public static HotCombatRulesSnapshot Capture(CombatState combat)
    {
        HotCombatRulesSnapshot rules = Capture(combat.RunState, combat.Dominion?.Id.Entry);
        int playerCount = combat.Players.Count;
        if (playerCount <= 1)
            return rules;
        // Both vanilla call sites read the count from the combat/run player list and the scaling from
        // the encounter + current act; neither changes during a combat.
        decimal scaling = MultiplayerScalingModel.GetMultiplayerScaling(
            combat.Encounter, combat.RunState.CurrentActIndex);
        return rules with
        {
            PlayerCount = playerCount,
            MultiplayerHpScale = playerCount * scaling,
            MultiplayerEnemyBlockScale = playerCount <= 2 ? playerCount : playerCount * scaling,
        };
    }
'''),
        (r'''    public void Append(StringBuilder text)
        => text.Append(";combat_rules=").Append((int)GameMode).Append(',')
            .Append(AscensionLevel).Append(',').Append(Ascensions).Append(',').Append(DominionId);
''', r'''    public void Append(StringBuilder text)
    {
        text.Append(";combat_rules=").Append((int)GameMode).Append(',')
            .Append(AscensionLevel).Append(',').Append(Ascensions).Append(',').Append(DominionId);
        // Single-player text is unchanged so existing route caches and continuation stamps stay valid.
        if (IsMultiplayer)
            text.Append(",players=").Append(PlayerCount);
    }
'''),
    ],
    "HotKernel/Sim/HotCombatEngine.Damage.cs": [
        (r'''            if ((s.ActiveHookMask & (ulong)HotHookMask.ModifyBlockMultiplicative) != 0
                && rules.FrailPowerDefId != 0
                && GetPowerAmount(in s, targetSlot, rules.FrailPowerDefId) > 0)
            {
                block *= 0.75m;
            }
        }
''', r'''            if ((s.ActiveHookMask & (ulong)HotHookMask.ModifyBlockMultiplicative) != 0
                && rules.FrailPowerDefId != 0
                && GetPowerAmount(in s, targetSlot, rules.FrailPowerDefId) > 0)
            {
                block *= 0.75m;
            }
            block *= MultiplayerEnemyBlockScale(rules, targetSlot);
        }
'''),
        (r'''            if (mulArgs.DecimalMultiplier >= decimal.MaxValue && block > 0m)
                block = SettlementCeilingDecimal;
            else
                block *= mulArgs.DecimalMultiplier;
        }

        return Math.Max(0m, block);
    }
''', r'''            if (mulArgs.DecimalMultiplier >= decimal.MaxValue && block > 0m)
                block = SettlementCeilingDecimal;
            else
                block *= mulArgs.DecimalMultiplier;
        }

        // MultiplayerScalingModel is a RunModel listener of the same ModifyBlockMultiplicative hook; it is
        // not a power/relic, so it is applied here instead of through ActiveHookMask dispatch.
        if (isCardBlock && !isUnpowered)
            block *= MultiplayerEnemyBlockScale(rules, targetSlot);

        return Math.Max(0m, block);
    }

    /// <summary>
    /// STS2 0.113.1 <c>MultiplayerScalingModel.ModifyBlockMultiplicative</c>: only enemy-side targets
    /// (primary or secondary) and only powered card/monster-move block. Callers pass the powered check.
    /// </summary>
    private static decimal MultiplayerEnemyBlockScale(HotRules rules, byte targetSlot)
        => targetSlot >= 3 && targetSlot < 3 + HotLimits.MaxEnemies
            ? rules.RulesSnapshot.MultiplayerEnemyBlockScale
            : 1m;
'''),
    ],
    "HotKernel/Sim/Content/HotMonsters.cs": [
        (r'''                    int hp = HotRng.NextInt(ref s.Rng[(int)HotRngStream.Niche], minHp, maxHp + 1);
                    s.Enemies[slot].Vital.MaxHp = hp;
''', r'''                    // ToughEgg.Hatch: ScaleHpForMultiplayer(Niche.NextInt(min, max + 1), ...)
                    int hp = rules.RulesSnapshot.ScaleMonsterHp(
                        HotRng.NextInt(ref s.Rng[(int)HotRngStream.Niche], minHp, maxHp + 1));
                    s.Enemies[slot].Vital.MaxHp = hp;
'''),
        (r'''            int hp = respawns == 1
                ? GetStaticInt(monsterDef, "SecondFormHp", IsTough(rules) ? 212 : 200)
                : GetStaticInt(monsterDef, "ThirdFormHp", IsTough(rules) ? 313 : 300);
''', r'''            // TestSubject.Revive: ScaleHpForMultiplayer(baseRespawnHp, ...)
            int hp = rules.RulesSnapshot.ScaleMonsterHp(respawns == 1
                ? GetStaticInt(monsterDef, "SecondFormHp", IsTough(rules) ? 212 : 200)
                : GetStaticInt(monsterDef, "ThirdFormHp", IsTough(rules) ? 313 : 300));
'''),
        (r'''        int fatHp = RollSpawnHp(in s, ref s.Rng[(int)HotRngStream.Niche], rules, HotMonsterId.FatGremlin, fatDefId, 0, 0);
''', r'''        int fatHp = rules.RulesSnapshot.ScaleMonsterHp(
            RollSpawnHp(in s, ref s.Rng[(int)HotRngStream.Niche], rules, HotMonsterId.FatGremlin, fatDefId, 0, 0));
'''),
        (r'''        int baseHp = RollSpawnHp(
            in s,
            ref s.Rng[(int)HotRngStream.Niche],
            rules,
            monsterId,
            monsterDefId,
            customMinHp,
            customMaxHp);
''', r'''        // CombatState.CreateCreature: SetUniqueMonsterHpValue (unscaled roll, compared against the other
        // enemies' already-scaled max HP) and then ScaleMonsterHpForMultiplayer.
        int baseHp = rules.RulesSnapshot.ScaleMonsterHp(RollSpawnHp(
            in s,
            ref s.Rng[(int)HotRngStream.Niche],
            rules,
            monsterId,
            monsterDefId,
            customMinHp,
            customMaxHp));
'''),
    ],
    "Search/GrowthPolicy.cs": [
        (r'''        var player = combat.Players.Single();
''', r'''        // The growth ledger this feeds only tracks the local player; a co-op combat has teammates too.
        var player = MegaCrit.Sts2.Core.Context.LocalContext.GetMe(combat)
            ?? throw new InvalidOperationException("找不到本地玩家。");
'''),
    ],
    "Runtime/BattleDamageTracker.cs": [
        (r'''    private static Player? GetSinglePlayer(ICombatState? combat)
        => combat?.Players.Count == 1 ? combat.Players[0] : null;
''', r'''    // The tracked battle damage is the LOCAL player's; in a co-op combat that is LocalContext.GetMe,
    // not "the only player".
    private static Player? GetSinglePlayer(ICombatState? combat)
        => combat == null
            ? null
            : combat.Players.Count == 1
                ? combat.Players[0]
                : MegaCrit.Sts2.Core.Context.LocalContext.GetMe(combat.Players);
'''),
    ],
}
