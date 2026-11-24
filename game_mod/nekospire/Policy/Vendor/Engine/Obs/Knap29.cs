using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.ValueProps;

namespace Sts2Headless;

// 格式 29 的背包（敌人 18“本回合最多还能打多少”、19“打不打得死”）：包 2 的文件（R3-格式29规格.md 5.2）。
// EndTurnPreview 在 V29 下对每个敌人调 Knap29：返回 -1 表示不改（沿用原来的背包，帧与格式 28 相同），≥ 0 时替换原来的 best。
// 只对“这一回合内出牌能确定打出的伤害”负责；回合末被动、滑溜、硬化外壳仍由 EndTurnPreview 在 best 之后处理。全部只读（不改游戏对象）。
//
// 先做便宜的预检（Knap29Maybe：只看手牌、能力、遗物的类型），没有可能的条件就直接返回 -1，绝大多数帧不多花时间。
// 有条件时整份重算：按 KnapForFoe 的写法给每张牌构造选项（能量、星、箭、免费层、多打层、首张加成、束缚、张数），加上下面的扩展，
// 用 K29.Solve 里的多维 0–1 背包（每件至多选一个选项；张数维按“恰好选了几张”记，给位置相关的价值用）求最大值：
//   G16 凡庸：张数上限 3 − 本回合已打张数。ENTHRALLED：先扣它的费用，其余牌按“没有它时能不能打”。鸣响：挂鸣响的牌至多 1 张（同束缚维）。
//   G17 ONE_TWO_PUNCH：多打层（原来只给叽 诗句）改为“攻击牌多打一次”的层数，两者同时出现取层数多的。
//       幻影之刃、致死性：首张加成维（原来只给瞄准、寂灭），只有一张吃到加成；三种同时出现两种以上时只处理多算得最多的一种，其余照旧（按预览）。
//       奥斯提出手的牌算不了跳过致死性的伤害，按预览 ÷ (1 + 层数 / 100) 四舍五入。
//       虚空形态：免费层 = 层数 − 本回合已打张数，免费选项能量费、星费都为 0，不免费的费用去掉虚空形态（CostNoFree29、StarCostNoFree29）。
//   G18 X 费：X 费攻击（能量）与星尘（星）放在最后打：对留给它的每个 x，背包(其余资源) + 单段 × 段数(X(x)) × 打出次数。
//   G19 恶魔之焰放在最后打，段数 = 现在的段数 − 之前打出的张数。迟缓、软弱：每次打出占一个位置，第 j 次的单段伤害按迟缓乘数 1 + 0.1(s + j)
//       重算（PdSlow）或力量 −j 重算（PdWithStr）；迟缓时不打伤害的牌也进背包（价值 0，剩下的能量先打它们抬高迟缓）。每张牌只有一个选项时
//       （绝大多数情形）按子集精确求最好的出牌顺序，否则按迟缓伤害升序、软弱段数降序的固定顺序；
//       胆小：best > 格挡且没有单张牌打得死时 best − 层数（生命 + 格挡的口径：打穿格挡之后它加的格挡抵掉同样多）。
//   G20 打随机敌人的牌：对 f 的价值除以可攻击的敌人数；铭记死亡只对 MementoTarget 计全额。
//   G43 愚者：射击选项加爆炸箭矢的全体伤害；选过哇（本回合还没打过哇）时加一次暴力和弦（旗标维）。
//       铁甲：卡戎之灰（消耗张数 × 伤害；重振精神只数不进背包的非攻击牌，免得与进背包的消耗牌重复）、狱火（自伤牌每次打出）、
//       势不可当（只剩一个可攻击敌人时，每次获得格挡）进各牌的价值。
//   G64 撕咬：手里的撕咬合成一组，选 k 张时第 i 张的基础伤害加上之前各张的 Increase（PdWithStr）。
//   G76 得能量、得星：不限张数时，先按费用升序把净得能量 / 得星的牌打掉，余下的再做背包（与不先打的取大）。
//       现在只因能量、星不够打不出的牌也收进来（原样那份里费用约束照样挡掉）。
//   G76、G66②、G69 加力量：对每张本回合加力量的牌 b（燃烧、FEEDING_FRENZY、准备打击、烙印等，疯狂科学专长；红骷髅下让生命跨过一半的自伤牌，
//       RUINED_HELMET 首次翻倍），先打 b，其余攻击的单段伤害按 PdWithStr 的力量增量重算，与不先打的取大。另加几份（规格外的一小步，叠加）：
//       加力量的非攻击牌能打就一起先打，再各试一张加力量的攻击牌（两张 FEEDING_FRENZY、FEEDING_FRENZY + 准备打击）。先打的牌不能把自己打死。
// 另外两处同属背包口径的修正：自己的伤害只算打敌人的牌（能打出的毒素等状态牌的 Damage 是打自己的，原来被算成对敌人的伤害）；
// 先打掉的牌也让迟缓加一层、软弱减一点力量。
// 状态数超过上限（40 万）时返回 -1（沿用原来的背包）；出错时也返回 -1。
public sealed partial class Observation
{
    static readonly System.Reflection.FieldInfo? HelmetUsedField = GameCompat.Field(typeof(RuinedHelmet), "_usedThisCombat", "RUINED_HELMET 本场是否已翻倍过力量（格式 29 背包，G66②）");

    static float Knap29(ICombatState cs, Player me, Creature f, PlayerCombatState pcs, int energy, int stars)
    {
        K29? k = null;
        try
        {
            if (!Knap29Maybe(cs, me, f, pcs)) return -1;
            k = new K29(cs, me, f, pcs, energy, stars);
            return k.Run();
        }
        catch { return -1; }
        finally { k?.ResetPreviews(); }
    }

    /// <summary>背包的一个选项：占用（能量、星、箭（可为负：拔回）、免费层、多打层、首张加成、束缚、旗标、张数）与价值（自己的伤害 Own、附带伤害 Rider）。</summary>
    sealed class K29Opt
    {
        public int E, S, A, F, V, M, B, G, J, P = 1;     // P：打出次数（迟缓、软弱按每次打出各算一个位置）
        public float Own, Rider, HitsTotal;
        public int Card = -1;           // 位置相关的价值用：K29.Cards 的下标（撕咬组为 -1）
    }

    /// <summary>手里一张牌对敌人 f 的背包信息（与先打哪张无关的部分）。</summary>
    sealed class K29Card
    {
        public CardModel C = null!;
        public int Cost, Sc, CostNF, ScNF, Free, Plays, Ac, Recall;
        public float Pd, Pdx, Hits, Pb, Rider, ExploPerShot, RandF = 1;
        public bool Attack, Shoot, Melody, Wah, BoundLike, IsMaul, IsFiend, IsX, IsStarX, PdFromDamageVar;
        public float EnergyNet, EnergyGross, StarGain, StrGain, SelfLoss;
        public decimal StrOwn;          // 这张牌自己加的力量（× 打出次数，未按 RUINED_HELMET 翻倍）；StrGain 另含单独先打它时红骷髅的 +3，并已翻倍
        public bool PreGain;            // G76：净得能量或得星
    }

    /// <summary>一次背包求解的变体：先打掉的牌、可用资源、力量增量、额外价值、离手张数（恶魔之焰的段数用）。</summary>
    readonly record struct K29Var(HashSet<int> Gone, int E, int S, int F, int K, int B, decimal Str, float Extra, int HandGone);

