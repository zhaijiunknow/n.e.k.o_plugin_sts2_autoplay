using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.ValueProps;

namespace Sts2Headless;

// v0.113.0 观测修正第五轮（游戏更新v0.113.0/观测独立核查.md 第十二节）：
//   格式 26 的候选特征 12（这一步当场得到的能量）、13（这一步之后进手的牌数）；
//   Fool 的自动打出（啾 副歌、塔罗“愚者”“恶魔”、失落鲁特琴）、重放时只有第一次吃到的加成（拉伸、瞄准、寂灭）、背包的免费出牌与叽 诗句要用的辅助函数。
// 全部只读。
public sealed partial class Observation
{
    // ---------- 跳过指定监听者重算（照 Hook.ModifyDamage / ModifyBlock 的顺序）。只用来求“某几个监听者贡献了多少”，结果取与全量重算的差 ----------

    static float DmgRecalc(CardModel c, Creature? t, Func<AbstractModel, bool> skip)
    {
        var cs = c.Owner.Creature.CombatState!;
        decimal n; ValueProp props;
        if (c.DynamicVars.TryGetValue("CalculatedDamage", out var cv) && cv is CalculatedDamageVar cd) { n = cd.Calculate(t); props = cd.Props; }
        else if (c.DynamicVars.TryGetValue("Damage", out var dv) && dv is DamageVar d) { n = d.BaseValue; props = d.Props; }
        else return -1;
        if (c.Enchantment is { } e) { n += e.EnchantDamageAdditive(n, props); n *= e.EnchantDamageMultiplicative(n, props); }
        var ls = cs.RunState.IterateHookListeners(cs).Where(l => !skip(l)).ToList();
        var dealer = c.Owner.Creature;
        foreach (var l in ls) n += l.ModifyDamageAdditive(t, n, props, dealer, c, null);
        foreach (var l in ls) n *= l.ModifyDamageMultiplicative(t, n, props, dealer, c, null);
        foreach (var l in ls) n *= l.ModifyDamageMultiplicativeLate(t, n, props, dealer, c, null);
        foreach (var l in ls) { var x = l.ModifyDamageCap(t, props, dealer, c, null); if (n > x) n = x; } n = Math.Min(n, OldHtkCap(t));      // v0.114.0：难杀照旧口径封顶（见 Damage.cs OldHtkCap）
        return MathF.Floor((float)Math.Max(0m, n));
    }

    static bool IsAimLike(AbstractModel l) => l is AimPower or QuietusPower;

    /// <summary>
    /// 瞄准、寂灭只加在第一次箭攻击上（AimPower / QuietusPower.BeforeAttack 记下第一条 FromArrow 的有力攻击，AfterAttack 扣层数或移除）：
    /// 同一张牌重放、或背包里的第二张射击牌，单段伤害要去掉它们的贡献。pd 为界面预览（含它们）。没有这两个能力时原样返回。
    /// </summary>
    static float PdNoAim(CardModel c, Creature? t, float pd)
    {
        try
        {
            var me = c.Owner.Creature;
            if (pd <= 0 || !(me.HasPower<AimPower>() || me.HasPower<QuietusPower>())) return pd;
            float all = DmgRecalc(c, t, _ => false), ex = DmgRecalc(c, t, IsAimLike);
            if (all < 0 || ex < 0) return pd;
            return Math.Max(0, pd - (all - ex));
        }
        catch { return pd; }
    }

    static float BlockRecalc(CardModel c, Func<AbstractModel, bool> skip)
    {
        var cs = c.Owner.Creature.CombatState!;
        decimal n; ValueProp props;
        if (c.DynamicVars.TryGetValue("Block", out var bv) && bv is BlockVar b) { n = b.BaseValue; props = b.Props; }
        else return -1;
        if (c.Enchantment is { } e) { n += e.EnchantBlockAdditive(n); n *= e.EnchantBlockMultiplicative(n); }
        var ls = cs.IterateHookListeners().Where(l => !skip(l)).ToList();
        var me = c.Owner.Creature;
        foreach (var l in ls) n += l.ModifyBlockAdditive(me, n, props, c, null);
        foreach (var l in ls) n *= l.ModifyBlockMultiplicative(me, n, props, c, null);
        return MathF.Floor((float)Math.Max(0m, n));
    }

