// Reflection shim for the vendored CombatSolver build. CombatSolver's source does a lot of DIRECT access
// to game private members (`card._cloneOf`, `power._amount`, `cost._unused`, ...). The game's mod-load
// runtime does NOT honor Krafs.Publicizer's `IgnoresAccessChecksTo("sts2")` for a plain-SDK plugin mod
// (verified: a direct `ldfld` to a private field still throws FieldAccessException), and RitsuLib does not
// runtime-publicize. So every such access is routed through reflection (FieldInfo/PropertyInfo
// GetValue/SetValue), which is legal in .NET Core across any load context and reads the exact same value.
// MemberInfo is cached per (declaring type, member). Semantics are unchanged.
using System;
using System.Collections.Concurrent;
using System.Reflection;

namespace CombatSolver.Engine.Common;

internal static class GameRef
{
    private const BindingFlags InstanceFlags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    private const BindingFlags StaticFlags = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;

    private static readonly ConcurrentDictionary<(Type Type, string Member, bool Static), MemberInfo> _cache = new();

    // ---- instance fields / properties ----------------------------------------
    public static object Get(object target, string memberName)
    {
        MemberInfo member = Find(target.GetType(), memberName, staticMember: false);
        return member switch
        {
            FieldInfo field => field.GetValue(target)!,
            PropertyInfo property => property.GetValue(target)!,
            _ => throw new MissingMemberException(target.GetType().FullName, memberName),
        };
    }

    public static T Get<T>(object target, string memberName)
    {
        object value = Get(target, memberName);
        if (value is T typed)
            return typed;
        // The game's private members are decimal-valued in places; vendored callers often read them via a
        // narrower requested type (e.g. int for a decimal card value). Convert rather than throw, so a type
        // mismatch never crashes the search. Integer-valued decimals convert exactly.
        if (value == null)
            return default!;
        return (T)Convert.ChangeType(value, typeof(T));
    }

    public static void Set(object target, string memberName, object? value)
    {
        MemberInfo member = Find(target.GetType(), memberName, staticMember: false);
        switch (member)
        {
            case FieldInfo field:
                field.SetValue(target, value);
                break;
            case PropertyInfo property:
                if (!property.CanWrite)
                {
                    // Some read-only game properties are set via a backing field (e.g. CurrentTarget ->
                    // _currentTarget). Fall back to that so the write still lands, matching the game's
                    // own internal write path.
                    if (TrySetBackingField(property, target, value))
                        break;
                    throw new InvalidOperationException(
                        $"GameRef.Set: property '{property.Name}' on {property.DeclaringType?.FullName} " +
                        $"has no write access and no backing field was found on {target.GetType().FullName}.");
                }
                try
                {
                    property.SetValue(target, value);
                }
                catch (Exception ex)
                {
                    throw new InvalidOperationException(
                        $"GameRef.Set failed on property '{property.Name}' of {property.DeclaringType?.FullName}: {ex.Message}", ex);
                }
                break;
            default:
                throw new MissingMemberException(target.GetType().FullName, memberName);
        }
    }

    private static bool TrySetBackingField(PropertyInfo property, object target, object? value)
    {
        // Compiler-generated read-only auto-properties carry a private set_<Name> method even when the
        // property reports get-only via SetValue. Invoke the setter method directly (the same trick
        // CombatSolver's DynamicMethod used for IsMutable). Then fall back to a backing field.
        MethodInfo? setter = (property.DeclaringType ?? target.GetType()).GetMethod("set_" + property.Name, InstanceFlags);
        if (setter != null)
        {
            setter.Invoke(target, new[] { value });
            return true;
        }
        string backingName = "_" + char.ToLowerInvariant(property.Name[0]) + property.Name.Substring(1);
        FieldInfo? backing = target.GetType().GetField(backingName, InstanceFlags);
        if (backing == null)
            return false;
        backing.SetValue(target, value);
        return true;
    }

    public static void Set<T>(object target, string memberName, T value)
        => Set(target, memberName, (object?)value);

    // ---- static fields / properties (accessed as Type._member) ----------------
    public static object GetStatic(Type declaringType, string memberName)
    {
        MemberInfo member = Find(declaringType, memberName, staticMember: true);
        return member switch
        {
            FieldInfo field => field.GetValue(null)!,
            PropertyInfo property => property.GetValue(null)!,
            _ => throw new MissingMemberException(declaringType.FullName, memberName),
        };
    }

    public static T GetStatic<T>(Type declaringType, string memberName)
    {
        object value = GetStatic(declaringType, memberName);
        if (value is T typed)
            return typed;
        if (value == null)
            return default!;
        return (T)Convert.ChangeType(value, typeof(T));
    }

    // ---- method invocation (instance + static, incl. private) ----------------
    public static object? Invoke(object target, string methodName, params object?[] args)
    {
        MemberInfo member = Find(target.GetType(), methodName, staticMember: false);
        if (member is not MethodInfo method)
            throw new MissingMethodException(target.GetType().FullName, methodName);
        return method.Invoke(target, args);
    }

    public static object? InvokeStatic(Type declaringType, string methodName, params object?[] args)
    {
        MemberInfo member = Find(declaringType, methodName, staticMember: true);
        if (member is not MethodInfo method)
            throw new MissingMethodException(declaringType.FullName, methodName);
        return method.Invoke(null, args);
    }

    /// <summary>Invokes a private generic method by reflecting the type argument from a nested type name
    /// (e.g. power.GetInternalData&lt;HellraiserPower.Data&gt;() where Data is a private nested type).</summary>
    public static object? InvokeGeneric(object target, string methodName, string typeArgNestedName, params object?[] args)
    {
        Type type = target.GetType();
        MethodInfo? method = null;
        foreach (MethodInfo m in type.GetMethods(BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public))
        {
            if (m.Name == methodName && m.IsGenericMethodDefinition)
            {
                method = m;
                break;
            }
        }
        if (method == null)
            throw new MissingMethodException(type.FullName, methodName);
        Type? typeArg = type.GetNestedType(typeArgNestedName, BindingFlags.Public | BindingFlags.NonPublic);
        if (typeArg == null)
            throw new MissingMemberException(type.FullName, typeArgNestedName);
        MethodInfo closed = method.MakeGenericMethod(typeArg);
        return closed.Invoke(target, args);
    }

    private static MemberInfo Find(Type type, string memberName, bool staticMember)
    {
        return _cache.GetOrAdd((type, memberName, staticMember), static key =>
        {
            BindingFlags flags = key.Static ? StaticFlags : InstanceFlags;
            // Private base-class members are NOT returned by a derived type's reflection bind, so walk the
            // type hierarchy (Catastrophe -> ... -> CardModel -> AbstractModel) to find private members.
            MemberInfo? found = null;
            for (Type? t = key.Type; t != null && t != typeof(object); t = t.BaseType)
            {
                FieldInfo? field = t.GetField(key.Member, flags);
                if (field != null) { found = field; break; }
                PropertyInfo? property = t.GetProperty(key.Member, flags);
                if (property != null) { found = property; break; }
                MethodInfo? method = t.GetMethod(key.Member, flags);
                if (method != null) { found = method; break; }
            }
            if (found != null)
                return found;
            throw new MissingMemberException(key.Type.FullName, key.Member);
        });
    }
}