    sealed class K29
    {
        readonly ICombatState cs; readonly Player me; readonly Creature f; readonly PlayerCombatState pcs;
        readonly int energy0, stars0;
        readonly List<CardModel> touched = new();
        public readonly List<K29Card> Cards = new();
        List<Creature> foes = null!; IReadOnlyList<Creature> hittable = null!;
        bool fHit;
        // 约束
        int kcap = int.MaxValue; bool chains, ringing; int nEnth;
        bool arrowDim, kRecall; int avail, A, A0;
        int freeMax; bool voidOn;
        int vLayers; int vMech;        // 多打层：0 无，1 叽 诗句，2 ONE_TWO_PUNCH
        int mMech;                     // 首张加成：0 无，1 瞄准、寂灭，2 幻影之刃，3 致死性
        // 位置相关
        bool slow, tender; float slowS;
        // 附带伤害
        float chordDmg; float exploDmg;
        // 撕咬组
        readonly List<int> mauls = new();
        int fiend = -1;
        readonly List<int> xCards = new();
        bool skittish; float skittishAmt;
        bool conditions;
        float maxSingle;
        decimal curStr;                // 正在求解的变体的力量增量（软弱的位置价值用）
        int posOff;                    // 正在求解的变体先打掉了几张（迟缓、软弱的位置从这里起）
        readonly Dictionary<(int card, int pos, decimal str), float> posMemo = new();

        public K29(ICombatState cs, Player me, Creature f, PlayerCombatState pcs, int energy, int stars)
        {
            this.cs = cs; this.me = me; this.f = f; this.pcs = pcs; energy0 = energy; stars0 = stars;
        }

        public void ResetPreviews() { foreach (var c in touched) { try { Preview(c, null); } catch { } } }

        static int PlayedThisTurn(ICombatState cs, Player me, Func<CardModel, bool>? pred = null)
        {
            try { return CombatManager.Instance.History.CardPlaysStarted.Count(e => e.HappenedThisTurn(cs) && e.CardPlay.Player == me && (pred == null || pred(e.CardPlay.Card))); }
            catch { return 0; }
        }

