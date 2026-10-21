using System.Collections.Concurrent;
using System.Reflection;

namespace CombatSolver;

// ---------------------------------------------------------------------------------------------
// T45 runtime private-access layer.
//
// Why this exists
// ---------------
// Upstream CombatSolver compiles against a Krafs.Publicizer-rewritten copy of sts2.dll, so its
// source touches `internal`/`private` members of the game assembly directly. That is a *compile
// time only* trick: the CLR loads the real, unmodified sts2.dll at runtime and enforces
// accessibility, so a direct `ldfld`/`call` on such a member dies with
//   "Attempt by method '...' to access field 'MegaCrit...CardEnergyCost._localModifiers' failed."
// and the mod then permanently reports in_combat=false. (Measured on the real client, t44.)
//
// Our vendored build therefore does not reference Krafs.Publicizer at all and instead reaches every
// non-public game member through this file: System.Reflection metadata lookups resolved once per
// (runtime type, member) pair, wrapped in strongly-typed accessors generated from the real
// sts2.dll metadata (T44PrivateAccess.Generated.cs).
//
// Contract
// --------
//  * One cached accessor plan per (runtime type, member); no reflection lookup per call.
//  * A member that exists only as a property (or only as a field) is never reported as missing:
//    field / property / backing-field candidates are resolved together before warning.
//  * A genuinely missing member logs exactly one `warn:` line ever and yields a safe default, so a
//    game patch that renames a field degrades one prediction detail instead of killing the solver.
//  * Writes prefer a real setter, then a `<Name>k__BackingField` declared anywhere in the
//    hierarchy, so internal-set / get-only-auto properties are still writable.
// ---------------------------------------------------------------------------------------------
internal static partial class T44PrivateAccess
{
    private const BindingFlags InstanceFlags =
        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

    private const BindingFlags StaticFlags =
        BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;

    private sealed class Accessor
    {
        public FieldInfo? Field;
        public MethodInfo? Getter;
        public MethodInfo? Setter;
        public FieldInfo? BackingField;
        public bool Found => Field != null || Getter != null || Setter != null || BackingField != null;
        public bool Writable => Setter != null || Field != null || BackingField != null;
    }

    private static readonly ConcurrentDictionary<(Type Type, string Member), Accessor> Accessors = new();
    private static readonly ConcurrentDictionary<(Type Type, string Member, int Arity), MethodInfo?> Methods = new();
    private static readonly ConcurrentDictionary<string, byte> Warned = new(StringComparer.Ordinal);

    private static void WarnOnce(string key, string message)
    {
        if (Warned.TryAdd(key, 0))
            Entry.Logger.Warn("[T44PrivateAccess] " + message);
    }

    /// <summary>Resolves every way of reaching <paramref name="name"/> on <paramref name="type"/>,
    /// walking the WHOLE inheritance chain. The whole chain matters: PowerModel re-declares
    /// <c>IsMutable</c> as a get-only property, so stopping at the first match on a derived type
    /// silently loses AbstractModel's private setter (real bug caught on the live client).</summary>
    private static Accessor Resolve(Type? type, string name)
        => type == null
            ? new Accessor()
            : Accessors.GetOrAdd((type, name), static key =>
            {
                var plan = new Accessor();
                string backingName = "<" + key.Member + ">k__BackingField";
                for (Type? t = key.Type; t != null; t = t.BaseType)
                {
                    plan.Field ??= t.GetField(key.Member, InstanceFlags | StaticFlags);
                    plan.BackingField ??= t.GetField(backingName, InstanceFlags | StaticFlags);
                    foreach (PropertyInfo candidate in t.GetProperties(InstanceFlags | StaticFlags))
                    {
                        if (!string.Equals(candidate.Name, key.Member, StringComparison.Ordinal))
                            continue;
                        plan.Getter ??= candidate.GetGetMethod(true);
                        // A settable declaration anywhere in the chain wins over a get-only shadow.
                        if (plan.Setter == null && candidate.GetSetMethod(true) is { } setter)
                            plan.Setter = setter;
                    }
                }
                if (!plan.Found)
                    WarnOnce("x:" + key.Type.FullName + ":" + key.Member,
                        $"sts2 member {key.Type.FullName}.{key.Member} not found; using default.");
                return plan;
            });

