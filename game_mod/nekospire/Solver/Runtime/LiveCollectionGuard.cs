using System.Collections.Concurrent;
using System.Reflection;

namespace CombatSolver;

/// <summary>
/// 实机集合在根捕获窗口内被并发改写时的确定性拒绝。它替代 List 枚举器抛出的裸
/// Collection was modified，让日志能指出集合名、数量与版本变化。
/// </summary>
internal sealed class LiveCollectionModifiedException : InvalidOperationException
{
    public LiveCollectionModifiedException(string collectionName, string detail)
        : base($"Live collection '{collectionName}' changed during combat root capture ({detail}). " +
               "The root snapshot was rejected; the live combat state stays untouched.")
        => CollectionName = collectionName;

    public string CollectionName { get; }
}

/// <summary>
/// 读取实机 List 的稳定快照，并在根捕获窗口前后核对版本。实机状态只在主线程捕获；
/// 窗口内的任何写入都必须变成具名拒绝，而不是静默的不一致根或被枚举器中断的搜索初始化。
/// </summary>
internal static class LiveCollectionGuard
{
    private static readonly ConcurrentDictionary<Type, FieldInfo> VersionFields = new();

    /// <summary>仅无人测试使用：窗口打开后注入一次实机集合写入，用来取得确定性的拒绝证据。</summary>
    internal static Action? CaptureWindowProbeForTesting { get; set; }

    /// <summary>拷贝实机列表；拷贝期间版本变化即抛出具名拒绝。</summary>
    public static T[] SnapshotStable<T>(List<T> list, string collectionName)
    {
        int beforeVersion = ReadVersion(list);
        int beforeCount = list.Count;
        T[] snapshot = list.ToArray();
        int afterVersion = ReadVersion(list);
        if (beforeVersion != afterVersion || beforeCount != list.Count)
        {
            throw new LiveCollectionModifiedException(
                collectionName,
                $"count {beforeCount}->{list.Count}, version {beforeVersion}->{afterVersion}, phase=snapshot");
        }
        return snapshot;
    }

    /// <summary>登记需要在根捕获窗口内保持不动的实机列表。</summary>
    public static Window BeginWindow(params (string Name, object List)[] lists) => new(lists);

    private static int ReadVersion(object list)
    {
        FieldInfo versionField = VersionFields.GetOrAdd(list.GetType(), static type =>
            type.GetField("_version", BindingFlags.Instance | BindingFlags.NonPublic)
                ?? throw new MissingFieldException(type.FullName, "_version"));
        return (int)versionField.GetValue(list)!;
    }

    /// <summary>根捕获窗口：窗口内被写过的实机集合会让整个根捕获被拒绝。</summary>
    internal sealed class Window
    {
        private readonly (string Name, object List, int Count, int Version)[] _entries;

        internal Window((string Name, object List)[] lists)
        {
            _entries = new (string, object, int, int)[lists.Length];
            for (int index = 0; index < lists.Length; index++)
            {
                (string name, object list) = lists[index];
                _entries[index] = (name, list, CountOf(list), ReadVersion(list));
            }
            CaptureWindowProbeForTesting?.Invoke();
        }

        /// <summary>核对窗口内登记的实机集合；被写过即抛出具名拒绝。</summary>
        public void Verify()
        {
            foreach ((string name, object list, int count, int version) in _entries)
            {
                int currentCount = CountOf(list);
                int currentVersion = ReadVersion(list);
                if (currentCount != count || currentVersion != version)
                {
                    throw new LiveCollectionModifiedException(
                        name,
                        $"count {count}->{currentCount}, version {version}->{currentVersion}, phase=window");
                }
            }
        }

        private static int CountOf(object list)
            => ((System.Collections.ICollection)list).Count;
    }
}