        public float Run()
        {
            var hand = pcs.Hand.Cards.ToList();
            foes = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
            hittable = Hittable(cs);
            fHit = IsHittableM(f);
            int nH = hittable.Count;

            // ---------- 张数上限、束缚、ENTHRALLED（G16）
            bool normality = hand.Any(c => c is Normality);
            foreach (var pw in me.Creature.Powers)
            {
                if (pw is SlothPower) kcap = Math.Min(kcap, Math.Max(0, pw.Amount - pw.DisplayAmount));
                else if (pw is EncorePower) kcap = Math.Min(kcap, Math.Max(0, pw.DisplayAmount));
            }
            foreach (var r in me.Relics)
                if (r is VelvetChoker && RelicLive(r)) kcap = Math.Min(kcap, Math.Max(0, r.DynamicVars.Cards.IntValue - r.DisplayAmount));
            if (normality) { kcap = Math.Min(kcap, Math.Max(0, 3 - PlayedThisTurn(cs, me))); conditions = true; }
            chains = me.Creature.HasPower<ChainsOfBindingPower>();
            var enth = hand.Where(c => c is Enthralled && CanPlayM(c)).ToList();
            nEnth = enth.Count;
            List<CardModel> playable;
            if (nEnth > 0)
            {
                conditions = true;
                // ENTHRALLED 在手时别的牌 CanPlay 为假（Enthralled.ShouldPlay 只放行自动打出）：按“没有它时能不能打”判断：不带不能打出、有费用
                playable = hand.Where(c =>
                {
                    try { return c is not Enthralled && !Kw(c).Contains(CardKeyword.Unplayable) && (c.EnergyCost.CostsX || CostAll(c) >= 0); }
                    catch { return false; }
                }).ToList();
            }
            else playable = hand.Where(c =>
            {
                try
                {
                    if (CanPlayM(c)) return true;
                    // 只因能量、星不够打不出的（先打得能量、得星的牌之后可能打得出；原样求解时费用约束照样挡掉）
                    return !c.EnergyCost.CostsX && !Kw(c).Contains(CardKeyword.Unplayable) && (CostAll(c) > energy0 || StarCost(c) > stars0);
                }
                catch { return false; }
            }).ToList();
            playable = playable.Where(c => !(c is MegaCrit.Sts2.Core.Models.Cards.GodByrd)).ToList();
            ringing = me.Creature.HasPower<RingingPower>() && playable.Count(c => c.Affliction is MegaCrit.Sts2.Core.Models.Afflictions.Ringing) >= 2;
            if (ringing) conditions = true;
            int E = energy0 - nEnth * 2;
            if (E < 0) return conditions ? 0 : -1;
            int S = stars0;
            if (nEnth > 0 && kcap < int.MaxValue) kcap = Math.Max(0, kcap - nEnth);

            // ---------- 箭、免费层、多打层、首张加成（同 KnapForFoe）
            arrowDim = playable.Any(IsShoot);
            avail = pcs.AvailableArrowCount;
            int kvN = Math.Clamp(me.Creature.GetPowerAmount<KiVersePower>(), 0, 3);
            int otp = Math.Clamp(me.Creature.GetPowerAmount<OneTwoPunchPower>(), 0, 3);
            bool aimOn = me.Creature.HasPower<AimPower>() || me.Creature.HasPower<QuietusPower>();
            int voidLeft = VoidLeft(me);
            voidOn = voidLeft > 0;
            int chord = !PlayedWah(me) ? me.Creature.GetPowerAmount<ViolentChordPower>() : 0;
            int explo = me.Creature.GetPowerAmount<ExplosiveArrowsPower>();
            var unp = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered;
            if (fHit && chord > 0) chordDmg = HitDmg(cs, f, me.Creature, chord, unp, null);
            if (fHit && explo > 0) exploDmg = HitDmg(cs, f, me.Creature, explo, unp, null);
            var charon = me.Relics.FirstOrDefault(r => r is CharonsAshes && RelicLive(r));
            float charonDmg = fHit && charon != null && charon.DynamicVars.TryGetValue("Damage", out var cdv) ? HitDmg(cs, f, me.Creature, cdv.BaseValue, unp, null) : 0;
            int inferno = me.Creature.GetPowerAmount<InfernoPower>();
            float infernoDmg = fHit && inferno > 0 ? HitDmg(cs, f, me.Creature, inferno, unp, null) : 0;
            int jugg = me.Creature.GetPowerAmount<JuggernautPower>();
            float juggDmg = fHit && jugg > 0 && nH == 1 ? HitDmg(cs, f, me.Creature, jugg, unp, null) : 0;
            int rage = me.Creature.GetPowerAmount<RagePower>(), fnp = me.Creature.GetPowerAmount<FeelNoPainPower>(), image = me.Creature.GetPowerAmount<AfterimagePower>();
            var slowP = f.GetPower<SlowPower>();
            slowS = slowP != null && slowP.DynamicVars.TryGetValue("SlowAmount", out var sv) ? (float)sv.BaseValue : 0;
            bool tenderOn = me.Creature.HasPower<TenderPower>();
            var skit = f.GetPower<SkittishPower>();
            skittish = skit != null && !skit.HasGainedBlockThisTurn;
            skittishAmt = skit?.Amount ?? 0;
            bool phantomOn = me.Creature.HasPower<PhantomBladesPower>() && !ShivFinishedThisTurn();
            bool lethOn = me.Creature.HasPower<LethalityPower>() && PlayedThisTurn(cs, me, c => c.Type == CardType.Attack) == 0;
            var redSkull = me.Relics.FirstOrDefault(r => r is RedSkull && RelicLive(r));
            bool skullArmed = redSkull != null && me.Creature.CurrentHp > me.Creature.MaxHp * 0.5m;
            decimal strMul = HelmetUnused(me) ? 2 : 1;
            var memento = nH >= 2 && playable.Any(c => c is MementoMori) ? MementoTarget(me) : null;
            bool anyFree = me.Creature.Powers.Any(IsFreePower);

            // ---------- 每张牌
            foreach (var card in playable)
            {
                var k = new K29Card { C = card };
                try
                {
                    k.IsX = card.EnergyCost.CostsX;
                    k.IsStarX = card.HasStarCostX;
                    k.Cost = k.IsX ? 0 : Math.Max(0, CostAll(card));
                    k.Sc = k.IsStarX ? 0 : Math.Max(0, StarCost(card));
                    k.Attack = card.Type == CardType.Attack;
                    k.Shoot = arrowDim && IsShoot(card);
                    k.Melody = IsMelody(card);
                    k.Wah = card.Tags.Contains(CardTag.Wah);
                    k.IsMaul = card is Maul;
                    k.IsFiend = card is FiendFire;
                    k.BoundLike = (chains && card.Affliction is MegaCrit.Sts2.Core.Models.Afflictions.Bound)
                                  || (ringing && card.Affliction is MegaCrit.Sts2.Core.Models.Afflictions.Ringing);
                    k.Plays = PlayCount(card, f);
                    touched.Add(card);
                    var (pd, pb, hits) = Preview(card, f);
                    k.Pb = pb; k.Hits = Math.Max(0, hits);
                    k.PdFromDamageVar = card.DynamicVars.ContainsKey("Damage") || card.DynamicVars.ContainsKey("CalculatedDamage");
                    k.Recall = card is Recall or Wrest or Uproot
                        ? foes.Select(x => x.GetPowerInstances<ImpaledPower>().Where(p => p.Applier == me.Creature).Sum(p => p.Arrows.Count)).DefaultIfEmpty(0).Max() : 0;
                    // 自己的伤害只算打敌人的牌：烧伤、毒素、凋萎等状态与诅咒的 Damage 是回合末打自己的（能打出的毒素原来被算成对敌人的伤害）
                    k.Pd = card.TargetType is TargetType.AnyEnemy or TargetType.AllEnemies or TargetType.RandomEnemy || card is MadScience ? pd : -1;
                    if (pd > 0 && k.Pd < 0) conditions = true;
                    // 免费层：虚空形态（能量、星都免，不分类型）或免费出牌类能力（只免能量）
                    if (voidOn)
                    {
                        k.CostNF = k.IsX ? 0 : Math.Max(0, CostNoFree29(card));
                        k.ScNF = k.IsStarX ? 0 : Math.Max(0, StarCostNoFree29(card));
                        k.Free = (k.CostNF > k.Cost || k.ScNF > k.Sc) ? 1 : 0;
                    }
                    else if (anyFree)
                    {
                        int nf = CostNoFree(card), fl = !k.IsX && nf > k.Cost ? FreeLayersFor(me, card) : 0;
                        k.CostNF = fl > 0 ? nf : k.Cost; k.ScNF = k.Sc; k.Free = fl;
                        freeMax = Math.Max(freeMax, fl);
                    }
                    else { k.CostNF = k.Cost; k.ScNF = k.Sc; }
                    // G20：打随机敌人
                    if (card.TargetType == TargetType.RandomEnemy && nH >= 2)
                    {
                        k.RandF = !fHit ? 0 : card is MementoMori ? (ReferenceEquals(memento, f) ? 1 : 0) : 1f / nH;
                        conditions = true;
                    }
                    // G43：附带伤害（对 f）
                    float rider = 0;
                    int exhaustN = charonDmg > 0 || (juggDmg > 0 && fnp > 0) ? (ExhaustsOnPlay(card, me) ? 1 : 0) + ExhaustOthers(card, me) : 0;
                    if (charonDmg > 0 && exhaustN > 0 && !k.IsFiend) rider += exhaustN * charonDmg;
                    float self = SelfHpLoss(cs, me, card);      // 不在自伤名单里的牌直接返回 0
                    k.SelfLoss = self * k.Plays;
                    if (infernoDmg > 0 && self > 0) rider += k.Plays * infernoDmg;
                    if (juggDmg > 0)
                    {
                        int own = pb > 0 ? Math.Max(IroncladCounts(card, me, 1).gains, BlockGains(card)) : 0;
                        int gains = (own + (image > 0 ? 1 : 0) + (rage > 0 && k.Attack ? 1 : 0)) * k.Plays + (fnp > 0 ? exhaustN : 0);
                        rider += gains * juggDmg;
                    }
                    k.Rider = rider;
                    if (k.Shoot && exploDmg > 0) k.ExploPerShot = exploDmg;
                    // G76：净得能量、得星、加力量
                    float Var(string n) => card.DynamicVars.TryGetValue(n, out var v) ? (float)v.BaseValue : 0;
                    if (EnergyCards.Contains(card.GetType())) { k.EnergyGross = Var("Energy") * k.Plays; k.EnergyNet = k.EnergyGross - k.Cost; }
                    if (StarCards29.Contains(card.GetType())) k.StarGain = Var("Stars") * k.Plays;
                    k.PreGain = (k.EnergyNet > 0 || k.StarGain > 0) && !k.IsX;
                    decimal str = 0;
                    if (StrCards29.Contains(card.GetType()) && card.DynamicVars.TryGetValue("StrengthPower", out var spv)) str = spv.BaseValue * k.Plays;
                    else if (card is MadScience ms && ms.TinkerTimeType == CardType.Power && ms.TinkerTimeRider == MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Expertise
                             && card.DynamicVars.TryGetValue("ExpertiseStrength", out var esv)) str = esv.BaseValue;
                    k.StrOwn = str;
                    if (skullArmed && self > 0 && me.Creature.CurrentHp - k.SelfLoss <= me.Creature.MaxHp * 0.5f && redSkull!.DynamicVars.TryGetValue("StrengthPower", out var rsv))
                        str += rsv.BaseValue;
                    k.StrGain = (float)(str * strMul);
                }
                catch { continue; }
                Cards.Add(k);
            }
            if (Cards.Count == 0) return conditions ? 0 : -1;
            if (charonDmg > 0)
                foreach (var k in Cards.Where(k => k.C is SecondWind))
                {
                    int rest = pcs.Hand.Cards.Count(c => !ReferenceEquals(c, k.C) && c.Type != CardType.Attack
                                                         && !Cards.Any(o => ReferenceEquals(o.C, c) && (o.Pd > 0 || o.Rider > 0)));
                    k.Rider -= (ExhaustOthers(k.C, me) - rest) * charonDmg;
                }

            // ---------- 首张加成、多打层的机制选择（G17）
            var mOver = new float[4];
            foreach (var k in Cards)
            {
                if (k.Pd <= 0) continue;
                float h = k.Hits;
                if (aimOn && k.Shoot) { float x = PdNoAim(k.C, f, k.Pd); mOver[1] += (k.Pd - x) * h; }
                if (phantomOn && k.C.Tags.Contains(CardTag.Shiv)) { float x = PdSkip(k.C, k.Pd, l => l is PhantomBladesPower); mOver[2] += (k.Pd - x) * h; }
                if (lethOn && k.Attack) { float x = PdLeth(k); mOver[3] += (k.Pd - x) * h; }
            }
            int nShiv = Cards.Count(k => k.Pd > 0 && k.C.Tags.Contains(CardTag.Shiv)), nAtk = Cards.Count(k => k.Pd > 0 && k.Attack);
            if (phantomOn && nShiv >= 2 && mOver[2] > 0) conditions = true; else mOver[2] = 0;
            if (lethOn && nAtk >= 2 && mOver[3] > 0) conditions = true; else mOver[3] = 0;
            mMech = 0;
            for (int i = 1; i <= 3; i++) if (mOver[i] > 0 && (mMech == 0 || mOver[i] > mOver[mMech])) mMech = i;
            foreach (var k in Cards)
            {
                k.Pdx = k.Pd;
                if (k.Pd <= 0) continue;
                if (mMech == 1 && k.Shoot) k.Pdx = PdNoAim(k.C, f, k.Pd);
                else if (mMech == 2 && k.C.Tags.Contains(CardTag.Shiv)) k.Pdx = PdSkip(k.C, k.Pd, l => l is PhantomBladesPower);
                else if (mMech == 3 && k.Attack) k.Pdx = PdLeth(k);
            }
            int nOtpCards = Cards.Count(k => k.Attack && k.Plays >= 2 && (k.Pd > 0 || k.Rider > 0));
            if (otp > 0 && nOtpCards > otp) conditions = true;
            if (otp > 0 && otp >= kvN && nOtpCards > otp) { vMech = 2; vLayers = otp; }
            else if (kvN > 0 && Cards.Any(k => k.Melody && k.Plays >= 2)) { vMech = 1; vLayers = kvN; }
            if (voidOn && Cards.Count(k => k.Free > 0) > voidLeft) conditions = true;
            freeMax = voidOn ? Math.Min(voidLeft, 6) : freeMax;

            // ---------- X 费、恶魔之焰、撕咬、位置相关、附带伤害（G18、G19、G43、G64）
            for (int i = 0; i < Cards.Count; i++)
            {
                var k = Cards[i];
                if ((k.IsX && k.Attack) || k.IsStarX) { if (k.Pd > 0) { xCards.Add(i); conditions = true; } }
                else if (k.IsFiend && fiend < 0 && !k.BoundLike) fiend = i;
                else if (k.IsMaul && !ringing && !chains) mauls.Add(i);
            }
            if (fiend >= 0 && Cards.Count(k => !k.IsFiend && (k.Pd > 0 || k.Rider > 0)) >= 1) conditions = true;
            if (mauls.Count >= 2) conditions = true; else mauls.Clear();
            int nDmg = Cards.Count(k => k.Pd > 0 && k.Attack);
            slow = slowP != null && nDmg >= 1;
            tender = !slow && tenderOn && nDmg >= 1;
            if (slow || tender || skittish) conditions = true;
            if (chordDmg > 0 && Cards.Any(k => k.Wah)) conditions = true; else chordDmg = 0;
            if (exploDmg > 0 && Cards.Any(k => k.Shoot)) conditions = true;
            if (Cards.Any(k => k.Rider > 0)) conditions = true;
            bool preOk = kcap == int.MaxValue && !chains && !ringing;
            List<(K29Card k, int i)> pre = preOk ? Cards.Select((k, i) => (k, i)).Where(t => t.k.PreGain).ToList() : new();
            if (pre.Count > 0) conditions = true;
            var strB = Cards.Select((k, i) => (k, i)).Where(t => t.k.StrGain > 0).ToList();
            if (strB.Count > 0 && nDmg >= 1) conditions = true;
            if (!conditions) return -1;

            // 箭维（同 KnapForFoe）
            kRecall = arrowDim && Cards.Any(k => k.Recall > 0);
            foreach (var k in Cards)
            {
                k.Ac = k.Shoot ? 1 : kRecall && k.Recall > 0 ? -k.Recall : 0;
                if (k.Shoot && kRecall && k.Pd == 0 && ShootDisabled(k.C))
                    foreach (var (name, v) in k.C.DynamicVars) if (name is "Damage" or "CalculatedDamage") { k.Pd = k.Pdx = (float)v.PreviewValue; }
            }
            int nShoot = Cards.Count(k => k.Shoot);
            A = arrowDim ? Math.Min(nShoot, avail + (kRecall ? Cards.Sum(k => k.Recall) : 0)) : 0;
            A0 = Math.Min(avail, A);

            // ---------- 求解：原样一份；先打净得能量 / 得星的牌一份；每张加力量的牌先打各一份；全部加力量的非攻击牌一起先打一份。取各份的最大值
            int B0 = chains || ringing ? 1 : 0;
            // G19 胆小：第一张打穿格挡的攻击牌打完之后它加层数点格挡（本回合只一次），生命 + 格挡净少层数点；
            // 单张牌就打得死（一张牌的全部段数在加格挡之前打完）时不扣。每份按它自己的单张最大值判断
            float Adj(float v) => v >= 0 && skittish && v > f.Block && maxSingle < f.CurrentHp + f.Block ? Math.Max(0, v - skittishAmt) : v;
            float best = Adj(Solve(new K29Var(new HashSet<int>(), E, S, freeMax, kcap, B0, 0, 0, 0)));
            if (best < 0) return -1;
            // 先打的牌不能把自己打死（祭品、放血等自伤）
            float hpNow = me.Creature.CurrentHp;
            if (pre.Count > 0)
            {
                int e = E, s = S; float extra = 0, selfLoss = 0; var gone = new HashSet<int>();
                foreach (var (k, i) in pre.OrderBy(t => t.k.Cost))
                {
                    if (k.Cost > e || k.Sc > s || selfLoss + k.SelfLoss >= hpNow) continue;
                    e += (int)Math.Round(k.EnergyGross) - k.Cost;
                    s = Math.Min(40, s - k.Sc + (int)k.StarGain);
                    extra += OwnVal(k, k.Plays, true, 0) + k.Rider;
                    selfLoss += k.SelfLoss;
                    gone.Add(i);
                }
                if (gone.Count > 0)
                {
                    var pieces = gone.Select(i => Cards[i].StrOwn).Where(x => x > 0).ToList();
                    if (skullArmed && me.Creature.CurrentHp - selfLoss <= me.Creature.MaxHp * 0.5f && redSkull!.DynamicVars.TryGetValue("StrengthPower", out var rsv)) pieces.Add(rsv.BaseValue);
                    decimal str = pieces.Sum() + (strMul > 1 && pieces.Count > 0 ? pieces.Max() : 0);
                    best = Math.Max(best, Adj(Solve(new K29Var(gone, e, s, freeMax, kcap, B0, str, extra, gone.Count))));
                }
            }
            foreach (var (b, i) in strB)
            {
                if (b.Cost > E || b.Sc > S || kcap < 1 || (b.BoundLike && B0 < 1) || b.SelfLoss >= hpNow) continue;
                int e = E - b.Cost + (int)Math.Round(b.EnergyGross), s = Math.Min(40, S - b.Sc + (int)b.StarGain);
                float own = OwnVal(b, b.Plays, true, 0) + b.Rider + (b.Wah ? chordDmg : 0) + (b.Shoot ? b.ExploPerShot : 0);
                best = Math.Max(best, Adj(Solve(new K29Var(new HashSet<int> { i }, e, s, freeMax, kcap == int.MaxValue ? kcap : kcap - 1, b.BoundLike ? 0 : B0, (decimal)b.StrGain, own, 1))));
            }
            // 规格外的一小步（叠加）：加力量的非攻击牌按费用升序能打就一起先打，再各试一张加力量的攻击牌（准备打击、FIGHT_ME：
            // 它自己按之前的力量打，打完再加）；先打两张以上时才另算（一张的已经在上面）。两张 FEEDING_FRENZY、FEEDING_FRENZY + 准备打击等
            if (kcap == int.MaxValue)
            {
                var strNA = strB.Where(t => !t.k.Attack && !t.k.BoundLike).OrderBy(t => t.k.Cost).ToList();
                var strAt = strB.Where(t => t.k.Attack && !t.k.BoundLike).Select(t => ((K29Card k, int i)?)t).Prepend(null).ToList();
                foreach (var at in strAt)
                {
                    int e = E, s = S; float own = 0, selfLoss = 0; var pieces = new List<decimal>(); var gone = new HashSet<int>();
                    foreach (var (b, i) in strNA)
                    {
                        if (b.Cost > e || b.Sc > s || selfLoss + b.SelfLoss >= hpNow) continue;
                        if (at is { } a0 && b.Cost + a0.k.Cost > e) continue;      // 给这张攻击留能量
                        e += (int)Math.Round(b.EnergyGross) - b.Cost; s = Math.Min(40, s - b.Sc + (int)b.StarGain);
                        own += OwnVal(b, b.Plays, true, 0) + b.Rider; selfLoss += b.SelfLoss; pieces.Add(b.StrOwn);
                        gone.Add(i);
                    }
                    decimal Str() => pieces.Sum() + (strMul > 1 && pieces.Count > 0 ? pieces.Max() : 0)
                                     + (skullArmed && me.Creature.CurrentHp - selfLoss <= me.Creature.MaxHp * 0.5f && redSkull!.DynamicVars.TryGetValue("StrengthPower", out var rsv) ? rsv.BaseValue : 0);
                    if (at is { } a)
                    {
                        if (a.k.Cost > e || a.k.Sc > s || selfLoss + a.k.SelfLoss >= hpNow) continue;
                        e += (int)Math.Round(a.k.EnergyGross) - a.k.Cost; s = Math.Min(40, s - a.k.Sc + (int)a.k.StarGain);
                        own += OwnVal(a.k, a.k.Plays, true, StrDelta(a.k, Str())) + a.k.Rider; selfLoss += a.k.SelfLoss; pieces.Add(a.k.StrOwn);
                        gone.Add(a.i);
                    }
                    if (gone.Count >= 2) best = Math.Max(best, Adj(Solve(new K29Var(gone, e, s, freeMax, kcap, B0, Str(), own, gone.Count))));
                }
            }
            return Math.Max(0, best);
        }

