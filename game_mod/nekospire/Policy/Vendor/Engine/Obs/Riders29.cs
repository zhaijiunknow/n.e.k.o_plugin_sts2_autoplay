using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MC = MegaCrit.Sts2.Core.Models.Cards;
using MP = MegaCrit.Sts2.Core.Models.Powers;
using MR = MegaCrit.Sts2.Core.Models.Relics;

namespace Sts2Headless;

// 格式 29 包 4（R3-格式29规格.md 5.4）：出牌附带效果的新口径 Riders29（格式 29 下 Riders22 改调它，旧函数原样留着），
// 以及储君（G38）、亡灵契约师（G39、G41）的附带效果。只在 V29 下调用，只读。
public sealed partial class Observation
{
    /// <summary>格式 29：这个出牌候选里每个敌人这一步的掉血（Candidates 传给 SilentPreview 的 hl；SilentPreview 在 V29 下记下，Riders29 重算 4–7 时更新）。</summary>
    Dictionary<Creature, float>? _hl29;

    /// <summary>
    /// 格式 29：Riders22 的新口径（在它的基础上）：
    ///   G01 消耗连锁：ExhaustOthers 认浩劫、THRASH、STOKE；浩劫打出的抽牌堆顶牌已知时（KnownTop）按自动打出算（AutoPlayEffect）：
    ///       格挡进候选 8（狂怒、余像随之触发），确定的伤害按“无目标”口径进 4–7 与击杀，期望伤害只进 6，能量与进手加到 _autoGain。
    ///   G02 整手消耗：重振精神（手里其余非攻击牌）、恶魔之焰、STOKE（手里其余全部）把这组牌记进 _autoGone，_autoHand 减去张数（候选 10、13 用）。
    ///   G37：这张牌自己的格挡只在 card.GainsBlock 时算一次获得格挡（创世之柱、厄运之衣的 Block 是层数，不触发势不可当）。
    ///   G50、G72：啾 尖刺的临时荆棘、铁蒺藜的荆棘层数 × 打出次数记进 _thornsGain（包 3 交给回合结束预演）。
    ///   G66①：红骷髅：自伤的攻击牌（御血术、突破）先自伤再攻击，自伤跨过一半生命时这一下每段按力量 +3（RUINED_HELMET 本场还没加过力量时 +6）重算。
    ///   G38：储君的附带格挡（群星之子、星系尘埃、创世之柱、君王矿石）与黑洞的全体伤害（RegentRiders29）。
    ///   G39、G41：亡灵契约师的附带效果（NecroRiders29：厄运之衣、血肉戏法、灰烬之灵、死亡之舞、纠缠、吞噬生命、紧追不放），
    ///       奥斯提出手的段以奥斯提为攻击者，奥斯提打带荆棘的敌人时被反伤掉的血记进 ostyEx。
    /// 重算 4–7 时另把每个敌人这一步的掉血写回 hl（_hl29），后面判断毒得死、阈值眩晕、灾厄处决用。
    /// </summary>
    (float c4, float c5, float c6, float c7, List<Creature>? killed, float block, float selfLoss, float ostyEx) Riders29(CardModel card, Creature? tgt, Player me,
        float c4, float c5, float c6, float c7, List<Creature>? killed, float block)
    {
        try
        {
            var cs = me.Creature.CombatState!;
            var pcs = me.PlayerCombatState!;
            var foes = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
            var hittable = foes.Where(c => Hittable(cs).Contains(c)).ToList();
            var unpowered = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered;
            var move = MegaCrit.Sts2.Core.ValueProps.ValueProp.Move;
            int plays = PlayCount(card, tgt);
            float selfPer = SelfHpLoss(cs, me, card), self = selfPer * plays;
            float ostyEx = OstyChange(cs, me, card, plays);
            bool exhausts = ExhaustsOnPlay(card, me);
            int exhaustN = (exhausts ? 1 : 0) + ExhaustOthers(card, me);
            int ownGains = IroncladCounts(card, me, 1).gains;
            int rage = me.Creature.GetPowerAmount<MP.RagePower>();
            int fnp = me.Creature.GetPowerAmount<MP.FeelNoPainPower>();
            int jugg = me.Creature.GetPowerAmount<MP.JuggernautPower>();
            int inferno = me.Creature.GetPowerAmount<MP.InfernoPower>();
            int image = me.Creature.GetPowerAmount<MP.AfterimagePower>();
            var charon = me.Relics.FirstOrDefault(r => r is MR.CharonsAshes && RelicLive(r));
            var flute = me.Relics.FirstOrDefault(r => r is MR.BoneFlute && RelicLive(r));
            bool ostyAtk = card.Tags.Contains(CardTag.OstyAttack) && me.IsOstyAlive;
            Creature dealer = ostyAtk ? me.Osty! : me.Creature;      // G41：奥斯提出手的段，攻击者是奥斯提

            // G50、G72：这一步新加的荆棘
            try
            {
                if (card is MC.ChoBarbs && card.DynamicVars.TryGetValue("ChoBarbsPower", out var cb)) _thornsGain += (float)cb.BaseValue * plays;
                else if (card is MC.Caltrops && card.DynamicVars.TryGetValue("ThornsPower", out var tp)) _thornsGain += (float)tp.BaseValue * plays;
            }
            catch { }

            // G02：整手消耗的牌离开手牌
            var others = OtherHand(card, me);
            List<CardModel>? handGone = card switch
            {
                MC.SecondWind => others.Where(c => c.Type != CardType.Attack).ToList(),
                MC.FiendFire or MC.Stoke => others,
                _ => null,
            };
            if (handGone is { Count: > 0 })
            {
                _autoGone = _autoGone == null ? handGone : _autoGone.Concat(handGone.Where(g => !_autoGone.Contains(g))).ToList();
                _autoHand -= handGone.Count;
            }

            // G01：浩劫打出的抽牌堆顶牌（已知时）
            var havocTop = card is MC.Havoc ? HavocChain(me).played : new List<CardModel>();
            var hap = havocTop.Count > 0 ? AutoPlayEffect29(havocTop, me) : default;
            if (havocTop.Count > 0) _autoGain = (_autoGain.energy + hap.energy, _autoGain.cards + hap.cards);
            int havocSelfN = 0;
            foreach (var t in havocTop)
            {
                float s1 = SelfHpLoss(cs, me, t) * PlayCount(t, null);
                if (s1 > 0) { self += s1; havocSelfN++; }
            }

            // 获得格挡的每一次（势不可当按次数触发）
            var gains = new List<float>();
            var (_, ownBlock, _) = Preview(card, tgt);
            Preview(card, null);
            bool gainsBlock = true;
            try { gainsBlock = card.GainsBlock; } catch { }
            float add = 0;
            for (int r = 0; r < plays; r++)
            {
                if (ownBlock > 0 && gainsBlock) for (int g = 0; g < ownGains; g++) { gains.Add(ownBlock); if (g > 0) add += ownBlock; }
                if (image > 0) gains.Add(AfterimageBlock(cs, me, image));
            }
            void Gain(float b, int times) { for (int i = 0; i < times; i++) { gains.Add(b); add += b; } }
            if (rage > 0 && card.Type == CardType.Attack) Gain(AfterimageBlock(cs, me, rage), plays);
            if (fnp > 0 && exhaustN > 0) Gain(AfterimageBlock(cs, me, fnp), exhaustN);
            if (flute != null && ostyAtk && flute.DynamicVars.TryGetValue("Block", out var fb)) Gain(AfterimageBlock(cs, me, fb.IntValue), plays);
            if (havocTop.Count > 0)
            {
                if (hap.block > 0) Gain(hap.block, 1);
                if (image > 0) Gain(AfterimageBlock(cs, me, image), havocTop.Count);
                if (rage > 0) Gain(AfterimageBlock(cs, me, rage), havocTop.Count(t => t.Type == CardType.Attack));
            }
            // G38、G39：储君、亡灵契约师的附带格挡与伤害
            var rr = RegentRiders29(card, tgt, me, plays, others);
            foreach (var b in rr.gains) Gain(b, 1);
            var nr = NecroRiders29(card, tgt, me, plays, hittable);
            foreach (var b in nr.gains) Gain(b, 1);
            ostyEx += nr.ostyEx;
            // G41：奥斯提打带荆棘的敌人，被玩家现有格挡挡下之外的反伤掉奥斯提的血（挡下的部分 AttackReactions 记进当场挨的伤害）
            if (ostyAtk) { float ot = OstyThorns29(card, tgt, me); if (ot > me.Creature.Block) ostyEx -= ot - me.Creature.Block; }

            // G66①：红骷髅（这一下自伤跨过一半生命）
            decimal skullStr = selfPer > 0 && card.Type == CardType.Attack ? RedSkullStr29(cs, me, selfPer) : 0;

            int juggHits = jugg > 0 ? gains.Count(b => b > 0) : 0;
            bool charonHit = charon != null && exhaustN > 0 && charon.DynamicVars.TryGetValue("Damage", out _);
            int infernoHits = inferno > 0 ? (selfPer > 0 ? plays : 0) + havocSelfN : 0;
            bool doom = card is MC.EndOfDays;
            int necro = me.Creature.GetPowerAmount<MP.NecroMasteryPower>();
            float necroLoss = necro > 0 && OstyKillCards.Contains(card.GetType()) && me.IsOstyAlive ? me.Osty!.CurrentHp : 0;
            bool silentOwn = card is MC.KnifeTrap or MC.Outbreak;
            bool havocDmg = havocTop.Count > 0 && (hap.sure.Values.Any(l => l.Count > 0) || hap.exp.Values.Any(v => v > 0));
            bool riders29 = havocDmg || skullStr != 0 || rr.blackHoles > 0 || nr.hits.Count > 0 || nr.exp.Values.Any(v => v > 0);
            if (silentOwn || (juggHits == 0 && !charonHit && infernoHits == 0 && !doom && necroLoss <= 0 && !riders29))
                return (c4, c5, c6, c7, killed, block + add, self, ostyEx);

            // 重算 4–7：每个敌人的一串伤害（这张牌自己的 + 附带的），按游戏的结算顺序逐次打出
            var seq = foes.ToDictionary(f => f, _ => new List<Hit>());
            var own = foes.ToDictionary(f => f, _ => 0f);
            var extra = foes.ToDictionary(f => f, _ => 0f);
            float ownMax = 0, ownHits = 0;
            var strangle = foes.Select(f => (f, amt: f.Powers.OfType<MP.StranglePower>().Where(p => p.Applier?.Player == me).Sum(p => p.Amount)))
                .Where(x => x.amt > 0).ToList();
            int serpent = me.Creature.GetPowerAmount<MP.SerpentFormPower>();
            var poisonProps = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unblockable | unpowered;
            void Det(Creature f, float d) { if (!seq.ContainsKey(f)) return; seq[f].Add(new Hit(d, true, unpowered, me.Creature, null)); extra[f] += d; }
            void RandomHit(float amount)
            {
                if (hittable.Count == 1) Det(hittable[0], HitDmg(cs, hittable[0], me.Creature, (decimal)amount, unpowered, null));
                else foreach (var f in hittable) extra[f] += HitDmg(cs, f, me.Creature, (decimal)amount, unpowered, null) / hittable.Count;
            }
            void Riders21()
            {
                if (serpent > 0) RandomHit(serpent);
                foreach (var (f, amt) in strangle)
                {
                    var h = new Hit(HitDmg(cs, f, null, amt, poisonProps, null), false, poisonProps, null, null);
                    seq[f].Add(h); extra[f] += h.Dmg;
                }
            }
            List<Creature>? hitList = tgt != null ? new List<Creature> { tgt } : card.TargetType switch
            {
                TargetType.AllEnemies => foes.Where(c => IsHittableM(c)).ToList(),
                TargetType.RandomEnemy when foes.Count == 1 && IsHittableM(foes[0]) => foes,
                TargetType.RandomEnemy when card is MC.MementoMori && MementoTarget(me) is { } mt => new List<Creature> { mt },
                _ => null,
            };
            bool all = tgt == null;
            var per = new Dictionary<Creature, (float pd, int hits)>();
            foreach (var f in hitList ?? new List<Creature>())
            {
                var (pd, _, hits) = Preview(card, f);
                if (pd < 0) { per.Clear(); break; }
                per[f] = (pd, (int)hits);
            }
            Preview(card, null);
            // G66①：红骷髅的力量在这一下之前到账（单段伤害按力量多 skullStr 重算，取与不加时之差，免得和界面预览的其余修正对不上）
            if (skullStr != 0)
                foreach (var f in per.Keys.ToList())
                {
                    float a0 = PdWithStr(card, f, 0), a1 = PdWithStr(card, f, skullStr);
                    if (a0 >= 0 && a1 >= 0 && per[f].pd > 0) per[f] = (Math.Max(0, per[f].pd + a1 - a0), per[f].hits);
                }
            if (per.Count == 0) all = true;
            foreach (var (_, v) in per) { ownHits = Math.Max(ownHits, v.hits); ownMax = Math.Max(ownMax, v.pd); }
            for (int r = 0; r < plays; r++)
            {
                if (inferno > 0 && selfPer > 0) foreach (var f in hittable) Det(f, HitDmg(cs, f, me.Creature, inferno, unpowered, null));
                foreach (var (f, v) in per)
                    for (int i = 0; i < v.hits; i++) { var h = new Hit(v.pd, true, move, dealer, card); seq[f].Add(h); own[f] += h.Dmg; }
                Riders21();
                // 亡灵：血肉戏法（每次上减益）、纠缠（打出灵魂）这次打出的伤害
                foreach (var (rr0, f, h) in nr.hits) if (rr0 == r && seq.ContainsKey(f)) { seq[f].Add(h); extra[f] += h.Dmg; }
            }
            foreach (var (f, v) in nr.exp) if (extra.ContainsKey(f)) extra[f] += v;
            // 浩劫打出的顶牌：确定的逐段记进去，按期望摊的只进 6；它自伤时狱火随之触发
            if (havocTop.Count > 0)
            {
                for (int i = 0; i < havocSelfN && inferno > 0; i++) foreach (var f in hittable) Det(f, HitDmg(cs, f, me.Creature, inferno, unpowered, null));
                foreach (var f in foes)
                {
                    if (hap.sure.TryGetValue(f, out var hs)) foreach (var h in hs) { seq[f].Add(h); extra[f] += h.Dmg; }
                    if (hap.exp.TryGetValue(f, out var ev)) extra[f] += ev;
                }
            }
            // 储君：黑洞（花了星的牌打完、每次得星，对全体层数点无力伤害）
            for (int i = 0; i < rr.blackHoles; i++) foreach (var f in hittable) Det(f, HitDmg(cs, f, me.Creature, rr.blackHole, unpowered, null));
            for (int i = 0; i < juggHits; i++) RandomHit(jugg);
            if (necroLoss > 0) foreach (var f in hittable) { var h = new Hit(HitDmg(cs, f, me.Creature, (decimal)(necroLoss * necro), poisonProps, null), false, poisonProps, me.Creature, null); seq[f].Add(h); extra[f] += h.Dmg; }
            if (charonHit) { var dv = charon!.DynamicVars["Damage"]; for (int x = 0; x < exhaustN; x++) foreach (var f in hittable) Det(f, HitDmg(cs, f, me.Creature, dv.BaseValue, unpowered, null)); }

            var dead = foes.Where(f => seq[f].Count > 0 && SeqHpLoss(f, seq[f]) >= f.CurrentHp).ToList();
            if (_hl29 != null) foreach (var f in foes) if (seq[f].Count > 0) _hl29[f] = SeqHpLoss(f, seq[f]);
            if (doom && card.DynamicVars.TryGetValue("DoomPower", out var doomVar))
            {
                var canon = ModelDb.Power<MP.DoomPower>();
                foreach (var f in hittable)
                {
                    if (dead.Contains(f) || !f.CanReceivePowers) continue;
                    var pw = (PowerModel?)f.GetPower<MP.DoomPower>() ?? canon;
                    decimal given = MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountGiven(cs, pw, me.Creature, doomVar.BaseValue * plays, f, card, out _);
                    int now = f.GetPowerAmount<MP.DoomPower>() + (int)MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountReceived(cs, pw, f, given, me.Creature, out _);
                    float hpNow = f.CurrentHp - (_hl29 != null && _hl29.TryGetValue(f, out var l0) ? l0 : 0);
                    if (hpNow <= now) dead.Add(f);
                }
            }
            if (!all)
            {
                c4 = ownMax; c5 = ownHits; c6 = own[tgt!] + extra[tgt!]; c7 = dead.Contains(tgt!) ? 1 : 0;
            }
            else if (card.TargetType == TargetType.RandomEnemy && hitList == null)
            {
                // 合并后核对 5.2（G20）：打随机敌人的牌在多敌时，牌本身的期望伤害由候选侧（RandomTarget29）写进传进来的 4–6；
                // 这里只把附带伤害加到 6、7 取两者较大（原来整段重算，牌本身的期望伤害丢了）
                c6 += foes.Sum(f => extra[f]); c7 = Math.Max(c7, dead.Count);
            }
            else
            {
                bool hasOwn = own.Values.Any(v => v > 0) || ownHits > 0;
                c4 = hasOwn ? ownMax : foes.Select(f => seq[f].Select(h => h.Dmg).DefaultIfEmpty(0).Max()).DefaultIfEmpty(0).Max();
                c5 = hasOwn ? ownHits : plays;
                c6 = foes.Sum(f => own[f] + extra[f]); c7 = dead.Count;
            }
            return (c4, c5, c6, c7, dead.Count > 0 ? dead : null, block + add, self, ostyEx);
        }
        catch { return (c4, c5, c6, c7, killed, block, 0, 0); }
    }

