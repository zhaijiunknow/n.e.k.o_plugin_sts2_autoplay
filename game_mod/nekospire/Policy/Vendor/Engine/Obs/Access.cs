using System.Reflection;
using System.Reflection.Emit;

namespace Sts2Headless;

// 反射读取的编译版（2026-11-16，引擎提速）：观测编码按名字读游戏的私有字段、属性（能力的内部数据、能力与遗物的“本回合 / 本场已触发”、
// 战斗历史的轮次……），原来每帧几百次 FieldInfo / PropertyInfo.GetValue。这里第一次用到某个成员时编译一个读取委托（同样把值装箱成 object），
// 之后直接调用。读到的值与 GetValue 相同；出错时抛的异常类型不同（GetValue 包一层 TargetException 等），调用处都是整段 catch，结果相同。
// 委托表是线程静态的，不在局面复制的根里（ClonePolicy.SkipStatic）。
public sealed partial class Observation
{
    [ThreadStatic] static Dictionary<MemberInfo, Func<object, object?>>? _getters;

    /// <summary>同 f.GetValue(o)。</summary>
    static object? GetF(FieldInfo f, object o) => Getter(f)(o);

    /// <summary>同 p.GetValue(o)（无索引参数的实例属性）。</summary>
    static object? GetP(PropertyInfo p, object o) => Getter(p)(o);

    static Func<object, object?> Getter(MemberInfo mi)
    {
        var g = _getters ??= new Dictionary<MemberInfo, Func<object, object?>>();
        if (g.TryGetValue(mi, out var f)) return f;
        f = Compile(mi);
        g[mi] = f;
        return f;
    }

    static Func<object, object?> Compile(MemberInfo mi)
    {
        var decl = mi.DeclaringType!;
        Type valueType = mi is FieldInfo fi ? fi.FieldType : ((PropertyInfo)mi).PropertyType;
        // 静态成员、值类型上的成员、拿不到读取方法的：照用反射
        if (decl.IsValueType || (mi is FieldInfo f0 && f0.IsStatic) || (mi is PropertyInfo p0 && (p0.GetMethod == null || p0.GetMethod.IsStatic || p0.GetIndexParameters().Length > 0)))
            return mi is FieldInfo ff ? ff.GetValue : ((PropertyInfo)mi).GetValue;
        var dm = new DynamicMethod("obs_get_" + mi.Name, typeof(object), new[] { typeof(object) }, typeof(Observation).Module, skipVisibility: true);
        var il = dm.GetILGenerator();
        il.Emit(OpCodes.Ldarg_0);
        il.Emit(OpCodes.Castclass, decl);
        if (mi is FieldInfo fld) il.Emit(OpCodes.Ldfld, fld);
        else il.Emit(OpCodes.Callvirt, ((PropertyInfo)mi).GetMethod!);
        if (valueType.IsValueType) il.Emit(OpCodes.Box, valueType);
        il.Emit(OpCodes.Ret);
        return (Func<object, object?>)dm.CreateDelegate(typeof(Func<object, object?>));
    }
}
