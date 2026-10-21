using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Modding;

namespace CombatSolver;

internal static class PowerDynamicVarWarmup
{
    private static bool _canonicalPowersMaterialized;
    private static readonly object AdaptedLock = new();
    private static readonly List<Type> AdaptedPowerTypes = [];
    private static int _adaptedMaterializedCount;

    /// <summary>
    /// 第三方适配层声明：这个第三方 Power 的规范实例可以在主线程上提前物化显示变量。
    /// </summary>
    /// <remarks>
    /// <para>
    /// 预热默认只访问原版来源：第三方的 <c>CanonicalVars</c> 可能依赖附着后的 Owner，规范实例上一读就抛。
    /// 但搜索里新挂一个 Power 要克隆它的规范实例，克隆会读 <c>DynamicVars</c>，而后台禁止惰性创建
    /// （<see cref="PowerDynamicVarMaterializationGuardPatch" />）。所以一个第三方 Power 只要可能在搜索中
    /// 第一次被施加，就必须有人担保它的规范实例能在主线程物化——这件事只有适配层知道。
    /// </para>
    /// <para>
    /// 登记过的类型在下一次 <see cref="EnsureMaterialized" /> 时物化，之后每局只做一次。物化失败照常抛出，
    /// 根捕获失败比在搜索里撞守卫更早、更明确。登记可以在任意时刻进行，只要早于需要它的那次建根。
    /// </para>
    /// </remarks>
    public static void RegisterAdaptedCanonicalPower(Type powerType)
    {
        ArgumentNullException.ThrowIfNull(powerType);
        if (!typeof(PowerModel).IsAssignableFrom(powerType) || powerType.IsAbstract)
            throw new ArgumentException($"{powerType.FullName} is not a concrete PowerModel.", nameof(powerType));
        // 适配层在 mod 初始化阶段登记，那时 AssemblyInfo.ModForType 还不能用，直接比程序集。
        if (powerType.Assembly == typeof(PowerModel).Assembly)
            throw new ArgumentException($"{powerType.FullName} 是原版 Power，已在默认预热范围内。", nameof(powerType));
        lock (AdaptedLock)
        {
            if (AdaptedPowerTypes.Contains(powerType))
                throw new ArgumentException($"{powerType.FullName} 已经登记过。", nameof(powerType));
            AdaptedPowerTypes.Add(powerType);
        }
    }

    /// <inheritdoc cref="RegisterAdaptedCanonicalPower(Type)" />
    public static void RegisterAdaptedCanonicalPower<TPower>() where TPower : PowerModel
        => RegisterAdaptedCanonicalPower(typeof(TPower));

    public static void EnsureMaterialized(CombatState state)
    {
        if (!NGame.IsMainThread())
            throw new InvalidOperationException("Power dynamic variables must be materialized on the main thread.");

        if (!_canonicalPowersMaterialized)
        {
            EnsureCanonicalMaterialized(ModelDb.AllPowers);
            _canonicalPowersMaterialized = true;
        }
        EnsureAdaptedCanonicalMaterialized();

        foreach (PowerModel power in state.Creatures.SelectMany(creature => creature.Powers))
            _ = power.DynamicVars;
    }

    internal static void EnsureCanonicalMaterialized(IEnumerable<PowerModel> powers)
    {
        foreach (PowerModel power in powers)
        {
            _ = AssemblyInfo.ModForType(power.GetType(), out bool isBaseGame);
            if (isBaseGame) _ = power.DynamicVars;
        }
    }

    private static void EnsureAdaptedCanonicalMaterialized()
    {
        Type[] pending;
        lock (AdaptedLock)
        {
            if (_adaptedMaterializedCount == AdaptedPowerTypes.Count)
                return;
            pending = AdaptedPowerTypes.Skip(_adaptedMaterializedCount).ToArray();
        }
        foreach (Type type in pending)
        {
            PowerModel canonical = ModelDb.AllPowers.FirstOrDefault(power => power.GetType() == type)
                ?? throw new InvalidOperationException(
                    $"登记预热的第三方 Power {type.FullName} 不在 ModelDb.AllPowers 里。");
            _ = canonical.DynamicVars;
        }
        lock (AdaptedLock)
            _adaptedMaterializedCount += pending.Length;
    }
}