    /// <summary>
    /// G01：浩劫从抽牌堆顶自动打出的牌（同 AutoPlayEffect 的口径）。抽牌堆里的牌的界面预览不过全局钩子（CardModel.UpdateDynamicVarPreview 只对手牌、出牌区跑钩子），
    /// 打出时它已经在出牌区，所以格挡改用 BlockRecalc、单段伤害改用 DmgRecalc（过全部监听者：力量、敏捷、虚弱、脆弱、易伤等），段数取预览。
    /// </summary>
    (Dictionary<Creature, List<Hit>> sure, Dictionary<Creature, float> exp, float block, float energy, float cards) AutoPlayEffect29(IEnumerable<CardModel> cards, Player me)
    {
        var cs = me.Creature.CombatState!;
        var foes = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
        var sure = foes.ToDictionary(f => f, _ => new List<Hit>());
        var exp = foes.ToDictionary(f => f, _ => 0f);
        float block = 0, energy = 0, drawn = 0;
        var move = MegaCrit.Sts2.Core.ValueProps.ValueProp.Move;
        foreach (var ac in cards)
        {
            try
            {
                if (Kw(ac).Contains(CardKeyword.Unplayable)) continue;
                var hittable = Hittable(cs).Where(f => sure.ContainsKey(f) && !(sure[f].Count > 0 && SeqHpLoss(f, sure[f]) >= f.CurrentHp)).ToList();
                int plays = PlayCount(ac, null);
                bool gb = false;
                try { gb = ac.GainsBlock; } catch { }
                float pb = gb ? BlockRecalc(ac, _ => false) : -1;
                if (pb > 0) block += CardBlockTotal(ac, pb, plays);
                if (ac.Type == CardType.Attack && hittable.Count > 0 && !ShootDisabled(ac))
                {
                    bool aoe = ac.TargetType == TargetType.AllEnemies;
                    foreach (var f in hittable)
                    {
                        var (pv, _, h) = Preview(ac, f);
                        float pd = pv < 0 ? -1 : DmgRecalc(ac, f, _ => false);
                        if (pd <= 0) continue;
                        if (aoe || hittable.Count == 1)
                            for (int r = 0; r < plays; r++) for (int i = 0; i < (int)h; i++) sure[f].Add(new Hit(pd, true, move, me.Creature, ac));
                        else exp[f] += pd * plays * h / hittable.Count;
                    }
                    Preview(ac, null);
                }
                var (ge, gc) = CardGains(ac, null, me, null);
                energy += ge; drawn += gc;
            }
            catch { }
        }
        return (sure, exp, block, energy, drawn);
    }

