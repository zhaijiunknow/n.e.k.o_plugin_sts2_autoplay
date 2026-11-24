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

// 回合结束预演（玩家与敌人的 16–19、候选 10 用的“假设”版本），凯撒蟹的被包围。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    Dictionary<Creature, (float a, float b, float c, float d)>? _endTurn;
    float _worstLoss, _luckyKills;      // 版本 13：随机目标的伤害不算击杀时的预计掉血；要靠随机目标才打得死的敌人数（AddCombatStats 输出）
    /// <summary>版本 19（决策记录 227）：回合结束预演的“假设”版本：多出 ex 点回合结束前的格挡、不算 skip 里这些敌人的攻击时，
    /// 现在结束回合预计掉多少血（同 _worstLoss 的口径：随机目标的伤害不算击杀）。候选级的存活特征用，战斗外为 null。</summary>
    LossAfterFn? _lossAfter;
    /// <summary>候选级“活过本回合”用的回合结束预演：多出 ex 点回合结束前的格挡、不算 skip 里这些敌人的攻击、奥斯提多出 ostyEx 点生命
    /// （格式 22 的召唤；奥斯提不在时为召唤出来的生命）时，现在结束回合预计掉多少血。格式 22 以前 ostyEx 不起作用。
    /// v0.113.0 修正（F113）：gone 为这一步打出、离开手牌的牌，它自己的回合末手牌效果（召唤之引、毒素这类能打出的状态牌）不再算。</summary>
    internal delegate float LossAfterFn(float ex, IReadOnlyCollection<Creature>? skip, float ostyEx = 0, float handEx = 0, IReadOnlyDictionary<Creature, FoeMod>? mod = null, CardModel? gone = null, CandFx fx = default);
    /// <summary>
    /// v0.113.0 修正第四轮（F113，观测独立核查.md 第十一节）：候选做完之后会改变回合末结算的几项。Imm 这一步当场得到的格挡（奥利哈钢在回合末最早的
    /// BeforeSideTurnEnd VeryEarly 看格挡是否为 0），Attack 这一步打出攻击牌（涟漪盆），HandDelta 回合末手牌张数的变化（打出一张为 −1：斗篷扣、悔恨），
    /// Plating、Intangible、Buffer 这一步给玩家的覆甲层数、无实体、缓冲层数（钢铁之心、石甲、灵瓶、怨灵形态、幸运补剂、缓冲等）；
    /// BlockBoost 这一步让回合末到账的格挡多乘的倍数减 1（打出影融：2^层数 − 1）。
    /// 第五轮：Gone 这一步除了打出的这张之外确定离手的牌（啾 副歌、塔罗“愚者”自动打出的，恶魔消耗的整手），它们的回合末手牌效果不再算。
    /// </summary>
    internal readonly record struct CandFx(float Imm, bool Attack, int HandDelta, float Plating, bool Intangible, int Buffer, float BlockBoost = 0, IReadOnlyCollection<CardModel>? Gone = null,
                                           // 格式 29（R3-格式29规格.md 第四节）：这一步在预演之外已经掉的血、直接打死的敌人、新加的荆棘、挂上 THE_GAMBIT、打出的牌
                                           float SelfLoss = 0, IReadOnlyCollection<Creature>? Dead = null, float Thorns = 0, bool Gambit = false, CardModel? Played = null);
    /// <summary>F113：这回合结束会因为佩尔之眼（本回合一张牌都没打）多一个回合。出牌候选打出一张牌就没有这个额外回合，改用 _lossAfterNoExtra。</summary>
    static bool PaelsExtraTurn;
    /// <summary>F113：现在有佩尔之眼的额外回合时，“打出一张牌、额外回合没了”的回合结束预演（出牌候选的 10 用）。</summary>
    LossAfterFn? _lossAfterNoExtra;
    /// <summary>格式 23：这个候选做完之后某个敌人的攻击怎么变（见 Fx23）：每段的基础伤害加 Add（力量的增减、玩家身上的感染，加法项），
    /// Weak 为这一步让它新挂上虚弱（原来没有；乘法项按虚弱倍率，含纸鹤、衰弱）。</summary>
    internal readonly record struct FoeMod(decimal Add, bool Weak, decimal Mult = 1m);
    /// <summary>格式 23：回合结束预演里每个敌人“被动打掉的确定伤害”造成的掉血（hpS），候选判断毒得死时用。</summary>
    Dictionary<Creature, float>? _sureHp;
    /// <summary>第七轮（F113）：回合结束预演里每个敌人（不带滑溜的）被动打到的确定伤害，逐段（格挡之前），前 orbN 段来自充能球；
    /// 候选判断“这一步之后被动打得死”用（充能球那几段按这一步之后的球重算，见 OrbFx 的 _orbAfter）。</summary>
    Dictionary<Creature, (List<float> hits, int orbN)>? _sureHits;
    /// <summary>第七轮（F113）：这个出牌候选做完之后的充能球（种类、被动值、被动结算次数），OrbFx 填；没有出牌为 null（按现在的球）。</summary>
    List<(char k, float pv, int trig)>? _orbAfter;
    /// <summary>格式 20：这回合结束后会用流沙吞掉玩家的贪食者（见 EndTurnPreview；没有为 null）。</summary>
    Creature? _devourer;

    /// <summary>
    /// 格式 20：凯撒蟹的“被包围”（2026-11-07，储君决策记录第 14 条）。游戏里只有凯撒蟹的两只钳子（粉碎者、火箭）会挂被包围与背刺，
    /// 别的战斗里这里的代码都不执行，观测与原来逐字节相同。玩家面朝一侧（SurroundedPower.Facing），背后那只钳子的伤害 ×1.5；
    /// 对钳子打出有目标的牌、用有目标的药水时先转向它（UpdateDirection：它在背后才转），一只钳子死后若剩下的都在同一侧就转向它们（AfterDeath）。
    /// 记录：玩家的被包围 8 为面朝左（1）还是右（0）；钳子的背刺 8 为它现在在不在玩家背后。
    /// 候选 10：会让玩家最终换了朝向的出牌、用药水候选，按转身之后的朝向算回合结束预演（临时翻转朝向重算一遍，算完翻回来）。
    /// </summary>
    MegaCrit.Sts2.Core.Models.Powers.SurroundedPower? _surround;
    LossAfterFn? _lossAfterFlip;
    static readonly System.Reflection.FieldInfo? FacingField = GameCompat.Field(typeof(MegaCrit.Sts2.Core.Models.Powers.SurroundedPower), "_facing", "凯撒蟹战里玩家的朝向");

    /// <summary>朝向为 facing 时这只钳子在不在玩家背后（同 SurroundedPower.ModifyDamageMultiplicative：面朝右时背后是带左背刺的）。</summary>
    static bool Behind(MegaCrit.Sts2.Core.Models.Powers.SurroundedPower.Direction facing, Creature c) =>
        facing == MegaCrit.Sts2.Core.Models.Powers.SurroundedPower.Direction.Right
            ? c.HasPower<MegaCrit.Sts2.Core.Models.Powers.BackAttackLeftPower>() : c.HasPower<MegaCrit.Sts2.Core.Models.Powers.BackAttackRightPower>();

    /// <summary>朝向翻过去时的回合结束预演（只读：临时改 _facing，算完还原）。</summary>
    static LossAfterFn? FlippedLoss(ICombatState cs, Player me, MegaCrit.Sts2.Core.Models.Powers.SurroundedPower s)
    {
        if (FacingField == null) return null;
        var old = FacingField.GetValue(s);
        var turned = s.Facing == MegaCrit.Sts2.Core.Models.Powers.SurroundedPower.Direction.Right
            ? MegaCrit.Sts2.Core.Models.Powers.SurroundedPower.Direction.Left : MegaCrit.Sts2.Core.Models.Powers.SurroundedPower.Direction.Right;
        MemoPause();      // 临时改了朝向：期间不用、不记 Encode 的记忆（Memo.cs）
        try
        {
            FacingField.SetValue(s, turned);
            EndTurnPreview(cs, me, out _, out _, out var flip, out _, out _);
            // 格式 23：候选改了敌人的攻击时（mod）要按转身之后的朝向重算每段伤害（AtkPerHit 在调用时才算），调用期间再临时转过去
            if (V23 && flip != null)
            {
                var inner = flip;
                flip = (ex, skip, ostyEx, handEx, mod, gone, fx) =>
                {
                    if (mod == null) return inner(ex, skip, ostyEx, handEx, null, gone, fx);
                    var cur = FacingField.GetValue(s);
                    MemoPause();
                    try { FacingField.SetValue(s, turned); return inner(ex, skip, ostyEx, handEx, mod, gone, fx); }
                    finally { FacingField.SetValue(s, cur); MemoResume(); }
                };
            }
            return flip;
        }
        catch { return null; }
        finally { FacingField.SetValue(s, old); MemoResume(); }
    }

    /// <summary>这个候选做完之后玩家的朝向会不会和现在不同。打死目标且剩下的可打敌人都在同一侧时，最后面向剩下的那只；
    /// 否则对着背后的钳子出牌、用药水就转身。不选目标的（打全体、打随机）不转。</summary>
    bool SurroundTurns(Choice ch, List<Creature>? killed)
    {
        if (_surround is not { } s || ch.Kind is not (ActionKind.PlayCard or ActionKind.UsePotion) || ch.B is not Creature t || t.IsPlayer) return false;
        if (killed != null && killed.Contains(t))
        {
            var rest = Hittable(t.CombatState!).Where(c => !killed.Contains(c)).ToList();
            if (rest.Count > 0 && (rest.All(c => c.HasPower<MegaCrit.Sts2.Core.Models.Powers.BackAttackLeftPower>())
                                   || rest.All(c => c.HasPower<MegaCrit.Sts2.Core.Models.Powers.BackAttackRightPower>())))
                return Behind(s.Facing, rest[0]);
        }
        return Behind(s.Facing, t);
    }

    /// <summary>
    /// 回合结束预演（观测格式版本 10，2026-10-27 起）：把玩家心算的几步交给引擎。充能球的被动在玩家回合结束、敌人行动之前结算
    /// （闪电打一个随机敌人，玻璃打全体，冰霜给自己格挡；黑暗只攒激发值，等离子在回合开始给能量）。
    /// 敌人：16 回合结束时被动预计对它造成的伤害（随机打一个敌人的，只有一个可打的敌人时全打它，多个时按期望平分），17 扣掉之后还剩的生命 + 格挡，
    /// 18 用现有能量、手里能打的牌本回合最多还能对它打多少伤害（界面预览、按目标算，费用约束下取最大，X 费牌不计），19 本回合能不能杀掉它（17 ≤ 18）。
    /// 玩家：16 回合结束时充能球给的格挡（冰霜球被动；版本 12 起含吞噬暗影激发的冰霜球），17 现在结束回合预计掉多少血，
    /// 18 回合结束对自己造成的伤害与失去的生命合计，19 充能球以外回合结束得到的格挡。
    /// 17 =（被动打不死的敌人的意图总伤害 + 可格挡的自伤 − 当前格挡 − 16 − 19，不小于 0）+ 无视格挡的失去生命；版本 12 起分两段算：
    /// 吞噬暗影激发的冰霜格挡晚于手牌自伤到账，只能挡能力自伤和敌人的攻击。
    /// 版本 11：手牌里回合结束生效的牌（凋萎、烧伤、腐朽、感染、毒素按伤害，可被格挡；召唤、霉运按失去生命，悔恨为手牌张数，不可格挡），覆甲、奥利哈钢。
    /// 版本 12：补上能力与遗物在回合结束的效果（游戏代码里结果确定的那些）：
    ///   对敌人：冰雹风暴（有冰霜球时打全体）、炸弹（倒数到 1 时打全体）、吞噬暗影（回合结束激发最后的球若干次：闪电随机一个、玻璃全体、
    ///   黑暗打生命最低的，激发值含这回合被动攒的；雷霆给闪电激发再加伤害）、石历（第 7 回合打全体）、尖叫酒壶（手牌为空时打全体）、
    ///   格挡盾（回合结束时格挡不少于 10 就随机打一个）；
    ///   对自己：缠绕、瓦解、魔法炸弹（施加者活着时）按伤害，可被格挡；消亡按失去生命，不可格挡；
    ///   格挡：斗篷扣（手牌每张 1 点）、涟漪盆（本回合没打攻击牌）、假奥利哈钢；奥利哈钢改为按游戏的判定（回合结束前当前格挡为 0，版本 11 把冰霜、覆甲也算了进去）。
    /// 敌人回合开始时才结算的效果（毒等）不算。
    /// 版本 13 修正：17 与最坏情况掉血按段结算，每一次伤害或失去生命分别过无实体、格挡、钨棒、缓冲，敌人的多段攻击逐段算。
    /// </summary>
    internal static Dictionary<Creature, (float a, float b, float c, float d)> EndTurnPreview(ICombatState cs, Player me, out float worstLoss, out float luckyKills,
                                                                                    out LossAfterFn? lossAfter, out Creature? devourer, out Dictionary<Creature, float>? sureHp,
                                                                                    bool noExtraTurn = false, Dictionary<Creature, (List<float> hits, int orbN)>? sureHitsOut = null)
    {
        var res = new Dictionary<Creature, (float, float, float, float)>();
        worstLoss = 0; luckyKills = 0; lossAfter = null; devourer = null; sureHp = null;
        var sureOut = V23 || F113 ? new Dictionary<Creature, float>() : null;      // 第七轮（F113）：所有格式都记（Fx23 与候选的“打残之后回合末被动打死”用）
        Creature? pit = null;
        LossAfterFn? after = null;
        Et29 et29 = default;      // 格式 29：主调用（期望口径）的拆分输出，末尾写进 Et29Last（V29Common.cs）
        try
        {
            var pcs = me.PlayerCombatState!;
            var foes = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
            // v0.113.0 修正（F113）：傻瓜回合末自动打出的牌（确定的才算，见 SimpletonPlays；它们不再留在手里）；瓦皮在格挡之前替玩家挨有力攻击（见 WuppyOf）
            var auto = F113 && me.Creature.GetPower<MegaCrit.Sts2.Core.Models.Powers.SimpletonPower>() is { } simp ? SimpletonPlays(pcs, simp.Amount) : new List<CardModel>();
            var wuppy = F113 ? WuppyOf(cs, me) : null;
            float wup0 = wuppy?.CurrentHp ?? 0, wupLast = 0;
            var hittable = foes.Where(c => IsHittableM(c)).ToList();
            var passive = foes.ToDictionary(c => c, _ => 0f);
            // 版本 13：sure 只记目标确定的伤害（全体、打生命最低的、只有一个可打的敌人时的随机目标），随机目标按最坏情况（一点也没打到它）算
            var sure = foes.ToDictionary(c => c, _ => 0f);
            // F113 第四轮：确定的被动每一段各记一笔（滑溜按段压成 1）
            var sureHits = foes.ToDictionary(c => c, _ => new List<float>());
            // 格式 29（G23）：回合末被动伤害打到挂着无实体的敌人时每一段先压到 1（IntangiblePower.ModifyDamageCap 先于格挡）；格式 ≤ 28 原样
            float Cap29(Creature f, float v) => V29 && f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.IntangiblePower>() > 0 ? Math.Min(v, 1) : v;
            void All(float v) { foreach (var f in hittable) { float d = Cap29(f, v); passive[f] += d; sure[f] += d; sureHits[f].Add(d); } }
            void Spread(float v) { foreach (var f in hittable) { float d = Cap29(f, v); passive[f] += d / hittable.Count; if (hittable.Count == 1) { sure[f] += d; sureHits[f].Add(d); } } }
            // 格式 29：每个敌人数值不同的一段（过 HitDmg 的势不可当、狱火、践踏、彼岸嚎叫）：打全体（AllBy）、打随机一个（SpreadBy，只剩一个可攻击敌人时确定）
            void AllBy(Func<Creature, float> v) { foreach (var f in hittable) { float d = Cap29(f, v(f)); passive[f] += d; sure[f] += d; sureHits[f].Add(d); } }
            void SpreadBy(Func<Creature, float> v) { foreach (var f in hittable) { float d = Cap29(f, v(f)); passive[f] += d / hittable.Count; if (hittable.Count == 1) { sure[f] += d; sureHits[f].Add(d); } } }
            static float Var(MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVarSet vars, string name) => vars.TryGetValue(name, out var v) ? (float)v.BaseValue : 0;
            float frost = 0;
            int juggN = 0;      // 格式 29（G12）：回合末每一次到账的格挡（> 0）各让势不可当打一次随机敌人，这里数次数
            var orbs = pcs.OrbQueue.Orbs.ToList();
            // 版本 15 修正：每个球的被动按游戏实际的结算次数（OrbModel.TriggerPassive 经 Hook.ModifyOrbPassiveTriggerCount，镀金缆线让最前面的球多一次），
            // 每次分别结算：闪电每次随机打一个，玻璃每次打全体、打完基础值减 1，黑暗每次把被动值加进激发值（吞噬暗影激发时用）
            var trig = orbs.ToDictionary(o => o, o => PassiveTriggers(cs, o));
            foreach (var orb in orbs)
            {
                float v = (float)orb.PassiveVal;
                for (int t = 0; t < trig[orb]; t++)
                    switch (orb)
                    {
                        case MegaCrit.Sts2.Core.Models.Orbs.LightningOrb: Spread(v); break;
                        case MegaCrit.Sts2.Core.Models.Orbs.GlassOrb: if (v - t > 0) All(v - t); break;
                        case MegaCrit.Sts2.Core.Models.Orbs.FrostOrb: frost += v; if (v > 0) juggN++; break;
                    }
            }
            var orbN = sureHits.ToDictionary(kv => kv.Key, kv => kv.Value.Count);      // 第七轮：每个敌人的确定被动里，前 orbN 段来自充能球
            // 版本 11 起：手牌里回合结束生效的牌（可格挡的伤害 selfDmg、不可格挡的失去生命 selfLoss），充能球以外的回合结束格挡 extra
            float selfDmg = 0, selfLoss = 0, extra = 0;
            // 按段结算（掉血预演用）：手牌、能力的每一次自伤（数值，能否被格挡），按结算顺序
            // （F113：手牌的每一段记下是哪张牌，候选打出它时去掉；见 LossAfterFn 的 gone）
            // 格式 29（G27）：lateSeq 每一项记下施加者（缠绕、魔法炸弹；其余为 null），候选打死施加者时这一项随之移除
            var handSeq = new List<(float v, bool blockable, CardModel? card)>(); var lateSeq = new List<(float v, bool blockable, Creature? src)>();
            // v0.113.0 修正（F113）：敌方回合之后、下一个决策之前的自伤（下个玩家回合开始时的赤红斗篷、炼狱），在敌人的攻击之后结算
            var postSeq = new List<(float v, bool blockable)>();
            HandShoved = 0;
            foreach (var card in pcs.Hand.Cards)
            {
                try
                {
                    if (!card.HasTurnEndInHandEffect || auto.Contains(card)) continue;
                    if (card is MegaCrit.Sts2.Core.Models.Cards.Regret) { int hn = pcs.Hand.Cards.Count - auto.Count; selfLoss += hn; handSeq.Add((hn, false, card)); continue; }
                    float hpLoss = -1, dmg = -1;
                    foreach (var (name, v) in card.DynamicVars)
                    {
                        if (name == "HpLoss") hpLoss = (float)v.BaseValue;
                        else if (name == "Damage") dmg = (float)v.BaseValue;
                    }
                    if (hpLoss >= 0) { selfLoss += hpLoss; handSeq.Add((hpLoss, false, card)); } else if (dmg >= 0) { selfDmg += dmg; handSeq.Add((dmg, true, card)); }
                }
                catch { }
            }
            float handDmg = selfDmg, frostPassive = frost;     // 格挡盾判定用：手牌的可格挡自伤，吞噬暗影激发之前的冰霜格挡
            // 版本 12：能力（覆甲以外的为新加）
            int frostOrbs = orbs.Count(o => o is MegaCrit.Sts2.Core.Models.Orbs.FrostOrb);
            float thunder = 0, shadow = 0, tmpFocus = 0, shadowFocusDrop = 0;
            int buffer = 0; bool intangible = false;
            foreach (var pw in me.Creature.Powers)
            {
                try
                {
                    switch (pw)
                    {
                        case MegaCrit.Sts2.Core.Models.Powers.PlatingPower: extra += pw.Amount; if (pw.Amount > 0) juggN++; break;
                        case MegaCrit.Sts2.Core.Models.Powers.HailstormPower when frostOrbs >= Var(pw.DynamicVars, "FrostOrbs"): All(pw.Amount); break;
                        case MegaCrit.Sts2.Core.Models.Powers.TheBombPower when pw.Amount <= 1: All(Var(pw.DynamicVars, "Damage")); break;
                        case MegaCrit.Sts2.Core.Models.Powers.ThunderPower: thunder += pw.Amount; break;
                        // 版本 17（第十五份评审）：回合结束后按能力列表的先后结算，排在吞噬暗影前面的临时集中（热修复等）先撤掉，激发时的集中少这么多
                        case MegaCrit.Sts2.Core.Models.Powers.TemporaryFocusPower: tmpFocus += pw.Amount; break;
                        case MegaCrit.Sts2.Core.Models.Powers.ConsumingShadowPower: shadow += pw.Amount; shadowFocusDrop = tmpFocus; break;
                        case MegaCrit.Sts2.Core.Models.Powers.ConstrictPower: selfDmg += pw.Amount; lateSeq.Add((pw.Amount, true, pw.Applier)); break;
                        case MegaCrit.Sts2.Core.Models.Powers.DisintegrationPower: selfDmg += pw.Amount; lateSeq.Add((pw.Amount, true, null)); break;
                        case MegaCrit.Sts2.Core.Models.Powers.MagicBombPower when pw.Applier is { IsDead: false }: selfDmg += pw.Amount; lateSeq.Add((pw.Amount, true, pw.Applier)); break;
                        case MegaCrit.Sts2.Core.Models.Powers.DemisePower: selfLoss += pw.Amount; lateSeq.Add((pw.Amount, false, null)); break;
                        // v0.113.0 修正（F113）：赤红斗篷、炼狱在下个玩家回合开始时（AfterPlayerTurnStart，下一个决策之前）对自己造成 SelfDamage 点
                        // Unblockable | Unpowered 伤害（每打出一次同名牌加 1）。算进玩家 17 与候选 10，不进 18（18 只管回合结束时的自伤）
                        case MegaCrit.Sts2.Core.Models.Powers.CrimsonMantlePower or MegaCrit.Sts2.Core.Models.Powers.InfernoPower when F113:
                            { float v = Var(pw.DynamicVars, "SelfDamage"); if (v > 0) postSeq.Add((v, false)); }
                            break;
                        case MegaCrit.Sts2.Core.Models.Powers.BufferPower: buffer += pw.Amount; break;
                        case MegaCrit.Sts2.Core.Models.Powers.IntangiblePower when pw.Amount > 0: intangible = true; break;
                    }
                }
                catch { }
            }
            // 吞噬暗影：被动结算之后从最后一个球起激发（黑暗的激发值先加上这回合的被动）
            for (int k = orbs.Count - 1, n = 0; k >= 0 && n < shadow; k--, n++)
            {
                var orb = orbs[k];
                switch (orb)
                {
                    case MegaCrit.Sts2.Core.Models.Orbs.LightningOrb: Spread(Math.Max(0, (float)orb.EvokeVal - shadowFocusDrop) + thunder); break;
                    case MegaCrit.Sts2.Core.Models.Orbs.GlassOrb: All(2 * Math.Max(0, (float)orb.PassiveVal - trig[orb])); break;     // 版本 15：被动结算后基础值少了，激发值为两倍
                    case MegaCrit.Sts2.Core.Models.Orbs.FrostOrb: { float fv = Math.Max(0, (float)orb.EvokeVal - shadowFocusDrop); frost += fv; if (fv > 0) juggN++; } break;
                    case MegaCrit.Sts2.Core.Models.Orbs.DarkOrb when hittable.Count > 0:
                        var low = hittable.MinBy(c => c.CurrentHp)!;
                        float dv = Cap29(low, (float)orb.EvokeVal + trig[orb] * (float)orb.PassiveVal);
                        passive[low] += dv; sure[low] += dv; sureHits[low].Add(dv); break;
                }
            }
            // 遗物（奥利哈钢以外的为新加）
            int hand = pcs.Hand.Cards.Count - auto.Count;
            bool attacked = CombatManager.Instance.History.CardPlaysFinished.Any(e => e.HappenedThisTurn(cs) && e.CardPlay.Card.Type == CardType.Attack && e.CardPlay.Card.Owner == me);
            float parry = 0, parryDmg = 0, rod = 0;
            // F113 第四轮：回合末遗物里取决于“这回合做了什么”的部分单独记下，候选改了条件时扣掉（见 CandFx）
            float oriBlock = 0, rippleBlock = 0, claspPer = 0, blockMul = 1;
            foreach (var r in me.Relics)
            {
                // 版本 14 修正：已融化的蜡制遗物、失效的遗物不再触发（游戏的触发流程跳过已融化的遗物）
                if (r.IsMelted || r.Status == MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Disabled) continue;
                try
                {
                    switch (r)
                    {
                        case MegaCrit.Sts2.Core.Models.Relics.Orichalcum or MegaCrit.Sts2.Core.Models.Relics.FakeOrichalcum when me.Creature.Block <= 0:
                            { float v = Var(r.DynamicVars, "Block"); extra += v; oriBlock += v; if (v > 0) juggN++; }
                            break;
                        case MegaCrit.Sts2.Core.Models.Relics.CloakClasp: extra += hand * Var(r.DynamicVars, "Block"); claspPer += Var(r.DynamicVars, "Block"); if (hand > 0) juggN++; break;
                        case MegaCrit.Sts2.Core.Models.Relics.RippleBasin when !attacked: { float v = Var(r.DynamicVars, "Block"); extra += v; rippleBlock += v; if (v > 0) juggN++; } break;
                        case MegaCrit.Sts2.Core.Models.Relics.ScreamingFlagon when hand == 0: All(Var(r.DynamicVars, "Damage")); break;
                        case MegaCrit.Sts2.Core.Models.Relics.StoneCalendar when pcs.TurnNumber == (int)Var(r.DynamicVars, "DamageTurn"): All(Var(r.DynamicVars, "Damage")); break;
                        case MegaCrit.Sts2.Core.Models.Relics.TungstenRod: rod += Var(r.DynamicVars, "HpLossReduction"); break;
                        case MegaCrit.Sts2.Core.Models.Relics.ParryingShield: parry = Var(r.DynamicVars, "Block"); parryDmg = Var(r.DynamicVars, "Damage"); break;
                        // v0.113.0 新遗物黑曜石钳子：回合结束前对随机一个可攻击的敌人造成“手牌张数 × 1”点无力伤害（ObsidianVice.BeforeSideTurnEnd），同闪电球按随机均摊
                        case MegaCrit.Sts2.Core.Models.Relics.ObsidianVice when hand > 0: Spread(hand * Var(r.DynamicVars, "Damage")); break;
                    }
                }
                catch { }
            }
            // 格式 22：虚无牌在回合结束时消耗（CombatManager.DoTurnEnd：充能球被动之后、手牌的回合结束效果之前），
            // 每消耗一张，无惧疼痛给格挡（算进回合结束前的格挡），卡戎之灰打全体
            // 格式 29（G11）：回合末自动打出（AutoPostPlay 阶段，虚无牌消耗之前，Combat/PlayerTurnPhase.cs）：践踏从手里随机打攻击牌、消耗堆里的彼岸嚎叫打全体；
            // 狂怒每次打出攻击给格挡。stampGone 为确定被践踏打出、离开手牌的牌（不再算它们的回合末手牌效果与虚无消耗）
            List<CardModel>? stampGone = null;
            if (V29) stampGone = AutoPostPlay29(cs, me, pcs, hittable, auto, ref extra, ref juggN, passive, sure, sureHits, Cap29);
            if (stampGone != null && stampGone.Count > 0) handSeq.RemoveAll(x => x.card != null && stampGone.Contains(x.card));
            // 格式 29（G02）：虚无消耗另记下计入的牌与每张的无惧疼痛格挡（候选打出、离手的虚无牌回合末不再给格挡，见 FxBlock）；卡戎之灰逐张各打一段
            List<CardModel>? ethSet = null; float fnpPer = 0;
            if (V29)
            {
                var ex29 = new List<CardModel>(auto);
                if (stampGone != null) ex29.AddRange(stampGone);
                (ethSet, fnpPer) = EtherealExhaust29(cs, me, ref extra, All, ex29);
                if (fnpPer > 0) juggN += ethSet.Count;
            }
            else if (V22) EtherealExhaust(cs, me, ref extra, All);
            // 格式 20：已知在抽牌堆顶的“我无敌”在回合结束时自动打出（IAmInvincible.AfterAutoPostPlayPhaseEntered，回合结束效果之前），格挡算进回合结束前的格挡。
            // 只看已知的顶部：碰巧洗到顶部、玩家不知道的不算
            if (V20 && DrawTop.Known(pcs.DrawPile) > 0 && pcs.DrawPile.Cards[0] is MegaCrit.Sts2.Core.Models.Cards.IAmInvincible inv)
            {
                var (_, ib, _) = Preview(inv, null);
                if (ib > 0) { int ipc = PlayCount(inv, null); extra += ib * ipc; juggN += ipc; }
            }
            // v0.113.0 修正（F113）：傻瓜自动打出的牌（回合结束效果之前）：格挡算进回合结束前的格挡（啾 四肢等按 BlockGains）；伤害打全体的逐个敌人算，
            // 单体的按随机目标（只有一个可打的敌人时确定，多个时按期望平分，同闪电球）
            foreach (var ac in auto)
            {
                try
                {
                    int apc = PlayCount(ac, null);
                    var (_, ab, _) = Preview(ac, null);
                    if (ab > 0) { extra += ab * apc * BlockGains(ac); juggN += apc; }
                    if (ac.Type == CardType.Attack && hittable.Count > 0)
                    {
                        bool aoe = ac.TargetType == TargetType.AllEnemies;
                        foreach (var f in hittable)
                        {
                            var (ad, _, ah) = Preview(ac, f);
                            if (ad <= 0) continue;
                            ad = Cap29(f, ad);      // 格式 29（G23）：无实体的敌人每段压到 1
                            float v = ad * ah * apc;
                            if (aoe || hittable.Count == 1) { passive[f] += v; sure[f] += v; for (int q = 0; q < (int)(ah * apc); q++) sureHits[f].Add(ad); } else passive[f] += v / hittable.Count;
                        }
                        Preview(ac, null);
                    }
                }
                catch { }
            }
            // v0.113.0 修正第四轮（F113）：影融（ShadowmeldPower.ModifyBlockMultiplicative：所有格挡 × 2^层数，玩家回合结束之后才撤）让回合末到账的格挡
            // （覆甲、奥利哈钢、斗篷扣、涟漪盆、冰霜被动与激发等）也翻倍；玩家 16、19 跟着变
            if (F113 && me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.ShadowmeldPower>() is var smN && smN > 0)
            { blockMul = (float)Math.Pow(2, smN); extra *= blockMul; frost *= blockMul; frostPassive *= blockMul; }
            // 格挡盾在回合结束之后（弃牌时）才看格挡：这时回合结束的格挡都已到账，手牌的回合结束自伤已先扣掉一部分格挡
            if (parry > 0 && me.Creature.Block + frostPassive + extra - handDmg >= parry) Spread(parryDmg);
            // 版本 15：现在结束回合会多一个回合（Hook.ShouldTakeExtraTurn：佩尔之眼在本回合一张牌都没打时、龙涎香等）：敌人这回合不行动；
            // 佩尔之眼在回合结束前先消耗全部手牌，手牌的回合结束效果不发生
            bool extraTurn = false;
            // v0.113.0：Hook.ShouldTakeExtraTurn 加了 out source（先问各监听者的 ShouldTakeExtraTurnEarly，再问 ShouldTakeExtraTurn）；
            // 佩尔之眼改成在 ShouldTakeExtraTurnEarly 里判断（原来的 pe.ShouldTakeExtraTurn 恒为 false），龙涎香的能力删了、换成通用的 ExtraTurnPower。
            // 是不是佩尔之眼直接看 source
            object? extraSrc = null;
            try { extraTurn = MegaCrit.Sts2.Core.Hooks.Hook.ShouldTakeExtraTurn(cs, me, out var src); extraSrc = src; } catch { }
            if (noExtraTurn && extraSrc is MegaCrit.Sts2.Core.Models.Relics.PaelsEye) extraTurn = false;      // F113：打出一张牌之后佩尔之眼不再给额外回合
            if (!noExtraTurn) PaelsExtraTurn = F113 && extraTurn && extraSrc is MegaCrit.Sts2.Core.Models.Relics.PaelsEye;
            if (extraTurn && extraSrc is MegaCrit.Sts2.Core.Models.Relics.PaelsEye) handSeq.Clear();
            // 格式 29：玩家一侧掉血的几项，Loss29 与下面的狱火模拟共用。律动残余（G03）：从现在到下个玩家回合开始还能掉多少（remLeft），
            // 下个玩家回合开始前额度清零（BeatingRemnant.BeforeSideTurnStart），之后按 remCap 重新起算；钨棒与律动残余同在掉血钩子 AfterOsty 段，
            // 按遗物顺序先后结算（rodFirst），缓冲在 AfterOstyLate 段、在两者之后。恶魔之舌（G32）：本回合还没触发时，玩家一方回合里第一次掉血回同样多的血
            float remLeft = float.PositiveInfinity, remCap = float.PositiveInfinity; bool rodFirst = true, tongueLive = false, tongueFresh = false;
            if (V29)
            {
                try
                {
                    remLeft = RemnantLeft(me);
                    int iRod = -1, iRem = -1;
                    for (int i = 0; i < me.Relics.Count; i++)
                    {
                        var r = me.Relics[i];
                        if (!RelicLive(r)) continue;
                        if (r is MegaCrit.Sts2.Core.Models.Relics.TungstenRod) { if (iRod < 0) iRod = i; }
                        else if (r is MegaCrit.Sts2.Core.Models.Relics.BeatingRemnant) { if (iRem < 0) { iRem = i; remCap = (float)r.DynamicVars["MaxHpLoss"].BaseValue; } }
                        else if (r is MegaCrit.Sts2.Core.Models.Relics.DemonTongue) { tongueLive = true; tongueFresh = !UsedThisTurn(r); }
                    }
                    rodFirst = iRod < 0 || iRem < 0 || iRod < iRem;
                    if (float.IsPositiveInfinity(remLeft)) remCap = float.PositiveInfinity;
                }
                catch { remLeft = remCap = float.PositiveInfinity; }
                // G12：势不可当（JuggernautPower.AfterBlockGained：每次获得 > 0 的格挡，对随机一个可攻击的敌人造成层数点无力伤害）。回合末到账的每一次格挡（juggN）各打一次
                int jugg = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.JuggernautPower>();
                if (jugg > 0 && hittable.Count > 0)
                    for (int q = 0; q < juggN; q++) SpreadBy(f => HitDmg(cs, f, me.Creature, jugg, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null));
                // G32：狱火（InfernoPower.AfterDamageReceived：玩家一方回合里每次真正掉血，对全体可攻击的敌人造成层数点无力伤害）。按回合末格挡模拟手牌自伤与能力自伤（handSeq、lateSeq）
                int inf = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.InfernoPower>();
                if (inf > 0 && hittable.Count > 0)
                {
                    float blk = me.Creature.Block + frostPassive + extra, rem = remLeft; int bf = buffer, nInf = 0;
                    void Sim(float d, bool bl)
                    {
                        if (intangible) d = Math.Min(d, 1);
                        if (bl) { float b = Math.Min(blk, d); blk -= b; d -= b; }
                        d = RodRem29(d, rod, rem, rodFirst);
                        if (d <= 0) return;
                        if (bf > 0) { bf--; return; }
                        rem -= d; nInf++;
                    }
                    foreach (var (v, bl, _) in handSeq) Sim(v, bl);
                    blk += frost - frostPassive;
                    foreach (var (v, bl, _) in lateSeq) Sim(v, bl);
                    for (int q = 0; q < nInf; q++) AllBy(f => HitDmg(cs, f, me.Creature, inf, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null));
                }
            }
            // 手牌在现有能量下对每个敌人最多能打多少：0–1 背包（手牌最多十来张，能量很少，直接按能量做动态规划）
            int energy = Math.Max(0, pcs.Energy);
            // F113：异鸟猛击只施加能力、下个你的回合开始才打全体（GodByrdPower），卡面的伤害不是本回合的，不进背包
            var playable = pcs.Hand.Cards.Where(c => { try { return CanPlayM(c) && !c.EnergyCost.CostsX && !(V20 && c.HasStarCostX) && !(F113 && c is MegaCrit.Sts2.Core.Models.Cards.GodByrd); } catch { return false; } }).ToList();
            // 格式 20：星费也是约束，按（能量，星）二维背包；星 X 费同能量 X 费不计。星数封顶 40，够用
            int stars = V20 ? Math.Clamp(pcs.Stars, 0, 40) : 0;
            var incoming = new List<(float v, int n)>(); var incomingWorst = new List<(float v, int n)>();
            var worstBy = new List<(Creature f, (float v, int n) a)>();     // 版本 19：最坏情况的每次攻击记下是谁打的（候选打死它时去掉）
            var incomingBy = new List<(Creature f, (float v, int n) a)>();  // 格式 22：期望情况也记下是谁打的（反伤打死攻击者）
            var worstAtk = new List<AttackIntent>();                          // 格式 23：worstBy 每一项是哪个攻击意图（候选改了这个敌人的攻击时重算）
            var toughExp = new Dictionary<Creature, float>(); var toughWorst = new Dictionary<Creature, float>();   // 格式 22：轮到它出手时的生命 + 格挡
            // 格式 29（G04、G29）：第一遍记下每个敌人（按出手顺序）出不出手（outer：被动打不死且没有额外回合；w、e：最坏、期望口径下也没被毒死、眩晕）、
            // 死没死（deadW、deadE：被动打死或毒死）与这一招塞进手里的牌数；第二遍再加攻击意图
            var pend29 = new List<(Creature f, bool outer, bool w, bool e, bool deadW, bool deadE)>();
            var shove29 = new Dictionary<Creature, int>();
            var worstOrig = new List<float>(); var worstAlly = new List<float>();     // 格式 29：worstBy 每一项的原始每段伤害、算进去的队友加力量
            var actorsW = new List<(Creature f, float gain)>();                        // 格式 29（G29）：最坏口径下会出手、给队友加力量的敌人（按出手顺序）
            var foeIdx = new Dictionary<Creature, int>();
            for (int i = 0; i < foes.Count; i++) foeIdx[foes[i]] = i;
            // v0.113.0 修正（F113）：射击牌每张占一支箭，最多用可用箭数张（背包多一维；拔回的箭不算，偏保守）。瞄准只加在下一次箭攻击上，箭通常只有一支，大多数情况下也就只算一次
            bool arrowDim = F113 && playable.Any(IsShoot);
            int arrowsCap = arrowDim ? Math.Min(pcs.AvailableArrowCount, playable.Count(IsShoot)) : 0;
            // v0.113.0 修正第四轮（F113）：每回合出牌张数上限：懒惰（SlothPower.ShouldPlay：本回合已打 DisplayAmount 张，满 Amount 张就不能再打；
            // 恐惧模式知识恶魔统治的二选一、标准模式知识恶魔 Boss 的诅咒）、天鹅绒颈圈（6 张，VelvetChoker.DisplayAmount 为本回合已打张数）、
            // 安可（EncorePower.ShouldPlay：挂上之后最多再打 PlaysRemaining = DisplayAmount 张）。背包多一维张数。
            // 女王的束缚之链（ChainsOfBindingPower：挂“束缚”苦痛的牌每回合只能打 1 张；打过之后 CanPlay 已为假）：背包里束缚牌最多选 1 张
            int kcap = int.MaxValue;
            bool chains = F113 && me.Creature.HasPower<MegaCrit.Sts2.Core.Models.Powers.ChainsOfBindingPower>();
            if (F113)
            {
                foreach (var pw in me.Creature.Powers)
                {
                    if (pw is MegaCrit.Sts2.Core.Models.Powers.SlothPower) kcap = Math.Min(kcap, Math.Max(0, pw.Amount - pw.DisplayAmount));
                    else if (pw is MegaCrit.Sts2.Core.Models.Powers.EncorePower) kcap = Math.Min(kcap, Math.Max(0, pw.DisplayAmount));
                }
                foreach (var r in me.Relics)
                    if (r is MegaCrit.Sts2.Core.Models.Relics.VelvetChoker && !r.IsMelted && r.Status != MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Disabled)
                        kcap = Math.Min(kcap, Math.Max(0, r.DynamicVars.Cards.IntValue - r.DisplayAmount));
            }
            // v0.113.0 修正第五轮（F113，观测独立核查.md 第十二节）：背包的四个约束，出现时改用通用背包 KnapX（没出现时走下面原来的背包，结果不变）：
            //   免费出牌（FreeCardPower 等：TryModifyEnergyCostInCombatLate 让所有合格的牌都是 0 费，但 BeforeCardPlayed 每打一张扣一层）：只有层数张免费，
            //     其余按 CostNoFree 的费用；
            //   叽 诗句（KiVersePower.ModifyCardPlayCount：旋律牌多打一次，AfterModifyingCardPlayCount 每张扣一层）：只有层数张旋律牌多打一次；
            //   瞄准、寂灭只加在第一次箭攻击上（PdNoAim）：最多一张射击牌吃到，它自己重放的第二次起也没有；
            //   召回、拧下、连根拔起拔回目标身上你的箭（ArrowCmd.DislodgeArrow），拔回的箭能再射：箭数一维允许“负的占用”（按先拔后射）
            int kvN = 0, freeMax = 0; bool aimOn = false;
            var kInfo = new Dictionary<CardModel, (int cost, int costNF, int free, int sc, int recall)>(ReferenceEqualityComparer.Instance);
            if (F113)
            {
                kvN = Math.Clamp(me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.KiVersePower>(), 0, 3);
                aimOn = me.Creature.HasPower<MegaCrit.Sts2.Core.Models.Powers.AimPower>() || me.Creature.HasPower<MegaCrit.Sts2.Core.Models.Powers.QuietusPower>();
                foreach (var card in playable)
                {
                    try
                    {
                        int cost = Math.Max(0, CostAll(card));
                        int nf = CostNoFree(card), fl = nf > cost ? FreeLayersFor(me, card) : 0;
                        int sc = V20 ? Math.Max(0, StarCost(card)) : 0;
                        int rc = card is MegaCrit.Sts2.Core.Models.Cards.Recall or MegaCrit.Sts2.Core.Models.Cards.Wrest or MegaCrit.Sts2.Core.Models.Cards.Uproot
                            ? foes.Select(x => x.GetPowerInstances<MegaCrit.Sts2.Core.Models.Powers.ImpaledPower>().Where(p => p.Applier == me.Creature).Sum(p => p.Arrows.Count)).DefaultIfEmpty(0).Max() : 0;
                        kInfo[card] = (cost, fl > 0 ? nf : cost, fl, sc, rc);
                        freeMax = Math.Max(freeMax, fl);
                    }
                    catch { }
                }
            }
            bool kRecall = arrowDim && kInfo.Values.Any(x => x.recall > 0);
            bool kSpecial = F113 && (freeMax > 0 || (kvN > 0 && playable.Any(IsMelody)) || aimOn && playable.Any(IsShoot) || kRecall);
            foreach (var f in foes)
            {
                var dp = new float[energy + 1];
                var dp2 = V20 ? new float[energy + 1, stars + 1] : null;
                var dp3 = arrowDim ? new float[energy + 1, stars + 1, arrowsCap + 1] : null;
                var items = kcap < int.MaxValue || chains ? new List<(int c, int s, int a, float d, bool b)>() : null;
                float minPd = float.MaxValue;      // 滑溜用：能打这个敌人的牌里最小的单段伤害
                foreach (var card in playable)
                {
                    var (pd, _, hits) = Preview(card, f);
                    if (pd <= 0) continue;
                    int cost;
                    try { cost = Math.Max(0, CostAll(card)); } catch { continue; }
                    if (cost > energy) continue;
                    float dmg = pd * hits * PlayCount(card, f);
                    minPd = Math.Min(minPd, pd);
                    if (items != null)
                    {
                        int sc0 = 0;
                        try { sc0 = V20 ? Math.Max(0, StarCost(card)) : 0; } catch { }
                        items.Add((cost, sc0, arrowDim && IsShoot(card) ? 1 : 0, dmg, chains && card.Affliction is MegaCrit.Sts2.Core.Models.Afflictions.Bound));
                    }
                    if (dp3 != null)
                    {
                        int sc, ac = IsShoot(card) ? 1 : 0;
                        try { sc = Math.Max(0, StarCost(card)); } catch { continue; }
                        if (sc > stars || ac > arrowsCap) continue;
                        for (int e = energy; e >= cost; e--) for (int t = stars; t >= sc; t--) for (int a = arrowsCap; a >= ac; a--)
                            dp3[e, t, a] = Math.Max(dp3[e, t, a], dp3[e - cost, t - sc, a - ac] + dmg);
                    }
                    else if (dp2 != null)
                    {
                        int sc;
                        try { sc = Math.Max(0, StarCost(card)); } catch { continue; }
                        if (sc > stars) continue;
                        for (int e = energy; e >= cost; e--) for (int t = stars; t >= sc; t--) dp2[e, t] = Math.Max(dp2[e, t], dp2[e - cost, t - sc] + dmg);
                    }
                    else for (int e = energy; e >= cost; e--) dp[e] = Math.Max(dp[e], dp[e - cost] + dmg);
                }
                float best = dp3 != null ? dp3[energy, stars, arrowsCap] : dp2 != null ? dp2[energy, stars] : dp[energy];
                if (items != null && (items.Count > kcap || items.Count(x => x.b) > 1))
                {
                    // 张数维 K（不限张数时取牌数）；束缚牌先不进背包，最后最多补一张
                    int K = Math.Min(kcap, items.Count);
                    var d4 = new float[energy + 1, stars + 1, arrowsCap + 1, K + 1];
                    foreach (var (c0, s0, a0, v0, b0) in items)
                    {
                        if (b0 || c0 > energy || s0 > stars || a0 > arrowsCap) continue;
                        for (int e = energy; e >= c0; e--) for (int t = stars; t >= s0; t--) for (int a = arrowsCap; a >= a0; a--) for (int k = K; k >= 1; k--)
                            d4[e, t, a, k] = Math.Max(d4[e, t, a, k], d4[e - c0, t - s0, a - a0, k - 1] + v0);
                    }
                    float bb = d4[energy, stars, arrowsCap, K];
                    foreach (var (c0, s0, a0, v0, b0) in items)
                        if (b0 && K >= 1 && c0 <= energy && s0 <= stars && a0 <= arrowsCap)
                            bb = Math.Max(bb, d4[energy - c0, stars - s0, arrowsCap - a0, K - 1] + v0);
                    best = bb;
                }
                if (kSpecial)
                {
                    float kb = KnapForFoe(f, playable, kInfo, me, energy, stars, arrowDim, kRecall, pcs.AvailableArrowCount, freeMax, kvN, aimOn, kcap, chains);
                    if (kb >= 0) best = kb;
                }
                if (V29) { float k29 = Knap29(cs, me, f, pcs, energy, stars); if (k29 >= 0) best = k29; }     // 格式 29 的背包（包 2，Knap29.cs）
                // 版本 15 修正：硬化外壳限制本回合最多掉多少血（同回合的出牌与回合结束的被动共用额度）：被动伤害、还差多少、手牌最多能打多少都按额度封顶
                float cap = ShellLeft(f);
                // v0.113.0 修正第四轮（F113）：滑溜（SlipperyPower：每次 ≥ 1 的掉血压成 1，掉一次扣一层；恐惧模式万托姆统治下全幕都有）。
                // 确定的被动逐段算（slipCut 为少掉的血，期望口径同样扣掉）；手牌这回合先出，滑溜先被牌吃掉：18 扣掉 层数 ×（最小单段 − 1），19 按没扣的被动比
                int slip = F113 ? f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.SlipperyPower>() : 0;
                float slipCut = 0;
                if (slip > 0)
                {
                    float blk = f.Block, tot = 0; int sl = slip;
                    foreach (var v in sureHits[f]) { float d = v, b = Math.Min(blk, d); blk -= b; d -= b; if (d >= 1 && sl > 0) { d = 1; sl--; } tot += Math.Max(0, d); }
                    slipCut = Math.Max(0, Math.Max(0, sure[f] - f.Block) - tot);
                }
                float hpP = Math.Min(Math.Max(0, passive[f] - f.Block - slipCut), cap), blockLeft = Math.Max(0, f.Block - passive[f]);
                float left = hpP >= f.CurrentHp ? 0 : f.CurrentHp - hpP + blockLeft;
                float most = Math.Min(best, blockLeft + Math.Max(0, cap - hpP));
                float leftCmp = left;
                if (slip > 0 && best > 0)
                {
                    float hpPraw = Math.Min(Math.Max(0, passive[f] - f.Block), cap);
                    leftCmp = hpPraw >= f.CurrentHp ? 0 : f.CurrentHp - hpPraw + blockLeft;
                    most = Math.Max(0, Math.Min(best - slip * Math.Max(0, (minPd == float.MaxValue ? 1 : minPd) - 1), blockLeft + Math.Max(0, cap - hpPraw)));
                }
                res[f] = (Math.Min(passive[f], f.Block + cap), left, most, leftCmp <= most ? 1 : 0);
                float hpS = Math.Min(Math.Max(0, sure[f] - f.Block - slipCut), cap);
                bool sureLeft = hpS < f.CurrentHp;
                if (left <= 0 && sureLeft) luckyKills++;
                // 版本 15：毒在敌方回合开始时先结算（PoisonPower.AfterSideTurnStart，怪物行动之前；硬化外壳的额度在新回合重置），毒得死就不会出手
                float pois = 0;
                // 格式 23：按 PoisonNext（血条预告再补上滑溜、缓冲、硬化外壳的掉血修正），与敌人中毒记录 12–13 一致
                foreach (var pp in f.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.PoisonPower>()) { try { pois += V23 ? PoisonNext(f, pp) : pp.CalculateTotalDamageNextTurn(); } catch { } }
                if (sureOut != null) sureOut[f] = hpS;
                if (sureHitsOut != null && slip <= 0) sureHitsOut[f] = (sureHits[f], orbN[f]);
                float capNext = f.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.HardenedShellPower>().Select(x => (float)x.Amount).DefaultIfEmpty(float.MaxValue).Min();
                bool poisonKills(float hpLost) => pois > 0 && Math.Min(pois, capNext) >= f.CurrentHp - hpLost;
                // 格式 20：贪食者的流沙（储君决策记录第 16 条；只有贪食者会挂）：目标是玩家、计数 ≤ 1、敌方回合开始前它不会先死（被动打不死、毒不死，
                // 毒在 AfterSideTurnStart、流沙在 AfterSideTurnStartLate）、这回合结束后没有额外回合时，敌方回合开始计数减到 0、流沙移除，
                // 玩家被直接吞掉（SandpitPower.AfterRemoved，CreatureCmd.Kill force，仙女瓶救不回来）
                if (V20 && sureLeft && !extraTurn && !poisonKills(hpS)
                    && f.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.SandpitPower>().Any(sp => ReferenceEquals(sp.Target, me.Creature) && sp.Amount <= 1))
                    pit = f;
                if (V22T) { toughExp[f] = left; toughWorst[f] = f.CurrentHp - hpS + Math.Max(0, f.Block - sure[f]); }
                // v0.113.0 修正（F113）：会出手的敌人这一招往玩家手里塞几张牌（头部 32、33 用，见 HandAdds）
                if (F113 && sureLeft && !extraTurn && !poisonKills(hpS) && f.Monster is { } mh)
                {
                    if (V29) shove29[f] = HandAddsOf(mh);       // 格式 29（G04）：等第二遍知道战斗会不会当场结束再计
                    else HandShoved += HandAddsOf(mh);
                }
                // v0.113.0 修正第四轮（F113）：回合末被动打过阈值就眩晕、这回合不出手：仪式兽的犁击（PlowPower：没被挡住的伤害之后生命 ≤ 层数）、
                // 恐怖鳗的尖啸（ShriekPower，同），掘地者的钻地（BurrowedPower.AfterBlockBroken：格挡被打破）
                bool StunBy(float lost, float raw) => F113 && ((lost > 0 && ((f.GetPower<MegaCrit.Sts2.Core.Models.Powers.PlowPower>() is { } pl && f.CurrentHp - lost <= pl.Amount)
                                                                          || (f.GetPower<MegaCrit.Sts2.Core.Models.Powers.ShriekPower>() is { } sh && f.CurrentHp - lost <= sh.Amount)))
                                                                || (f.Block > 0 && raw >= f.Block && f.HasPower<MegaCrit.Sts2.Core.Models.Powers.BurrowedPower>()));
                bool stunW = StunBy(hpS, sure[f]), stunE = StunBy(hpP, passive[f]);
                // 格式 29（G04、G29）：攻击意图放到第二遍再加（要先知道主要敌人是否全被被动或毒打死、前面出手的队友加了多少力量）
                if (V29) { pend29.Add((f, sureLeft && !extraTurn, !poisonKills(hpS) && !stunW, left > 0 && !poisonKills(hpP) && !stunE, !sureLeft || poisonKills(hpS), left <= 0 || poisonKills(hpP))); continue; }
                if (sureLeft && !extraTurn && f.Monster is { } m)
                    foreach (var intent in m.NextMove.Intents)
                        if (intent is AttackIntent atk)
                        {
                            try
                            {
                                int n = Math.Max(1, atk.Repeats); var a = ((float)AtkTotal(atk, cs.PlayerCreatures, f) / n, n);
                                if (!poisonKills(hpS) && !stunW) { incomingWorst.Add(a); worstBy.Add((f, a)); worstAtk.Add(atk); }
                                if (left > 0 && !poisonKills(hpP) && !stunE) { incoming.Add(a); incomingBy.Add((f, a)); }
                            }
                            catch { }
                        }
            }
            if (V29)
            {
                // 格式 29 第二遍。G04：被动打死、毒死的敌人去掉之后，场上没有活着的主要敌人（且没有能力阻止战斗结束）时，战斗在敌人出手之前就结束，
                // 次要敌人（爪牙等，IsSecondaryEnemy）随之离场，它们的攻击不算，塞牌也不算（最坏、期望两种口径各判断一次）。
                // G29：按出手顺序（Combat/CombatManager.cs:1509-1525），前面会出手的敌人这一招给队友加的力量（MoveGain29），加到后面敌人的每段攻击上（AtkPerHit）
                var goneW = pend29.Where(p => p.deadW).Select(p => p.f).ToList(); var goneE = pend29.Where(p => p.deadE).Select(p => p.f).ToList();
                bool endW = false, endE = false;
                try { endW = goneW.Count > 0 && CombatEndsWithout(cs, goneW); endE = goneE.Count > 0 && CombatEndsWithout(cs, goneE); } catch { }
                float allyW = 0, allyE = 0;
                foreach (var (f, outer, w, e, _, _) in pend29)
                {
                    bool sec = false;
                    try { sec = f.IsSecondaryEnemy; } catch { }
                    bool aw = outer && w && !(endW && sec), ae = outer && e && !(endE && sec);
                    if (shove29.TryGetValue(f, out int sh) && !(endW && sec)) HandShoved += sh;
                    if (!outer || f.Monster is not { } m) continue;
                    foreach (var intent in m.NextMove.Intents)
                        if (intent is AttackIntent atk)
                        {
                            try
                            {
                                int n = Math.Max(1, atk.Repeats); float orig = (float)AtkTotal(atk, cs.PlayerCreatures, f) / n;
                                if (aw)
                                {
                                    var a = (allyW > 0 ? AtkPerHit(cs, me, f, atk, new FoeMod((decimal)allyW, false), orig) : orig, n);
                                    incomingWorst.Add(a); worstBy.Add((f, a)); worstAtk.Add(atk); worstOrig.Add(orig); worstAlly.Add(allyW);
                                }
                                if (ae) { var a = (allyE > 0 ? AtkPerHit(cs, me, f, atk, new FoeMod((decimal)allyE, false), orig) : orig, n); incoming.Add(a); incomingBy.Add((f, a)); }
                            }
                            catch { }
                        }
                    float gain = 0;
                    try { gain = MoveGain29(m, m.NextMove.StateId).ally; } catch { }
                    if (gain > 0)
                    {
                        if (aw) { allyW += gain; actorsW.Add((f, gain)); }
                        if (ae) allyE += gain;
                    }
                }
            }
            foreach (var card in playable) Preview(card, null);     // 恢复不带目标的预览
            // 按游戏的结算顺序：回合结束前的格挡（当前格挡 + 遗物、覆甲 + 冰霜被动）先挡手牌的自伤，剩下的加上吞噬暗影激发的冰霜，
            // 再挡回合结束之后的能力自伤（缠绕、瓦解、魔法炸弹）和敌人的攻击
            // 版本 13 修正：按段结算，每一次伤害或失去生命分别过无实体（先压到 1）、格挡、钨棒（每次少掉 1）、缓冲（抵掉一次掉血）；
            // 敌人的多段攻击逐段算。没有这些效果时与原来按总数算的结果相同
            float early = me.Creature.Block + frostPassive + extra;
            // F113 第四轮：候选做完之后回合末格挡的变化（奥利哈钢、涟漪盆的条件，斗篷扣的手牌张数，这一步给的覆甲）
            float FxBlock(CandFx fx)
            {
                float d = (fx.Imm > 0 ? -oriBlock * blockMul : 0) + (fx.Attack ? -rippleBlock * blockMul : 0) + claspPer * blockMul * fx.HandDelta + fx.Plating * blockMul;
                // 格式 29（G02）：这一步打出或离手的虚无牌回合末不再被消耗，它那一份无惧疼痛格挡扣掉（卡戎之灰那一份不扣：要重跑敌人循环）
                if (V29 && fnpPer > 0 && ethSet != null && (fx.Played != null || fx.Gone != null))
                {
                    int k = 0;
                    foreach (var c in ethSet) if (ReferenceEquals(c, fx.Played) || (fx.Gone?.Contains(c) ?? false)) k++;
                    d -= fnpPer * blockMul * k;
                }
                return d + (extra + frostPassive + d) * fx.BlockBoost;      // 打出影融：回合末到账的格挡再乘
            }
            // 悔恨按回合末手牌张数失去生命，这一步让手牌少一张就少 1
            float HandAdj(float v, CardModel? hc, CandFx fx) => fx.HandDelta != 0 && hc is MegaCrit.Sts2.Core.Models.Cards.Regret ? Math.Max(0, v + fx.HandDelta) : v;
            float Loss(List<(float v, int n)> atks, float ex = 0, float pre = 0, CardModel? gone = null, IReadOnlyCollection<Creature>? skip = null, CandFx fx = default)
            {
                float block = early + ex + FxBlock(fx), loss = 0, w = wup0, wl = 0; int buf = buffer + fx.Buffer; bool intg = intangible || fx.Intangible;
                void Hit(float d, bool blockable)
                {
                    if (intg) d = Math.Min(d, 1);
                    if (blockable) { float b = Math.Min(block, d); block -= b; d -= b; }
                    d = Math.Max(0, d - rod);
                    if (d <= 0) return;
                    if (buf > 0) { buf--; return; }
                    loss += d;
                }
                if (pre > 0) Hit(pre, true);      // F113 第四轮：这一步当场挨的伤害（敌人的荆棘、通电等，候选 10 的 handEx）
                foreach (var (v, bl, hc) in handSeq) if ((gone == null || !ReferenceEquals(hc, gone)) && !(hc != null && fx.Gone != null && fx.Gone.Contains(hc))) Hit(HandAdj(v, hc, fx), bl);
                block += (frost - frostPassive) * (1 + fx.BlockBoost);
                foreach (var (v, bl, _) in lateSeq) Hit(v, bl);
                foreach (var (v, n) in atks)
                    for (int i = 0; i < n; i++)
                    {
                        // v0.113.0 修正（F113）：活着的瓦皮在格挡之前先替玩家挨这一段（CreatureCmd.Damage 的 preBlockTarget，直接掉它的生命），挨不下的溢出回到玩家
                        if (w > 0) { float d = intg ? Math.Min(v, 1) : v; float a = Math.Min(w, d); w -= a; wl += a; if (d - a > 0) Hit(d - a, true); }
                        else Hit(v, true);
                    }
                foreach (var (v, bl) in postSeq) Hit(v, bl);
                wupLast = wl;
                return loss;
            }
            float wupExp = 0;
            if (!V29)
            {
                res[me.Creature] = (frost, Loss(incoming), selfDmg + selfLoss, extra);
                wupExp = wupLast;
                worstLoss = Loss(incomingWorst);
            }
            // 格式 22：奥斯提替玩家挨打、荆棘与火焰屏障的反伤（见 Loss22）
            var osty = V22 && me.IsOstyAlive ? me.Osty : null;
            float thorns = 0;
            // 格式 23 也算荆棘（猎人的磨蚀、青铜鳞片、液态青铜药水）
            if (V22T) thorns = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.ThornsPower>() + me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.FlameBarrierPower>();
            var back = new Dictionary<Creature, float>();
            if (thorns > 0) foreach (var f in foes) back[f] = HitDmg(cs, f, me.Creature, (decimal)thorns, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null);
            float ostyHp = osty?.CurrentHp ?? 0;
            int necro = V22 ? me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.NecroMasteryPower>() : 0;
            float Loss22(List<(Creature f, (float v, int n) a)> atks, Dictionary<Creature, float> tough, float ex, float ostyEx, out float ostyLoss, float handEx = 0, CardModel? gone = null, IReadOnlyCollection<Creature>? skip = null, CandFx fx = default)
            {
                float block = early + ex + FxBlock(fx), loss = 0, o = ostyHp + ostyEx, ol = 0, w = wup0, wl = 0; int buf = buffer + fx.Buffer; bool intg = intangible || fx.Intangible;
                void Self(float d, bool blockable)
                {
                    if (intg) d = Math.Min(d, 1);
                    if (blockable) { float b = Math.Min(block, d); block -= b; d -= b; }
                    d = Math.Max(0, d - rod);
                    if (d <= 0) return;
                    if (buf > 0) { buf--; return; }
                    loss += d;
                }
                if (handEx > 0) Self(handEx, true);
                foreach (var (v, bl, hc) in handSeq) if ((gone == null || !ReferenceEquals(hc, gone)) && !(hc != null && fx.Gone != null && fx.Gone.Contains(hc))) Self(HandAdj(v, hc, fx), bl);
                block += (frost - frostPassive) * (1 + fx.BlockBoost);
                foreach (var (v, bl, _) in lateSeq) Self(v, bl);
                var left = new Dictionary<Creature, float>(tough);
                // 亡灵精通：奥斯提每掉 1 点血对全体可攻击的敌人造成层数点伤害（Unblockable，按“生命 + 格挡”的额度近似），打死的敌人后面的段不再打
                void Necro(float lost) { if (necro <= 0 || lost <= 0) return; foreach (var f in left.Keys.ToList()) left[f] -= lost * necro; }
                foreach (var (f, (v, n)) in atks)
                    for (int i = 0; i < n; i++)
                    {
                        if ((thorns > 0 || necro > 0) && left.TryGetValue(f, out var tf) && tf <= 0) break;     // 攻击者已被反伤或亡灵精通打死，剩下的段不打
                        float d = intg ? Math.Min(v, 1) : v;
                        if (w > 0) { float wa = Math.Min(w, d); w -= wa; wl += wa; d -= wa; }      // v0.113.0 修正（F113）：瓦皮在格挡之前先挨
                        float b = Math.Min(block, d); block -= b; d -= b;      // 玩家的格挡先挡（奥斯提用的也是玩家的格挡）
                        if (d > 0 && o > 0) { float a = Math.Min(o, d); o -= a; ol += a; d -= a; Necro(a); }     // 没挡住的有力攻击由活着的奥斯提承受，溢出的回到玩家
                        if (d > 0) { d = Math.Max(0, d - rod); if (d > 0) { if (buf > 0) buf--; else loss += d; } }
                        if (thorns > 0 && back.TryGetValue(f, out var bd) && left.ContainsKey(f)) left[f] -= bd;
                    }
                foreach (var (v, bl) in postSeq) Self(v, bl);
                ostyLoss = ol; wupLast = wl;
                return loss;
            }
            // 格式 29：统一的逐段结算（R3-格式29规格.md 5.1，Loss 与 Loss22 合一，按攻击者逐段）。结算顺序：
            //   1 pre（handEx，这一步当场挨的伤害）→ 2 handSeq（跳过 gone、fx.Gone、fx.Played）→ 3 吞噬暗影激发的冰霜格挡到账 → 4 lateSeq（跳过施加者在 fx.Dead 里的，G27）
            //   → 5 玩家的灾厄（G36：DoomPower 在玩家一方回合结束后处决，生命 ≤ 层数即死）
            //   → 6 敌人的攻击，逐个攻击者、逐段：瓦皮先挨 → 无实体压到 1 → 格挡（倒映 G51：挡下的反弹给攻击者）→ 奥斯提 → 钨棒、律动残余（G03）→ 缓冲
            //       → THE_GAMBIT（G32：有力攻击真正掉血即死）→ 纸片割伤（G28：掉血的段让最大生命减层数）→ 荆棘（含这一步新加的 fx.Thorns，G50/G72）、亡灵精通扣攻击者的生命，打死后剩下的段不打
            //   → 7 postSeq（下个玩家回合开始：律动残余额度、恶魔之舌重新起算）。
            // 玩家一侧（1、2、4、7）的每一次掉血过：无实体、格挡（可格挡的）、钨棒与律动残余、缓冲、恶魔之舌（第一次掉血回同样多的血，G32）。
            // 没有这些新效果时与 Loss / Loss22 的结果相同。主调用（期望口径）之后读 l29* 得 Et29 的拆分（手牌与能力自伤、敌人攻击掉的血、最大生命损失、敌方回合后剩下的格挡）
            int doomP = V29 ? me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.DoomPower>() : 0;
            bool reflect = V29 && me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.ReflectPower>() > 0;
            bool gambit0 = V29 && me.Creature.HasPower<MegaCrit.Sts2.Core.Models.Powers.TheGambitPower>();
            var paper = new Dictionary<Creature, int>();
            if (V29) foreach (var f in foes) { int pc = f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.PaperCutsPower>(); if (pc > 0) paper[f] = pc; }
            float l29Hand = 0, l29Foe = 0, l29Mhl = 0, l29Block = 0; int l29Clay = 0;
            float Loss29(List<(Creature f, (float v, int n) a)> atks, Dictionary<Creature, float> tough, float ex, float ostyEx, out float ostyLoss, float handEx = 0, CardModel? gone = null, CandFx fx = default)
            {
                float block = early + ex + FxBlock(fx), loss = 0, o = ostyHp + ostyEx, ol = 0, w = wup0, wl = 0; int buf = buffer + fx.Buffer; bool intg = intangible || fx.Intangible;
                float rem = Math.Max(0, remLeft - fx.SelfLoss), hand = 0, foe = 0, mhl = 0; int clay = 0;
                bool tongue = tongueLive && tongueFresh && fx.SelfLoss <= 0, dead = false;
                float Self(float d, bool blockable)
                {
                    if (intg) d = Math.Min(d, 1);
                    if (blockable) { float b = Math.Min(block, d); block -= b; d -= b; }
                    d = RodRem29(d, rod, rem, rodFirst);
                    if (d <= 0) return 0;
                    if (buf > 0) { buf--; return 0; }
                    rem -= d; clay++;
                    if (tongue) { tongue = false; return 0; }
                    loss += d;
                    return d;
                }
                if (handEx > 0) hand += Self(handEx, true);
                foreach (var (v, bl, hc) in handSeq)
                    if (!(hc != null && (ReferenceEquals(hc, gone) || ReferenceEquals(hc, fx.Played) || (fx.Gone?.Contains(hc) ?? false)))) hand += Self(HandAdj(v, hc, fx), bl);
                block += (frost - frostPassive) * (1 + fx.BlockBoost);
                foreach (var (v, bl, src) in lateSeq) if (!(src != null && (fx.Dead?.Contains(src) ?? false))) hand += Self(v, bl);
                if (doomP > 0 && me.Creature.CurrentHp - fx.SelfLoss - loss <= doomP) dead = true;     // 合并后核对 5.1：扣掉这一步在预演之外的自伤
                float th = thorns + (V29 ? fx.Thorns : 0);
                var bk = back;
                if (fx.Thorns > 0) { bk = new Dictionary<Creature, float>(); foreach (var f in foes) bk[f] = HitDmg(cs, f, me.Creature, (decimal)th, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null); }
                var left = new Dictionary<Creature, float>(tough);
                void Necro(float lost) { if (necro <= 0 || lost <= 0) return; foreach (var f in left.Keys.ToList()) left[f] -= lost * necro; }
                if (!dead)
                    foreach (var (f, (v, n)) in atks)
                    {
                        for (int i = 0; i < n; i++)
                        {
                            if ((th > 0 || necro > 0 || reflect) && left.TryGetValue(f, out var tf) && tf <= 0) break;     // 攻击者已被反伤、倒映或亡灵精通打死
                            float d = intg ? Math.Min(v, 1) : v;
                            if (w > 0) { float wa = Math.Min(w, d); w -= wa; wl += wa; d -= wa; }
                            float b = Math.Min(block, d); block -= b; d -= b;
                            if (reflect && b > 0 && left.ContainsKey(f)) left[f] -= HitDmg(cs, f, me.Creature, (decimal)b, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null);
                            if (d > 0 && o > 0) { float a = Math.Min(o, d); o -= a; ol += a; d -= a; Necro(a); }
                            if (d > 0)
                            {
                                d = RodRem29(d, rod, rem, rodFirst);
                                if (d > 0)
                                {
                                    if (buf > 0) buf--;
                                    else
                                    {
                                        loss += d; foe += d; rem -= d; clay++;
                                        if (gambit0 || fx.Gambit) { dead = true; break; }
                                        if (paper.TryGetValue(f, out int pc)) mhl += pc;
                                    }
                                }
                            }
                            if (th > 0 && bk.TryGetValue(f, out var bd) && left.ContainsKey(f)) left[f] -= bd;
                        }
                        if (dead) break;
                    }
                float blockAfter = block; int clayEnd = clay;      // 自塑黏土只数下个回合清格挡之前的掉血（postSeq 在那之后）
                if (!dead)
                {
                    rem = remCap; tongue = tongueLive;      // 下个玩家回合开始前：律动残余额度清零、恶魔之舌重新可触发（两者都在 BeforeSideTurnStart）
                    foreach (var (v, bl) in postSeq) Self(v, bl);
                }
                ostyLoss = ol; wupLast = wl;
                l29Hand = hand; l29Foe = foe; l29Mhl = mhl; l29Block = blockAfter; l29Clay = clayEnd;
                // 必死（THE_GAMBIT、玩家灾厄）：主调用返回现在的生命（玩家 17 的口径）；出牌候选（fx.Played）返回不小于最大生命，
                // 候选侧再加这一步的自伤、NOT_YET 按回血后的生命算候选 11 时都仍判死（合并后核对 5.1：原来返回现在的生命，NOT_YET 的候选 11 成了回血量）
                return dead ? (fx.Played != null ? Math.Max(loss, me.Creature.MaxHp) : me.Creature.CurrentHp) : loss;
            }
            if (V29)
            {
                res[me.Creature] = (frost, Loss29(incomingBy, toughExp, 0, 0, out float ol), selfDmg + selfLoss, extra);
                wupExp = wupLast;
                et29 = new Et29 { HandLoss = l29Hand, FoeLoss = l29Foe, MaxHpLoss = l29Mhl, NextTurnBlock = NextTurnBlock29(me, pcs, l29Block, l29Clay) };
                worstLoss = Loss29(worstBy, toughWorst, 0, 0, out _);
                if (osty != null) res[osty] = (ol, Math.Max(0, ostyHp - ol), 0, 0);
            }
            else if (V22T && (osty != null || thorns > 0))
            {
                res[me.Creature] = (frost, Loss22(incomingBy, toughExp, 0, 0, out float ol), selfDmg + selfLoss, extra);
                wupExp = wupLast;
                worstLoss = Loss22(worstBy, toughWorst, 0, 0, out _);
                if (osty != null) res[osty] = (ol, Math.Max(0, ostyHp - ol), 0, 0);
            }
            // v0.113.0 修正（F113）：瓦皮自己的 16–17：现在结束回合预计替玩家挨多少、还剩多少生命（同奥斯提的口径）
            if (wuppy != null) res[wuppy] = (wupExp, Math.Max(0, wuppy.CurrentHp - wupExp), 0, 0);
            if (pit != null)        // 会被流沙吞掉：现在结束回合预计掉的血、最坏情况都是当前全部生命
            {
                res[me.Creature] = (frost, me.Creature.CurrentHp, selfDmg + selfLoss, extra);
                worstLoss = me.Creature.CurrentHp;
            }
            // 格式 23（第七轮起所有格式，F113）：去掉 skip 里的敌人，mod 里的敌人按这一步之后的力量、虚弱重算每段攻击（AtkPerHit）
            List<(Creature f, (float v, int n) a)> Worst(IReadOnlyCollection<Creature>? skip, IReadOnlyDictionary<Creature, FoeMod>? mod)
            {
                var r = new List<(Creature f, (float v, int n) a)>(worstBy.Count);
                for (int i = 0; i < worstBy.Count; i++)
                {
                    var (f, a) = worstBy[i];
                    if (skip != null && skip.Any(s => ReferenceEquals(s, f))) continue;
                    if (mod != null && mod.TryGetValue(f, out var m)) a = (AtkPerHit(cs, me, f, worstAtk[i], m, a.v), a.n);
                    r.Add((f, a));
                }
                return r;
            }
            // 格式 29：同 Worst，另按 skip 重算队友加的力量（G29：skip 里的加力量的敌人不出手，后面敌人的攻击少加这一份）；
            // 重算时一律从原始每段伤害（worstOrig）起，mod 的力量变化与队友加的力量相加
            List<(Creature f, (float v, int n) a)> Worst29(IReadOnlyCollection<Creature>? skip, IReadOnlyDictionary<Creature, FoeMod>? mod)
            {
                var r = new List<(Creature f, (float v, int n) a)>(worstBy.Count);
                for (int i = 0; i < worstBy.Count; i++)
                {
                    var (f, a) = worstBy[i];
                    if (skip != null && skip.Any(s => ReferenceEquals(s, f))) continue;
                    float ally = 0;
                    if (actorsW.Count > 0)
                        foreach (var (g, gain) in actorsW)
                            if (foeIdx[g] < foeIdx[f] && !(skip != null && skip.Any(s => ReferenceEquals(s, g)))) ally += gain;
                    bool hasMod = mod != null && mod.TryGetValue(f, out _);
                    if (hasMod || ally != worstAlly[i])
                    {
                        var m = hasMod ? mod![f] : new FoeMod(0, false);
                        a = (AtkPerHit(cs, me, f, worstAtk[i], m with { Add = m.Add + (decimal)ally }, worstOrig[i]), a.n);
                    }
                    r.Add((f, a));
                }
                return r;
            }
            if (V29)
                after = (ex, skip, ostyEx, handEx, mod, gone, fx) => Loss29(Worst29(skip, mod), toughWorst, ex, ostyEx, out _, handEx, gone, fx);
            else if (!V23)
            {
                // 第七轮（F113）：格式 19、20、22 也按 Fx23 的 mod（这一步给敌人上的虚弱、减力量等）重算敌人的攻击
                after = (ex, skip, _, handEx, mod, gone, fx) => Loss(F113 && mod != null ? Worst(skip, mod).Select(x => x.a).ToList()
                    : skip == null ? incomingWorst : worstBy.Where(x => !skip.Any(s => ReferenceEquals(s, x.f))).Select(x => x.a).ToList(), ex, F113 ? handEx : 0, gone, skip, fx);
                // 格式 22：召唤（ostyEx）在奥斯提不在时就是召唤出来的生命；有奥斯提、反伤或召唤时走 Loss22
                if (V22)
                {
                    var old = after;
                    after = (ex, skip, ostyEx, handEx, mod22, gone, fx) => osty == null && thorns <= 0 && ostyEx <= 0 && handEx <= 0 ? old(ex, skip, mod: mod22, gone: gone, fx: fx)
                        : Loss22(F113 && mod22 != null ? Worst(skip, mod22) : skip == null ? worstBy : worstBy.Where(x => !skip.Any(s => ReferenceEquals(s, x.f))).ToList(), toughWorst, ex, ostyEx, out _, handEx, gone, skip, fx);
                }
            }
            else
            {
                // 格式 23：去掉 skip 里的敌人，mod 里的敌人按这一步之后的力量、虚弱重算每段攻击（Worst，见上），再按格式 21（没有荆棘、凋萎时）或 Loss22 的口径结算
                // 格式 28（V22 也为真）：有奥斯提或召唤（ostyEx）时同格式 22 走 Loss22 并带上召唤；格式 23–27 没有奥斯提（osty 只在 V22 取）、ostyEx 恒为 0（只有 Riders22 给），与原来相同
                after = (ex, skip, ostyEx, handEx, mod, gone, fx) => thorns <= 0 && handEx <= 0 && osty == null && ostyEx <= 0 ? Loss(Worst(skip, mod).Select(x => x.a).ToList(), ex, gone: gone, skip: skip, fx: fx)
                    : Loss22(Worst(skip, mod), toughWorst, ex, ostyEx, out _, handEx, gone, skip, fx);
            }
        }
        catch { }
        lossAfter = after;
        devourer = pit;
        sureHp = sureOut;
        if (V29) Et29Last = et29;
        return res;
    }

    /// <summary>
    /// 格式 23：这个攻击意图在敌人力量变化 m.Add、新挂上虚弱（m.Weak）之后的每段伤害。照游戏的算法（AttackIntent.GetSingleDamage →
    /// Hook.ModifyDamage：加法项（力量、玩家身上的感染等）→ 乘法项（虚弱、易伤等）→ 上限（无实体等）→ 不小于 0 → 取整）：
    /// 基础伤害加上 m.Add 再过加法与乘法项，新挂的虚弱在乘法这一步乘上倍率（WeakPower.ModifyDamageMultiplicative：0.75，纸鹤、衰弱另改），最后过上限。
    /// 不改时先按同样的算法算一遍，与原来的每段伤害（orig）对不上就不改（算法覆盖不到的意图）。只读。
    /// </summary>
    static float AtkPerHit(ICombatState cs, Player me, Creature f, AttackIntent atk, FoeMod m, float orig)
    {
        try
        {
            if (atk.DamageCalc is not { } calc) return orig;
            var rs = cs.RunState; var p = me.Creature;
            var move = MegaCrit.Sts2.Core.ValueProps.ValueProp.Move;
            var am = MegaCrit.Sts2.Core.Hooks.ModifyDamageHookType.Additive | MegaCrit.Sts2.Core.Hooks.ModifyDamageHookType.Multiplicative;
            var cap = MegaCrit.Sts2.Core.Hooks.ModifyDamageHookType.Cap;
            decimal b = calc();
            decimal Calc(decimal add, bool weak, decimal mult = 1m)
            {
                decimal d = MegaCrit.Sts2.Core.Hooks.Hook.ModifyDamage(rs, cs, p, f, b + add, move, null, null, am, CardPreviewMode.None, out _);
                if (weak) d *= WeakMult(me, f);
                d *= mult;      // F113：指挥棒的 ×0.75（ConductorsBatonPower.ModifyDamageMultiplicative，见 Fx23）
                return Math.Max(0, (int)MegaCrit.Sts2.Core.Hooks.Hook.ModifyDamage(rs, cs, p, f, d, move, null, null, cap, CardPreviewMode.None, out _));
            }
            if (Calc(0, false) != (decimal)orig) return orig;
            return (float)Calc(m.Add, m.Weak, m.Mult);
        }
        catch { return orig; }
    }

    /// <summary>格式 23：敌人 f 新挂上虚弱时它的攻击乘的倍率（同 WeakPower.ModifyDamageMultiplicative：能力的 DamageDecrease 0.75，
    /// 玩家有纸鹤、它身上有衰弱时再改）。</summary>
    static decimal WeakMult(Player me, Creature f)
    {
        var move = MegaCrit.Sts2.Core.ValueProps.ValueProp.Move;
        decimal num = ModelDb.Power<MegaCrit.Sts2.Core.Models.Powers.WeakPower>().DynamicVars["DamageDecrease"].BaseValue;
        if (me.GetRelic<MegaCrit.Sts2.Core.Models.Relics.PaperKrane>() is { } pk) num = pk.ModifyWeakMultiplier(me.Creature, num, move, f, null);
        if (f.GetPower<MegaCrit.Sts2.Core.Models.Powers.DebilitatePower>() is { } deb) num = deb.ModifyWeakMultiplier(f, num, move, f, null);
        return num;
    }

    /// <summary>通用背包的一个选项：占用能量、星、箭（可为负：拔回）、免费层、叽 诗句层、瞄准、张数、束缚牌，价值为对这个敌人的伤害。</summary>
    readonly record struct KOpt(int E, int S, int A, int F, int V, int M, int K, int B, float Val);

    /// <summary>第五轮：一个敌人的通用背包（见 EndTurnPreview 里 KnapX 的说明）。每张牌按免费与否、多打与否、吃不吃瞄准展开成几个选项，至多选一个。</summary>
    static float KnapForFoe(Creature f, List<CardModel> playable, Dictionary<CardModel, (int cost, int costNF, int free, int sc, int recall)> kInfo, Player me,
        int energy, int stars, bool arrowDim, bool kRecall, int avail, int freeMax, int kvN, bool aimOn, int kcap, bool chains)
    {
        try
        {
            int nShoot = playable.Count(IsShoot);
            int A = arrowDim ? Math.Min(nShoot, avail + (kRecall ? kInfo.Values.Sum(x => x.recall) : 0)) : 0;
            int A0 = Math.Min(avail, A);
            bool kOn = kcap < int.MaxValue;
            var items = new List<List<KOpt>>();
            foreach (var card in playable)
            {
                if (!kInfo.TryGetValue(card, out var ki)) continue;
                var (pd, _, hits) = Preview(card, f);
                int plays = PlayCount(card, f);
                bool shoot = arrowDim && IsShoot(card);
                // 现在没有可用的箭时射击牌的预览为 0（ShootDisabled），召回之后能射：按卡面预览值算
                if (shoot && kRecall && pd == 0 && ShootDisabled(card))
                    foreach (var (name, v) in card.DynamicVars) if (name is "Damage" or "CalculatedDamage") pd = (float)v.PreviewValue;
                int ac = shoot ? 1 : kRecall && ki.recall > 0 ? -ki.recall : 0;
                if (pd <= 0 && ac >= 0) continue;
                float pdx = pd > 0 && aimOn ? PdNoAim(card, f, pd) : pd;
                bool aimS = aimOn && pd > 0 && pdx < pd;
                bool kvS = kvN > 0 && IsMelody(card) && plays >= 2;
                int b = chains && card.Affliction is MegaCrit.Sts2.Core.Models.Afflictions.Bound ? 1 : 0;
                var opts = new List<KOpt>();
                foreach (var (ce, fu) in ki.free > 0 ? new[] { (ki.cost, 1), (ki.costNF, 0) } : new[] { (ki.cost, 0) })
                {
                    if (ce > energy || ki.sc > stars) continue;
                    foreach (var (vu, p) in kvS ? new[] { (1, plays), (0, plays - 1) } : new[] { (0, plays) })
                    {
                        float h = Math.Max(0, hits);
                        if (aimS)
                        {
                            opts.Add(new KOpt(ce, ki.sc, ac, fu, vu, 1, kOn ? 1 : 0, b, p > 0 ? pd * h + (p - 1) * pdx * h : 0));
                            opts.Add(new KOpt(ce, ki.sc, ac, fu, vu, 0, kOn ? 1 : 0, b, pdx * h * p));
                        }
                        else opts.Add(new KOpt(ce, ki.sc, ac, fu, vu, 0, kOn ? 1 : 0, b, pd > 0 ? pd * h * p : 0));
                    }
                }
                if (opts.Count > 0) items.Add(opts);
            }
            Preview(playable.FirstOrDefault() ?? null!, null);
            foreach (var c in playable) Preview(c, null);
            int K = kOn ? Math.Min(kcap, items.Count) : 0;
            return KnapX(items, energy, stars, A, A0, freeMax, kvN, aimOn ? 1 : 0, K, chains ? 1 : 0);
        }
        catch { return -1; }
    }

    /// <summary>
    /// 多维 0–1 背包（每件至多选一个选项）。容量维：能量、星、箭（选项可以给回箭：A 为负，剩余容量加回、封顶）、免费层、叽 诗句层、瞄准（0/1）、张数、束缚牌（0/1）。
    /// dp[状态] 为各维剩余容量不超过状态时的最大伤害；返回 A 维取 A0（可用箭数）、其余取满时的值。状态数超过 40 万时返回 −1（调用处沿用原来的背包）。
    /// </summary>
    static float KnapX(List<List<KOpt>> items, int E, int S, int A, int A0, int F, int V, int M, int K, int B)
    {
        int[] dim = { E + 1, S + 1, A + 1, F + 1, V + 1, M + 1, K + 1, B + 1 };
        long total = 1; foreach (var d in dim) total *= d;
        if (total > 400_000) return -1;
        var st = new int[8]; st[7] = 1;
        for (int i = 6; i >= 0; i--) st[i] = st[i + 1] * dim[i + 1];
        var dp = new float[total];
        var x = new int[8];
        foreach (var opts in items)
        {
            var nd = (float[])dp.Clone();
            foreach (var o in opts)
            {
                int[] u = { o.E, o.S, o.A, o.F, o.V, o.M, o.K, o.B };
                for (int idx = 0; idx < total; idx++)
                {
                    int r = idx; bool ok = true; int prev = 0;
                    for (int d = 0; d < 8; d++)
                    {
                        x[d] = r / st[d]; r -= x[d] * st[d];
                        int px;
                        if (d == 2 && u[d] < 0) px = Math.Min(A, x[d] - u[d]);
                        else { px = x[d] - u[d]; if (px < 0) { ok = false; break; } }
                        prev += px * st[d];
                    }
                    if (!ok) continue;
                    float v = dp[prev] + o.Val;
                    if (v > nd[idx]) nd[idx] = v;
                }
            }
            dp = nd;
        }
        int[] fin = { E, S, A0, F, V, M, K, B };
        int fi = 0; for (int d = 0; d < 8; d++) fi += fin[d] * st[d];
        return dp[fi];
    }
}