        bool ShivFinishedThisTurn()
        {
            try { return CombatManager.Instance.History.CardPlaysFinished.Any(e => e.HappenedThisTurn(cs) && e.CardPlay.Card.Tags.Contains(CardTag.Shiv) && e.CardPlay.Player == me); }
            catch { return true; }
        }

        /// <summary>首张加成之外的单段伤害：pd 减去跳过这些监听者重算时少掉的部分（同 PdNoAim 的写法）。</summary>
        float PdSkip(CardModel c, float pd, Func<AbstractModel, bool> skip)
        {
            try
            {
                float all = DmgRecalc(c, f, _ => false), ex = DmgRecalc(c, f, skip);
                if (all < 0 || ex < 0) return pd;
                return Math.Max(0, pd - (all - ex));
            }
            catch { return pd; }
        }

        /// <summary>致死性之外的单段伤害。奥斯提出手的牌（OstyDamage，DmgRecalc 算不了）按 预览 ÷ (1 + 层数 / 100) 四舍五入。</summary>
        float PdLeth(K29Card k)
        {
            if (k.PdFromDamageVar) return PdSkip(k.C, k.Pd, l => l is LethalityPower);
            int amt = me.Creature.GetPowerAmount<LethalityPower>();
            return amt > 0 ? MathF.Round(k.Pd / (1 + amt / 100f)) : k.Pd;
        }

