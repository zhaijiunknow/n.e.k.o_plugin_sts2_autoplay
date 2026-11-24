using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MC = MegaCrit.Sts2.Core.Models.Cards;

namespace Sts2Headless;

// 格式 29 包 4（R3-格式29规格.md 5.4 G42，R1-故障 D1–D4、D10、D11）：OrbFx 的新口径 OrbFx29。只在 V29 下调用，只读。
public sealed partial class Observation
{
    /// <summary>格式 29：打出这张牌生成哪些充能球（OrbChannels 之外补上的：空值、暗影护盾 1 黑暗，吞噬暗影 Repeat 个黑暗，METEOR_STRIKE 3 个等离子，
    /// 伏特按 CalculatedChannels 个闪电，升级的 SPINNER 1 个玻璃，混沌 Repeat 个种类未知的球“?”：只模拟它挤掉最前面的球）。</summary>
    static string OrbChannels29(CardModel c, Creature? tgt, Player me)
    {
        string s = OrbChannels(c, me);
        try
        {
            int Var(string n) => c.DynamicVars.TryGetValue(n, out var v) ? v.IntValue : 0;
            switch (c)
            {
                case MC.Null or MC.ShadowShield: s += "D"; break;
                case MC.ConsumingShadow: s += new string('D', Math.Max(0, Var("Repeat"))); break;
                case MC.MeteorStrike: s += "PPP"; break;
                case MC.Voltaic when c.DynamicVars.TryGetValue("CalculatedChannels", out var cc) && cc is MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedVar cv:
                    s += new string('L', Math.Max(0, (int)cv.Calculate(tgt))); break;
                case MC.Spinner when c.IsUpgraded: s += "G"; break;
                case MC.Chaos: s += new string('?', Math.Max(0, Var("Repeat"))); break;
            }
        }
        catch { }
        return s;
    }

