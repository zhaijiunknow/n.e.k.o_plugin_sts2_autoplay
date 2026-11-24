using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.ValueProps;
using RL = MegaCrit.Sts2.Core.Models.Relics;
using PT = MegaCrit.Sts2.Core.Models.Potions;

namespace Sts2Headless;

// 格式 29 包 3（R3-格式29规格.md 5.3）：战斗内候选 8–13 与新列 14、15、17 的新算法，选牌候选（G02、G45），药水的 4–8（G10、G78），
// 打随机敌人的 4–7（G20），战斗外的同名计数（G33）。只在 V29 下调用（Candidates.cs 的 AddChoices 分支进来）；格式 19–28 照旧走 Candidates.cs 原来的代码。
public sealed partial class Observation
{
    /// <summary>用药水这一步的效果（第 1 步算好，4–7、8、10、12–13 用）。</summary>
    internal sealed class Pot29
    {
        public float C4, C5, C6, C7, Block, Self;
        public List<Creature>? Killed;
        public List<CardModel>? Gone;
        public Gain29 Gain;
        public int HandAfter;
    }

    /// <summary>选牌候选这一步的效果（G02、G45）。</summary>
    internal sealed class Sel29
    {
        public CardModel Card = null!;
        /// <summary>这一步离手的全部牌：A，以及这一下凑满张数时一并结算的、之前已选的牌。</summary>
        public List<CardModel> All = new();
        public float Block;
        public List<Creature>? Dead;
        public Gain29 Gain;
    }

    /// <summary>
    /// G10、G78：PotionEffects 的超集。带 Damage 的单体、全体药水（火焰药水、爆炸安瓿、药水形状的石头、污浊药水）照出牌口径给 4–7（4 各目标单次伤害最大值、5 = 1、
    /// 6 合计、7 打死几个），打死的进不算名单；污浊药水打全场，对玩家的那一下记进当场挨的伤害（FoulPotion.cs：Creatures.Where(!IsPet)）。
    /// 强化剂的格挡（同 PotionEffects）。发光水消耗整手：无惧疼痛每张给格挡，卡戎之灰每张打全体，黑暗之拥每张抽层数张、战鼓被消耗得能量、金纸、燃烧木棍。
    /// 瓶装潜能把整手洗回抽牌堆。hl 照旧累加这一步对每个敌人造成的掉血（Fx23 用）。
    /// </summary>
    Pot29 PotionEffects29(PotionModel pot, Creature? tgt, Player me, Dictionary<Creature, float>? hl)
    {
        var r = new Pot29();
        try
        {
            var cs = me.Creature.CombatState!;
            var pcs = me.PlayerCombatState!;
            r.HandAfter = pcs.Hand.Cards.Count;
            if (pot is PT.Fortifier) r.Block = BlockOf(cs, me, me.Creature.Block * 2m);
            if (pot.DynamicVars.TryGetValue("Damage", out var dv) && dv.BaseValue > 0)
            {
                var props = dv is MegaCrit.Sts2.Core.Localization.DynamicVars.DamageVar ddv ? ddv.Props : ValueProp.Unpowered;
                bool foul = pot is PT.FoulPotion;
                var targets = pot.TargetType == TargetType.AnyEnemy && tgt != null ? new List<Creature> { tgt }
                    : pot.TargetType == TargetType.AllEnemies || foul ? Hittable(cs).ToList() : new List<Creature>();
                foreach (var f in targets)
                {
                    float d = HitDmg(cs, f, me.Creature, dv.BaseValue, props, null);
                    float lost = FoeHpLoss(f, null, d, 1, f.Block);
                    if (hl != null) hl[f] = hl.GetValueOrDefault(f) + lost;
                    if (lost >= f.CurrentHp) (r.Killed ??= new()).Add(f);
                    r.C4 = Math.Max(r.C4, d); r.C6 += d;
                }
                if (targets.Count > 0) { r.C5 = 1; r.C7 = r.Killed?.Count ?? 0; }
                if (foul) r.Self = HitDmg(cs, me.Creature, me.Creature, dv.BaseValue, props, null);
            }
            if (pot is PT.GlowwaterPotion or PT.BottledPotential)
            {
                var hand = pcs.Hand.Cards.ToList();
                r.Gone = hand; r.HandAfter = 0;
                if (pot is PT.GlowwaterPotion && hand.Count > 0)
                {
                    int n = hand.Count;
                    int fnp = me.Creature.GetPowerAmount<FeelNoPainPower>();
                    if (fnp > 0) r.Block += n * AfterimageBlock(cs, me, fnp);
                    foreach (var rl in me.Relics)
                    {
                        if (!RelicLive(rl)) continue;
                        if (rl is RL.CharonsAshes && rl.DynamicVars.TryGetValue("Damage", out var cd))
                            foreach (var f in Hittable(cs))
                            {
                                if (r.Killed != null && r.Killed.Contains(f)) continue;
                                float d = HitDmg(cs, f, me.Creature, cd.BaseValue, ValueProp.Unpowered, null);
                                float before = hl?.GetValueOrDefault(f) ?? 0;
                                float lost = Math.Min(before + FoeHpLoss(f, null, d, n, before > 0 ? 0 : f.Block), ShellLeft(f));
                                if (hl != null) hl[f] = lost;
                                if (lost >= f.CurrentHp) (r.Killed ??= new()).Add(f);
                            }
                        else if (rl is RL.JossPaper)
                        {
                            int per = rl.DynamicVars["ExhaustAmount"].IntValue;
                            if (per > 0) r.Gain += new Gain29(0, (rl.DisplayAmount + n) / per, 0);
                        }
                        else if (rl is RL.BurningSticks && !UsedThisCombat(rl) && hand.Any(x => x.Type == CardType.Skill)) r.Gain += new Gain29(0, 0, 1);
                    }
                    if (me.Creature.GetPower<DarkEmbracePower>() is { } de && de.Amount > 0) r.Gain += new Gain29(0, de.Amount * n, 0);
                    foreach (var dr in hand.OfType<DrumOfBattle>()) r.Gain += new Gain29((float)dr.DynamicVars.Energy.BaseValue * PlayCount(dr, null), 0, 0);
                }
            }
        }
        catch { }
        return r;
    }

