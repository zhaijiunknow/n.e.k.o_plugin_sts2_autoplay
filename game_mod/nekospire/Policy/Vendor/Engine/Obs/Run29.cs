using System.Reflection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Orbs;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.Models.Relics;

namespace Sts2Headless;

// 格式 29 的全局、遗物、局外几项：包 5 的文件（研究报告/2026-11-20_全角色观测缺口调研/R3-格式29规格.md 5.5）。
//   G21/G72 全局 22（下个玩家回合开始时的能量）、G62 J5 全局 23（战斗外的能量上限）、G03 遗物 22（律动残余）、G34 J3（偷窃跳蚤还回的牌）。
// 只在 V29 下被调用。
public sealed partial class Observation
{
    static readonly FieldInfo? ArtOfWarAttacked = GameCompat.Field(typeof(ArtOfWar), "_anyAttacksPlayedThisTurn", "孙子兵法：本回合打过攻击（格式 29 全局 22，G21）");
    static readonly FieldInfo? SpecialRewardCard = GameCompat.Field(typeof(MegaCrit.Sts2.Core.Rewards.SpecialCardReward), "_card", "特殊卡牌奖励的牌（格式 29，G34 J3：偷窃跳蚤还回的牌）");

    /// <summary>
    /// G21/G72 全局 22：现在结束回合，下个玩家回合开始时（第一个决策帧）的能量。照 CombatManager.SetupPlayerTurn 与回合开始的钩子：
    /// 1. 能量上限按下一回合重算（面包第 1 回合以外 +1、佩尔之肉第 3 回合起 +1，其余 ModifyMaxEnergy 照现在）；
    /// 2. ShouldPlayerResetEnergy 为假（冰淇淋，第 1 回合以外）时保留现有能量，否则清零，再加上限；
    /// 3. AfterEnergyReset：下回合能量（EnergyNextTurnPower，含 OUTMANEUVER、放松、侧步）、光辉（RadiancePower）、孙子兵法（本回合没打攻击）；
    /// 4. AfterSideTurnStart：开心小花（及仿品）计数到阈值、烛台（下回合是第 2 回合）、吊灯（第 3 回合）、佩尔之泪（现在还有能量）、黄金印（金币够）；
    /// 5. 充能球回合开始：等离子球的被动，循环让最前面的等离子球多触发。
    /// 不算：抽牌时的能量变化（虚空、自动化）、敌方回合里新挂上的减能量。读不到为现有上限。
    /// </summary>
    static float NextEnergy29(Player me, PlayerCombatState pcs, ICombatState cs)
    {
        try
        {
            int next = pcs.TurnNumber + 1;
            var ls = cs.IterateHookListeners().ToList();
            bool reset = true;
            foreach (var l in ls)
            {
                bool r = l is IceCream ic ? (ic.Owner != me || next == 1) : l.ShouldPlayerResetEnergy(me);
                if (!r) { reset = false; break; }
            }
            decimal max = me.MaxEnergy;
            foreach (var l in ls)
                max = l switch
                {
                    Bread b when b.Owner == me => max + (next == 1 ? 0 : b.DynamicVars["GainEnergy"].BaseValue),
                    PaelsFlesh pf when pf.Owner == me => max + (next < 3 ? 0 : pf.DynamicVars.Energy.BaseValue),
                    _ => l.ModifyMaxEnergy(me, max),
                };
            decimal e = (reset ? 0 : pcs.Energy) + (int)max;
            foreach (var pw in me.Creature.Powers)
            {
                if (pw is EnergyNextTurnPower) e += pw.Amount;
                else if (pw is RadiancePower && pw.Amount > 0) e += pw.DynamicVars.Energy.IntValue;
            }
            foreach (var r in me.Relics)
            {
                if (!RelicLive(r)) continue;
                switch (r)
                {
                    case ArtOfWar aw when next > 1 && ArtOfWarAttacked?.GetValue(aw) is false: e += aw.DynamicVars.Energy.BaseValue; break;
                    case HappyFlower hf when (hf.TurnsSeen + 1) % hf.DynamicVars["Turns"].IntValue == 0: e += hf.DynamicVars.Energy.BaseValue; break;
                    case FakeHappyFlower fh when (fh.TurnsSeen + 1) % fh.DynamicVars["Turns"].IntValue == 0: e += fh.DynamicVars.Energy.BaseValue; break;
                    case Candelabra cd when next == 2: e += cd.DynamicVars.Energy.BaseValue; break;
                    case Chandelier ch when next == 3: e += ch.DynamicVars.Energy.BaseValue; break;
                    case PaelsTears pt when pcs.Energy > 0: e += pt.DynamicVars.Energy.BaseValue; break;
                    case SealOfGold sg when me.Gold >= sg.DynamicVars.Gold.IntValue: e += sg.DynamicVars.Energy.BaseValue; break;
                }
            }
            var orbs = pcs.OrbQueue.Orbs;
            foreach (var o in orbs) if (o is PlasmaOrb) e += o.PassiveVal;
            if (orbs.Count > 0 && orbs[0] is PlasmaOrb front && me.Creature.GetPower<LoopPower>() is { } loop) e += loop.Amount * front.PassiveVal;
            return (float)e;
        }
        catch { return pcs.MaxEnergy; }
    }

    /// <summary>
    /// G62 J5 全局 23：战斗外的能量上限。Player.MaxEnergy 本身是角色的基础值（游戏里没有直接改它的地方），加能量的遗物（低语耳环、赐福鹿角、
    /// 带刺手甲、天鹅绒颈圈、面包、南瓜蜡烛等）都经 ModifyMaxEnergy 钩子在战斗中生效，所以这里把玩家遗物的 ModifyMaxEnergy 依次过一遍，
    /// 即“第 2 回合起的能量上限”。按回合数变的两件不调钩子（战斗外 PlayerCombatState 是上一场留下的，回合数不可靠）：
    /// 面包按第 2 回合起 +1 算，佩尔之肉（第 3 回合起 +1）不算。
    /// </summary>
    static float MaxEnergyOut29(Player me)
    {
        decimal x = me.MaxEnergy;
        foreach (var r in me.Relics)
        {
            if (!RelicLive(r)) continue;
            try
            {
                x = r switch
                {
                    Bread b => x + b.DynamicVars["GainEnergy"].BaseValue,
                    PaelsFlesh => x,
                    _ => r.ModifyMaxEnergy(me, x),
                };
            }
            catch { }
        }
        return (float)x;
    }

    /// <summary>G34 J3：特殊卡牌奖励（偷窃跳蚤还回的牌、灯笼钥匙）里的牌（私有字段 _card）；读不到为 null。</summary>
    static CardModel? SpecialCard29(MegaCrit.Sts2.Core.Rewards.SpecialCardReward r)
    {
        try { return SpecialRewardCard?.GetValue(r) as CardModel; }
        catch { return null; }
    }
}
