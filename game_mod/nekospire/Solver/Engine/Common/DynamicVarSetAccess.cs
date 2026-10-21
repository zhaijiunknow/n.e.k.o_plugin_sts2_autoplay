using System.Linq.Expressions;
using System.Reflection;
using MegaCrit.Sts2.Core.Localization.DynamicVars;

namespace CombatSolver;

/// <summary>
/// DynamicVarSet 内部字典的跨游戏构建访问桥。当前构建的 _vars 字段经 Krafs.Publicizer
/// 公开，但部分运行环境（例如玩家安装的 CrossVersionCompat）会把模组里的直访字段指令
/// 改写为抛错 shim。这里改为一次性解析字段并缓存读取委托：正常构建保持零装箱读取，
/// 字段缺失时回退 DynamicVarSet 的公开枚举，模组不再直接引用该字段。
/// </summary>
internal static class DynamicVarSetAccess
{
    private static readonly Func<DynamicVarSet, Dictionary<string, DynamicVar>?>? FieldReader =
        BuildFieldReader();

    private static volatile bool s_forcePublicFallback;

    /// <summary>测试开关：强制走公开枚举回退路径，用于验证两条路径语义一致。</summary>
    internal static bool ForcePublicFallback
    {
        get => s_forcePublicFallback;
        set => s_forcePublicFallback = value;
    }

    internal static bool HasFieldBridge => FieldReader != null;

    private static Func<DynamicVarSet, Dictionary<string, DynamicVar>?>? BuildFieldReader()
    {
        FieldInfo? field = typeof(DynamicVarSet).GetField(
            "_vars", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        if (field == null || field.FieldType != typeof(Dictionary<string, DynamicVar>))
            return null;
        ParameterExpression set = Expression.Parameter(typeof(DynamicVarSet), "set");
        return Expression.Lambda<Func<DynamicVarSet, Dictionary<string, DynamicVar>?>>(
            Expression.Field(set, field), set).Compile();
    }

    public static Dictionary<string, DynamicVar>? TryGetVars(DynamicVarSet set)
        => s_forcePublicFallback ? null : FieldReader?.Invoke(set);

    public static Dictionary<string, DynamicVar> RequireVars(DynamicVarSet set)
        => TryGetVars(set) ?? throw new InvalidOperationException(
            "当前游戏构建缺少 DynamicVarSet._vars，且公开回退不支持该写入操作。");

    public static IEnumerable<KeyValuePair<string, DynamicVar>> Entries(DynamicVarSet set)
        => TryGetVars(set) is { } vars ? vars : set;

    public static IEnumerable<string> Keys(DynamicVarSet set)
        => TryGetVars(set) is { } vars ? vars.Keys : set.Keys;

    public static IEnumerable<DynamicVar> Values(DynamicVarSet set)
        => TryGetVars(set) is { } vars ? vars.Values : set.Values;

    public readonly struct EntryEnumerable
    {
        private readonly DynamicVarSet _set;

        public EntryEnumerable(DynamicVarSet set) => _set = set;

        public EntryEnumerator GetEnumerator() => new(_set);
    }

    public struct EntryEnumerator : IDisposable
    {
        private readonly bool _usesField;
        private Dictionary<string, DynamicVar>.Enumerator _dictionary;
        private IEnumerator<KeyValuePair<string, DynamicVar>>? _fallback;

        internal EntryEnumerator(DynamicVarSet set)
        {
            if (TryGetVars(set) is { } vars)
            {
                _dictionary = vars.GetEnumerator();
                _fallback = null;
                _usesField = true;
            }
            else
            {
                _dictionary = default;
                _fallback = set.GetEnumerator();
                _usesField = false;
            }
        }

        public KeyValuePair<string, DynamicVar> Current
            => _usesField ? _dictionary.Current : _fallback!.Current;

        public bool MoveNext()
            => _usesField ? _dictionary.MoveNext() : _fallback!.MoveNext();

        public void Dispose()
        {
            if (_usesField)
                _dictionary.Dispose();
            else
                _fallback?.Dispose();
        }
    }
}