    /// <summary>
    /// G20：打随机敌人（TargetType.RandomEnemy，铭记死亡除外）、可攻击的敌人 ≥ 2 个时的 4–7：4 各可攻击敌人单段伤害的最大值，5 段数，
    /// 6 = Σ_f 单段 × 段数 × 打出次数 / 敌人数（每段等概率打到各敌人的期望总伤害），7 = 0，不进击杀与 hl。不满足条件时返回 false。
    /// </summary>
    static bool RandomTarget29(CardModel c, Player me, out float c4, out float c5, out float c6)
    {
        c4 = c5 = c6 = 0;
        try
        {
            if (c.TargetType != TargetType.RandomEnemy || c is MementoMori) return false;
            var hs = Hittable(me.Creature.CombatState!).Where(f => f.IsAlive).ToList();
            if (hs.Count < 2) return false;
            int plays = PlayCount(c, null);
            foreach (var f in hs)
            {
                var (pd, _, hits) = Preview(c, f);
                if (pd < 0) { c4 = c5 = c6 = 0; Preview(c, null); return true; }
                c4 = Math.Max(c4, pd); c5 = Math.Max(c5, hits); c6 += pd * hits * plays / hs.Count;
            }
            Preview(c, null);
            return true;
        }
        catch { return false; }
    }

    /// <summary>G46：逃脱计划抽到的一定是技能（已知顶部第一张是技能；或抽牌堆不空且全是技能；或抽牌堆空、弃牌堆不空且全是技能），且能抽牌、手没满。</summary>
    static bool EscapeSkillSure(Player me)
    {
        try
        {
            var pcs = me.PlayerCombatState!;
            var cs = me.Creature.CombatState!;
            if (!MegaCrit.Sts2.Core.Hooks.Hook.ShouldDraw(cs, me, false, out _)) return false;
            if (pcs.Hand.Cards.Count - 1 >= CardPile.MaxCardsInHand) return false;
            var draw = pcs.DrawPile.Cards; var disc = pcs.DiscardPile.Cards;
            if (draw.Count > 0)
                return (DrawTop.Known(pcs.DrawPile) > 0 && draw[0].Type == CardType.Skill) || draw.All(x => x.Type == CardType.Skill);
            return disc.Count > 0 && disc.All(x => x.Type == CardType.Skill);
        }
        catch { return false; }
    }