    /// <summary>
    /// 格式 29（G42）：OrbFx 的新口径。在它的基础上：
    ///   D1 风暴：每次打出能力牌之后按打出前的层数生成闪电（StormPower：BeforeCardPlayed 记层数、AfterCardPlayed 生成）；
    ///   D2 生成名单补全（OrbChannels29）；D3 BULK_UP 去掉 OrbSlots 个球位、超出时从队尾移除（不激发），电容器、MODDED 加 Repeat 个球位；
    ///   D4 镀金缆线只让最前面的球被动多结算一次：被动次数按这一步之后的位置重算，候选 9 = Σ（冰霜被动 × 次数）之差，_orbAfter 的次数随之更新；
    ///   D10 节拍器：本场生成的第 OrbCount 个球时对全体 Damage 点无力伤害（进 hl 与不算名单）；
    ///   激发的冰霜、闪电按这一步已经变了的集中算（碎片整理之后风暴生成闪电挤掉的球）；
    ///   D11 注入核心：场上没有闪电球时新闪电按 3 + 集中 + ExtraDamage 算；SMOKESTACK：这一步生成状态牌（BOOST_AWAY、TURBO、GUNK_UP、OVERCLOCK 各 1 张，
    ///     FIGHT_THROUGH 2 张），每张对全体层数点无力伤害。
    /// 吞噬暗影的回合末激发（D9）、冰雹风暴的冰霜球数不做。
    /// </summary>
    (float imm, float passive, List<Creature>? kills) OrbFx29(CardModel c, Creature? tgt, Player me, Dictionary<Creature, float>? hl, List<Creature>? killed)
    {
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
            int cables = me.Relics.Count(r => r is MegaCrit.Sts2.Core.Models.Relics.GoldPlatedCables && RelicLive(r));
            int Trig(int pos) => 1 + (pos == 0 ? cables : 0);
            var orbs = q.Orbs.Select(o => (k: Kind(o), pv: (float)o.PassiveVal, ev: (float)o.EvokeVal, trig: 1)).ToList();
            float before = q.Orbs.Select((o, i) => Kind(o) == 'F' ? (float)o.PassiveVal * PassiveTriggers(cs, o) : 0f).Sum();
            var infused = me.Relics.FirstOrDefault(r => r is MegaCrit.Sts2.Core.Models.Relics.InfusedCore && RelicLive(r));
            float infusedAdd = infused != null && infused.DynamicVars.TryGetValue("ExtraDamage", out var xd) ? (float)xd.BaseValue : 0;
            (char k, float pv, float ev, int trig) New(char k)
            {
                var known = q.Orbs.FirstOrDefault(o => Kind(o) == k);
                if (known != null && k != 'D' && k != 'G' && k != '?') return (k, (float)known.PassiveVal, (float)known.EvokeVal, 1);
                return k switch
                {
                    'F' => (k, Math.Max(0, 2 + focus), Math.Max(0, 5 + focus), 1),
                    'L' => (k, Math.Max(0, 3 + focus + infusedAdd), Math.Max(0, 8 + focus + infusedAdd), 1),
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
            float imm = 0, dF = 0;      // dF：这一步到目前为止的集中变化（之后激发的冰霜、闪电按它算）
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
            void All(float v) { foreach (var f in live.ToList()) Hit(f, v); }
            void Evoke((char k, float pv, float ev, int trig) o)
            {
                switch (o.k)
                {
                    case 'F': imm += Math.Max(0, o.ev + dF); break;
                    case 'L': if (live.Count == 1) { var t = live[0]; Hit(t, Math.Max(0, o.ev + dF)); Hit(t, thunder); } break;
                    case 'D': if (live.Count > 0) Hit(live.MinBy(f => f.CurrentHp - lost[f])!, o.ev); break;
                    case 'G': foreach (var f in live.ToList()) Hit(f, o.ev); break;
                }
            }
            var metro = me.Relics.FirstOrDefault(r => r is MegaCrit.Sts2.Core.Models.Relics.Metronome && RelicLive(r));
            int metroCount = metro != null && metro.DynamicVars.TryGetValue("OrbCount", out var oc) ? oc.IntValue : 0;
            int metroDone = metro != null ? metro.DisplayAmount : 0;
            void Channel(char k)
            {
                if (cap <= 0) return;
                if (orbs.Count >= cap) { var f0 = orbs[0]; orbs.RemoveAt(0); Evoke(f0); }
                orbs.Add(New(k));
                if (metro != null && metroDone < metroCount && ++metroDone == metroCount) All((float)metro.DynamicVars.Damage.BaseValue);
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
            int plays = PlayCount(c, tgt);
            int storm = c.Type == CardType.Power ? me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.StormPower>() : 0;
            int smoke = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.SmokestackPower>();
            int statusMade = c switch { MC.BoostAway or MC.Turbo or MC.GunkUp or MC.Overclock => 1, MC.FightThrough => 2, _ => 0 };
            for (int r = 0; r < plays; r++)
            {
                switch (c)
                {
                    case MC.BulkUp: cap = Math.Max(0, cap - (int)V("OrbSlots")); while (orbs.Count > cap) orbs.RemoveAt(orbs.Count - 1); break;
                    case MC.Capacitor or MC.Modded: cap += (int)V("Repeat"); break;
                }
                foreach (char k in OrbChannels29(c, tgt, me)) Channel(k);
                switch (c)
                {
                    case MC.Tempest: { int n = X(); for (int i = 0; i < n; i++) Channel('L'); } break;
                    case MC.Dualcast: EvokeFirst(2); break;
                    case MC.MultiCast: EvokeFirst(X()); break;
                    case MC.Quadcast: EvokeFirst((int)V("Repeat")); break;
                    case MC.Shatter: { int n = orbs.Count; for (int i = 0; i < n; i++) EvokeFirst(2); } break;
                    case MC.Hotfix or MC.FocusedStrike or MC.BiasedCognition or MC.Defragment: dF += V("FocusPower"); break;
                    case MC.Hyperbeam: dF -= V("FocusPower"); break;
                    case MC.Synchronize:
                        try { dF += (float)((MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedVar)c.DynamicVars["CalculatedFocus"]).Calculate(tgt); } catch { }
                        break;
                }
                for (int i = 0; i < statusMade * (smoke > 0 ? 1 : 0); i++) All(smoke);
                for (int i = 0; i < storm; i++) Channel('L');
            }
            for (int i = 0; i < orbs.Count; i++) orbs[i] = (orbs[i].k, orbs[i].pv, orbs[i].ev, Trig(i));
            float after = orbs.Where(o => o.k == 'F').Sum(o => (dF == 0 ? o.pv : Math.Max(0, (o.pv > 0 ? o.pv : 2 + focus) + dF)) * o.trig);
            if (hl != null) foreach (var (f, l) in lost) if (l > 0) hl[f] = l;
            _orbAfter = orbs.Select(o => (o.k, Math.Max(0, (o.pv > 0 || o.k != 'L' ? o.pv : 3 + focus) + (o.k is 'L' or 'G' ? dF : 0)), o.trig)).ToList();
            return (imm, after - before, kills);
        }
        catch { return (0, 0, null); }
    }
}
