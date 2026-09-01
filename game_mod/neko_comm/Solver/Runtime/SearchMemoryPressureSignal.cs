namespace CombatSolver;

internal sealed class SearchMemoryPressureSignal
{
    private long _allocatedBytesAtStart;
    private long _allocationLimitBytes = long.MaxValue;
    private Action<CancellationToken>? _reclaimAndContinue;

    public int ReclaimCount { get; private set; }

    public long AllocatedBytes
        => Math.Max(0, GC.GetTotalAllocatedBytes(precise: false) - Volatile.Read(ref _allocatedBytesAtStart));

    public long AllocationLimitBytes => Volatile.Read(ref _allocationLimitBytes);

    public void Configure(
        long allocatedBytesAtStart,
        long allocationLimitBytes,
        Action<CancellationToken> reclaimAndContinue)
    {
        if (allocationLimitBytes <= 0)
            throw new ArgumentOutOfRangeException(nameof(allocationLimitBytes));
        ArgumentNullException.ThrowIfNull(reclaimAndContinue);
        Volatile.Write(ref _allocatedBytesAtStart, allocatedBytesAtStart);
        Volatile.Write(ref _reclaimAndContinue, reclaimAndContinue);
        Volatile.Write(ref _allocationLimitBytes, allocationLimitBytes);
    }

    public bool IsLimitReached()
        => AllocatedBytes >= AllocationLimitBytes;

    public void ReclaimAndContinue(CancellationToken cancellationToken)
    {
        Action<CancellationToken> reclaim = Volatile.Read(ref _reclaimAndContinue)
            ?? throw new InvalidOperationException("搜索内存回收信号尚未配置。");
        reclaim(cancellationToken);
        ReclaimCount++;
    }

    public void Disable()
    {
        Volatile.Write(ref _allocationLimitBytes, long.MaxValue);
        Volatile.Write(ref _reclaimAndContinue, null);
    }
}