    /// <summary>
    /// G66①：红骷髅（RedSkull.ModifyStrengthIfNecessary：生命 ≤ 最大生命 × HpThreshold% 时 +Strength 力量）在自伤之后、攻击之前到账：
    /// 遗物活着、现在生命高于阈值（力量还没加）、自伤之后不高于阈值时，返回这一下多出的力量（经加层钩子：RUINED_HELMET 本场第一次加力量翻倍），否则 0。
    /// </summary>
    static decimal RedSkullStr29(ICombatState cs, Player me, float selfPer)
    {
        try
        {
            var skull = me.Relics.FirstOrDefault(r => r is MR.RedSkull && RelicLive(r));
            if (skull == null) return 0;
            decimal th = (decimal)me.Creature.MaxHp * (skull.DynamicVars["HpThreshold"].BaseValue / 100m);
            decimal hp = me.Creature.CurrentHp;
            if (hp <= th || hp - (decimal)selfPer > th) return 0;
            decimal amt = skull.DynamicVars.Strength.BaseValue;
            var canon = (PowerModel?)me.Creature.GetPower<MP.StrengthPower>() ?? ModelDb.Power<MP.StrengthPower>();
            return MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountReceived(cs, canon, me.Creature, amt, me.Creature, out _);
        }
        catch { return 0; }
    }