    /// <summary>
    /// 拉伸（StretchPower：给第一张获得格挡的牌的这一次打出加层数，AfterCardPlayed 扣掉）：重放的第二次起不再有。
    /// 啾 叽 例外（ChoKi.OnPlay 获得格挡后把拉伸原样加回）。pb 为卡面预览（含拉伸）；返回重放时每次的格挡。
    /// </summary>
    static float PbNoStretch(CardModel c, float pb)
    {
        try
        {
            if (pb <= 0 || c is ChoKi || !c.Owner.Creature.HasPower<StretchPower>()) return pb;
            float all = BlockRecalc(c, _ => false), ex = BlockRecalc(c, l => l is StretchPower);
            if (all < 0 || ex < 0) return pb;
            return Math.Max(0, pb - (all - ex));
        }
        catch { return pb; }
    }

    /// <summary>出牌候选 8 的卡面格挡部分：每次打出 BlockGains 次，第一次带拉伸、重放不带（见 PbNoStretch）。</summary>
    static float CardBlockTotal(CardModel c, float pb, int plays) =>
        plays <= 1 ? pb * plays * BlockGains(c) : BlockGains(c) * (pb + (plays - 1) * PbNoStretch(c, pb));

    static bool IsFreePower(AbstractModel l) => l is FreeAttackPower or FreeSkillPower or FreePowerPower;

    /// <summary>不算“免费出牌”时这张牌的费用（照 CardEnergyCost.GetWithModifiers：Local 之后过 TryModifyEnergyCostInCombat、再过 Late，Late 跳过 Free*Power）。X 费为 −1。</summary>
    static int CostNoFree(CardModel c)
    {
        try
        {
            if (c.EnergyCost.CostsX) return -1;
            decimal m = c.EnergyCost.GetWithModifiers(CostModifiers.Local);
            if (m < 0) return -1;
            var cs = c.Owner.Creature.CombatState!;
            foreach (var l in cs.IterateHookListeners()) if (l.TryModifyEnergyCostInCombat(c, m, out var m1)) m = m1;
            foreach (var l in cs.IterateHookListeners()) if (!IsFreePower(l) && l.TryModifyEnergyCostInCombatLate(c, m, out var m2)) m = m2;
            return Math.Max(0, (int)m);
        }
        catch { return -1; }
    }

    /// <summary>“免费出牌”还剩几张（v0.114.0 起没有不限类型的 FreeCardPower（调音重做时删掉）；FreeAttack / Skill / Power 按类型，BeforeCardPlayed 每张扣一层），封顶 3。</summary>
    static int FreeLayersFor(Player me, CardModel c)
    {
        int n = 0;
        foreach (var pw in me.Creature.Powers)
            n += pw switch
            {
                FreeAttackPower when c.Type == CardType.Attack => pw.Amount,
                FreeSkillPower when c.Type == CardType.Skill => pw.Amount,
                FreePowerPower when c.Type == CardType.Power => pw.Amount,
                _ => 0,
            };
        return Math.Clamp(n, 0, 3);
    }

    static bool IsMelody(CardModel c) { try { return c.Tags.Contains(CardTag.Wah) || c.Tags.Contains(CardTag.Cho) || c.Tags.Contains(CardTag.Ki); } catch { return false; } }

    static readonly CardTag[] MelodyTags = { CardTag.Wah, CardTag.Cho, CardTag.Ki };

    /// <summary>叽 诗句多打一次的牌：v0.114.0 起只有带哇或啾的（KiVersePower.ModifyCardPlayCount 去掉了 Ki）。</summary>
    static bool KiVerseCard(CardModel c) { try { return c.Tags.Contains(CardTag.Wah) || c.Tags.Contains(CardTag.Cho); } catch { return false; } }

