using MegaCrit.Sts2.Core.Models;

namespace CombatSolver.Engine.InCombat.Simulation;

internal readonly record struct RecordedRelicTrigger(string RelicId, string Summary);

/// <summary>
/// Enabled only for the single final-route replay. Normal Beam expansion keeps this null, so
/// displaying relic provenance does not add a list or string allocation to every transition.
/// </summary>
internal sealed class ActionRelicTriggerRecorder
{
    private readonly Dictionary<int, List<RecordedRelicTrigger>> _triggers = [];
    private int _actionIndex = -1;

    public void BeginAction(int actionIndex) => _actionIndex = actionIndex;

    public void Record(RelicModel relic, string summary)
    {
        if (_actionIndex < 0)
            throw new InvalidOperationException("Relic trigger was recorded outside a planned action.");
        RecordedRelicTrigger trigger = new(relic.Id.Entry, summary);
        if (!_triggers.TryGetValue(_actionIndex, out List<RecordedRelicTrigger>? entries))
        {
            entries = [];
            _triggers.Add(_actionIndex, entries);
        }
        if (!entries.Contains(trigger))
            entries.Add(trigger);
    }

    public IReadOnlyList<RecordedRelicTrigger> ForAction(int actionIndex)
        => _triggers.GetValueOrDefault(actionIndex) ?? [];
}
