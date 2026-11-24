using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Powers;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using System.Collections;
using System.Reflection;
using MegaCrit.Sts2.Core.MonsterMoves.Intents;
using MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;


namespace Sts2Headless;

// 伤害与掉血的公用算法：一段伤害过钩子、一串伤害的实际掉血、硬化外壳额度、打出次数。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>一段伤害（格式 21）：数值（已过伤害钩子、截掉小数），吃不吃格挡，掉血钩子用的属性与来源。</summary>
    readonly record struct Hit(float Dmg, bool Blockable, MegaCrit.Sts2.Core.ValueProps.ValueProp Props, Creature? Dealer, CardModel? Card);

    /// <summary>一次伤害经游戏的伤害钩子（Hook.ModifyDamage，同 CreatureCmd.Damage 的 All、不预览）之后的数值，截掉小数。只读。</summary>
    static float HitDmg(ICombatState cs, Creature target, Creature? dealer, decimal amount, MegaCrit.Sts2.Core.ValueProps.ValueProp props, CardModel? card)
    {
        try { return MathF.Floor((float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyDamage(cs.RunState, cs, target, dealer, amount, props, card, null, MegaCrit.Sts2.Core.Hooks.ModifyDamageHookType.All, CardPreviewMode.None, out _)); }
        catch { return MathF.Floor((float)amount); }
    }

    /// <summary>
    /// 格式 21：一串伤害对这个敌人实际造成的掉血（同 FoeHpLoss 的口径，只是每段可以不同）：吃格挡的逐段先过格挡，
    /// 再过游戏的掉血钩子（硬化外壳、无实体、滑溜等），截掉小数；硬化外壳的本回合额度按累计封顶。只读。
    /// </summary>
    static float SeqHpLoss(Creature f, IEnumerable<Hit> hits)
    {
        float loss = 0, block = f.Block;
        foreach (var h in hits)
        {
            float d = h.Dmg;
            if (h.Blockable) { float b = Math.Min(block, d); block -= b; d -= b; }
            if (d <= 0) continue;
            try { d = (float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyHpLost(RunSession.State, f.CombatState, f, (decimal)d, h.Props, h.Dealer, h.Card, MegaCrit.Sts2.Core.Hooks.HpLossHookPhase.All, out _); } catch { }
            loss += MathF.Floor(d);
        }
        return Math.Min(loss, ShellLeft(f));
    }

    /// <summary>这张牌打出时实际执行几次（版本 15，同 CardModel 出牌：重放次数 + 1，经 Hook.ModifyCardPlayCount 修正；只读）。</summary>

    static int PlayCount(CardModel c, Creature? target)
    {
        // 提速：同一次 Encode 里同一牌、同一目标只算一次（Memo.cs）；抛异常（按 1 次）时不记
        var m = Memo;
        if (m != null && m.Plays.TryGetValue((c, target), out int n)) return n;
        try { n = Math.Max(1, MegaCrit.Sts2.Core.Hooks.Hook.ModifyCardPlayCount(c.Owner.Creature.CombatState!, c, c.GetEnchantedReplayCount() + 1, target, out _)); } catch { return 1; }
        if (m != null) m.Plays[(c, target)] = n;
        return n;
    }

    /// <summary>硬化外壳本回合还剩的掉血额度（界面上显示的数）；没有为无穷大。</summary>
    static float ShellLeft(Creature f)
    {
        float cap = float.MaxValue;
        foreach (var p in f.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.HardenedShellPower>()) { try { cap = Math.Min(cap, p.DisplayAmount); } catch { } }
        return cap;
    }

    /// <summary>
    /// 一组同样的攻击（每段 perHit，共 n 段）对敌人实际造成的掉血（版本 15）：逐段先过格挡，再过游戏的掉血修正
    /// （Hook.ModifyHpLost：硬化外壳、无实体、滑溜，以及玩家这边的靴子等），硬化外壳的本回合额度按累计封顶。只读。
    /// </summary>
    static float FoeHpLoss(Creature f, CardModel? card, float perHit, int n, float block)
    {
        float loss = 0;
        var me = card?.Owner?.Creature;
        // 格式 22：奥斯提出手的牌，打出伤害的是奥斯提（掉血钩子按攻击者判断，如靴子只管玩家自己的攻击）
        if (V22 && card != null && card.Tags.Contains(CardTag.OstyAttack) && card.Owner?.Osty is { } osty) me = osty;
        for (int i = 0; i < n; i++)
        {
            float unb = Math.Max(0, perHit - block); block = Math.Max(0, block - perHit);
            if (unb <= 0) continue;
            try { unb = (float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyHpLost(RunSession.State, f.CombatState, f, (decimal)unb, MegaCrit.Sts2.Core.ValueProps.ValueProp.Move, me, card, MegaCrit.Sts2.Core.Hooks.HpLossHookPhase.All, out _); } catch { }
            loss += V20 || F113 ? MathF.Floor(unb) : unb;      // 格式 20：每段掉血截掉小数（Creature.LoseHpInternal 的 (int)）；F113 第六轮：格式 19 也截
        }
        return Math.Min(loss, ShellLeft(f));
    }
}