        /// <summary>力量多 add 时单段伤害的变化（只对有 Damage / CalculatedDamage 数值的攻击；PdWithStr 两次之差，与预览的其余部分无关）。</summary>
        float StrDelta(K29Card k, decimal add)
        {
            if (add == 0 || !k.PdFromDamageVar || k.Pd <= 0) return 0;
            if (strMemo.TryGetValue((k, add), out float v)) return v;
            float a = PdWithStr(k.C, f, add), b = PdWithStr(k.C, f, 0);
            v = a < 0 || b < 0 ? 0 : a - b;
            strMemo[(k, add)] = v;
            return v;
        }
        readonly Dictionary<(K29Card, decimal), float> strMemo = new();

        /// <summary>自己的伤害：打出 p 次，first 为吃到首张加成（只第一次）；d 为单段伤害的变化（力量增量）。按随机目标折算。</summary>
        float OwnVal(K29Card k, int p, bool first, float d)
        {
            if (p <= 0 || k.Pd <= 0) return 0;
            float pd = Math.Max(0, k.Pd + d), pdx = Math.Max(0, k.Pdx + d);
            float v = first ? pd * k.Hits + (p - 1) * pdx * k.Hits : pdx * k.Hits * p;
            return v * k.RandF;
        }

        /// <summary>背包求解（一个变体），顺便记下这份里单张牌最大的价值（maxSingle，胆小用）。状态数超限返回 −1。
        /// 迟缓时不打伤害的牌也进背包（价值 0，排在最前）：剩下的能量先打它们，迟缓多一层。</summary>
        float Solve(K29Var vr)
        {
            maxSingle = 0;
            bool trackJ = vr.K < int.MaxValue || fiend >= 0 || slow || tender;
            curStr = vr.Str;
            posOff = vr.HandGone;       // 先打掉的牌也让迟缓加一层、软弱减一点力量
            var items = new List<List<K29Opt>>();
            int nCards = 0;
            int sNeed = 0;
            for (int i = 0; i < Cards.Count; i++)
            {
                var k = Cards[i];
                if (vr.Gone.Contains(i) || k.IsX || k.IsStarX || i == fiend || mauls.Contains(i)) continue;
                if (k.SelfLoss >= me.Creature.CurrentHp) continue;      // 打出它会把自己打死（放血、祭品等），不算
                float d = StrDelta(k, vr.Str);
                bool kvS = vMech == 1 ? k.Melody && k.Plays >= 2 : vMech == 2 && k.Attack && k.Plays >= 2;
                bool mS = k.Pdx < k.Pd;
                if (k.Pd <= 0 && k.Rider <= 0 && k.Ac >= 0 && !(k.Wah && chordDmg > 0) && !slow) continue;
                var opts = new List<K29Opt>();
                foreach (var (ce, se, fu) in k.Free > 0 ? new[] { (0, 0, 1), (k.CostNF, k.ScNF, 0) } : new[] { (k.Cost, k.Sc, 0) })
                {
                    int ceR = voidOn || k.Free == 0 ? ce : (fu == 1 ? k.Cost : ce);
                    int seR = voidOn ? se : k.Sc;
                    if (ceR > vr.E || seR > vr.S) continue;
                    foreach (var (vu, p) in kvS ? new[] { (1, k.Plays), (0, k.Plays - 1) } : new[] { (0, k.Plays) })
                    {
                        float expl = k.ExploPerShot * Math.Min(p, Math.Max(1, avail));
                        float h = k.Hits * p * k.RandF;
                        if (mS)
                        {
                            opts.Add(new K29Opt { E = ceR, S = seR, A = k.Ac, F = fu, V = vu, M = 1, B = k.BoundLike ? 1 : 0, G = k.Wah && chordDmg > 0 ? 1 : 0, J = 1, P = Math.Max(1, p), Own = OwnVal(k, p, true, d), Rider = k.Rider + expl, HitsTotal = h, Card = i });
                            opts.Add(new K29Opt { E = ceR, S = seR, A = k.Ac, F = fu, V = vu, M = 0, B = k.BoundLike ? 1 : 0, G = k.Wah && chordDmg > 0 ? 1 : 0, J = 1, P = Math.Max(1, p), Own = OwnVal(k, p, false, d), Rider = k.Rider + expl, HitsTotal = h, Card = i });
                        }
                        else opts.Add(new K29Opt { E = ceR, S = seR, A = k.Ac, F = fu, V = vu, M = 0, B = k.BoundLike ? 1 : 0, G = k.Wah && chordDmg > 0 ? 1 : 0, J = 1, P = Math.Max(1, p), Own = OwnVal(k, p, true, d), Rider = k.Rider + expl, HitsTotal = h, Card = i });
                        sNeed += seR;
                    }
                }
                if (opts.Count > 0) { items.Add(opts); nCards++; }
            }
            // 撕咬组：按 Increase、伤害降序取前 k 张，第 i 张的基础伤害加上之前各张的 Increase
            if (mauls.Count >= 2)
            {
                var ms = mauls.Where(i => !vr.Gone.Contains(i)).OrderByDescending(i => Inc(Cards[i])).ThenByDescending(i => Cards[i].Pd).ToList();
                var opts = new List<K29Opt>();
                int ce = 0, se = 0; float val = 0, rider = 0; decimal acc = 0;
                for (int t = 0; t < ms.Count; t++)
                {
                    var k = Cards[ms[t]];
                    ce += k.Cost; se += k.Sc;
                    if (ce > vr.E || se > vr.S) break;
                    val += OwnVal(k, k.Plays, true, StrDelta(k, vr.Str + acc) );
                    rider += k.Rider;
                    acc += (decimal)Inc(k) * k.Plays;
                    opts.Add(new K29Opt { E = ce, S = se, J = t + 1, Own = val, Rider = rider, Card = -1 });
                    sNeed += se;
                }
                if (opts.Count > 0) { items.Add(opts); nCards += ms.Count; }
            }
            // 位置相关：迟缓按自己的伤害升序、软弱按段数降序（DP 按物品顺序给位置）
            if (slow) items = items.OrderBy(o => o.Max(x => x.Own)).ToList();
            else if (tender) items = items.OrderByDescending(o => o.Max(x => x.HitsTotal)).ToList();

            int Ecap = vr.E, Scap = Math.Min(vr.S, sNeed + xCards.Where(i => Cards[i].IsStarX).Select(_ => vr.S).DefaultIfEmpty(0).Max());
            int Jcap = trackJ ? Math.Min(vr.K == int.MaxValue ? nCards + 1 : vr.K, nCards + 1) : 0;
            int Fcap = freeMax, Vcap = vMech > 0 ? vLayers : 0, Mcap = mMech > 0 ? 1 : 0, Bcap = vr.B, Gcap = chordDmg > 0 ? 1 : 0;
            int[] dim = { Ecap + 1, Scap + 1, A + 1, Fcap + 1, Vcap + 1, Mcap + 1, Bcap + 1, Gcap + 1, Jcap + 1 };
            long total = 1; foreach (var x in dim) total *= x;
            if (total > 400_000) return -1;
            var st = new int[9]; st[8] = 1;
            for (int i = 7; i >= 0; i--) st[i] = st[i + 1] * dim[i + 1];
            bool simple = (slow || tender) && items.Count <= 12
                          && items.All(o => o.Count == 1 && o[0].Card >= 0 && o[0].A == 0 && o[0].F == 0 && o[0].V == 0 && o[0].M == 0 && o[0].B == 0 && o[0].G == 0);
            var dp = new float[total];
            for (int idx = 0; idx < total; idx++) dp[idx] = (idx / st[7] % dim[7]) == 0 && (idx % dim[8]) == 0 ? 0 : float.NegativeInfinity;
            var x9 = new int[9];
            foreach (var opts in simple ? new List<List<K29Opt>>() : items)      // 子集 DP 适用时结果只用它，不做通用 DP
            {
                var nd = (float[])dp.Clone();
                foreach (var o in opts)
                {
                    int[] u = { o.E, o.S, o.A, o.F, o.V, o.M, o.B, 0, trackJ ? o.J : 0 };
                    for (int idx = 0; idx < total; idx++)
                    {
                        int r = idx; bool ok = true; int prev = 0;
                        for (int d = 0; d < 9; d++)
                        {
                            x9[d] = r / st[d]; r -= x9[d] * st[d];
                            if (d == 7) continue;
                            int px;
                            if (d == 2 && u[d] < 0) px = Math.Min(A, x9[d] - u[d]);
                            else { px = x9[d] - u[d]; if (px < 0) { ok = false; break; } }
                            prev += px * st[d];
                        }
                        if (!ok) continue;
                        int g = x9[7];
                        int pos = (trackJ ? x9[8] - u[8] : 0) + posOff;
                        float add = ValAt(o, pos) + o.Rider;
                        if (o.G == 1)
                        {
                            if (g == 0) continue;
                            float a = dp[prev], b = dp[prev + st[7]];      // 之前没选过哇（g = 0）或已选过（g = 1）
                            float v = Math.Max(a, b) + add;
                            if (v > nd[idx]) nd[idx] = v;
                        }
                        else
                        {
                            float v = dp[prev + g * st[7]] + add;
                            if (v > nd[idx]) nd[idx] = v;
                        }
                    }
                }
                dp = nd;
            }
            // 迟缓、软弱且每张牌只有一个选项、不占箭 / 免费 / 多打 / 首张 / 束缚 / 旗标维时（绝大多数情形），按子集精确求最好的出牌顺序：
            // ord[mask] = max_i ord[mask − i] + i 在位置“mask − i 的打出次数之和”的价值（取整让升序不一定最好，往日雕像 35 对 34）
            float[]? ord = null; int[]? mCost = null, mStar = null, mCnt = null;
            if (simple)
            {
                int n = items.Count, nm = 1 << n;
                ord = new float[nm]; mCost = new int[nm]; mStar = new int[nm]; mCnt = new int[nm]; var mPl = new int[nm];
                for (int mask = 1; mask < nm; mask++)
                {
                    int lo = System.Numerics.BitOperations.TrailingZeroCount(mask), rest = mask & (mask - 1);
                    var o0 = items[lo][0];
                    mCost[mask] = mCost[rest] + o0.E; mStar[mask] = mStar[rest] + o0.S; mCnt[mask] = mCnt[rest] + 1; mPl[mask] = mPl[rest] + o0.P;
                    float bestV = float.NegativeInfinity;
                    for (int i = 0; i < n; i++)
                    {
                        if ((mask >> i & 1) == 0) continue;
                        int pm = mask ^ (1 << i);
                        var o = items[i][0];
                        float v = ord[pm] + ValAt(o, mPl[pm] + posOff) + o.Rider;
                        if (v > bestV) bestV = v;
                    }
                    ord[mask] = bestV;
                }
            }
            float At(int e, int s, int a, int b, int g, int j) =>
                e < 0 || s < 0 || a < 0 || b < 0 ? float.NegativeInfinity : dp[e * st[0] + Math.Min(s, Scap) * st[1] + Math.Min(a, A) * st[2] + Fcap * st[3] + Vcap * st[4] + Mcap * st[5] + b * st[6] + g * st[7] + j * st[8]];
            // 剩余资源 (e, s, a, b) 下的最好值（张数 ≤ jMax；旗标为 1 时加暴力和弦）
            float Best(int e, int s, int a, int b, int jMax)
            {
                float m = float.NegativeInfinity;
                if (ord != null)
                {
                    if (e < 0 || s < 0 || a < 0 || b < 0) return m;
                    for (int mask = 0; mask < ord.Length; mask++)
                        if (mCost![mask] <= e && mStar![mask] <= s && mCnt![mask] <= jMax && ord[mask] > m) m = ord[mask];
                    return m;
                }
                for (int g = 0; g <= Gcap; g++)
                    for (int j = 0; j <= Math.Min(jMax, Jcap); j++)
                    {
                        float v = At(e, s, a, b, g, j);
                        if (v > float.NegativeInfinity) m = Math.Max(m, v + (g == 1 ? chordDmg : 0));
                    }
                return m;
            }
            float best = Best(Ecap, vr.S, A0, Bcap, Jcap);
            if (best == float.NegativeInfinity) best = 0;
            foreach (var opts in items) foreach (var o in opts)
                    if (o.E <= vr.E && o.S <= vr.S && o.J <= 1) maxSingle = Math.Max(maxSingle, o.Own + o.Rider + (o.G == 1 ? chordDmg : 0) + vr.Extra);
            int kRest = vr.K == int.MaxValue ? int.MaxValue : vr.K - 1;
            // G19 恶魔之焰放在最后：段数 = 现在的段数 − 之前打出的张数 − 先打掉的张数
            if (fiend >= 0 && !vr.Gone.Contains(fiend) && kRest >= 0)
            {
                var k = Cards[fiend];
                if (k.Cost <= vr.E && k.Sc <= vr.S)
                    for (int j = 0; j <= Math.Min(Jcap, kRest); j++)
                    {
                        float pre = Best(vr.E - k.Cost, vr.S - k.Sc, A0, Bcap, j) ;
                        if (pre == float.NegativeInfinity) continue;
                        // Best(.., j) 是“张数 ≤ j”的最好值，段数按 j 张算偏保守，取各 j 的最大值即为“恰好 j 张”的最大值
                        float hits = Math.Max(0, k.Hits - j - vr.HandGone);
                        float pd = Math.Max(0, k.Pd + StrDelta(k, vr.Str));
                        float v = pd * hits * k.Plays * k.RandF + k.Rider + (charonDmgOf(k) * (1 + Math.Max(0, OthersInHand(k) - j - vr.HandGone)));
                        best = Math.Max(best, pre + v);
                        if (j == 0) maxSingle = Math.Max(maxSingle, v + vr.Extra);
                    }
            }
            // G18 X 费：放在最后，用掉剩下的全部能量（星尘为星）
            foreach (int xi in xCards)
            {
                if (vr.Gone.Contains(xi) || kRest < 0) continue;
                var k = Cards[xi];
                float pd = Math.Max(0, k.Pd + StrDelta(k, vr.Str));
                int aUse = k.Shoot ? 1 : 0;
                if (k.IsStarX)
                {
                    if (k.Cost > vr.E) continue;
                    for (int s = 1; s <= vr.S; s++)
                    {
                        float pre = Best(vr.E - k.Cost, vr.S - s, A0 - aUse, Bcap - (k.BoundLike ? 1 : 0), kRest);
                        if (pre == float.NegativeInfinity) continue;
                        float v = pd * XHits29(k.C, s) * k.Plays * k.RandF + k.Rider;
                        best = Math.Max(best, pre + v);
                        maxSingle = Math.Max(maxSingle, v + vr.Extra);
                    }
                }
                else
                    for (int x = 1; x <= vr.E; x++)
                    {
                        float pre = Best(vr.E - x, vr.S - k.Sc, A0 - aUse, Bcap - (k.BoundLike ? 1 : 0), kRest);
                        if (pre == float.NegativeInfinity) continue;
                        float v = pd * XHits29(k.C, x) * k.Plays * k.RandF + k.Rider + k.ExploPerShot * aUse;
                        best = Math.Max(best, pre + v);
                        maxSingle = Math.Max(maxSingle, v + vr.Extra);
                    }
            }
            return best + vr.Extra;
        }

