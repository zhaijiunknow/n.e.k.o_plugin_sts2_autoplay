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

// 故障机器人：哪些牌生成哪些充能球、生成后的格挡变化、被动结算次数。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>
    /// 版本 19（决策记录 227）：打出这张牌生成哪些充能球（F 冰霜、L 闪电、D 黑暗、P 等离子、G 玻璃），按卡牌类别查；
    /// 随机生成（混沌）、按能量生成（风暴）等没有列出的返回空，按不生成算。
    /// </summary>
    /// <summary>OrbChannels 按类名写的牌（selfcheck 核对这些类名在新版游戏里还在不在）。</summary>
    internal static readonly string[] OrbChannelCards = { "Coolheaded", "ColdSnap", "Glacier", "Hibernate", "IceLance", "Chill", "Rainbow", "Zap", "BallLightning", "Darkness", "Fusion", "Glasswork", "Refract" };

    static string OrbChannels(CardModel c, Player me)
    {
        static int Rep(CardModel c) { try { return c.DynamicVars.TryGetValue("Repeat", out var v) ? (int)v.BaseValue : 1; } catch { return 1; } }
        switch (c.GetType().Name)
        {
            case "Coolheaded": case "ColdSnap": return "F";
            case "Glacier": return "FF";
            case "Hibernate": case "IceLance": return new string('F', Math.Max(0, Rep(c)));
            case "Chill":
                try { return new string('F', me.Creature.CombatState!.Creatures.Count(x => !x.IsPlayer && x.Side != me.Creature.Side && x.IsAlive && IsHittableM(x))); }
                catch { return ""; }
            case "Rainbow": return "LFD";
            case "Zap": case "BallLightning": return "L";
            case "Darkness": return "D";
            case "Fusion": return "P";
            case "Glasswork": return "G";
            case "Refract" when F113: return new string('G', Math.Max(0, Rep(c)));     // v0.113.0 修正：折射打完生成 Repeat（2）个玻璃球（Refract.OnPlay，新旧相同，原来漏了）
            default: return "";
        }
    }

    /// <summary>
    /// 版本 19（决策记录 227）：打出这张牌之后，充能球带来的格挡变化：（当场得到的：挤掉的冰霜球的激发格挡，回合结束冰霜被动格挡的变化）。
    /// 按游戏的规则：球位满了再生成时先激发最前面的球；新冰霜球的数值取已有冰霜球的（同一集中下相同），没有时按 2、5 加集中算。
    /// </summary>
    static (float imm, float passive) OrbDelta(CardModel c, Player me)
    {
        string seq = OrbChannels(c, me);
        if (seq.Length == 0) return (0, 0);
        try
        {
            var q = me.PlayerCombatState!.OrbQueue;
            int cap = q.Capacity;
            if (cap <= 0) return (0, 0);
            var orbs = q.Orbs.Select(o => o is MegaCrit.Sts2.Core.Models.Orbs.FrostOrb fo ? (frost: true, pv: (float)fo.PassiveVal, ev: (float)fo.EvokeVal) : (frost: false, pv: 0f, ev: 0f)).ToList();
            float focus = me.Creature.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.FocusPower>().Sum(p => (float)p.Amount);
            var known = orbs.FirstOrDefault(o => o.frost);
            float fpv = known.frost ? known.pv : Math.Max(0, 2 + focus), fev = known.frost ? known.ev : Math.Max(0, 5 + focus);
            float before = orbs.Where(o => o.frost).Sum(o => o.pv), imm = 0;
            foreach (char k in seq)
            {
                if (orbs.Count >= cap) { var f = orbs[0]; orbs.RemoveAt(0); if (f.frost) imm += f.ev; }
                orbs.Add(k == 'F' ? (frost: true, pv: fpv, ev: fev) : (frost: false, pv: 0f, ev: 0f));
            }
            return (imm, orbs.Where(o => o.frost).Sum(o => o.pv) - before);
        }
        catch { return (0, 0); }
    }

    /// <summary>
    /// 第七轮（F113，观测独立核查.md 第十四节）：出牌对充能球的全部影响，替代 OrbDelta（格式 19 起的口径扩展）。返回（当场得到的格挡，
    /// 回合结束冰霜被动格挡的变化，激发确定打死的敌人）。照各牌 OnPlay 与 OrbCmd 的结算顺序，每次打出（PlayCount）各模拟一遍：
    ///   生成：OrbChannels 的名单，加暴风雨（X 个闪电，Tempest.OnPlay）；球位满了先激发最前面的球；
    ///   激发：双重释放（最前面的球激发两次后移除）、多重释放（X 次）、四重释放（Repeat 次）、打碎（每个球激发两次后移除，先打全体）；
    ///   激发的效果：冰霜得格挡；闪电打随机一个可攻击的敌人（只剩一个时确定，雷霆另加层数）；黑暗打生命最低的；玻璃打全体；等离子不算。
    ///   伤害按 Unpowered 过伤害钩子、敌人格挡（这一步已经掉过血的按格挡打光算）与掉血钩子，累计这一步的掉血（hl）不小于生命时算打死，
    ///   打死的不再是后面激发的目标；hl 加上激发造成的掉血（后面判断毒得死、阈值眩晕、回合末被动打死用）。
    ///   集中：热修复、集中打击、同步（临时）、偏差认知、碎片整理（永久）、超能光束（临时减）改变回合末冰霜被动（集中到回合结束之后才撤，被动先结算），
    ///   每个冰霜球的被动按 max(0, 原值 + 变化) 算。
    /// 新生成的球的数值取已有同类球的（同一集中下相同），没有时按基础值加集中算；被动结算次数（镀金缆线）不算，同 OrbDelta。只读。
    /// </summary>
    (float imm, float passive, List<Creature>? kills) OrbFx(CardModel c, Creature? tgt, Player me, Dictionary<Creature, float>? hl, List<Creature>? killed)
    {
        if (V29) return OrbFx29(c, tgt, me, hl, killed);      // 格式 29 的新口径（Defect29.cs），本函数原样留给旧格式
        try
        {
            var cs = me.Creature.CombatState!;
            var pcs = me.PlayerCombatState!;
            var q = pcs.OrbQueue;
            int cap = q.Capacity;
            float focus = me.Creature.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.FocusPower>().Sum(p => (float)p.Amount);
            static char Kind(OrbModel o) => o switch
            {
                MegaCrit.Sts2.Core.Models.Orbs.FrostOrb => 'F', MegaCrit.Sts2.Core.Models.Orbs.LightningOrb => 'L', MegaCrit.Sts2.Core.Models.Orbs.DarkOrb => 'D',
                MegaCrit.Sts2.Core.Models.Orbs.GlassOrb => 'G', MegaCrit.Sts2.Core.Models.Orbs.PlasmaOrb => 'P', _ => '?',
            };
            var orbs = q.Orbs.Select(o => (k: Kind(o), pv: (float)o.PassiveVal, ev: (float)o.EvokeVal, trig: PassiveTriggers(cs, o))).ToList();
            float before = orbs.Where(o => o.k == 'F').Sum(o => o.pv);
            (char k, float pv, float ev, int trig) New(char k)
            {
                var known = q.Orbs.FirstOrDefault(o => Kind(o) == k);
                if (known != null && k != 'D' && k != 'G') return (k, (float)known.PassiveVal, (float)known.EvokeVal, 1);
                return k switch
                {
                    'F' => (k, Math.Max(0, 2 + focus), Math.Max(0, 5 + focus), 1),
                    'L' => (k, Math.Max(0, 3 + focus), Math.Max(0, 8 + focus), 1),
                    'D' => (k, known != null ? (float)known.PassiveVal : Math.Max(0, 6 + focus), 6, 1),
                    'G' => (k, Math.Max(0, 4 + focus), 2 * Math.Max(0, 4 + focus), 1),
                    _ => (k, 0, 0, 1),
                };
            }
            var live = Hittable(cs).Where(f => f.IsAlive && !(killed?.Contains(f) ?? false)).ToList();
            var blk = live.ToDictionary(f => f, f => (hl != null && hl.GetValueOrDefault(f) > 0) ? 0f : (float)f.Block);
            var lost = live.ToDictionary(f => f, f => hl?.GetValueOrDefault(f) ?? 0f);
            float thunder = me.Creature.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.ThunderPower>().Sum(p => (float)p.Amount);
            List<Creature>? kills = null;
            float imm = 0;
            void Hit(Creature f, float v)
            {
                if (v <= 0 || !live.Contains(f)) return;
                float d = HitDmg(cs, f, me.Creature, (decimal)v, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null);
                float b = Math.Min(blk[f], d); blk[f] -= b; d -= b;
                if (d <= 0) return;
                try { d = (float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyHpLost(RunSession.State, cs, f, (decimal)d, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, me.Creature, null, MegaCrit.Sts2.Core.Hooks.HpLossHookPhase.All, out _); } catch { }
                lost[f] = Math.Min(lost[f] + MathF.Floor(d), ShellLeft(f));
                if (lost[f] >= f.CurrentHp) { live.Remove(f); (kills ??= new()).Add(f); }
            }
            void Evoke((char k, float pv, float ev, int trig) o)
            {
                switch (o.k)
                {
                    case 'F': imm += o.ev; break;
                    case 'L': if (live.Count == 1) { var t = live[0]; Hit(t, o.ev); Hit(t, thunder); } break;
                    case 'D': if (live.Count > 0) Hit(live.MinBy(f => f.CurrentHp - lost[f])!, o.ev); break;
                    case 'G': foreach (var f in live.ToList()) Hit(f, o.ev); break;
                }
            }
            void Channel(char k)
            {
                if (cap <= 0) return;
                if (orbs.Count >= cap) { var f0 = orbs[0]; orbs.RemoveAt(0); Evoke(f0); }
                orbs.Add(New(k));
            }
            void EvokeFirst(int times)
            {
                if (orbs.Count == 0 || times <= 0) return;
                var f0 = orbs[0];
                for (int i = 0; i < times; i++) Evoke(f0);
                orbs.RemoveAt(0);
            }
            int X() => Math.Max(0, MegaCrit.Sts2.Core.Hooks.Hook.ModifyXValue(cs, c, pcs.Energy)) + (c.IsUpgraded ? 1 : 0);
            float V(string name) => c.DynamicVars.TryGetValue(name, out var v) ? (float)v.BaseValue : 0;
            float dF = 0;
            int plays = PlayCount(c, tgt);
            for (int r = 0; r < plays; r++)
            {
                foreach (char k in OrbChannels(c, me)) Channel(k);
                switch (c)
                {
                    case MegaCrit.Sts2.Core.Models.Cards.Tempest: { int n = X(); for (int i = 0; i < n; i++) Channel('L'); } break;
                    case MegaCrit.Sts2.Core.Models.Cards.Dualcast: EvokeFirst(2); break;
                    case MegaCrit.Sts2.Core.Models.Cards.MultiCast: EvokeFirst(X()); break;
                    case MegaCrit.Sts2.Core.Models.Cards.Quadcast: EvokeFirst((int)V("Repeat")); break;
                    case MegaCrit.Sts2.Core.Models.Cards.Shatter: { int n = orbs.Count; for (int i = 0; i < n; i++) EvokeFirst(2); } break;
                    case MegaCrit.Sts2.Core.Models.Cards.Hotfix or MegaCrit.Sts2.Core.Models.Cards.FocusedStrike
                        or MegaCrit.Sts2.Core.Models.Cards.BiasedCognition or MegaCrit.Sts2.Core.Models.Cards.Defragment: dF += V("FocusPower"); break;
                    case MegaCrit.Sts2.Core.Models.Cards.Hyperbeam: dF -= V("FocusPower"); break;
                    case MegaCrit.Sts2.Core.Models.Cards.Synchronize:
                        try { dF += (float)((MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedVar)c.DynamicVars["CalculatedFocus"]).Calculate(tgt); } catch { }
                        break;
                }
            }
            float after = orbs.Where(o => o.k == 'F').Sum(o => Math.Max(0, (o.pv > 0 ? o.pv : 2 + focus) + dF));
            if (dF == 0) after = orbs.Where(o => o.k == 'F').Sum(o => o.pv);
            if (hl != null) foreach (var (f, l) in lost) if (l > 0) hl[f] = l;
            _orbAfter = orbs.Select(o => (o.k, Math.Max(0, (o.pv > 0 || o.k != 'L' ? o.pv : 3 + focus) + (o.k is 'L' or 'G' ? dF : 0)), o.trig)).ToList();
            return (imm, after - before, kills);
        }
        catch { return (0, 0, null); }
    }

    /// <summary>回合结束时这个球的被动结算几次（同 OrbModel.TriggerPassive：从 1 起经 Hook.ModifyOrbPassiveTriggerCount 修正；只读，不触发遗物闪光）。</summary>
    static int PassiveTriggers(ICombatState cs, OrbModel orb)
    {
        try { return Math.Max(0, MegaCrit.Sts2.Core.Hooks.Hook.ModifyOrbPassiveTriggerCount(cs, orb, 1, out _)); } catch { return 1; }
    }
}