    /// <summary>这张牌打出后本回合第一次出现的旋律标签数（曲中意、失落鲁特琴：CardPlaysStarted 里本回合、自己开始的、带这个标签的恰好是这一张）。</summary>
    static int NewMelodyTags(Player me, CardModel c)
    {
        try
        {
            var cs = me.Creature.CombatState!;
            var started = CombatManager.Instance.History.CardPlaysStarted.Where(e => e.Actor == me.Creature && e.HappenedThisTurn(cs)).ToList();
            return MelodyTags.Count(t => c.Tags.Contains(t) && !started.Any(e => e.CardPlay.Card.Tags.Contains(t)));
        }
        catch { return 0; }
    }

    /// <summary>
    /// 这一步拔出几支你的箭（合股线、呀！、取箭神技用；与合股线原来的模拟同一口径）：对被你扎穿的目标逐段数 HitCounter，到 0 拔出一支；
    /// 射击牌每次打出（有箭时）再扎进一支（v0.114.0 起寂灭不再摧毁箭：QuietusPower.AfterArrowShot 删掉）；
    /// 目标被打死时扎着的全部拔出。召回、连根拔起、拧下：目标身上你的箭全部拔出。hit 为攻击段拔出的，death 为目标死亡拔出的。只看有目标的。
    /// </summary>
    static (int hit, int death, int shots) ArrowsOut(CardModel card, Creature? tgt, Player me, bool tgtDead)
    {
        try
        {
            var pcs = me.PlayerCombatState!;
            int plays = PlayCount(card, tgt), avail = pcs.AvailableArrowCount;
            bool shoot = IsShoot(card);
            int shots = shoot ? Math.Min(plays, avail) : 0;
            if (tgt == null) return (0, 0, shots);
            var imp = tgt.GetPowerInstances<ImpaledPower>().FirstOrDefault(p => p.Applier == me.Creature);
            int n = imp?.Arrows.Count ?? 0, ctr = imp?.HitCounter ?? ImpaleHits, d = 0;
            if (card is Recall or Uproot or Wrest) return (n, 0, 0);
            if (card.Type != CardType.Attack) return (0, 0, shots);
            var (pd, _, h) = Preview(card, tgt);
            Preview(card, null);
            if (pd < 0) return (0, 0, shots);
            for (int r = 0; r < plays; r++)
            {
                if (shoot && r >= shots) continue;
                for (int i = 0; i < (int)h && n > 0; i++) { if (--ctr <= 0) { ctr = ImpaleHits; n--; d++; } }
                // v0.114.0：寂灭不再摧毁它射出的箭（QuietusPower.AfterArrowShot 删掉，ArrowSlot.DestroyAfterImpaled、ArrowStatus.Destroyed 一并删掉），每次射出都扎进一支
                if (shoot) n++;
            }
            return (d, tgtDead ? n : 0, shots);
        }
        catch { return (0, 0, 0); }
    }

    // ---------- 自动打出（CardCmd.AutoPlay：完整的出牌，不花能量，无目标的单体牌随机挑可攻击的敌人；带“不能打出”的直接移去结算牌堆） ----------