        float charonDmgOf(K29Card k)
        {
            if (!k.IsFiend) return 0;
            var charon = me.Relics.FirstOrDefault(r => r is CharonsAshes && RelicLive(r));
            return fHit && charon != null && charon.DynamicVars.TryGetValue("Damage", out var dv)
                ? HitDmg(cs, f, me.Creature, dv.BaseValue, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null) : 0;
        }

        int OthersInHand(K29Card k) => pcs.Hand.Cards.Count(c => !ReferenceEquals(c, k.C));

        static float Inc(K29Card k) => k.C.DynamicVars.TryGetValue("Increase", out var v) ? (float)v.BaseValue : 0;

        /// <summary>选项 o 在位置 pos（之前已打出几次）打出时自己的伤害：o.P 次打出各占一个位置，每次的单段伤害按 PosDelta 修正。没有迟缓、软弱时就是 o.Own。</summary>
        float ValAt(K29Opt o, int pos)
        {
            if (!(slow || tender) || o.Card < 0 || o.Own <= 0 || o.HitsTotal <= 0) return o.Own;
            float d = 0;
            for (int r = 0; r < o.P; r++) d += PosDelta(o.Card, pos + r);
            return Math.Max(0, o.Own + d * o.HitsTotal / o.P);
        }

        /// <summary>第 pos 次打出（0 起，之前已打出 pos 次）比第 0 次多（少）的单段伤害：迟缓按乘数 1 + 0.1(s + pos) 重算（PdSlow），软弱按力量 −pos（PdWithStr）。</summary>
        float PosDelta(int card, int pos)
        {
            if (pos <= 0) return 0;
            if (posMemo.TryGetValue((card, pos, curStr), out float dd)) return dd;
            var k = Cards[card];
            if (slow)
            {
                float a = PdSlow(k.C, curStr, pos), b = PdSlow(k.C, curStr, 0);
                dd = a < 0 || b < 0 ? k.Pd * 0.1f * pos / (1 + 0.1f * slowS) : a - b;
            }
            else dd = StrDelta(k, curStr - pos) - StrDelta(k, curStr);
            posMemo[(card, pos, curStr)] = dd;
            return dd;
        }

