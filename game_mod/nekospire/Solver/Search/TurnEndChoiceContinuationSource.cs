namespace CombatSolver;

internal sealed class TurnEndChoiceContinuationSource : IFrontierContinuationSource
{
    private readonly CombatBeamSolver _builder;
    private readonly PlanAction[] _beforeEndTurn;
    private readonly string _chosenKey;

    public bool DeduplicatePrefixes => false;

    internal TurnEndChoiceContinuationSource(
        CombatBeamSolver builder,
        PlanAction[] beforeEndTurn,
        string chosenKey)
    {
        _builder = builder;
        _beforeEndTurn = beforeEndTurn;
        _chosenKey = chosenKey;
    }

    public IEnumerable<PlanAction[]> Enumerate()
        => _builder.BuildTurnEndChoiceActionsAfterPrefix(_beforeEndTurn)
            .Where(action => CombatBeamSolver.TurnEndChoiceKey(action) != _chosenKey)
            .Take(2)
            .Select(action => _beforeEndTurn.Append(action).ToArray());

    public bool CanReplay(PlanAction[] prefix) => true;
}
