using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;

namespace Sts2Headless;

// 格式 29 的回合结束预演（包 1，R3-格式29规格.md 5.1）：EndTurnPreview 在 V29 下调用的几个辅助函数。只读，只在 V29 下调用。
public sealed partial class Observation
{
    /// <summary>格式 29：玩家这一次掉血过钨棒（每次少 rod）与律动残余（封顶到还剩的额度 rem）。两者同在掉血钩子的 AfterOsty 段，
    /// 按遗物顺序先后结算（rodFirst：钨棒在前）。rem 为 +∞ 时同原来只过钨棒。</summary>
    static float RodRem29(float d, float rod, float rem, bool rodFirst)
    {
        if (rodFirst) { d = Math.Max(0, d - rod); return Math.Min(d, rem); }
        d = Math.Min(d, rem);
        return Math.Max(0, d - rod);
    }

    /// <summary>
    /// 格式 29（G02）：回合结束时被消耗的虚无牌（同 Characters/Ironclad.cs 的 EtherealExhaust：有回合结束效果的牌不算，经 Hook.ShouldEtherealTrigger），
    /// 另外记下计入的牌（ethSet）与每张的无惧疼痛格挡（fnpPer，未乘影融），候选打出、离手的虚无牌回合末不再给格挡（EndTurnPreview 的 FxBlock 扣掉）。
    /// exclude 里的牌（傻瓜、践踏确定会自动打出的）回合末已不在手里，不算。卡戎之灰逐张各打一段全体（原来合成一段）。
    /// </summary>
    static (List<CardModel> eth, float fnpPer) EtherealExhaust29(ICombatState cs, Player me, ref float extra, Action<float> all, IReadOnlyCollection<CardModel>? exclude)
    {
        var eth = new List<CardModel>();
        float per = 0;
        try
        {
            foreach (var c in me.PlayerCombatState!.Hand.Cards)
            {
                try
                {
                    if (exclude != null && exclude.Contains(c)) continue;
                    if (!c.HasTurnEndInHandEffect && Kw(c).Contains(CardKeyword.Ethereal) && MegaCrit.Sts2.Core.Hooks.Hook.ShouldEtherealTrigger(cs, c)) eth.Add(c);
                }
                catch { }
            }
            if (eth.Count == 0) return (eth, 0);
            int fnp = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.FeelNoPainPower>();
            if (fnp > 0) { per = AfterimageBlock(cs, me, fnp); extra += eth.Count * per; }
            foreach (var r in me.Relics)
                if (r is MegaCrit.Sts2.Core.Models.Relics.CharonsAshes && RelicLive(r) && r.DynamicVars.TryGetValue("Damage", out var dv))
                    for (int i = 0; i < eth.Count; i++) all((float)dv.BaseValue);
        }
        catch { }
        return (eth, per);
    }