    private static MethodInfo? ResolveMethod(Type? type, string name, int arity)
        => type == null
            ? null
            : Methods.GetOrAdd((type, name, arity), static key =>
            {
                for (Type? t = key.Type; t != null; t = t.BaseType)
                {
                    foreach (MethodInfo method in t.GetMethods(InstanceFlags | StaticFlags))
                    {
                        if (string.Equals(method.Name, key.Member, StringComparison.Ordinal)
                            && method.GetGenericArguments().Length == key.Arity)
                            return method;
                    }
                }
                WarnOnce("m:" + key.Type.FullName + ":" + key.Member + "`" + key.Arity,
                    $"sts2 method {key.Type.FullName}.{key.Member} not found; using default.");
                return null;
            });

    // ---- reads ----------------------------------------------------------------------------------

    internal static object? Read(object? target, string name)
        => target == null ? null : ReadOn(target.GetType(), target, name);

    internal static object? ReadStatic(Type? type, string name)
        => type == null ? null : ReadOn(type, null, name);

    private static object? ReadOn(Type type, object? target, string name)
    {
        Accessor plan = Resolve(type, name);
        if (plan.Field != null)
            return plan.Field.GetValue(plan.Field.IsStatic ? null : target);
        if (plan.Getter != null)
            return plan.Getter.Invoke(plan.Getter.IsStatic ? null : target, null);
        if (plan.BackingField != null)
            return plan.BackingField.GetValue(plan.BackingField.IsStatic ? null : target);
        return null;
    }

    internal static T Read<T>(object? target, string name)
    {
        object? raw = Read(target, name);
        return raw is T typed ? typed : default!;
    }

    internal static T ReadStatic<T>(Type? type, string name)
    {
        object? raw = ReadStatic(type, name);
        return raw is T typed ? typed : default!;
    }

    // ---- writes ---------------------------------------------------------------------------------

    internal static void Write(object? target, string name, object? value)
    {
        if (target != null)
            WriteOn(target.GetType(), target, name, value);
    }

    internal static void WriteStatic(Type? type, string name, object? value)
    {
        if (type != null)
            WriteOn(type, null, name, value);
    }

    private static void WriteOn(Type type, object? target, string name, object? value)
    {
        Accessor plan = Resolve(type, name);
        // Property setter first: that is the semantically correct entry point and it is what upstream's
        // publicized build would emit. Field/backing-field are the fallbacks for get-only properties.
        if (plan.Setter != null)
        {
            plan.Setter.Invoke(plan.Setter.IsStatic ? null : target, [value]);
            return;
        }
        FieldInfo? slot = plan.Field ?? plan.BackingField;
        if (slot != null)
        {
            slot.SetValue(slot.IsStatic ? null : target, value);
            return;
        }
        WarnOnce("w:" + type.FullName + ":" + name,
            $"sts2 member {type.FullName}.{name} is not writable; write dropped.");
    }

    // ---- invokes --------------------------------------------------------------------------------

    internal static object? Invoke(object? target, string name, params object?[] args)
    {
        if (target == null)
            return null;
        // Arity is either 0 or the supplied argument count; both are probed.
        MethodInfo? method = ResolveMethod(target.GetType(), name, 0)
            ?? ResolveMethod(target.GetType(), name, args.Length);
        return method == null ? null : method.Invoke(method.IsStatic ? null : target, args);
    }

    internal static object? InvokeStatic(Type? type, string name, params object?[] args)
    {
        if (type == null)
            return null;
        MethodInfo? method = ResolveMethod(type, name, 0) ?? ResolveMethod(type, name, args.Length);
        return method == null ? null : method.Invoke(method.IsStatic ? null : type, args);
    }