    // ------------------------------------------------------------------ G38 储君

    /// <summary>
    /// G38（R1-储君 G1、G8）：储君出牌的附带格挡（每一次获得格挡一项，势不可当按次数触发）与黑洞（触发次数、每次的层数）。
    ///   花的星 s（星 X 费取现有的星，一次出牌只花一次，重放不再花）：群星之子 + 层数 × s（无力格挡）；星系尘埃每累计花满 Stars 颗 + Block；
    ///   这一步生成的张数 g（查表，见 RegentGenerated29）：创世之柱每张 + 层数（无力，不吃敏捷）；君王矿石本回合还没触发时 + Block 一次；
    ///   黑洞：花了星记 1 次（AfterCardPlayed，这张牌最后一次打出之后），每次得星（GainStars）各 1 次，每次对全体可攻击的敌人层数点无力伤害。
    /// 迷你储君不做（R3 2.3）；月亮糕点 + 黑洞的回合末不做。
    /// </summary>
    (List<float> gains, int blackHoles, decimal blackHole) RegentRiders29(CardModel card, Creature? tgt, Player me, int plays, List<CardModel> others)
    {
        var gains = new List<float>();
        int holes = 0; decimal hole = 0;
        try
        {
            var cs = me.Creature.CombatState!;
            var pcs = me.PlayerCombatState!;
            int s = 0;
            try { s = card.HasStarCostX ? pcs.Stars : Math.Max(0, StarCost(card)); } catch { }
            if (s > 0 && me.Creature.GetPowerAmount<MP.ChildOfTheStarsPower>() is int cot and > 0) gains.Add(AfterimageBlock(cs, me, cot * s));
            if (s > 0 && me.Relics.FirstOrDefault(r => r is MR.GalacticDust && RelicLive(r)) is MR.GalacticDust gd)
            {
                int per = gd.DynamicVars.Stars.IntValue;
                if (per > 0 && (gd.StarsSpent + s) / per > 0) gains.Add(BlockOf(cs, me, ((gd.StarsSpent + s) / per) * gd.DynamicVars.Block.BaseValue));
            }
            int g = RegentGenerated29(card, me, plays, others);
            if (g > 0)
            {
                int pillar = me.Creature.GetPowerAmount<MP.PillarOfCreationPower>();
                if (pillar > 0) for (int i = 0; i < g; i++) gains.Add(AfterimageBlock(cs, me, pillar));
                if (me.Relics.FirstOrDefault(r => r is MR.Regalite && RelicLive(r)) is { } reg && !UsedThisTurn(reg)) gains.Add(BlockOf(cs, me, reg.DynamicVars.Block.BaseValue));
            }
            hole = me.Creature.GetPowerAmount<MP.BlackHolePower>();
            if (hole > 0)
            {
                if (s > 0) holes++;
                if (card is MC.BigBang or MC.GatherLight or MC.Glow or MC.HiddenCache or MC.RoyalGamble or MC.ShiningStrike or MC.SolarStrike or MC.Venerate
                    && card.DynamicVars.TryGetValue("Stars", out var sv) && sv.BaseValue > 0) holes += plays;
            }
        }
        catch { }
        return (gains, holes, hole);
    }