    /// <summary>
    /// 一串自动打出的牌的确定部分。sure：打全体的、或只剩一个可攻击敌人时的单体，逐段记下；多个可攻击敌人时单体按期望摊进 exp（不算击杀）。
    /// 格挡：卡面预览 × CardBlockTotal；能量、进手：CardGains（无目标）。没箭的射击牌不攻击。旋律之间的连锁（暴力和弦、歌之道等）不另算。
    /// </summary>
    (Dictionary<Creature, List<Hit>> sure, Dictionary<Creature, float> exp, float block, float energy, float cards) AutoPlayEffect(IEnumerable<CardModel> cards, Player me, CardModel? outer = null)
    {
        // v0.114.0：MelodyCmd.PlayedThisTurn 改按 CardPlaysStarted 判断，正在结算的外层牌（啾 副歌）与先自动打出的牌都已“开始”，
        // 后面自动打出的哇 爆破看得到它们的旋律（补丁说明“Fixed Cho Chorus not triggering the autoplayed Wah Blast double hit effect”）
        bool choOrKi = PlayedCho(me) || PlayedKi(me) || (outer != null && (outer.Tags.Contains(CardTag.Cho) || outer.Tags.Contains(CardTag.Ki)));
        var cs = me.Creature.CombatState!;
        var foes = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
        var sure = foes.ToDictionary(f => f, _ => new List<Hit>());
        var exp = foes.ToDictionary(f => f, _ => 0f);
        float block = 0, energy = 0, drawn = 0;
        var move = ValueProp.Move;
        foreach (var ac in cards)
        {
            try
            {
                if (Kw(ac).Contains(CardKeyword.Unplayable)) continue;
                var hittable = Hittable(cs).Where(f => sure.ContainsKey(f) && !(sure[f].Count > 0 && SeqHpLoss(f, sure[f]) >= f.CurrentHp)).ToList();
                int plays = PlayCount(ac, null);
                var (_, pb, _) = Preview(ac, null);
                if (pb > 0) block += CardBlockTotal(ac, pb, plays);
                if (ac.Type == CardType.Attack && hittable.Count > 0 && !ShootDisabled(ac))
                {
                    bool aoe = ac.TargetType == TargetType.AllEnemies;
                    foreach (var f in hittable)
                    {
                        var (pd, _, h) = Preview(ac, f);
                        if (pd <= 0) continue;
                        if (ac is WahBlast && choOrKi) h = ModHits(ac, 2);      // WahBlast.HitsTwice：本回合（已开始的出牌里）有啾或叽
                        float pdx = plays > 1 ? PdNoAim(ac, f, pd) : pd;
                        if (aoe || hittable.Count == 1)
                            for (int r = 0; r < plays; r++) for (int i = 0; i < (int)h; i++) sure[f].Add(new Hit(r == 0 ? pd : pdx, true, move, me.Creature, ac));
                        else exp[f] += (pd + pdx * (plays - 1)) * h / hittable.Count;
                    }
                    Preview(ac, null);
                }
                var (ge, gc) = CardGains(ac, null, me, null);
                energy += ge; drawn += gc;
            }
            catch { }
            try { if (ac.Tags.Contains(CardTag.Cho) || ac.Tags.Contains(CardTag.Ki)) choOrKi = true; } catch { }
        }
        return (sure, exp, block, energy, drawn);
    }

    /// <summary>抽牌堆顶已知的前 n 张（DrawTop：召回放到顶上的牌、固有牌移顶等；抽牌堆里的牌全都相同时也算已知）。不知道的部分不算。</summary>
    static List<CardModel> KnownTop(PlayerCombatState pcs, int n)
    {
        var res = new List<CardModel>();
        try
        {
            var pile = pcs.DrawPile.Cards;
            int known = DrawTop.Known(pcs.DrawPile);
            if (pile.Count > 0 && pile.All(c => SameCard(c, pile[0]))) known = pile.Count;
            for (int i = 0; i < Math.Min(n, Math.Min(known, pile.Count)); i++) res.Add(pile[i]);
        }
        catch { }
        return res;
    }

    /// <summary>两张牌打出来完全一样（同一张牌、同升级、同附魔与层数、同苦痛），随机挑哪张都一样。</summary>
    static bool SameCard(CardModel a, CardModel b)
    {
        try
        {
            return Vocab.Id(a) == Vocab.Id(b) && a.CurrentUpgradeLevel == b.CurrentUpgradeLevel
                && Vocab.Id(a.Enchantment) == Vocab.Id(b.Enchantment) && (a.Enchantment?.Amount ?? 0) == (b.Enchantment?.Amount ?? 0)
                && a.Affliction?.GetType() == b.Affliction?.GetType();
        }
        catch { return false; }
    }