        /// <summary>迟缓的敌人在这张牌之前又多了 extra 张出牌时的单段伤害（照 DmgRecalc，力量多 str，SlowPower 的乘数换成 1 + 0.1(层 + extra)）。算不了为 −1。</summary>
        float PdSlow(CardModel c, decimal str, int extra)
        {
            try
            {
                if (!(c.DynamicVars.TryGetValue("CalculatedDamage", out var cv) && cv is MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedDamageVar)
                    && !(c.DynamicVars.TryGetValue("Damage", out var dv0) && dv0 is MegaCrit.Sts2.Core.Localization.DynamicVars.DamageVar)) return -1;
                decimal n; MegaCrit.Sts2.Core.ValueProps.ValueProp props;
                if (c.DynamicVars.TryGetValue("CalculatedDamage", out var cv2) && cv2 is MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedDamageVar cd) { n = cd.Calculate(f); props = cd.Props; }
                else { var d = (MegaCrit.Sts2.Core.Localization.DynamicVars.DamageVar)c.DynamicVars["Damage"]; n = d.BaseValue; props = d.Props; }
                if (c.Enchantment is { } e) { n += e.EnchantDamageAdditive(n, props); n *= e.EnchantDamageMultiplicative(n, props); }
                n += str;
                var ls = cs.RunState.IterateHookListeners(cs).ToList();
                var dealer = c.Owner.Creature;
                foreach (var l in ls) n += l.ModifyDamageAdditive(f, n, props, dealer, c, null);
                foreach (var l in ls)
                    n *= l is SlowPower sp && sp.Owner == f && props.IsPoweredAttack() ? 1m + 0.1m * ((decimal)slowS + extra) : l.ModifyDamageMultiplicative(f, n, props, dealer, c, null);
                foreach (var l in ls) n *= l.ModifyDamageMultiplicativeLate(f, n, props, dealer, c, null);
                foreach (var l in ls) { var x = l.ModifyDamageCap(f, props, dealer, c, null); if (n > x) n = x; }
                return MathF.Floor((float)Math.Max(0m, n));
            }
            catch { return -1; }
        }