    /// <summary>
    /// 出牌候选 8 里卡面格挡以外、这张牌自己再给的格挡：G68 壕沟（当前格挡 × (2^次数 − 1)，Unpowered | Move，Entrench.cs:21-23）；
    /// G81 附魔自带的格挡（伶俐：每次打出 GainBlock 附魔的 Block，Move，第一次带拉伸、重放不带，Adroit.cs）；G32 拳拳到肉（每次打出获得对目标造成的伤害，Move，Fisticuffs.cs:32）。
    /// </summary>
    static float CardBlockExtra29(CardModel c, Creature? tgt, Player me, int plays)
    {
        float add = 0;
        try
        {
            var cs = me.Creature.CombatState!;
            float Blk(decimal amount, ValueProp props) => MathF.Floor((float)Math.Max(0m, MegaCrit.Sts2.Core.Hooks.Hook.ModifyBlock(cs, me.Creature, amount, props, c, null, out _)));
            if (c is Entrench && me.Creature.Block > 0)
                add += Blk(me.Creature.Block * (decimal)(Math.Pow(2, plays) - 1), ValueProp.Unpowered | ValueProp.Move);
            if (c.Enchantment is { GainsBlock: true } en && en.DynamicVars.TryGetValue("Block", out var eb) && eb.BaseValue > 0)
            {
                float first = Blk(eb.BaseValue, ValueProp.Move);
                float stretch = me.Creature.GetPower<StretchPower>() is { } st && plays > 1 ? st.Amount : 0;
                add += first + (plays - 1) * Math.Max(0, first - stretch);
            }
            if (c is Fisticuffs && tgt != null)
            {
                var (pd, _, hits) = Preview(c, tgt);
                Preview(c, null);
                if (pd > 0) add += plays * Blk((decimal)(pd * hits), ValueProp.Move);
            }
        }
        catch { }
        return add;
    }

    /// <summary>这一帧选牌界面里已经选了、还没结算的牌（选项里不在候选中的；AddChoices 开头按帧算，V29 才算）。</summary>
    List<CardModel>? _selPending;