    /// <summary>
    /// 这张牌打出时会自动打出哪些牌（确定的部分），以及额外离开手牌的牌。
    ///   啾 副歌：打出时手里所有带哇或叽的牌（ChoChorus.OnPlay 先取快照，逐张 AutoPlay）。
    ///   塔罗“愚者”：从手里能打出的牌里随机挑 Cards 张自动打出（TheFool.OnPlay），能打出的不多于 Cards 张时就是全部（之后挑剩下不能打出的，它们移去结算牌堆）；
    ///     能打出的都一样时取前 Cards 张；否则只知道离手的张数。
    ///   塔罗“恶魔”：消耗整手牌，再从抽牌堆顶自动打出同样张数（TheDevil.OnPlay）；顶上已知的部分算进来。
    ///   失落鲁特琴（LostLutePower.AfterCardPlayed）：打出本回合第一张某种旋律时，每个新旋律从抽牌堆顶自动打出层数张；顶上已知的部分算进来。
    /// handDelta 为除了这张牌自己之外手牌张数的变化（≤ 0），gone 为确定离手的牌。
    /// </summary>
    (List<CardModel> plays, int handDelta, List<CardModel>? gone) AutoPlays(CardModel card, Player me)
    {
        var res = new List<CardModel>();
        int hd = 0; List<CardModel>? gone = null;
        try
        {
            var pcs = me.PlayerCombatState!;
            var others = pcs.Hand.Cards.Where(c => !ReferenceEquals(c, card)).ToList();
            switch (card)
            {
                case ChoChorus:
                    gone = others.Where(c => c.Tags.Contains(CardTag.Wah) || c.Tags.Contains(CardTag.Ki)).ToList();
                    res.AddRange(gone); hd = -gone.Count;
                    break;
                case TheFool:
                {
                    // v0.114.0：没有能打出的牌时不再改挑“不能打出”的牌（TheFool.OnPlay 删掉了回退分支），不能打出的牌留在手里
                    int n = card.DynamicVars.Cards.IntValue;
                    var ok = others.Where(c => !Kw(c).Contains(CardKeyword.Unplayable)).ToList();
                    hd = -Math.Min(n, ok.Count);
                    if (ok.Count <= n) { res.AddRange(ok); gone = new List<CardModel>(ok); }
                    else if (ok.All(c => SameCard(c, ok[0]))) { res.AddRange(ok.Take(n)); gone = new List<CardModel>(res); }
                    break;
                }
                case TheDevil:
                    gone = others; hd = -others.Count;
                    res.AddRange(KnownTop(pcs, others.Count));
                    break;
                // v0.114.0 新牌乐观：消耗手里所有悲伤（Optimism.OnPlay：Hand 里 c is Sadness 的逐张 CardCmd.Exhaust），之后每张抽 1（见 CardGains）
                case Optimism:
                    gone = others.Where(c => c is Sadness).ToList(); hd = -gone.Count;
                    break;
            }
            if (me.Creature.GetPower<LostLutePower>() is { } lute && lute.Amount > 0 && IsMelody(card))
            {
                int k = NewMelodyTags(me, card) * lute.Amount;
                if (k > 0 && card is not TheDevil) res.AddRange(KnownTop(pcs, k));
            }
        }
        catch { }
        return (res, hd, gone);
    }

    // ---------- 格式 26：候选 12、13 ----------

    /// <summary>无条件当场加能量的牌（OnPlay 顶层的 GainEnergy 用 Energy 数值）。别的带 Energy 数值的牌是下回合、扣能量或阈值，不算。</summary>
    static readonly HashSet<Type> EnergyCards = new()
    {
        typeof(Adrenaline), typeof(Alignment), typeof(BigBang), typeof(Bloodletting), typeof(BorrowedTime), typeof(BrightestFlame), typeof(Encore),
        typeof(Fuel), typeof(Joy), typeof(Luminesce), typeof(Neurosurge), typeof(Offering), typeof(Production), typeof(Supercritical),
        typeof(Tactician), typeof(TheEmperor), typeof(TheSun), typeof(Turbo), typeof(Wisp),
    };