        /// <summary>X 费牌留给它 x 点能量（星尘为星）时的段数（同 Cards.cs 的 XHits，按参数而不是现在的能量）。</summary>
        float XHits29(CardModel c, int x)
        {
            int X(int v) => Math.Max(0, MegaCrit.Sts2.Core.Hooks.Hook.ModifyXValue(cs, c, v));
            int H(int h)
            {
                try { return (int)MegaCrit.Sts2.Core.Hooks.Hook.ModifyAttackHitCount(cs, MegaCrit.Sts2.Core.Commands.DamageCmd.Attack(0m).FromCard(c, null), h); }
                catch { return h; }
            }
            return c switch
            {
                HeavenlyDrill hd => H(X(x) >= hd.DynamicVars.Energy.IntValue ? 2 * X(x) : X(x)),
                _ => H(X(x)),
            };
        }
    }

    /// <summary>
    /// 便宜的预检：手牌、玩家能力、遗物、敌人能力里有没有可能触发某个条件的东西（只看类型，不过钩子）。都没有时 Knap29 直接返回 −1，
    /// 不做任何预览与钩子计算（绝大多数帧走这里，编码耗时不变）。有时再由 K29.Run 细判，细判下来都不成立仍返回 −1。
    /// </summary>
    static bool Knap29Maybe(ICombatState cs, Player me, Creature f, PlayerCombatState pcs)
    {
        if (f.HasPower<SlowPower>() || f.HasPower<SkittishPower>()) return true;
        foreach (var pw in me.Creature.Powers)
            if (pw is RingingPower or OneTwoPunchPower or PhantomBladesPower or LethalityPower or VoidFormPower or TenderPower
                or InfernoPower or JuggernautPower or ViolentChordPower or ExplosiveArrowsPower) return true;
        bool charon = false, skull = false;
        foreach (var r in me.Relics) { if (r is CharonsAshes) charon = true; else if (r is RedSkull) skull = true; }
        int maul = 0, nH = -1;
        foreach (var c in pcs.Hand.Cards)
        {
            var t = c.GetType();
            // 能打出、带 Damage 的状态与诅咒牌（毒素：Damage 是打自己的，原来的背包算成对敌人的伤害）
            if (c.Type is CardType.Status or CardType.Curse && c.DynamicVars.ContainsKey("Damage")) return true;
            if (c is Normality or Enthralled or FiendFire || c.HasStarCostX || EnergyCards.Contains(t) || StarCards29.Contains(t) || StrCards29.Contains(t)
                || c is MadScience { TinkerTimeType: CardType.Power }) return true;
            if (c.EnergyCost.CostsX && c.Type == CardType.Attack) return true;
            if (c is Maul && ++maul >= 2) return true;
            if (charon || (skull && SelfDamageCards.Contains(t))) return true;
            if (c.TargetType == TargetType.RandomEnemy && c.Type == CardType.Attack)
            {
                if (nH < 0) nH = Hittable(cs).Count;
                if (nH >= 2) return true;
            }
        }
        return false;
    }

    /// <summary>储君当场得星的牌（OnPlay 顶层 PlayerCmd.GainStars，无条件）。</summary>
    static readonly HashSet<Type> StarCards29 = new()
    {
        typeof(Glow), typeof(GatherLight), typeof(BigBang), typeof(ShiningStrike), typeof(RoyalGamble), typeof(HiddenCache), typeof(SolarStrike), typeof(Venerate),
    };

    /// <summary>打出后给自己加力量（本回合生效）的牌，数值 StrengthPower（G76、G69）。疯狂科学专长另判。</summary>
    static readonly HashSet<Type> StrCards29 = new()
    {
        typeof(Inflame), typeof(FeedingFrenzy), typeof(SetupStrike), typeof(Brand), typeof(BulkUp), typeof(Prowess), typeof(MegaCrit.Sts2.Core.Models.Cards.Strength), typeof(FightMe),
    };

    /// <summary>虚空形态本回合还剩几张免费（层数 − 内部数据 cardsPlayedThisTurn，不小于 0）；没有为 0。</summary>
    static int VoidLeft(Player me)
    {
        try
        {
            if (me.Creature.GetPower<VoidFormPower>() is not { } vf || InternalDataField?.GetValue(vf) is not { } data) return 0;
            var fld = data.GetType().GetField("cardsPlayedThisTurn", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
            return fld?.GetValue(data) is int played ? Math.Max(0, vf.Amount - played) : 0;
        }
        catch { return 0; }
    }

    /// <summary>RUINED_HELMET 还没翻倍过力量（本场第一次加力量翻倍）。</summary>
    static bool HelmetUnused(Player me)
    {
        try
        {
            var h = me.Relics.FirstOrDefault(r => r is RuinedHelmet && RelicLive(r));
            return h != null && HelmetUsedField?.GetValue(h) is false;
        }
        catch { return false; }
    }

    /// <summary>不算“免费出牌”与虚空形态时的能量费（同 CostNoFree，Late 里再跳过虚空形态）。X 费为 −1。</summary>
    static int CostNoFree29(CardModel c)
    {
        try
        {
            if (c.EnergyCost.CostsX) return -1;
            decimal m = c.EnergyCost.GetWithModifiers(CostModifiers.Local);
            if (m < 0) return -1;
            var cs = c.Owner.Creature.CombatState!;
            foreach (var l in cs.IterateHookListeners()) if (l.TryModifyEnergyCostInCombat(c, m, out var m1)) m = m1;
            foreach (var l in cs.IterateHookListeners()) if (!IsFreePower(l) && l is not VoidFormPower && l.TryModifyEnergyCostInCombatLate(c, m, out var m2)) m = m2;
            return Math.Max(0, (int)m);
        }
        catch { return -1; }
    }

    /// <summary>不算虚空形态时的星费（同 Hook.ModifyStarCost，跳过虚空形态）。没有星费为 −1。</summary>
    static int StarCostNoFree29(CardModel c)
    {
        try
        {
            if (c.HasStarCostX) return c.Owner.PlayerCombatState?.Stars ?? 0;
            decimal m = c.CurrentStarCost;
            if (m < 0) return -1;
            var cs = c.Owner.Creature.CombatState!;
            foreach (var l in cs.IterateHookListeners()) if (l is not VoidFormPower) { l.TryModifyStarCost(c, m, out var m1); m = m1; }
            return Math.Max(0, (int)m);
        }
        catch { return -1; }
    }
}
