using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;

namespace Sts2Headless;

// 亡灵契约师（格式 22，铁甲与亡灵契约师观测调研.md）：奥斯提的召唤与牺牲、灾厄。
// 奥斯提替玩家挨打的预演在 EndTurn.cs（Loss22），奥斯提出手的伤害预览在 Cards.cs（Preview 的 OstyDamage）。
// 内容表（哪些牌会让奥斯提死掉）写在这里，游戏更新改了这些牌时只改这个文件。
public sealed partial class Observation
{
    /// <summary>打出后奥斯提死掉的牌（OnPlay 里 CreatureCmd.Kill(Osty)）：牺牲（先算格挡再杀奥斯提）、碎骨（奥斯提打完全体再死）。</summary>
    static readonly HashSet<Type> OstyKillCards = new()
    {
        typeof(MegaCrit.Sts2.Core.Models.Cards.Sacrifice), typeof(MegaCrit.Sts2.Core.Models.Cards.BoneShards),
    };

    /// <summary>
    /// 打出时当场召唤的牌（OnPlay 里直接 OstyCmd.Summon）：来生、护卫、洁净、挽歌、骸骨军团、亡灵精通、吸引仇恨、死者苏生、增生。
    /// 唤起（Invoke）也有召唤数值，但它给的是“下回合召唤”（SummonNextTurnPower），不在这里。
    /// </summary>
    static readonly HashSet<Type> SummonCards = new()
    {
        typeof(MegaCrit.Sts2.Core.Models.Cards.Afterlife), typeof(MegaCrit.Sts2.Core.Models.Cards.Bodyguard), typeof(MegaCrit.Sts2.Core.Models.Cards.Cleanse),
        typeof(MegaCrit.Sts2.Core.Models.Cards.Dirge), typeof(MegaCrit.Sts2.Core.Models.Cards.LegionOfBone), typeof(MegaCrit.Sts2.Core.Models.Cards.NecroMastery),
        typeof(MegaCrit.Sts2.Core.Models.Cards.PullAggro), typeof(MegaCrit.Sts2.Core.Models.Cards.Reanimate), typeof(MegaCrit.Sts2.Core.Models.Cards.Spur),
    };

    /// <summary>
    /// 格式 22：打出这张牌之后奥斯提的生命变化（回合结束预演的 ostyEx）：召唤（SummonVar，经 Hook.ModifySummonAmount）每次打出加召唤量
    /// （奥斯提活着时加最大生命并回同样多的血，不在或死了时复活成召唤量的生命，OstyCmd.Summon）；会让奥斯提死掉的牌为负的现有生命。
    /// 治疗奥斯提（增生的 Heal）不算。只读。
    /// </summary>
    static float OstyChange(ICombatState cs, Player me, CardModel card, int plays)
    {
        try
        {
            if (OstyKillCards.Contains(card.GetType())) return me.IsOstyAlive ? -me.Osty!.CurrentHp : 0;
            if (!SummonCards.Contains(card.GetType()) || !card.DynamicVars.TryGetValue("Summon", out var sv)) return 0;
            decimal amt = MegaCrit.Sts2.Core.Hooks.Hook.ModifySummonAmount(cs, me, sv.BaseValue, card);
            if (V29) return OstyChange29(cs, me, card, plays, Math.Max(0, (float)amt));
            return Math.Max(0, (float)amt) * plays;
        }
        catch { return 0; }
    }

    /// <summary>
    /// 格式 29（G41，R1-亡灵 N8、N12）：召唤牌这一步让奥斯提多出的生命。挽歌召唤 X 次（Dirge.OnPlay：ResolveEnergyXValue，X 为现有能量经 Hook.ModifyXValue）；
    /// 增生先召唤再治疗奥斯提（Spur.OnPlay：Heal，奥斯提活着时召唤把上限与生命同加，治疗量封顶在上限 − 生命）。其余同格式 22（召唤量 × 打出次数）。
    /// </summary>
    static float OstyChange29(ICombatState cs, Player me, CardModel card, int plays, float amt)
    {
        try
        {
            switch (card)
            {
                case MegaCrit.Sts2.Core.Models.Cards.Dirge:
                    {
                        int x = Math.Max(0, MegaCrit.Sts2.Core.Hooks.Hook.ModifyXValue(cs, card, me.PlayerCombatState!.Energy));
                        return amt * x * plays;
                    }
                case MegaCrit.Sts2.Core.Models.Cards.Spur when me.IsOstyAlive && card.DynamicVars.TryGetValue("Heal", out var hv):
                    {
                        var o = me.Osty!;
                        float heal = Math.Min((float)hv.BaseValue, Math.Max(0, o.MaxHp - o.CurrentHp));
                        return amt * plays + heal;
                    }
            }
        }
        catch { }
        return amt * plays;
    }

    /// <summary>
    /// 格式 22：灾厄记录 12、13。敌人身上的灾厄在敌方回合结束时结算（DoomPower.BeforeSideTurnEnd：生命 ≤ 灾厄层数就被处决，敌人这回合已经出过手），
    /// 我方（玩家、奥斯提）身上的在玩家回合结束时结算（AfterSideTurnEnd）。生命按结算时算：敌人减去回合结束的被动伤害（回合结束预演的 16）与
    /// 敌方回合开始的毒伤（PoisonNext），我方用现在的生命。12 会不会被处决（结算前就死了的为 0），13 还差多少血（生命 − 层数，不小于 0）。
    /// 敌方回合里的回血、加格挡、反伤等不算。
    /// </summary>
    (float doomed, float margin) DoomInfo(Creature c, int doom, bool ally)
    {
        try
        {
            float hp = c.CurrentHp;
            // 格式 29（G36）：玩家自己的灾厄在玩家回合结束之后结算，手牌的回合末自伤（灼伤、腐朽、悔恨等，回合结束预演的 HandLoss）先掉
            if (V29 && ally && c.IsPlayer) hp -= Math.Max(0, _et29.HandLoss);
            if (!ally)
            {
                if (_endTurn != null && _endTurn.TryGetValue(c, out var et)) hp -= Math.Max(0, et.a - c.Block);
                foreach (var pp in c.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.PoisonPower>()) hp -= PoisonNext(c, pp);
            }
            if (hp <= 0) return (0, 0);
            return (hp <= doom ? 1 : 0, Math.Max(0, hp - doom));
        }
        catch { return (0, 0); }
    }
}