    /// <summary>
    /// G02、G45：战斗中从手牌选牌消耗、弃掉的候选（_sel 的来源是手牌，对象 A 在手里）。每个候选按“只选这一张”算：
    /// 消耗（提示 TO_EXHAUST，或来源是 PURITY、灰水）：无惧疼痛的格挡、卡戎之灰打全体（打死的进不算名单）、黑暗之拥抽层数张、金纸、燃烧木棍、选的是战鼓时的能量；
    /// 弃牌（提示 TO_DISCARD，或来源是赌徒特酿、赌博筹码）：A 本回合奇巧时自动打出（格挡、确定伤害打死的、能量与进手），铜钹打随机敌人（只剩一个可攻击敌人时确定），
    /// 结实绷带的格挡，赌徒特酿、赌博筹码弃一张抽一张。不是这两类、或 A 不在手里时返回 null（照旧）。
    /// </summary>
    Sel29? SelectFx29(Choice ch, Player me, Dictionary<Creature, float>? hl)
    {
        try
        {
            if (ch.Kind != ActionKind.SelectCard || ch.A is not CardModel a || _sel?.Info is not { } info) return null;
            if (info.Method == null || !info.Method.StartsWith("FromHand", StringComparison.Ordinal)) return null;
            var pcs = me.PlayerCombatState!;
            if (!pcs.Hand.Cards.Contains(a)) return null;
            bool exh = info.PromptKey == "TO_EXHAUST" || info.Source is Purity or PT.Ashwater;
            bool dis = !exh && (info.PromptKey == "TO_DISCARD" || info.Source is PT.GamblersBrew or RL.GamblingChip);
            if (!exh && !dis) return null;
            var cs = me.Creature.CombatState!;
            var r = new Sel29 { Card = a };
            // 选多张时，这一下凑满上限就连同之前已选的一起结算；凑不满时按“只选这一张”算（R3 规格 G02）
            r.All.Add(a);
            int max = Math.Min(_sel!.Max, _sel.Options.Count);
            if (_selPending is { Count: > 0 } pend && pend.Count + 1 >= max) r.All.AddRange(pend.Where(x => pcs.Hand.Cards.Contains(x)));
            int n = r.All.Count;
            void Hit(Creature f, float d)
            {
                if (d <= 0 || (r.Dead?.Contains(f) ?? false)) return;
                float before = hl?.GetValueOrDefault(f) ?? 0;
                float lost = Math.Min(before + FoeHpLoss(f, null, d, 1, before > 0 ? 0 : f.Block), ShellLeft(f));
                if (hl != null) hl[f] = lost;
                if (lost >= f.CurrentHp) (r.Dead ??= new()).Add(f);
            }
            if (exh)
            {
                int fnp = me.Creature.GetPowerAmount<FeelNoPainPower>();
                if (fnp > 0) r.Block += n * AfterimageBlock(cs, me, fnp);
                if (me.Creature.GetPower<DarkEmbracePower>() is { } de && de.Amount > 0) r.Gain += new Gain29(0, de.Amount * n, 0);
                foreach (var dr in r.All.OfType<DrumOfBattle>()) r.Gain += new Gain29((float)dr.DynamicVars.Energy.BaseValue * PlayCount(dr, null), 0, 0);
                foreach (var rl in me.Relics)
                {
                    if (!RelicLive(rl)) continue;
                    if (rl is RL.CharonsAshes && rl.DynamicVars.TryGetValue("Damage", out var cd))
                        for (int x = 0; x < n; x++) foreach (var f in Hittable(cs)) Hit(f, HitDmg(cs, f, me.Creature, cd.BaseValue, ValueProp.Unpowered, null));
                    else if (rl is RL.JossPaper)
                    {
                        int per = rl.DynamicVars["ExhaustAmount"].IntValue;
                        if (per > 0) r.Gain += new Gain29(0, (rl.DisplayAmount + n) / per, 0);
                    }
                    else if (rl is RL.BurningSticks && !UsedThisCombat(rl) && r.All.Any(x => x.Type == CardType.Skill)) r.Gain += new Gain29(0, 0, 1);
                }
            }
            else
            {
                var sly = r.All.Where(x => x.IsSlyThisTurn).ToList();
                if (sly.Count > 0)
                {
                    var ap = AutoPlayEffect(sly, me);
                    r.Block += ap.block;
                    foreach (var (f, hs) in ap.sure)
                        if (hs.Count > 0 && SeqHpLoss(f, hs) + (hl?.GetValueOrDefault(f) ?? 0) >= f.CurrentHp) (r.Dead ??= new()).Add(f);
                    foreach (var x in sly) r.Gain += CardGains29(x, null, me, null);
                }
                var hit = Hittable(cs).Where(f => !(r.Dead?.Contains(f) ?? false)).ToList();
                foreach (var rl in me.Relics)
                {
                    if (!RelicLive(rl)) continue;
                    if (rl is RL.Tingsha && hit.Count == 1 && rl.DynamicVars.TryGetValue("Damage", out var td))
                        for (int x = 0; x < n; x++) Hit(hit[0], HitDmg(cs, hit[0], me.Creature, td.BaseValue, ValueProp.Unpowered, null));
                    else if (rl is RL.ToughBandages && rl.DynamicVars.TryGetValue("Block", out var tb)) r.Block += n * BlockOf(cs, me, tb.BaseValue);
                }
                if (info.Source is PT.GamblersBrew or RL.GamblingChip) r.Gain += new Gain29(0, n, 0);
            }
            return r;
        }
        catch { return null; }
    }

    /// <summary>G25：一张牌如果留在手里到回合末，对自己造成的伤害（没有回合末手牌效果为 0；悔恨为回合末手牌张数；有 HpLoss 取它，否则取 Damage；同 EndTurnPreview 的 handSeq）。</summary>
    static float EndTurnSelfDmg(CardModel c, float handEnd)
    {
        try
        {
            if (!c.HasTurnEndInHandEffect) return 0;
            if (c is Regret) return Math.Max(0, handEnd);
            if (c.DynamicVars.TryGetValue("HpLoss", out var h)) return (float)h.BaseValue;
            if (c.DynamicVars.TryGetValue("Damage", out var d)) return (float)d.BaseValue;
        }
        catch { }
        return 0;
    }

