using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Models;

namespace Sts2Headless;

public sealed partial class Observation
{
    /// <summary>
    /// v0.114.0：DamageVar / CalculatedDamageVar / OstyDamageVar 的有目标预览在 Hook.ModifyDamage 之后多了一步
    /// CreatureCmd.GetDamageResultsPreview（过格挡与掉血钩子：硬化外壳、难杀、滑溜、发条靴……再求和 TotalDamage + OverkillDamage）。
    /// 观测里的单段伤害一直是“掉血钩子之前”的数（FoeHpLoss、SeqHpLoss、背包、滑溜修正都按它再过格挡与掉血钩子），
    /// 编码的预览期间（PreviewCore）让这一步原样返回伤害，口径同 v0.113.1。只在 RawDamagePreview 为真时生效，界面与游戏逻辑不受影响。
    /// </summary>
    [ThreadStatic] internal static bool RawDamagePreview;

    public static class RawPreview
    {
        public static void Install(HarmonyLib.Harmony h)
        {
            var m = GameCompat.Method(typeof(MegaCrit.Sts2.Core.Commands.CreatureCmd), "GetDamageResultsPreview", "观测的有目标伤害预览按掉血钩子之前的口径（v0.114.0）");
            GameCompat.Patch(h, m, "CreatureCmd.GetDamageResultsPreview", prefix: GameCompat.Own(typeof(RawPreview), nameof(Pre)));
        }

        static bool Pre(Creature target, decimal damage, MegaCrit.Sts2.Core.ValueProps.ValueProp props, ref List<DamageResult> __result)
        {
            if (!RawDamagePreview) return true;
            __result = new List<DamageResult> { new DamageResult(target, props) { UnblockedDamage = (int)damage } };
            return false;
        }
    }
}
