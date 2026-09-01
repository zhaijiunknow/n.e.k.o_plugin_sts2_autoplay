using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver.Engine.Common;

internal sealed class CombatPredictionRisk(IReadOnlyList<CombatPredictionRiskEntry> entries)
    : PredictionRisk(entries.Count > 0)
{
    public IReadOnlyList<CombatPredictionRiskEntry> Entries { get; } = entries;
}