    /// <summary>
    /// 格式 29（G11）：回合末 AutoPostPlay 阶段的自动打出（虚无消耗、手牌回合末效果之前；Combat/PlayerTurnPhase.cs 的 AutoPostPlay）。
    ///   践踏（StampedePower.AfterAutoPostPlayPhaseEntered）：每层从手里随机打出一张攻击牌（不带不能打出关键字，傻瓜已打的不算），目标随机。
    ///     手里这些攻击牌都一样（SameCard）时确定：打 min(层数, 张数) 张；单体牌只剩一个可攻击敌人、或打全体时进确定伤害（sure），否则按期望进 passive。
    ///     攻击牌不同时每张被挑中的概率为 min(层数, 张数) / 张数，按期望进 passive。
    ///   彼岸嚎叫（HowlFromBeyond.AfterAutoPostPlayPhaseEntered）：消耗堆里的每一张打全体一次（确定）。没有可攻击的敌人时都不算。
    ///   狂怒（RagePower.AfterCardPlayed）：每打出一张攻击牌给层数点格挡（进 extra，也算势不可当的一次）；确定打出的牌自己的格挡也算。
    /// 伤害按 Preview(card, f) × 段数 × PlayCount（不在手里的牌每段按 PdWithStr），每段过 cap（无实体压到 1，G23）。
    /// 践踏打出的牌给敌人上的易伤等对随后嚎叫的加成不算（先后相关，没建模）。返回确定被践踏打出、离开手牌的牌。
    /// </summary>
    static List<CardModel>? AutoPostPlay29(ICombatState cs, Player me, PlayerCombatState pcs, List<Creature> hittable, List<CardModel> auto, ref float extra, ref int juggN,
        Dictionary<Creature, float> passive, Dictionary<Creature, float> sure, Dictionary<Creature, List<float>> sureHits, Func<Creature, float, float> cap)
    {
        var gone = new List<CardModel>();
        float ex = 0; int jn = 0;
        try
        {
            if (hittable.Count == 0) return gone;
            int rage = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.RagePower>();
            float rageBlk = rage > 0 ? AfterimageBlock(cs, me, rage) : 0;
            // 自动打出前先过 Hook.ShouldPlay（CardCmd.AutoPlay：被鸣响、凡庸等挡下的牌移去结算牌堆，不生效）
            bool Allowed(CardModel c) { try { return MegaCrit.Sts2.Core.Hooks.Hook.ShouldPlay(cs, c, out _, AutoPlayType.Default); } catch { return true; } }
            void Play(CardModel c, float weight, bool exact)
            {
                try
                {
                    if (!Allowed(c)) return;
                    bool aoe = c.TargetType == TargetType.AllEnemies;
                    bool inHand = c.Pile?.Type == MegaCrit.Sts2.Core.Entities.Cards.PileType.Hand;
                    foreach (var f in hittable)
                    {
                        var (pd, _, h) = Preview(c, f);
                        // 不在手里的牌（消耗堆里的嚎叫）卡面预览不过攻击者、目标的伤害修正（虚弱、易伤等）：改按 PdWithStr（基础值过全部伤害钩子）
                        if (!inHand) { float pw2 = PdWithStr(c, f, 0); if (pw2 >= 0) pd = pw2; }
                        if (pd <= 0) continue;
                        float per = cap(f, pd), n = Math.Max(0, h) * PlayCount(c, f);
                        if (exact && (aoe || hittable.Count == 1))
                        {
                            passive[f] += per * n; sure[f] += per * n;
                            for (int q = 0; q < (int)n; q++) sureHits[f].Add(per);
                        }
                        else passive[f] += weight * per * n / (aoe ? 1 : hittable.Count);
                    }
                    if (exact)
                    {
                        var (_, ab, _) = Preview(c, null);
                        if (ab > 0) { int pc = PlayCount(c, null); ex += ab * pc; jn += pc; }
                    }
                    Preview(c, null);
                }
                catch { }
            }
            int stN = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.StampedePower>();
            if (stN > 0)
            {
                var atk = pcs.Hand.Cards.Where(c => { try { return !auto.Contains(c) && c.Type == CardType.Attack && !Kw(c).Contains(CardKeyword.Unplayable); } catch { return false; } }).ToList();
                int k = Math.Min(stN, atk.Count);
                if (k > 0)
                {
                    if (atk.All(c => SameCard(c, atk[0])))
                        for (int i = 0; i < k; i++) { Play(atk[i], 1, true); gone.Add(atk[i]); }
                    else
                        foreach (var c in atk) Play(c, (float)k / atk.Count, false);
                    if (rageBlk > 0)
                    {
                        float ok = atk.Count(Allowed) * (float)k / atk.Count;       // 被挡下的不算打出，狂怒按期望（确定时就是张数）
                        if (atk.All(c => SameCard(c, atk[0]))) ok = Allowed(atk[0]) ? k : 0;
                        ex += ok * rageBlk; jn += (int)Math.Round(ok);
                    }
                }
            }
            foreach (var c in pcs.ExhaustPile.Cards.OfType<MegaCrit.Sts2.Core.Models.Cards.HowlFromBeyond>().ToList())
            {
                if (!Allowed(c)) continue;
                Play(c, 1, true);
                if (rageBlk > 0) { ex += rageBlk; jn++; }
            }
        }
        catch { }
        extra += ex; juggN += jn;
        return gone;
    }