    /// <summary>无条件当场抽 Cards 张的牌（OnPlay 顶层的 Draw 用 Cards 数值）。</summary>
    static readonly HashSet<Type> DrawCards = new()
    {
        typeof(Acrobatics), typeof(Adrenaline), typeof(Backflip), typeof(BattleTrance), typeof(BigBang), typeof(BrightestFlame), typeof(BurningPact),
        typeof(Constellation), typeof(Coolheaded), typeof(DecisionsDecisions), typeof(DeepStretch), typeof(DrumOfBattle), typeof(Encore),
        typeof(Expertise), typeof(Finesse), typeof(FlashOfSteel), typeof(Glimmer), typeof(Glow), typeof(Joy), typeof(MasterOfStrategy), typeof(MinionStrike),
        typeof(Modded), typeof(Neurosurge), typeof(Offering), typeof(Overclock), typeof(Parse), typeof(PhotonCut), typeof(PommelStrike), typeof(Prepared),
        typeof(Prophesize), typeof(Reboot), typeof(Reflex), typeof(RocketPunch), typeof(Rummage), typeof(Scourge), typeof(Scrape), typeof(ShrugItOff),
        typeof(Skim), typeof(Slimed), typeof(Soul), typeof(SpoilsOfBattle), typeof(SweepingBeam), typeof(TheHermit), typeof(TheHighPriestess), typeof(TheSun),
        typeof(ThinkingAhead), typeof(WahChoKi),
    };