    /// <summary>
    /// G25 候选 15：这一步抽 d 张牌，抽到的牌在回合末对自己造成的期望伤害（不过格挡）。前 DrawTop.Known 张按实际牌，其余按抽牌堆里未知部分的平均，
    /// 超过抽牌堆张数的部分按弃牌堆的平均（洗牌）。handEnd 为回合末的手牌张数（悔恨用）。
    /// </summary>
    static float DrawRisk29(Player me, float d, float handEnd)
    {
        try
        {
            var pcs = me.PlayerCombatState!;
            var draw = pcs.DrawPile.Cards; var disc = pcs.DiscardPile.Cards;
            int n = (int)d;
            if (n <= 0) return 0;
            int known = DrawTop.Known(pcs.DrawPile);
            if (draw.Count > 0 && draw.All(x => SameCard(x, draw[0]))) known = draw.Count;
            known = Math.Min(known, draw.Count);
            float sum = 0;
            int k = Math.Min(n, known);
            for (int i = 0; i < k; i++) sum += EndTurnSelfDmg(draw[i], handEnd);
            int rest = n - k;
            int unk = draw.Count - known;
            int fromDraw = Math.Min(rest, unk);
            if (fromDraw > 0) sum += fromDraw * draw.Skip(known).Average(x => EndTurnSelfDmg(x, handEnd));
            int fromDisc = Math.Min(rest - fromDraw, disc.Count);
            if (fromDisc > 0) sum += fromDisc * disc.Average(x => EndTurnSelfDmg(x, handEnd));
            return sum;
        }
        catch { return 0; }
    }

    /// <summary>G33：战斗外对象 A 是卡牌的候选，牌组里与 A 同词表 ID 的其他张数、其中已升级的、带附魔的，与 A 同类型的其他张数（A 本身在牌组里时不算它）。写 _cy[0..3]。</summary>
    void DeckCounts29(Choice ch, Player me)
    {
        try
        {
            if (ch.A is not CardModel a) return;
            int id = Vocab.Id(a);
            var type = a.Type;
            foreach (var x in me.Deck.Cards)
            {
                if (ReferenceEquals(x, a)) continue;
                if (Vocab.Id(x) == id)
                {
                    _cy[0]++;
                    if (x.CurrentUpgradeLevel > 0) _cy[1]++;
                    if (x.Enchantment != null) _cy[2]++;
                }
                if (x.Type == type) _cy[3]++;
            }
        }
        catch { }
    }