    /// <summary>
    /// 格式 29（G21、G70）：现在结束回合，下个玩家回合开始时确定有的格挡（Et29.NextTurnBlock，包 5 写生物 29）。
    /// 回合开始先清格挡（Creature.AfterTurnStart → ClearBlock，Hook.ShouldClearBlock 由第一个阻止者定：能力排在遗物前面）：
    ///   壁垒、残影保留全部（blockAfter = 敌方回合之后剩下的格挡，期望口径），否则坚固夹钳保留至多 Block（10）点；
    /// 再加回合开始时给的：下回合格挡（BlockNextTurnPower）、自塑黏土（能力现有层数 + 遗物：这段时间里每一次真正掉血再挂 BlockNextTurn 层，clayHits 次）、
    ///   托里克坚韧（每个还有回合数的实例给它的 Block）、羊角夹板（下回合是第 2 回合）、船舵（第 3 回合）（以上为 Hook.AfterBlockCleared），
    ///   以及之后、第一个决策之前的赤红斗篷（层数）、释（Sai）、冷却液（不同充能球种类数 × 层数）。都是 Unpowered 格挡，按原值（截掉小数）。
    /// </summary>
    static float NextTurnBlock29(Player me, PlayerCombatState pcs, float blockAfter, int clayHits)
    {
        float r = 0;
        try
        {
            var c = me.Creature;
            // 每次获得格挡截掉小数（托里克坚韧存的格挡可以带小数，如虚弱时 6.75）
            static float Blk(MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVarSet vars, string name = "Block") => vars.TryGetValue(name, out var v) ? MathF.Floor((float)v.BaseValue) : 0;
            if (c.HasPower<MegaCrit.Sts2.Core.Models.Powers.BarricadePower>() || c.HasPower<MegaCrit.Sts2.Core.Models.Powers.BlurPower>()) r += Math.Max(0, blockAfter);
            else if (me.Relics.FirstOrDefault(x => x is MegaCrit.Sts2.Core.Models.Relics.SturdyClamp && RelicLive(x)) is { } clamp) r += Math.Max(0, Math.Min(blockAfter, Blk(clamp.DynamicVars)));
            r += c.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.BlockNextTurnPower>();
            r += c.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.SelfFormingClayPower>();
            foreach (var t in c.GetPowerInstances<MegaCrit.Sts2.Core.Models.Powers.ToricToughnessPower>()) if (t.Amount > 0) r += Blk(t.DynamicVars);
            r += c.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.CrimsonMantlePower>();
            int coolant = c.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.CoolantPower>();
            if (coolant > 0) r += coolant * pcs.OrbQueue.Orbs.Select(o => o.Id).Distinct().Count();
            int next = pcs.TurnNumber + 1;
            foreach (var rl in me.Relics)
            {
                if (!RelicLive(rl)) continue;
                switch (rl)
                {
                    case MegaCrit.Sts2.Core.Models.Relics.SelfFormingClay: r += clayHits * Blk(rl.DynamicVars, "BlockNextTurn"); break;
                    case MegaCrit.Sts2.Core.Models.Relics.HornCleat when next == 2: r += Blk(rl.DynamicVars); break;
                    case MegaCrit.Sts2.Core.Models.Relics.CaptainsWheel when next == 3: r += Blk(rl.DynamicVars); break;
                    case MegaCrit.Sts2.Core.Models.Relics.Sai: r += Blk(rl.DynamicVars); break;
                }
            }
        }
        catch { }
        return r;
    }
}