    /// <summary>
    /// 一张牌打出后当场得到的能量与进手的牌数（未封顶；CandGains 再封顶）。tgt 为目标（自动打出时为空），killed 为这一步打死的敌人。
    /// 能量：无条件的 × 打出次数；双倍能量（付费之后的能量，重放时翻倍再加）；持弓猛击（目标打之前被箭扎着，每次）；分离（打死目标）；
    ///   心神不宁（是唯一的手牌）；被遗忘的仪式（本回合消耗过牌）；取箭神技（攻击拔出你的箭，每支 Energy）；呀！（这一步拔出你的箭、本回合还没触发，层数）。
    /// 进手：无条件抽牌 × 打出次数（编译冲击、平稳韵律按 CalculatedCards）；投掷匕首、逃脱计划各 1；叽 跳跃（目标被箭扎着，每次）；拧下（目标被你扎着）；心神不宁；
    ///   曲中意（打出本回合第一张某种旋律，每个新旋律抽层数张）；花样射击（v0.114.0 起：每次射出箭抽 Cards 张）；呼哨箭矢（这一步射出箭时弃牌堆里的每张回手）；
    ///   叽 唱诵（抽牌堆 + 弃牌堆不空，选 1 张）；遮天蔽日 1；活跃气氛（悲伤 + 喜悦）；乐观（手里的悲伤张数）；自嘲（悲伤张数）；塔罗“教皇”（Cards 张）；
    ///   说笑、哀伤打击、刻薄、翻找塞进手里的悲伤，悲痛一击变成悲伤回手，凝神选进手的射击牌。
    /// v0.114.0 删掉的：箭矢戳击不再从弃牌堆拿射击牌，怒放改成手里一张牌变化成神射（手牌张数不变），花样射击不再回手。
    /// </summary>
    static (float energy, float cards) CardGains(CardModel c, Creature? tgt, Player me, List<Creature>? killed)
    {
        float en = 0, cards = 0;
        try
        {
            var pcs = me.PlayerCombatState!;
            int plays = PlayCount(c, tgt);
            float Var(string name) => c.DynamicVars.TryGetValue(name, out var v) ? (float)v.BaseValue : 0;
            bool tgtDead = tgt != null && killed != null && killed.Contains(tgt);
            bool arrowed = tgt != null && NailHits(tgt);
            bool mine = tgt != null && tgt.GetPowerInstances<ImpaledPower>().Any(p => p.Applier == me.Creature && p.Arrows.Count > 0);
            if (EnergyCards.Contains(c.GetType())) en += Var("Energy") * plays;
            if (DrawCards.Contains(c.GetType())) cards += Var("Cards") * plays;
            switch (c)
            {
                case DoubleEnergy:
                {
                    int cost = Math.Max(0, CostAll(c));
                    float left = Math.Max(0, pcs.Energy - cost);
                    en += left * (MathF.Pow(2, plays) - 1);
                    break;
                }
                case BowSmack when arrowed: en += Var("Energy") * plays; break;
                case Sunder when tgtDead: en += Var("Energy"); break;
                case Restlessness when pcs.Hand.Cards.Count == 1: en += Var("Energy"); cards += Var("Cards"); break;
                case ForgottenRitual when CombatManager.Instance.History.Entries.OfType<MegaCrit.Sts2.Core.Combat.History.Entries.CardExhaustedEntry>()
                                             .Any(e => e.HappenedThisTurn(me.Creature.CombatState!)):
                    en += Var("Energy"); break;
                case CompileDriver or SteadyCadence:
                    if (c.DynamicVars.TryGetValue("CalculatedCards", out var cc) && cc is CalculatedVar cv) cards += (float)cv.Calculate(tgt) * plays;
                    break;
                case DaggerThrow or EscapePlan: cards += plays; break;
                case KiHop when arrowed: cards += Var("Cards") * plays; break;
                case Wrest when mine: cards += Var("Cards"); break;
                case KiChant when pcs.DrawPile.Cards.Count + pcs.DiscardPile.Cards.Count > 0: cards += 1; break;
                // v0.114.0：怒放改成把手里一张牌变化成神射（Florescence.OnPlay：CardSelectCmd.FromHand → CardCmd.Transform），手牌张数不变，不再算进手
                case BlotOutTheSky: cards += 1; break;
                // v0.114.0 新牌乐观：消耗手里所有悲伤，消耗了几张就抽几张（Optimism.OnPlay：cardCount > 0 时 CardPileCmd.Draw(cardCount)）；
                // 只算第一次打出（重放时悲伤已经消耗光，抽到的是不是悲伤不知道）。离手的悲伤在 AutoPlays 里记进 _autoHand，封顶按它算
                case Optimism: cards += pcs.Hand.Cards.Count(x => x is Sadness && !ReferenceEquals(x, c)); break;
                case BrightenUp: cards += Var("Sadness") + Var("Joy"); break;
                // 自嘲：自己手里塞 "Sadness" 张悲伤（喜悦只给队友，单人局没有）；塔罗“教皇”：从无色卡池挑 Cards 张进手（v0.114.0 起不再减费，TheHierophant.OnPlay）
                case SelfDeprecate: cards += Var("Sadness") * plays; break;
                case TheHierophant: cards += Var("Cards") * plays; break;
                // 往手里塞悲伤（AddGeneratedCardsToCombat 到手牌）：说笑、哀伤打击 Cards 张，刻薄、翻找 1 张（翻找另抽 Cards 张，在 DrawCards 里）；
                // 悲痛一击最后一次打出后整张变成悲伤回手（GetResultLocationForCardPlay 改成手牌）；凝神从抽牌堆选一张射击牌进手
                case Jest or SorrowfulStrike: cards += Var("Cards") * plays; break;
                // v0.114.0：刻薄塞 Cards（2）张悲伤（Vitriol.OnPlay 循环 DynamicVars.Cards 次），翻找仍是 1 张
                case Vitriol: cards += Var("Cards") * plays; break;
                case Rummage: cards += plays; break;
                case MourningBlow: cards += 1; break;
                case LockedIn when pcs.DrawPile.Cards.Any(IsShoot): cards += 1; break;
            }
            // 拔箭与射箭
            var (rh, rd, shots) = c.Type == CardType.Attack || c is Recall or Uproot or Wrest ? ArrowsOut(c, tgt, me, tgtDead) : (0, 0, 0);
            if (c is EpicRetrieval) en += Var("Energy") * (rh + rd);      // 目标被打死时拔出的箭，来源也是这张牌（ImpaledPower.AfterDeath）
            if (rh + rd > 0 && me.Creature.GetPower<YahPower>() is { } yah && !InternalFlag(yah, "triggeredThisTurn")) en += yah.Amount;
            if (shots > 0) cards += pcs.DiscardPile.Cards.Count(x => x is WhistlingArrows);
            // v0.114.0：花样射击不再回手，改为射出箭时抽 Cards（2/3）张（TrickShot.OnPlay：ShootArrowAtTarget 返回真才 CardPileCmd.Draw）；
            // 每次打出各抽一次，只有前“可用箭数”次射得出箭（目标在前一次被打死时后面几次返回假，这里不细分）
            if (c is TrickShot) cards += Var("Cards") * shots;
            // v0.114.0：箭矢戳击不再从弃牌堆拿射击牌（ArrowStab.OnPlay 改成伤害 + 瞄准），不进手
            // 曲中意
            if (IsMelody(c) && me.Creature.GetPower<MotifPower>() is { } motif) cards += motif.Amount * NewMelodyTags(me, c);
        }
        catch { }
        return (en, cards);
    }