    /// <summary>
    /// 格式 29：战斗内候选的 8–13 与新列 14（G04）、15（G25）、17（G50）。计算顺序见 R3-格式29规格.md 5.3：
    /// 1. 8（卡面格挡只在 GainsBlock 时计，G37；逃脱计划只在确定抽到技能时计，G46；壕沟、附魔、拳拳到肉，G68、G81、G32；激发；药水；选牌），
    ///    当场挨的伤害 pre（荆棘、通电、污浊药水）；
    /// 2. 能量、抽牌、进手（CandGains29：G06–G09、G14、G65、G71、G78），算盘（G77）、候选 15（G25）；
    /// 3. Fx23、回合末被动打得死的、敌人的反应；主要敌人全死时次要敌人一并不出手（G04）；
    /// 4. 这一步之后战斗当场结束（G04）：候选 14 为 1，10 = 自伤（扣恶魔之舌）+ 当场挨的伤害过格挡；否则 fx 补齐（G03 SelfLoss、G27 Dead、G50 Thorns、G32 Gambit、G02 Played）后走预演；
    /// 5. 流沙（战斗结束时不覆盖）；11（NOT_YET 按回血之后的生命，G32）；
    /// 6. 12、13，候选 17 可用箭数（G50）。
    /// 出牌之外、药水之外、不是消耗弃牌选牌的战斗候选同格式 28（结束回合的 10 为现在的预计掉血）。
    /// </summary>
    (float c8, float c9, float c10, float c11, float g12, float g13) CombatCand29(Choice ch, Player me, List<Creature>? killed, Dictionary<Creature, float>? hl,
        Dictionary<Creature, int>? pout, float riderBlock, float selfLoss, float ostyEx, Pot29? pot)
    {
        float c8 = 0, c9 = 0, c10 = 0, c11 = 0;
        var cs = me.Creature.CombatState!;
        var pcs = me.PlayerCombatState!;
        int hand = pcs.Hand.Cards.Count;
        var played = ch.Kind == ActionKind.PlayCard ? ch.A as CardModel : null;
        var tgt = ch.B as Creature;
        int plays = played != null ? PlayCount(played, tgt) : 1;
        // 第 1 步
        float preNow = 0;
        List<Creature>? stunA = null, skip = null;
        Sel29? sel = null;
        Gain29 gain = default;
        int handAfter = hand, extraDiscard = 0;
        bool gains = false;
        float witherGen = 0;
        try
        {
            if (played != null)
            {
                var (_, pb, _) = Preview(played, tgt);
                Preview(played, null);
                if (!played.GainsBlock) pb = 0;                                    // G37：能力牌的 Block 是层数（创世之柱、厄运之衣）
                if (played is EscapePlan && !EscapeSkillSure(me)) pb = 0;          // G46
                // 合并后核对 G02：重振精神每消耗一张非攻击牌一份格挡，第二张起由 Riders29 的 ownGains 补上，这里的基数在一张都没得消耗时为 0
                if (pb > 0 && !(played is SecondWind && ExhaustOthers(played, me) == 0)) c8 = CardBlockTotal(played, pb, plays);
                c8 += CardBlockExtra29(played, tgt, me, plays);
                var (imm, pas, ok) = OrbFx(played, tgt, me, hl, killed);
                c8 += imm; c9 = pas;
                if (ok != null) { killed ??= new(); foreach (var f in ok) if (!killed.Contains(f)) killed.Add(f); }
                skip = killed;
                var (th, st1) = AttackReactions(played, tgt, me);
                preNow += th + GalvanizedSelfDamage(played, me);
                stunA = st1;
                handAfter = ClearsHand(played) ? 0 : Math.Max(0, hand - 1 + _autoHand - LeavesFirst(played, hand - 1));
                if (played is CalculatedGamble or WheelOfFortune) extraDiscard = OthersInHand(pcs, played);
                gain = CardGains29(played, tgt, me, killed) + RelicGains29(played, tgt, me, killed, plays) + new Gain29(_autoGain.energy, 0, _autoGain.cards);
                // 永世沙漏的凋萎存在（同 CandGains）：倒数到 0 时往手里塞一张凋萎，每 6 张一轮
                foreach (var f in cs.Creatures)
                    foreach (var wp in f.GetPowerInstances<WitheringPresencePower>())
                        if (wp.DisplayAmount >= 1 && wp.DisplayAmount <= plays) witherGen += 1 + (plays - wp.DisplayAmount) / 6;
                gains = true;
            }
            else if (ch.Kind == ActionKind.UsePotion && ch.A is PotionModel pot2)
            {
                if (pot2.DynamicVars.TryGetValue("Block", out var pbv)) c8 = (float)pbv.BaseValue;
                if (pot != null)
                {
                    c8 += pot.Block; preNow += pot.Self; handAfter = pot.HandAfter;
                    if (pot.Killed != null) skip = killed = pot.Killed;
                    gain = PotionGains29(pot2, me, handAfter) + pot.Gain;
                }
                gains = true;
            }
            else if ((sel = SelectFx29(ch, me, hl)) != null)
            {
                c8 = sel.Block;
                if (sel.Dead != null) skip = killed = sel.Dead;
                handAfter = Math.Max(0, hand - sel.All.Count);
                gain = sel.Gain;
                gains = true;
            }
        }
        catch { }
        if (riderBlock > 0) c8 += riderBlock;
        // 第 2 步：得失（G08：这一步当场掉没被挡住的血时百年积木抽 3 张）
        bool hurtNow = selfLoss > 0 || preNow > me.Creature.Block + c8;
        var go = gains ? CandGains29(me, gain, handAfter, hurtNow, extraDiscard) : default;
        try
        {
            // G77 算盘：这一步抽的牌多于抽牌堆、弃牌堆不空，会洗牌（TheAbacus.cs:21-26）
            if (go.Drawn > pcs.DrawPile.Cards.Count && pcs.DiscardPile.Cards.Count > 0)
                foreach (var rl in me.Relics)
                    if (rl is RL.TheAbacus && RelicLive(rl) && rl.DynamicVars.TryGetValue("Block", out var ab)) c8 += BlockOf(cs, me, ab.BaseValue);
            if (go.Drawn > 0) _cy[1] = DrawRisk29(me, go.Drawn, handAfter + go.Cards);      // G25 候选 15
        }
        catch { }
        float space = Math.Max(0, CardPile.MaxCardsInHand - handAfter);
        float g13 = Math.Min(go.Cards + witherGen, space);
        // G50 候选 17：这一步之后的可用箭数
        try
        {
            int arrows = pcs.AvailableArrowCount;
            if (played != null && (played.Type == CardType.Attack || played is Recall or Uproot or Wrest))
            {
                var (rh, rd, shots) = ArrowsOut(played, tgt, me, tgt != null && killed != null && killed.Contains(tgt));
                arrows = arrows - shots + rh + rd;
            }
            _cy[3] = Math.Max(0, arrows);
        }
        catch { }
        if (_lossAfter == null) return (0, 0, 0, 0, go.Energy, g13);
        // 格式 22：再打一张牌就塞进手里的凋萎；G25：手牌满了它进弃牌堆，不算
        float wither = ch.Kind == ActionKind.PlayCard ? _nextPlaySelfDmg : 0;
        if (wither > 0 && handAfter + go.Cards >= CardPile.MaxCardsInHand) wither = 0;
        float handEx = preNow + wither;
        // 第 3 步
        Dictionary<Creature, FoeMod>? mod = null;
        try
        {
            if (ch.Kind is ActionKind.PlayCard or ActionKind.UsePotion)
            {
                var (m23, pskip) = Fx23(ch, me, hl!, pout ?? new(), killed);
                mod = m23;
                if (pskip != null) skip = skip == null ? pskip : skip.Concat(pskip).ToList();
            }
            if (_sureHits != null && ch.Kind is ActionKind.PlayCard or ActionKind.UsePotion)
            {
                var live = Hittable(cs).Where(f => f.IsAlive && !(killed?.Contains(f) ?? false)).ToList();
                var orbsNow = _orbAfter ?? pcs.OrbQueue.Orbs.Select(o => (k: o switch
                {
                    MegaCrit.Sts2.Core.Models.Orbs.LightningOrb => 'L', MegaCrit.Sts2.Core.Models.Orbs.GlassOrb => 'G', _ => '?',
                }, pv: (float)o.PassiveVal, trig: PassiveTriggers(cs, o))).ToList();
                foreach (var f in live)
                {
                    if ((skip?.Contains(f) ?? false) || !_sureHits.TryGetValue(f, out var sh)) continue;
                    var hits = new List<float>();
                    foreach (var o in orbsNow)
                        for (int t = 0; t < o.trig; t++)
                        {
                            if (o.k == 'L' && live.Count == 1) hits.Add(o.pv);
                            else if (o.k == 'G' && o.pv - t > 0) hits.Add(o.pv - t);
                        }
                    hits.AddRange(sh.hits.Skip(sh.orbN));
                    if (hits.Count == 0) continue;
                    float lost = hl?.GetValueOrDefault(f) ?? 0, blk2 = lost > 0 ? 0 : f.Block, more = 0;
                    foreach (var v in hits)
                    {
                        float sd = v, b2 = Math.Min(blk2, sd); blk2 -= b2; sd -= b2;
                        if (sd <= 0) continue;
                        try { sd = (float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyHpLost(RunSession.State, cs, f, (decimal)sd, ValueProp.Unpowered, me.Creature, null, MegaCrit.Sts2.Core.Hooks.HpLossHookPhase.All, out _); } catch { }
                        more += MathF.Floor(Math.Max(0, sd));
                    }
                    if (Math.Min(lost + more, ShellLeft(f)) >= f.CurrentHp) (skip = skip == null ? new List<Creature>() : skip.ToList()).Add(f);
                }
            }
            // G04：这一步打死的、回合末被动打得死的、毒得死的去掉之后没有活着的主要敌人：次要敌人随之死亡，都不出手
            if (skip != null && skip.Count > 0 && CombatEndsWithout(cs, skip))
            {
                var sec = cs.Creatures.Where(e => e.IsAlive && e.IsSecondaryEnemy && !skip.Contains(e)).ToList();
                if (sec.Count > 0) skip = skip.Concat(sec).ToList();
            }
        }
        catch { }
        CandFx fx = default;
        try
        {
            List<Creature>? stun = stunA;
            var (st2, crab) = ThresholdReactions(cs, me, hl, skip);
            if (st2 != null) stun = stun == null ? st2 : stun.Concat(st2).ToList();
            if (stun != null) skip = skip == null ? stun : skip.Concat(stun).ToList();
            if (crab != null)
            {
                mod ??= new();
                foreach (var (f, add) in crab) mod[f] = mod.TryGetValue(f, out var m0) ? m0 with { Add = m0.Add + add } : new FoeMod(add, false);
            }
            var (plat, intg, bufN) = DefensiveGains(ch, me);
            float boost = played is Shadowmeld smc ? (float)Math.Pow(2, (double)smc.DynamicVars["Power"].BaseValue * PlayCount(smc, null)) - 1 : 0;
            int hd = played != null ? -1 + _autoHand : sel != null ? -sel.All.Count : pot?.Gone != null ? -pot.Gone.Count : 0;
            IReadOnlyCollection<CardModel>? goneAll = played != null ? _autoGone : sel != null ? sel.All : pot?.Gone;
            fx = new CandFx(c8, played != null && played.Type == CardType.Attack, hd, plat, intg, bufN, boost, goneAll)
            {
                SelfLoss = selfLoss, Dead = killed, Thorns = _thornsGain, Gambit = played is TheGambit, Played = played,
            };
        }
        catch { }
        bool extraNext = (played is TheHangedMan) || (ch.Kind == ActionKind.UsePotion && ch.A is PT.Ambergris);
        if (extraNext) skip = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
        CardModel? gone = played != null && played.HasTurnEndInHandEffect ? played : sel?.Card;
        // G32 恶魔之舌：遗物活着、本回合没触发时，这一步的第一次自伤会回同样多的血
        float selfLoss2 = selfLoss;
        if (selfLoss > 0 && played != null && me.Relics.Any(r => r is RL.DemonTongue && RelicLive(r) && !UsedThisTurn(r))) selfLoss2 = selfLoss - selfLoss / Math.Max(1, plays);
        // 第 4 步：G04 战斗当场结束
        bool ends = false;
        try { ends = gains && killed != null && killed.Count > 0 && CombatEndsWithout(cs, killed); } catch { }
        if (ends)
        {
            _cy[0] = 1;
            c10 = selfLoss2 + Math.Max(0, preNow - (me.Creature.Block + c8));
        }
        else
        {
            var lossFn = _lossAfterFlip != null && SurroundTurns(ch, killed) ? _lossAfterFlip
                : played != null && _lossAfterNoExtra != null ? _lossAfterNoExtra : _lossAfter;
            try { c10 = lossFn(c8 + c9, skip, ostyEx, handEx, mod, gone, fx); } catch { c10 = 0; }
            // 合并后核对 5.1：Loss29 对出牌候选判必死（THE_GAMBIT、玩家灾厄）时返回 ≥ 最大生命的标记；这里换成“这一步之后的生命 + 这一步的自伤”，
            // 即现在的生命加 NOT_YET 的回血，与打出之后那一帧结束回合的候选 10（必死时等于那时的生命）对得上，候选 11 随之 ≤ 0
            bool sureDeath = c10 >= me.Creature.MaxHp && played != null
                && (played is TheGambit || me.Creature.HasPower<TheGambitPower>() || me.Creature.GetPowerAmount<DoomPower>() > 0);
            float heal29 = played is NotYet ny0 && ny0.DynamicVars.TryGetValue("Heal", out var hv0) ? (float)hv0.BaseValue * plays : 0;
            if (sureDeath) c10 = Math.Min(me.Creature.MaxHp, me.Creature.CurrentHp + heal29) - selfLoss2;
            c10 += selfLoss2;
            if (extraNext && played is TheHangedMan hm && !(selfLoss > 0)) c10 += CardSelfHpLoss(me, hm);
            // 第 5 步：流沙（同格式 28）
            if (_devourer != null && !(skip?.Any(x => ReferenceEquals(x, _devourer)) ?? false) && played is not FranticEscape)
                c10 = me.Creature.CurrentHp;
        }
        c11 = me.Creature.CurrentHp - c10;
        // G32 NOT_YET：按回血之后的生命（v0.113.1 没有回血钩子，CreatureCmd.Heal 直接加，封顶最大生命）
        if (played is NotYet ny && ny.DynamicVars.TryGetValue("Heal", out var hv))
            c11 = Math.Min(me.Creature.MaxHp, me.Creature.CurrentHp + (float)hv.BaseValue * plays) - c10;
        return (c8, c9, c10, c11, go.Energy, g13);
    }
}
