using System.Collections.Concurrent;
using HarmonyLib;
using MegaCrit.Sts2.Core.Models;
using STS2RitsuLib.Patching.Models;

namespace CombatSolver;

/// <summary>
/// 原生 <c>ModelDb.GetId(Type)</c> 每次调用都跑三次正则，图鉴/纪元池枚举
/// （<c>Epoch.get_Cards</c> → <c>ModelDb.Card</c>）在搜索热路径里对每个模型重复触发它。
/// 这里只缓存 <c>Type → ModelId</c> 的值映射，不缓存 <c>ModelDb</c> 的内容字典或任何模型实例。
/// <para>
/// 这个映射只对原版类型从一开始就固定。内容库会在注册期改写模组类型的结果：RitsuLib 在
/// <c>ModelDb.GetEntry</c> 上用后缀补丁返回 <c>&lt;MODID&gt;_&lt;类别&gt;_&lt;类名&gt;</c>，而它的冲突检查会在分配前缀之前
/// 先按原版默认条目调用一次 <c>GetId</c>。若此时就缓存，排在本模组之后加载的内容模组会永久停在无前缀
/// ID 上（与原版同名时启动失败，否则本地化键全部对不上）。所以模组类型要等模型注册表初始化完成、
/// 注册已冻结之后才进缓存；之后 <c>ModelDb</c> 的内容字典已按 ID 建好，ID 不会再变。
/// </para>
/// </summary>
internal sealed class ModelDbGetIdCachePatch : IPatchMethod
{
    private static readonly ConcurrentDictionary<Type, ModelId> Cache = new();
    private static volatile bool _modelRegistryInitialized;

    public static string PatchId => "combat_solver_model_db_get_id_cache";

    public static string Description => "缓存 ModelDb.GetId(Type) 的类型→ModelId 映射（模组类型在注册表初始化后）";

    public static ModPatchTarget[] GetTargets() =>
    [
        new(typeof(ModelDb), "GetId", [typeof(Type)]),
    ];

    /// <summary>命中数只用于诊断。</summary>
    public static int CachedEntryCount => Cache.Count;

    /// <summary>
    /// 模型注册表初始化完成后调用（游戏内由 RitsuLib 的 <c>ModelRegistryInitializedEvent</c> 触发）。
    /// 没有收到这个信号时模组类型一直走原方法，只损失缓存收益，不会固化错误 ID。
    /// </summary>
    internal static void MarkModelRegistryInitialized() => _modelRegistryInitialized = true;

    internal static bool IsCacheable(Type type)
        => type.Assembly == typeof(ModelDb).Assembly || _modelRegistryInitialized;

    [HarmonyPriority(Priority.First)]
    public static bool Prefix(Type type, ref ModelId __result)
    {
        // null 参数保持原生失败路径，不把 NullReferenceException 换成字典异常。
        if (type is null)
            return true;
        if (Cache.TryGetValue(type, out ModelId? cached))
        {
            __result = cached;
            return false;
        }
        return true;
    }

    public static void Postfix(Type type, ModelId __result)
    {
        // 原方法抛异常时 Postfix 不会运行，失败不会被固化。
        if (type is not null && __result is not null && IsCacheable(type))
            Cache.TryAdd(type, __result);
    }

    internal static bool ModelRegistryInitializedForTesting => _modelRegistryInitialized;

    // 模组类型在初始化前命中原方法、初始化后才入缓存；原版类型始终可缓存。
    internal static bool ExerciseRegistrationGateForTesting(Type modType)
    {
        bool initialized = _modelRegistryInitialized;
        try
        {
            _modelRegistryInitialized = false;
            bool vanillaBefore = IsCacheable(typeof(ModelDb));
            bool modBefore = IsCacheable(modType);
            _modelRegistryInitialized = true;
            return vanillaBefore && !modBefore && IsCacheable(modType);
        }
        finally
        {
            _modelRegistryInitialized = initialized;
        }
    }
}