    /// <summary>
    /// G38：这一步生成几张牌（CardPileCmd.AddGeneratedCardToCombat、变化都触发创世之柱、君王矿石；R1-储君 G1 ②，R3 第八节第 5 条）：
    /// 碰撞轨迹、君权自授、类星体各 1；迫降补满手牌空位；新生之喜、大奖、万事通 Cards 张；传承之锤手里有无色牌时 Repeat 张；下去！手里有别的牌时 1；
    /// 冲锋！！min(Cards, 抽牌堆张数)；锻造牌在场上没有未消耗的君王之剑时 1（ForgeCmd.Forge）；横祸（CalamityPower）在身时打出攻击牌每次 Amount 张。护驾！！！不算。
    /// </summary>
    static int RegentGenerated29(CardModel card, Player me, int plays, List<CardModel> others)
    {
        try
        {
            var pcs = me.PlayerCombatState!;
            int Var(string n) => card.DynamicVars.TryGetValue(n, out var v) ? v.IntValue : 0;
            int g = card switch
            {
                MC.CollisionCourse or MC.ManifestAuthority or MC.Quasar => plays,
                MC.CrashLanding => Math.Max(0, CardPile.MaxCardsInHand - others.Count),
                MC.BundleOfJoy or MC.Jackpot or MC.JackOfAllTrades => Var("Cards") * plays,
                MC.HeirloomHammer => others.Any(c => c.VisualCardPool.IsColorless) ? Var("Repeat") * plays : 0,
                MC.Begone => others.Count > 0 ? 1 : 0,
                MC.Charge => Math.Min(Var("Cards"), pcs.DrawPile.Cards.Count),
                _ => 0,
            };
            bool forge = card.DynamicVars.ContainsKey("Forge") || card.DynamicVars.ContainsKey("CalculatedForge");
            if (forge && !pcs.Hand.Cards.Concat(pcs.DrawPile.Cards).Concat(pcs.DiscardPile.Cards).Any(c => c is MC.SovereignBlade)) g += 1;
            if (card.Type == CardType.Attack && me.Creature.GetPowerAmount<MP.CalamityPower>() is int cal and > 0) g += cal * plays;
            return g;
        }
        catch { return 0; }
    }

