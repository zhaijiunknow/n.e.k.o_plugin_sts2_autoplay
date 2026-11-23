using System.Text;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Ascension;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Runs;

namespace CombatSolver.HotKernel;

/// <summary>
/// Standalone immutable run-rules snapshot frozen with a combat root and shared across all HotKernel branches.
/// </summary>
internal sealed record HotCombatRulesSnapshot(
    GameMode GameMode,
    int AscensionLevel,
    ulong Ascensions,
    string? DominionId)
{
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
    public bool IsBlitz => GameMode == GameMode.Blitz;

    public bool HasAscension(AscensionLevel level)
        => (Ascensions & (1UL << (int)level)) != 0;

    public static HotCombatRulesSnapshot Capture(CombatState combat)
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

    internal static HotCombatRulesSnapshot Capture(IRunState run, string? dominionId)
    {
        if (!NGame.IsMainThread())
            throw new InvalidOperationException("Capture live run rules on the main thread.");
        AscensionManager ascensions = new(run.GameMode, run.AscensionLevel);
        ulong mask = 0;
        foreach (AscensionLevel level in Enum.GetValues<AscensionLevel>())
        {
            if (ascensions.HasLevel(level))
                mask |= 1UL << (int)level;
        }
        return new HotCombatRulesSnapshot(run.GameMode, run.AscensionLevel, mask, dominionId);
    }

    public bool Allows(CardModel card)
        => !IsBlitz || card.IsAllowedInBlitz;

    public void Append(StringBuilder text)
    {
        text.Append(";combat_rules=").Append((int)GameMode).Append(',')
            .Append(AscensionLevel).Append(',').Append(Ascensions).Append(',').Append(DominionId);
        // Single-player text is unchanged so existing route caches and continuation stamps stay valid.
        if (IsMultiplayer)
            text.Append(",players=").Append(PlayerCount);
    }
}
