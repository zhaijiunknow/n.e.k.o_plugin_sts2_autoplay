using System.Reflection;
using HarmonyLib;

namespace Sts2Headless;

/// <summary>
/// 游戏版本兼容层（引擎模块与游戏更新适配.md 第三节）：引擎里所有按名字去找游戏成员的地方——私有字段、属性、方法的反射，
/// Harmony 补丁的目标——都经这里，每一处连同用途登记下来。游戏更新改名、删掉了哪个成员，`selfcheck` 一次列全（GameCompat.Report），
/// 不用等到某个局面碰巧走到那段代码、静默地编出 0 才发现。
///
/// 行为与直接用 AccessTools / Type.GetXxx 完全相同（返回同一个对象，找不到时同样为 null）；补丁照旧：目标找不到时抛异常、引擎启动失败。
/// 只有自检模式（SelfCheck = true，`Sts2Headless.dll selfcheck`）下补丁找不到目标时记下来跳过，好把后面的问题也查出来。
/// 实机桥接模组也编译这个文件（engine/Directory.Build.targets）。
/// </summary>
public static class GameCompat
{
    /// <summary>一处按名字找的游戏成员：种类（字段、属性、方法、补丁……）、类型、成员名、用途、找到没有。</summary>
    public sealed record Need(string Kind, string Type, string Member, string Use, bool Found, bool Optional = false);

    static readonly List<Need> _needs = new();
    static readonly object _lock = new();
    /// <summary>自检模式：补丁的目标找不到时不抛异常，只登记（Program 的 selfcheck 在 Bootstrap 之前打开）。</summary>
    public static bool SelfCheck;

    public static IReadOnlyList<Need> Needs { get { lock (_lock) return _needs.ToList(); } }

    static T? Note<T>(string kind, Type t, string member, string use, T? found) where T : class
    {
        lock (_lock) _needs.Add(new Need(kind, t.FullName ?? t.Name, member, use, found != null));
        return found;
    }

    public static FieldInfo? Field(Type t, string name, string use) => Note("字段", t, name, use, AccessTools.Field(t, name));

    /// <summary>找不到也有退路的字段（如界面单例的后备字段，没有时改给 getter 打补丁）：登记，但自检不算问题。</summary>
    public static FieldInfo? OptionalField(Type t, string name, string use)
    {
        var f = AccessTools.Field(t, name);
        lock (_lock) _needs.Add(new Need("字段", t.FullName ?? t.Name, name, use, f != null, Optional: true));
        return f;
    }

    public static FieldInfo? Field(Type t, string name, BindingFlags flags, string use) => Note("字段", t, name, use, t.GetField(name, flags));

    public static PropertyInfo? Property(Type t, string name, string use) => Note("属性", t, name, use, AccessTools.Property(t, name));

    public static PropertyInfo? Property(Type t, string name, BindingFlags flags, string use) => Note("属性", t, name, use, t.GetProperty(name, flags));

    public static MethodInfo? Getter(Type t, string name, string use) => Note("属性", t, name, use, AccessTools.PropertyGetter(t, name));

    public static MethodInfo? Method(Type t, string name, string use, Type[]? args = null) => Note("方法", t, name, use, AccessTools.Method(t, name, args));

    public static MethodInfo? Method(Type t, string name, BindingFlags flags, string use) => Note("方法", t, name, use, t.GetMethod(name, flags));

    public static MethodInfo? Method(Type t, string name, Type[] args, string use, bool exact) =>
        Note("方法", t, name + "(" + string.Join(", ", args.Select(a => a.Name)) + ")", use, exact ? t.GetMethod(name, args) : AccessTools.Method(t, name, args));

    /// <summary>
    /// 装一个 Harmony 补丁。目标为 null（游戏里找不到）时：正常模式抛 MissingMethodException（引擎启动失败，与原来一样），自检模式登记后跳过。
    /// Harmony 自己报错（补丁方法的参数与新版目标对不上等）时同样处理。
    /// </summary>
    public static void Patch(Harmony h, MethodBase? target, string what, HarmonyMethod? prefix = null, HarmonyMethod? postfix = null, HarmonyMethod? finalizer = null)
    {
        if (target == null)
        {
            lock (_lock) _needs.Add(new Need("补丁", "", what, "补丁目标找不到", false));
            if (SelfCheck) return;
            throw new MissingMethodException(what);
        }
        try { h.Patch(target, prefix: prefix, postfix: postfix, finalizer: finalizer); }
        catch (Exception e) when (SelfCheck)
        {
            lock (_lock) _needs.Add(new Need("补丁", target.DeclaringType?.FullName ?? "", target.Name, "Harmony 装不上：" + e.GetBaseException().Message, false));
            return;
        }
        lock (_lock) _needs.Add(new Need("补丁", target.DeclaringType?.FullName ?? "", target.Name, what, true));
    }

    /// <summary>补丁里引擎自己的方法（前缀、后缀等，私有静态）：找不到是引擎自身的错，直接抛。</summary>
    public static HarmonyMethod Own(Type t, string name) =>
        new(t.GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Public) ?? throw new MissingMethodException(t.FullName, name));

    /// <summary>按类名找游戏的卡牌（名字写在内容表里、不是 typeof 的那些，如 OrbChannels）：登记，自检时列出不存在的名字。</summary>
    public static void CardNames(string use, params string[] names)
    {
        var have = MegaCrit.Sts2.Core.Models.ModelDb.AllCards.Select(c => c.GetType().Name).ToHashSet();
        foreach (var n in names) lock (_lock) _needs.Add(new Need("卡牌类名", "MegaCrit.Sts2.Core.Models.Cards", n, use, have.Contains(n)));
    }

    /// <summary>自检报告：找不到的放前面。</summary>
    public static IEnumerable<string> Report(bool all = false)
    {
        var ns = Needs.GroupBy(n => (n.Kind, n.Type, n.Member)).Select(g => g.First() with { Found = g.All(x => x.Found) }).ToList();
        foreach (var n in ns.Where(n => !n.Found && !n.Optional)) yield return $"缺  {n.Kind} {n.Type}.{n.Member}（{n.Use}）";
        if (all) foreach (var n in ns.Where(n => !n.Found && n.Optional)) yield return $"无  {n.Kind} {n.Type}.{n.Member}（{n.Use}，可选）";
        if (all) foreach (var n in ns.Where(n => n.Found)) yield return $"有  {n.Kind} {n.Type}.{n.Member}（{n.Use}）";
    }
}
