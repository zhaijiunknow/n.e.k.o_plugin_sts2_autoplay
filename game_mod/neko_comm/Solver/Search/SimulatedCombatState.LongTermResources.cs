namespace CombatSolver;

internal sealed partial class SimulatedCombatState
{
    private int _longTermResourceValue;
    private int _angerCopiesGenerated;

    public int LongTermResourceValue => _longTermResourceValue;
    public int AngerCopiesGenerated => _angerCopiesGenerated;

    public void RecordLongTermResource(int value)
    {
        if (value <= 0)
            throw new ArgumentOutOfRangeException(nameof(value), value, "长期资源增量必须为正数。");
        _longTermResourceValue = checked(_longTermResourceValue + value);
    }

    public void RecordAngerCopyGenerated()
        => _angerCopiesGenerated = checked(_angerCopiesGenerated + 1);
}