    /// <summary>能力内部数据里的一个布尔字段（如呀！的 triggeredThisTurn）。读不到为假。</summary>
    static bool InternalFlag(PowerModel pw, string field)
    {
        try
        {
            var data = InternalDataField?.GetValue(pw);
            var f = data?.GetType().GetField(field, System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
            return f?.GetValue(data) is true;
        }
        catch { return false; }
    }

    /// <summary>这一步自动打出的牌带来的能量与进手牌数（FoolPreview 算出，CandGains 加上；每个候选开头清零）。</summary>
    (float energy, float cards) _autoGain;
    /// <summary>这一步自动打出等让手牌额外少几张（≤ 0，不含打出的这张）与确定离手的牌（FoolPreview 算出，候选 10、13 用；每个候选开头清零）。</summary>
    int _autoHand;
    List<CardModel>? _autoGone;

    /// <summary>
    /// 格式 26：候选这一步当场得到的能量（12）与进手的牌数（13）。出牌：CardGains 加上自动打出的牌；用药水：能量药水、迅捷药水。
    /// 封顶：有“不能加能量”时 12 为 0；13 不超过“手牌上限 − 这一步之后的手牌数”，有“不能抽牌”时为 0。
    /// </summary>
    (float energy, float cards) CandGains(Choice ch, Player me, List<Creature>? killed, int handDelta)
    {
        try
        {
            var pcs = me.PlayerCombatState!;
            float en = 0, cards = 0;
            if (ch.Kind == ActionKind.PlayCard && ch.A is CardModel c)
            {
                (en, cards) = CardGains(c, ch.B as Creature, me, killed);
                en += _autoGain.energy; cards += _autoGain.cards;
            }
            else if (ch.Kind == ActionKind.UsePotion && ch.A is PotionModel p)
            {
                if (p is MegaCrit.Sts2.Core.Models.Potions.EnergyPotion && p.DynamicVars.TryGetValue("Energy", out var ev)) en = (float)ev.BaseValue;
                if (p is MegaCrit.Sts2.Core.Models.Potions.SwiftPotion && p.DynamicVars.TryGetValue("Cards", out var cv)) cards = (float)cv.BaseValue;
            }
            else return (0, 0);
            // 永世沙漏的凋萎存在（WitheringPresencePower.AfterCardPlayed）：倒数到 0 时往手里塞一张凋萎，每 6 张一轮
            if (ch.Kind == ActionKind.PlayCard && ch.A is CardModel pc)
            {
                int p = PlayCount(pc, ch.B as Creature);
                foreach (var f in me.Creature.CombatState!.Creatures)
                    foreach (var wp in f.GetPowerInstances<WitheringPresencePower>())
                        if (wp.DisplayAmount >= 1 && wp.DisplayAmount <= p) cards += 1 + (p - wp.DisplayAmount) / 6;
            }
            if (me.Creature.HasPower<NoEnergyGainPower>()) en = 0;
            if (me.Creature.HasPower<NoDrawPower>()) cards = 0;
            int handAfter = pcs.Hand.Cards.Count + handDelta;
            cards = Math.Min(cards, Math.Max(0, CardPile.MaxCardsInHand - handAfter));
            return (en, cards);
        }
        catch { return (0, 0); }
    }
}
