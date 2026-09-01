namespace CombatSolver;

internal sealed class SearchFramePressureSignal
{
    private long _frameSequence;
    private int _pressureEpoch;

    public void ResetPressure()
        => Volatile.Write(ref _pressureEpoch, 0);

    public void ObserveFrame(bool pressured)
    {
        Interlocked.Increment(ref _frameSequence);
        if (pressured)
            Interlocked.Increment(ref _pressureEpoch);
    }

    public bool WaitForRecovery(ref int observedPressureEpoch)
    {
        int pressureEpoch = Volatile.Read(ref _pressureEpoch);
        if (pressureEpoch == observedPressureEpoch)
            return false;
        observedPressureEpoch = pressureEpoch;
        long frameSequence = Volatile.Read(ref _frameSequence);
        SpinWait.SpinUntil(
            () => Volatile.Read(ref _frameSequence) != frameSequence,
            millisecondsTimeout: 25);
        return true;
    }
}