    internal static object? InvokeGeneric(object? target, string name, Type[] typeArguments, params object?[] args)
    {
        if (target == null)
            return null;
        MethodInfo? method = ResolveMethod(target.GetType(), name, typeArguments.Length);
        if (method == null)
            return null;
        if (method.IsGenericMethodDefinition)
            method = method.MakeGenericMethod(typeArguments);
        return method.Invoke(method.IsStatic ? null : target, args);
    }

    internal static T? InvokeGeneric<T>(object? target, string name, Type[] typeArguments, params object?[] args)
    {
        object? raw = InvokeGeneric(target, name, typeArguments, args);
        return raw is T typed ? typed : default;
    }

    /// <summary>Static call whose result is used as a bool (e.g. RunState.Contains, internal).</summary>
    internal static bool InvokeStaticBool(Type? type, string name, params object?[] args)
        => InvokeStatic(type, name, args) is bool value && value;

    /// <summary>Compares a boxed enum value against a member name. Needed where the enum type itself
    /// is internal (e.g. AttackCommand.SourceType) and therefore cannot be named in our source.</summary>
    internal static bool EnumMemberIs(object? boxedEnum, string name)
        => boxedEnum != null && string.Equals(boxedEnum.ToString(), name, StringComparison.Ordinal);

    // ---- curated strongly-typed wrappers --------------------------------------------------------
    // These are the members the t44 real-client failure report called out by name.
    // Everything else is covered mechanically by T44PrivateAccess.Generated.cs.

    /// <summary>CardEnergyCost._localModifiers (List&lt;LocalCostModifier&gt;, internal).</summary>
    internal static List<MegaCrit.Sts2.Core.Entities.Cards.LocalCostModifier> LocalModifiers(
        MegaCrit.Sts2.Core.Entities.Cards.CardEnergyCost cost)
        => Read<List<MegaCrit.Sts2.Core.Entities.Cards.LocalCostModifier>>(cost, "_localModifiers") ?? [];

    /// <summary>PlayerCombatState._player (Player, internal).</summary>
    internal static MegaCrit.Sts2.Core.Entities.Players.Player? Player(
        MegaCrit.Sts2.Core.Entities.Players.PlayerCombatState state)
        => Read<MegaCrit.Sts2.Core.Entities.Players.Player>(state, "_player");

    /// <summary>CardModel._temporaryStarCosts (List&lt;TemporaryCardCost&gt;, internal).</summary>
    internal static List<MegaCrit.Sts2.Core.Entities.Cards.TemporaryCardCost> TemporaryStarCosts(
        MegaCrit.Sts2.Core.Models.CardModel card)
        => Read<List<MegaCrit.Sts2.Core.Entities.Cards.TemporaryCardCost>>(card, "_temporaryStarCosts") ?? [];

    /// <summary>CombatManager._turnState (CombatTurnState — an internal CLR type, hence object).
    /// Use <see cref="TurnStateIsInProgress"/> / <see cref="TurnStateCombatState"/>.</summary>
    internal static object? TurnState(MegaCrit.Sts2.Core.Combat.CombatManager manager)
        => Read(manager, "_turnState");

    internal static bool TurnStateIsInProgress(object? turnState)
        => Read<bool>(turnState, "IsInProgress");

    internal static MegaCrit.Sts2.Core.Combat.CombatState? TurnStateCombatState(object? turnState)
        => Read<MegaCrit.Sts2.Core.Combat.CombatState>(turnState, "State");

    /// <summary>AbstractModel.IsMutable (public getter, internal setter).</summary>
    internal static void SetMutable(MegaCrit.Sts2.Core.Models.AbstractModel model, bool value)
        => Write(model, "IsMutable", value);

    /// <summary>PowerModel._internalData: the per-power private payload object.</summary>
    internal static object? InternalData(MegaCrit.Sts2.Core.Models.PowerModel power)
        => Read(power, "_internalData");
}