    // ------------------------------------------------------------------ G39、G41 亡灵契约师

    /// <summary>
    /// G39（R1-亡灵 N2、N3、N7、N13）：亡灵契约师出牌的附带效果。
    ///   厄运之衣：这一步每一次“灾厄层数实际变化”（施加者是你；人工制品挡掉的不算）各得层数点无力格挡（一次获得格挡一项）；
    ///   血肉戏法：每次给敌人上非临时减益（层数变化 ≠ 0），对它造成层数点可被格挡的无力伤害，在这张牌这次打出的伤害之后（hits，按第几次打出记）；
    ///   灰烬之灵（打出虚无牌）、死亡之舞（费用 ≥ Energy）：每次打出各得层数点无力格挡（BeforeCardPlayed，重放时每次都算）；
    ///   纠缠：打出灵魂，对随机一个可攻击的敌人层数点不吃格挡的伤害（只剩一个时确定，多个时按期望进 exp）；
    ///   吞噬生命：打出灵魂召唤层数点（ostyEx）；紧追不放：奥斯提出手每段打中带它的目标，召唤层数点（ostyEx）。
    /// 减益与灾厄的来源用 Debuffs29（同 Fx29 的牌表，加死神形态每段、湮灭、苦难、荒疫打击）。
    /// </summary>
    (List<float> gains, List<(int r, Creature f, Hit h)> hits, Dictionary<Creature, float> exp, float ostyEx) NecroRiders29(CardModel card, Creature? tgt, Player me, int plays, List<Creature> hittable)
    {
        var gains = new List<float>();
        var hits = new List<(int, Creature, Hit)>();
        var exp = new Dictionary<Creature, float>();
        float ostyEx = 0;
        try
        {
            var cs = me.Creature.CombatState!;
            int shroud = me.Creature.GetPowerAmount<MP.ShroudPower>();
            int sleight = me.Creature.GetPowerAmount<MP.SleightOfFleshPower>();
            int ash = me.Creature.GetPowerAmount<MP.SpiritOfAshPower>();
            var danse = me.Creature.GetPower<MP.DanseMacabrePower>();
            int haunt = me.Creature.GetPowerAmount<MP.HauntPower>();
            int devour = me.Creature.GetPowerAmount<MP.DevourLifePower>();
            bool soul = card is MC.Soul;
            var unpowered = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered;
            for (int r = 0; r < plays; r++)
            {
                if (ash > 0 && Kw(card).Contains(CardKeyword.Ethereal)) gains.Add(AfterimageBlock(cs, me, ash));
                if (danse != null && danse.Amount > 0)
                {
                    int cost = card.EnergyCost.CostsX ? me.PlayerCombatState!.Energy : card.EnergyCost.GetResolved();
                    if (cost >= danse.DynamicVars.Energy.IntValue) gains.Add(AfterimageBlock(cs, me, danse.Amount));
                }
                if (soul && haunt > 0 && hittable.Count > 0)
                {
                    var props = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unblockable | unpowered;
                    if (hittable.Count == 1) hits.Add((r, hittable[0], new Hit(HitDmg(cs, hittable[0], null, haunt, props, null), false, props, null, null)));
                    else foreach (var f in hittable) exp[f] = exp.GetValueOrDefault(f) + HitDmg(cs, f, null, haunt, props, null) / hittable.Count;
                }
                if (soul && devour > 0) ostyEx += Math.Max(0, (float)MegaCrit.Sts2.Core.Hooks.Hook.ModifySummonAmount(cs, me, devour, null));
            }
            // 紧追不放：奥斯提出手每段打中带它（施加者是你）的目标
            if (card.Tags.Contains(CardTag.OstyAttack) && me.IsOstyAlive)
            {
                var targets = tgt != null ? new List<Creature> { tgt } : card.TargetType == TargetType.AllEnemies ? hittable : new List<Creature>();
                foreach (var f in targets)
                    foreach (var se in f.Powers.OfType<MP.SicEmPower>().Where(p => p.Applier == me.Creature))
                    {
                        var (pd, _, h) = Preview(card, f);
                        if (pd > 0) ostyEx += Math.Max(0, (float)MegaCrit.Sts2.Core.Hooks.Hook.ModifySummonAmount(cs, me, se.Amount, null)) * h * plays;
                    }
                Preview(card, null);
            }
            if (shroud > 0 || sleight > 0)
            {
                var art = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToDictionary(f => f, f => f.GetPowerAmount<MP.ArtifactPower>());
                var pois = new Dictionary<Creature, int>();
                int rr = 0;
                void D(Creature f, PowerModel canon, decimal amount)
                {
                    if (amount == 0 || !art.ContainsKey(f) || !f.CanReceivePowers) return;
                    bool debuff = canon.GetTypeForAmount(amount) == MegaCrit.Sts2.Core.Entities.Powers.PowerType.Debuff;
                    if (debuff && art[f] > 0) { art[f]--; return; }
                    var pw = f.Powers.FirstOrDefault(p => p.GetType() == canon.GetType()) ?? canon;
                    decimal got = MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountGiven(cs, pw, me.Creature, amount, f, card, out _);
                    if (f.GetPowerAmount<MP.ArtifactPower>() <= 0) got = MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountReceived(cs, pw, f, got, me.Creature, out _);
                    if (got == 0) return;
                    if (canon is MP.PoisonPower) pois[f] = pois.GetValueOrDefault(f) + (int)got;
                    if (shroud > 0 && canon is MP.DoomPower) gains.Add(AfterimageBlock(cs, me, shroud));
                    if (sleight > 0 && debuff && canon is not MegaCrit.Sts2.Core.Models.ITemporaryPower)
                        hits.Add((rr, f, new Hit(HitDmg(cs, f, me.Creature, sleight, unpowered, null), true, unpowered, me.Creature, null)));
                }
                for (rr = 0; rr < plays; rr++)
                    Debuffs29(card, tgt, me, rr, hittable, f => f.HasPower<MP.PoisonPower>() || pois.GetValueOrDefault(f) > 0, D, vulRule: sleight > 0, hitRules: true);
            }
        }
        catch { }
        return (gains, hits, exp, ostyEx);
    }
}
