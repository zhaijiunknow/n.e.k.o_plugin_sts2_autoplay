using System.Reflection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.ValueProps;

namespace Sts2Headless;

// 格式 29 各实现包共用的接口（研究报告/2026-11-20_全角色观测缺口调研/R3-格式29规格.md 第四节“第 0 步”，铁甲战士决策记录第 27 条）。
// 由主会话在分包前加好，之后各包只读、不改；这里的函数与字段本身不改变任何一帧（只有各包在 V29 下调用它们时才起作用）。
public sealed partial class Observation
{
    /// <summary>G03：律动残余（BeatingRemnant）本回合已挨的伤害（私有字段）。</summary>
    static readonly FieldInfo? RemnantField = GameCompat.Field(typeof(MegaCrit.Sts2.Core.Models.Relics.BeatingRemnant), "_damageReceivedThisTurn", "律动残余本回合已挨的伤害（格式 29，G03）");

    /// <summary>G03：律动残余还能让玩家这回合再掉多少血（遗物每回合最多掉 MaxHpLoss 点）；没有这件遗物、遗物失效或读不到时为 +∞。</summary>
    static float RemnantLeft(Player me)
    {
        try
        {
            var r = me.Relics.FirstOrDefault(x => x is MegaCrit.Sts2.Core.Models.Relics.BeatingRemnant && RelicLive(x));
            if (r == null || RemnantField == null) return float.PositiveInfinity;
            decimal cap = r.DynamicVars["MaxHpLoss"].BaseValue;
            decimal got = (decimal)RemnantField.GetValue(r)!;
            return (float)Math.Max(0m, cap - got);
        }
        catch { return float.PositiveInfinity; }
    }

    /// <summary>G04：dead 里的敌人死后，场上是否已没有活着的主要敌人、也没有能力阻止战斗结束（即战斗当场结束，爪牙随之离场）。</summary>
    static bool CombatEndsWithout(ICombatState cs, IReadOnlyCollection<Creature>? dead)
        => !cs.Creatures.Any(e => e.IsAlive && e.IsPrimaryEnemy && !(dead?.Contains(e) ?? false))
           && !MegaCrit.Sts2.Core.Hooks.Hook.ShouldStopCombatFromEnding(cs);

    /// <summary>包 2、包 4 共用：这张牌对 t 的单段伤害在“力量多 addStr”时是多少（照 Gains26.cs 的 DmgRecalc：基础值先加 addStr，再过全部钩子）；没有伤害数值为 -1。</summary>
    static float PdWithStr(CardModel c, Creature? t, decimal addStr)
    {
        var cs = c.Owner.Creature.CombatState!;
        decimal n; ValueProp props;
        if (c.DynamicVars.TryGetValue("CalculatedDamage", out var cv) && cv is CalculatedDamageVar cd) { n = cd.Calculate(t); props = cd.Props; }
        else if (c.DynamicVars.TryGetValue("Damage", out var dv) && dv is DamageVar d) { n = d.BaseValue; props = d.Props; }
        else return -1;
        if (c.Enchantment is { } e) { n += e.EnchantDamageAdditive(n, props); n *= e.EnchantDamageMultiplicative(n, props); }
        n += addStr;
        var ls = cs.RunState.IterateHookListeners(cs).ToList();
        var dealer = c.Owner.Creature;
        foreach (var l in ls) n += l.ModifyDamageAdditive(t, n, props, dealer, c, null);
        foreach (var l in ls) n *= l.ModifyDamageMultiplicative(t, n, props, dealer, c, null);
        foreach (var l in ls) n *= l.ModifyDamageMultiplicativeLate(t, n, props, dealer, c, null);
        foreach (var l in ls) { var x = l.ModifyDamageCap(t, props, dealer, c, null); if (n > x) n = x; }
        return MathF.Floor((float)Math.Max(0m, n));
    }

    /// <summary>包 1 → 包 3、包 4、包 5：回合预演（期望口径、主调用）的拆分输出：最大生命损失（G28）、下回合开局的格挡（G21/G70）、
    /// 手牌回合末自伤（G08、G36）、敌人回合末自伤（G36 等）。包 1 在每次 EndTurnPreview 末尾写 Et29Last。</summary>
    internal struct Et29 { public float MaxHpLoss, NextTurnBlock, HandLoss, FoeLoss; }
    static Et29 Et29Last;
    /// <summary>AddCombat 在主调用 EndTurnPreview 之后立刻拷贝 Et29Last（之后的转身、无额外回合两次调用会覆盖 Et29Last）。</summary>
    Et29 _et29;
    /// <summary>EncodeBody 里 = run.Selecting（包 3 的选牌候选用），结尾清空。</summary>
    PendingCardChoice? _sel;
    /// <summary>包 4 写（这一步新加的荆棘，G50/G72），包 3 读；每个候选开头清零。</summary>
    float _thornsGain;
}
