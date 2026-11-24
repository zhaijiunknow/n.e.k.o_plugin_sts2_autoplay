using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Potions;
using MegaCrit.Sts2.Core.Models.Powers;

namespace Sts2Headless;

// 格式 29 包 4（R3-格式29规格.md 5.4）：Fx23 的新口径 Fx29（G15 漏项、G40 灾厄处决数），出牌给敌人上减益的公用牌表 Debuffs29，
// 静默猎手出牌附带的整手弃（G45）、速行者与回响斩击（G46）。只在 V29 下调用，只读。
public sealed partial class Observation
{
    /// <summary>
    /// 格式 29：一张牌第 r 次打出时依次给敌人上的减益（与灾厄），逐个交给 D(敌人, 能力的原型, 层数)，人工制品与加减层钩子由调用方处理。
    /// 牌表同 Fx23（顺序不变），另加：
    ///   hitRules（每段伤害触发的，在牌自己的减益之前）：死神形态（你与奥斯提每段有力攻击，TotalDamage × 层数点灾厄）、王之凝视（你的每段有力攻击，层数点临时减力量）、
    ///     涂毒（你的每段有力攻击打穿格挡，层数点毒）、荒疫打击（打完按 TotalDamage 加灾厄）。段数取界面预览，打到目标死为止不另算。
    ///   亡灵的灾厄牌：负能量脉冲、末日降临（全体）、鞭打、无处可逃（单体）；湮灭（敌人身上你的湮灭，每打出一张牌加层数点灾厄）；
    ///   摧残（打完给目标上摧残）；苦难（打完把目标身上的减益按层数抄给其余可攻击的敌人）；墨影附魔（给目标上虚弱，打全体的牌给全体）。
    ///   vulRule（巨像、血肉戏法相关时才打开，免得改动人工制品的扣减顺序）：有 VulnerablePower 数值、不在显式名单里的牌按目标类型上易伤；
    ///     冲击波、女皇、蹒跚射击、钉住、萎靡之外的“Power”数值牌在虚弱之后补上易伤。
    /// poisoned(f)：目标已中毒或这一步已经加过毒（咕嘟冒泡的条件）。只读。
    /// </summary>
    void Debuffs29(CardModel card, Creature? tgt, Player me, int r, IReadOnlyList<Creature> hittable, Func<Creature, bool> poisoned,
                   Action<Creature, PowerModel, decimal> D, bool vulRule, bool hitRules)
    {
        var cs = me.Creature.CombatState!;
        var pcs = me.PlayerCombatState!;
        void Dt<T>(Creature f, decimal amount) where T : PowerModel => D(f, ModelDb.Power<T>(), amount);
        List<Creature> Targets() => tgt != null ? new List<Creature> { tgt }
            : card.TargetType == TargetType.AllEnemies ? hittable.ToList()
            : card.TargetType == TargetType.RandomEnemy && hittable.Count == 1 ? hittable.ToList() : new List<Creature>();

        // 每段伤害触发的
        if (hitRules && card.Type == CardType.Attack)
        {
            try
            {
                int reaper = me.Creature.GetPowerAmount<ReaperFormPower>();
                int gaze = me.Creature.GetPowerAmount<MonarchsGazePower>();
                int envenom = me.Creature.GetPowerAmount<EnvenomPower>();
                bool ostyCard = card.Tags.Contains(CardTag.OstyAttack);
                bool blight = card is BlightStrike && tgt != null;
                if (reaper > 0 || (!ostyCard && (gaze > 0 || envenom > 0)) || blight)
                {
                    foreach (var f in Targets())
                    {
                        var (pd, _, h) = Preview(card, f);
                        if (pd <= 0) continue;
                        float blk = r == 0 ? f.Block : 0;
                        for (int i = 0; i < (int)h; i++)
                        {
                            float b = Math.Min(blk, pd); blk -= b;
                            if (reaper > 0) Dt<DoomPower>(f, (decimal)pd * reaper);
                            if (!ostyCard && gaze > 0) Dt<MonarchsGazeStrengthDownPower>(f, gaze);
                            if (!ostyCard && envenom > 0 && pd - b > 0) Dt<PoisonPower>(f, envenom);
                        }
                        if (blight && ReferenceEquals(f, tgt)) Dt<DoomPower>(f, (decimal)(pd * (int)h));
                    }
                    Preview(card, null);
                }
            }
            catch { }
        }

        // 湮灭：敌人身上你的湮灭，每打出一张牌（BeforeCardPlayed 记下层数，AfterCardPlayed 加灾厄）
        foreach (var f in hittable)
            foreach (var ob in f.Powers.OfType<OblivionPower>())
                if (ob.Applier == me.Creature && ob.Amount > 0) Dt<DoomPower>(f, ob.Amount);

        bool explicitVul = false;
        switch (card)
        {
            case Neutralize or SuckerPunch or LegSweep when tgt != null:
                Dt<WeakPower>(tgt, card.DynamicVars.Weak.BaseValue); break;
            case Haze:
                foreach (var f in hittable) Dt<PoisonPower>(f, card.DynamicVars.Poison.BaseValue);
                foreach (var f in hittable) Dt<WeakPower>(f, card.DynamicVars.Weak.BaseValue);
                break;
            case Malaise when tgt != null:
                int x = Math.Max(0, MegaCrit.Sts2.Core.Hooks.Hook.ModifyXValue(cs, card, pcs.Energy)) + (card.IsUpgraded ? 1 : 0);
                if (x > 0) { Dt<StrengthPower>(tgt, -x); Dt<WeakPower>(tgt, x); }
                break;
            case PiercingWail:
                foreach (var f in hittable) Dt<PiercingWailPower>(f, card.DynamicVars["StrengthLoss"].BaseValue); break;
            case DeadlyPoison or Snakebite or PoisonedStab when tgt != null:
                Dt<PoisonPower>(tgt, card.DynamicVars.Poison.BaseValue); break;
            case BubbleBubble when tgt != null:
                if (poisoned(tgt)) Dt<PoisonPower>(tgt, card.DynamicVars.Poison.BaseValue);
                break;
            case BouncingFlask when hittable.Count == 1:
                for (int i = 0; i < card.DynamicVars.Repeat.IntValue; i++) Dt<PoisonPower>(hittable[0], card.DynamicVars.Poison.BaseValue);
                break;
            case Vitriol when tgt != null:
                Dt<WeakPower>(tgt, card.DynamicVars.Weak.BaseValue); break;
            case TheEmpress when tgt != null:
                Dt<WeakPower>(tgt, card.DynamicVars["Power"].BaseValue);
                if (vulRule) Dt<VulnerablePower>(tgt, card.DynamicVars["Power"].BaseValue);
                explicitVul = true; break;
            case HobblingShot when tgt != null && r < pcs.AvailableArrowCount:
                Dt<WeakPower>(tgt, card.DynamicVars["Power"].BaseValue);
                if (vulRule) Dt<VulnerablePower>(tgt, card.DynamicVars["Power"].BaseValue);
                explicitVul = true; break;
            case Nail when tgt != null && NailHits(tgt):
                Dt<WeakPower>(tgt, card.DynamicVars["Power"].BaseValue);
                if (vulRule) Dt<VulnerablePower>(tgt, card.DynamicVars["Power"].BaseValue);
                explicitVul = true; break;
            case Suppress when tgt != null:
                Dt<WeakPower>(tgt, card.DynamicVars.Weak.BaseValue); break;
            case Shockwave:
                foreach (var f in hittable) Dt<WeakPower>(f, card.DynamicVars["Power"].BaseValue);
                if (vulRule) foreach (var f in hittable) Dt<VulnerablePower>(f, card.DynamicVars["Power"].BaseValue);
                explicitVul = true; break;
            case MadScience ms when tgt != null && ms.TinkerTimeRider == MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Sapping:
                Dt<WeakPower>(tgt, card.DynamicVars["SappingWeak"].BaseValue);
                if (vulRule) Dt<VulnerablePower>(tgt, card.DynamicVars["SappingVulnerable"].BaseValue);
                explicitVul = true; break;
            case GoForTheEyes when tgt != null:
                if (tgt.Monster is { IntendsToAttack: true }) Dt<WeakPower>(tgt, card.DynamicVars.Weak.BaseValue);
                break;
            case Null or Comet or FallingStar or GammaBlast or Defy or KnowThyPlace when tgt != null:
                Dt<WeakPower>(tgt, card.DynamicVars.Weak.BaseValue);
                if (card is Comet or FallingStar or GammaBlast or KnowThyPlace) Dt<VulnerablePower>(tgt, card.DynamicVars.Vulnerable.BaseValue);
                explicitVul = true; break;
            case Putrefy or Uppercut when tgt != null:
                Dt<WeakPower>(tgt, card.DynamicVars["Power"].BaseValue); Dt<VulnerablePower>(tgt, card.DynamicVars["Power"].BaseValue); explicitVul = true; break;
            case Deathbringer:
                foreach (var f in hittable) Dt<DoomPower>(f, card.DynamicVars.Doom.BaseValue);
                foreach (var f in hittable) Dt<WeakPower>(f, card.DynamicVars.Weak.BaseValue);
                break;
            case MeteorShower:
                foreach (var f in hittable) Dt<WeakPower>(f, card.DynamicVars.Weak.BaseValue);
                foreach (var f in hittable) Dt<VulnerablePower>(f, card.DynamicVars.Vulnerable.BaseValue);
                explicitVul = true; break;
            case Resonance:
                foreach (var f in hittable) Dt<StrengthPower>(f, -1m); break;
            case SharedFate when tgt != null:
                Dt<StrengthPower>(tgt, -card.DynamicVars["EnemyStrengthLoss"].BaseValue); break;
            case DarkShackles when tgt != null:
                Dt<DarkShacklesPower>(tgt, card.DynamicVars["StrengthLoss"].BaseValue); break;
            case EnfeeblingTouch when tgt != null:
                Dt<EnfeeblingTouchPower>(tgt, card.DynamicVars["StrengthLoss"].BaseValue); break;
            case Mangle when tgt != null:
                Dt<ManglePower>(tgt, card.DynamicVars["StrengthLoss"].BaseValue); break;
            case CrushUnder:
                foreach (var f in hittable) Dt<CrushUnderPower>(f, card.DynamicVars["StrengthLoss"].BaseValue); break;
            case DyingStar:
                foreach (var f in hittable) Dt<DyingStarPower>(f, card.DynamicVars["StrengthLoss"].BaseValue); break;
            // 格式 29：亡灵契约师的灾厄牌、摧残、苦难
            case NegativePulse or EndOfDays:
                foreach (var f in hittable) Dt<DoomPower>(f, card.DynamicVars.Doom.BaseValue); break;
            case Scourge when tgt != null:
                Dt<DoomPower>(tgt, card.DynamicVars.Doom.BaseValue); break;
            case NoEscape when tgt != null:
                try { Dt<DoomPower>(tgt, ((MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedVar)card.DynamicVars["CalculatedDoom"]).Calculate(tgt)); } catch { }
                break;
            case Oblivion when tgt != null:
                Dt<OblivionPower>(tgt, card.DynamicVars.Doom.BaseValue); break;
            case Debilitate when tgt != null:
                Dt<DebilitatePower>(tgt, card.DynamicVars["DebilitatePower"].BaseValue); break;
            case Misery when tgt != null:
                foreach (var p in tgt.Powers.Where(p => p.TypeForCurrentAmount == MegaCrit.Sts2.Core.Entities.Powers.PowerType.Debuff).ToList())
                    foreach (var f in hittable)
                        if (!ReferenceEquals(f, tgt)) D(f, p, p.Amount);
                break;
        }
        // 墨影附魔：给目标上虚弱（打全体的牌给全体）
        if (card.Enchantment is MegaCrit.Sts2.Core.Models.Enchantments.Inky inky)
        {
            try
            {
                decimal w = inky.DynamicVars.Weak.BaseValue;
                foreach (var f in card.TargetType == TargetType.AllEnemies ? hittable.ToList() : tgt != null ? new List<Creature> { tgt } : new List<Creature>()) Dt<WeakPower>(f, w);
            }
            catch { }
        }
        // 通用的易伤规则（巨像、血肉戏法相关时）
        if (vulRule && !explicitVul && card.DynamicVars.TryGetValue("VulnerablePower", out var vv) && vv.BaseValue > 0)
            foreach (var f in Targets()) Dt<VulnerablePower>(f, vv.BaseValue);
    }

    /// <summary>
    /// 格式 29：Fx23 的新口径（同 Fx23，牌表改用 Debuffs29），另加：
    ///   G15：巨像（打出巨像时，现在有易伤或这一步新上易伤的敌人；已有巨像时这一步新上易伤的敌人，有力攻击 × DamageDecrease）；
    ///     来吧（目标 + EnemyStrength × 打出次数力量，增益不吃人工制品）；王之凝视、涂毒、墨影（见 Debuffs29）；
    ///     摧残（目标已虚弱、这一步新上摧残：虚弱倍率由 w 变成 2w − 1）；苦难（抄减益，见 Debuffs29）；
    ///     不死符文（这一步之后、再扣回合末确定被动与下回合开始的毒，生命 ≤ 灾厄的敌人有力攻击 × DamageDecrease；现在已经是的不再乘）。
    ///   G40：这一步之后在这个敌方回合末会被灾厄处决的敌人数（不含这一步打死的；生命按同上扣减，大于 0 且 ≤ 灾厄），写候选新列 16（_cy[2]）。
    /// </summary>
    (Dictionary<Creature, FoeMod>? mod, List<Creature>? pskip) Fx29(Choice ch, Player me, Dictionary<Creature, float> hl, Dictionary<Creature, int> pout, List<Creature>? killed)
    {
        try
        {
            var cs = me.Creature.CombatState!;
            var foes = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
            var hittable = foes.Where(c => Hittable(cs).Contains(c)).ToList();
            var card = ch.A as CardModel;
            var pot = ch.A as PotionModel;
            var tgt = ch.B as Creature;
            int plays = card != null ? PlayCount(card, tgt) : 1;
            var add = new Dictionary<Creature, decimal>();
            var weak = new HashSet<Creature>();
            var shrink = new HashSet<Creature>();
            var vul = new HashSet<Creature>();
            var debil = new HashSet<Creature>();
            var doomAdd = new Dictionary<Creature, decimal>();
            var pois = new Dictionary<Creature, int>();
            var art = foes.ToDictionary(f => f, f => f.GetPowerAmount<ArtifactPower>());
            int accel = 0;
            bool colossusNow = me.Creature.HasPower<ColossusPower>();
            bool colossusCard = card is Colossus;
            bool vulRule = colossusNow || colossusCard;

            void Debuff(Creature f, PowerModel canon, decimal amount)
            {
                if (amount == 0 || !art.ContainsKey(f) || !f.CanReceivePowers) return;
                if (art[f] > 0) { art[f]--; return; }
                var pw = f.Powers.FirstOrDefault(p => p.GetType() == canon.GetType()) ?? canon;
                decimal got = MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountGiven(cs, pw, me.Creature, amount, f, card, out _);
                if (f.GetPowerAmount<ArtifactPower>() <= 0) got = MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountReceived(cs, pw, f, got, me.Creature, out _);
                if (got == 0) return;
                switch (pw)
                {
                    case WeakPower: if (got > 0 && !f.HasPower<WeakPower>()) weak.Add(f); break;
                    case StrengthPower: add[f] = add.GetValueOrDefault(f) + got; break;
                    case PiercingWailPower: add[f] = add.GetValueOrDefault(f) - got; break;
                    case ShacklingPotionPower: add[f] = add.GetValueOrDefault(f) - got; break;
                    case ShrinkPower: if (got != 0) shrink.Add(f); break;
                    case TemporaryStrengthPower when pw.Type == MegaCrit.Sts2.Core.Entities.Powers.PowerType.Debuff: add[f] = add.GetValueOrDefault(f) - got; break;
                    case PoisonPower: pois[f] = pois.GetValueOrDefault(f) + (int)got; break;
                    case VulnerablePower: if (got > 0 && !f.HasPower<VulnerablePower>()) vul.Add(f); break;
                    case DoomPower: doomAdd[f] = doomAdd.GetValueOrDefault(f) + got; break;
                    case DebilitatePower: if (got > 0) debil.Add(f); break;
                }
            }

            for (int r = 0; r < plays && card != null; r++)
            {
                Debuffs29(card, tgt, me, r, hittable, f => f.HasPower<PoisonPower>() || pois.GetValueOrDefault(f) > 0, Debuff, vulRule, hitRules: true);
                if (card is Accelerant) accel += (int)card.DynamicVars["Accelerant"].BaseValue;
            }
            switch (pot)
            {
                case PoisonPotion when tgt != null: Debuff(tgt, ModelDb.Power<PoisonPower>(), pot.DynamicVars.Poison.BaseValue); break;
                case WeakPotion when tgt != null: Debuff(tgt, ModelDb.Power<WeakPower>(), pot.DynamicVars.Weak.BaseValue); break;
                case PotionOfBinding: foreach (var f in hittable) Debuff(f, ModelDb.Power<WeakPower>(), pot.DynamicVars["VulnerablePower"].IntValue); break;
                case ShacklingPotion: foreach (var f in hittable) Debuff(f, ModelDb.Power<ShacklingPotionPower>(), pot.DynamicVars.Strength.BaseValue); break;
                case BeetleJuice when tgt != null: Debuff(tgt, ModelDb.Power<ShrinkPower>(), pot.DynamicVars.Repeat.BaseValue); break;
            }
            if (vulRule && pot != null && pot.DynamicVars.TryGetValue("VulnerablePower", out var pvv) && pvv.BaseValue > 0)
                foreach (var f in pot.TargetType == TargetType.AllEnemies ? hittable : tgt != null ? new List<Creature> { tgt } : new List<Creature>())
                    Debuff(f, ModelDb.Power<VulnerablePower>(), pvv.BaseValue);
            if (card != null)
            {
                decimal tainted = 0;
                foreach (var f in foes)
                {
                    if (card.Type == CardType.Skill && f.GetPower<EnragePower>() is { } en) add[f] = add.GetValueOrDefault(f) + en.Amount * plays;
                    if (card.Affliction is MegaCrit.Sts2.Core.Models.Afflictions.Tainted && f.GetPower<VitalSparkPower>() is { } vs) tainted += vs.Amount * plays;
                }
                if (tainted != 0) foreach (var f in foes) add[f] = add.GetValueOrDefault(f) + tainted;
                // G15：来吧给目标加力量（增益，不吃人工制品）
                if (card is FightMe && tgt != null && card.DynamicVars.TryGetValue("EnemyStrength", out var es))
                    add[tgt] = add.GetValueOrDefault(tgt) + es.BaseValue * plays;
            }

            decimal mult = 1m;
            if (card != null && !me.Creature.HasPower<ConductorsBatonPower>()
                && me.Relics.FirstOrDefault(x => x is MegaCrit.Sts2.Core.Models.Relics.ConductorsBaton) is { } baton && !baton.IsMelted && !UsedThisTurn(baton)
                && (PlayedWah(me) || card.Tags.Contains(CardTag.Wah))
                && (PlayedCho(me) || card.Tags.Contains(CardTag.Cho))
                && (PlayedKi(me) || card.Tags.Contains(CardTag.Ki)))
                mult = ModelDb.Power<ConductorsBatonPower>().DynamicVars["DamageDecrease"].BaseValue;

            // 这一步之后、敌方回合里的生命与灾厄（不死符文、灾厄处决用）
            float HpAfter(Creature f)
            {
                float lost = hl.GetValueOrDefault(f);
                float sure = _sureHp != null && _sureHp.TryGetValue(f, out var s) ? s : 0;
                int stacks = (pout.TryGetValue(f, out int left) ? left : f.GetPowerAmount<PoisonPower>()) + pois.GetValueOrDefault(f);
                float pn = stacks > 0 ? PoisonNextWith(f, stacks, accel == 0 ? null : AccelOf(f) + accel) : 0;
                return f.CurrentHp - lost - sure - pn;
            }
            decimal DoomAfter(Creature f) => f.GetPowerAmount<DoomPower>() + doomAdd.GetValueOrDefault(f);
            var sigil = me.Relics.FirstOrDefault(x => x is MegaCrit.Sts2.Core.Models.Relics.UndyingSigil && RelicLive(x));
            decimal colMult = ModelDb.Power<ColossusPower>().DynamicVars["DamageDecrease"].BaseValue;

            Dictionary<Creature, FoeMod>? mod = null;
            foreach (var f in foes)
            {
                decimal a = add.GetValueOrDefault(f);
                bool w = weak.Contains(f);
                decimal fm = mult * (shrink.Contains(f) ? (100m - ModelDb.Power<ShrinkPower>().DynamicVars["DamageDecrease"].BaseValue) / 100m : 1m);
                if ((colossusCard && !colossusNow && (f.HasPower<VulnerablePower>() || vul.Contains(f))) || (colossusNow && vul.Contains(f))) fm *= colMult;
                if (debil.Contains(f) && !f.HasPower<DebilitatePower>() && (w || f.HasPower<WeakPower>()))
                {
                    decimal wm = WeakMult(me, f);
                    if (wm > 0) fm *= Math.Max(0, 2 * wm - 1) / wm;
                }
                if (sigil != null && !(f.CurrentHp <= f.GetPowerAmount<DoomPower>()))
                {
                    decimal d2 = DoomAfter(f);
                    float h2 = HpAfter(f);
                    if (d2 > 0 && h2 > 0 && (decimal)h2 <= d2) fm *= sigil.DynamicVars["DamageDecrease"].BaseValue;
                }
                if (a != 0 || w || fm != 1m) (mod ??= new())[f] = new FoeMod(a, w, fm);
            }

            List<Creature>? pskip = null;
            int exec = 0;
            foreach (var f in foes)
            {
                if (killed != null && killed.Contains(f)) continue;
                // G40：敌方回合末会被灾厄处决
                decimal dm = DoomAfter(f);
                if (dm > 0) { float h2 = HpAfter(f); if (h2 > 0 && (decimal)h2 <= dm) exec++; }
                int added = pois.GetValueOrDefault(f);
                bool burst = pout.TryGetValue(f, out int left);
                float lost = hl.GetValueOrDefault(f);
                if (added == 0 && accel == 0 && lost <= 0 && !burst) continue;
                int stacks = (burst ? left : f.GetPowerAmount<PoisonPower>()) + added;
                if (stacks <= 0) continue;
                float pn = PoisonNextWith(f, stacks, accel == 0 ? null : AccelOf(f) + accel);
                float sure = _sureHp != null && _sureHp.TryGetValue(f, out var s) ? s : 0;
                if (pn > 0 && pn >= f.CurrentHp - lost - sure) (pskip ??= new()).Add(f);
            }
            _cy[2] = exec;
            return (mod, pskip);
        }
        catch { return (null, null); }
    }

    // ------------------------------------------------------------------ G45、G46：静默猎手

    /// <summary>这张牌（每次打出合计）从抽牌堆抽几张：Gains26 的无条件抽牌名单 × Cards × 打出次数、编译冲击与平稳韵律的 CalculatedCards、
    /// 投掷匕首与逃脱计划各 1、计算下注抽回弃掉的张数；有“不能抽牌”时为 0；不超过手牌空位与抽牌堆加弃牌堆（计算下注另加弃掉的牌）。</summary>
    static int DrawnBy29(CardModel card, Creature? tgt, Player me, int plays, int discarded)
    {
        try
        {
            var pcs = me.PlayerCombatState!;
            if (me.Creature.HasPower<NoDrawPower>()) return 0;
            float n = 0;
            if (DrawCards.Contains(card.GetType()) && card.DynamicVars.TryGetValue("Cards", out var cv)) n += (float)cv.BaseValue * plays;
            switch (card)
            {
                case CompileDriver or SteadyCadence:
                    if (card.DynamicVars.TryGetValue("CalculatedCards", out var cc) && cc is MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedVar ccv) n += (float)ccv.Calculate(tgt) * plays;
                    break;
                case DaggerThrow or EscapePlan: n += plays; break;
                case CalculatedGamble: n += discarded; break;
            }
            int handAfter = pcs.Hand.Cards.Count - 1 - discarded;
            int cap = Math.Min(CardPile.MaxCardsInHand - handAfter, pcs.DrawPile.Cards.Count + pcs.DiscardPile.Cards.Count + discarded);
            return (int)Math.Max(0, Math.Min(n, cap));
        }
        catch { return 0; }
    }

    static bool SlyNow(CardModel c)
    {
        try { return c.IsSlyThisTurn; } catch { return false; }
    }

    /// <summary>
    /// 格式 29：SilentPreview 的新口径（在它的基础上）：
    ///   G45 整手弃：计算下注、钢铁风暴、暗影步打出时弃掉手里其余全部的牌：本回合奇巧的牌之后按无目标自动打出（AutoPlayEffect：格挡进候选 8，
    ///     确定伤害进 4–7 与击杀，期望伤害只进 6，能量与进手——战术大师、本能反应等——加到 _autoGain；每张自动打出也触发紧勒、群蛇形态、余像）；
    ///     铜钹每弃一张对随机敌人 Damage 点（只剩一个可攻击的敌人时确定，多个时按期望），结实绷带每弃一张 Block 点格挡；
    ///     弃掉的牌记进 _autoGone，_autoHand 减去张数（它们的回合末手牌效果不再算）。
    ///   G46：速行者按这张牌实际抽到的张数（DrawnBy29）× 层数对全体可攻击的敌人造成无力伤害（打全体口径）；回响斩击按轮迭代，这一轮打死了敌人就对剩下的再打一轮。
    /// 另把 hpOut 记在 _hl29（Riders29 重算 4–7 时写回）。
    /// </summary>
    (float c4, float c5, float c6, float c7, List<Creature>? killed, float block) SilentPreview29(CardModel card, Creature? tgt, Player me,
                                                                                                float c4, float c5, float c6, float c7, List<Creature>? killed,
                                                                                                Dictionary<Creature, float>? hpOut, Dictionary<Creature, int>? poisOut)
    {
        _hl29 = hpOut;
        try
        {
            var cs = me.Creature.CombatState!;
            var pcs = me.PlayerCombatState!;
            var foes = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
            var hittable = foes.Where(c => Hittable(cs).Contains(c)).ToList();
            var unpowered = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered;
            var poisonProps = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unblockable | unpowered;
            var move = MegaCrit.Sts2.Core.ValueProps.ValueProp.Move;
            int plays = PlayCount(card, tgt);
            var strangle = foes.Select(f => (f, amt: f.Powers.OfType<StranglePower>().Where(p => p.Applier?.Player == me).Sum(p => p.Amount)))
                .Where(x => x.amt > 0).ToList();
            int serpent = me.Creature.GetPowerAmount<SerpentFormPower>();
            int image = me.Creature.GetPowerAmount<AfterimagePower>();
            bool riderDmg = strangle.Count > 0 || (serpent > 0 && hittable.Count > 0);
            bool knife = card is KnifeTrap && tgt != null;
            bool outbreak = card is Outbreak;
            // G45：整手弃
            bool wholeDiscard = card is CalculatedGamble or StormOfSteel or ShadowStep;
            var discarded = wholeDiscard ? OtherHand(card, me) : new List<CardModel>();
            var sly = discarded.Where(c => SlyNow(c) && !Kw(c).Contains(CardKeyword.Unplayable)).ToList();
            var tingsha = me.Relics.FirstOrDefault(r => r is MegaCrit.Sts2.Core.Models.Relics.Tingsha && RelicLive(r));
            var bandages = me.Relics.FirstOrDefault(r => r is MegaCrit.Sts2.Core.Models.Relics.ToughBandages && RelicLive(r));
            // G46
            int speed = me.Creature.GetPowerAmount<SpeedsterPower>();
            int drawn = speed > 0 && hittable.Count > 0 ? DrawnBy29(card, tgt, me, plays, discarded.Count) : 0;
            bool echo = card is EchoingSlash;
            bool extra29 = sly.Count > 0 || (tingsha != null && discarded.Count > 0) || drawn > 0 || echo;
            float block29 = 0;
            if (discarded.Count > 0)
            {
                _autoGone = _autoGone == null ? discarded : _autoGone.Concat(discarded.Where(g => !_autoGone.Contains(g))).ToList();
                _autoHand -= discarded.Count;
                if (bandages != null) block29 += discarded.Count * BlockOf(cs, me, bandages.DynamicVars.Block.BaseValue);
            }
            // 铭记死亡（v0.113.0）自动打出时同样打当前生命最低的可攻击敌人（MementoMori.OnPlay），目标确定，单独算
            var memento = sly.Where(c => c is MementoMori).ToList();
            var ap = sly.Count > memento.Count ? AutoPlayEffect(sly.Where(c => c is not MementoMori), me) : default;
            if (sly.Count > 0) { block29 += ap.block; _autoGain = (_autoGain.energy + ap.energy, _autoGain.cards + ap.cards); }
            if (!riderDmg && !knife && !outbreak && image <= 0 && !extra29) return (c4, c5, c6, c7, killed, block29);

            var seq = foes.ToDictionary(f => f, _ => new List<Hit>());
            var own = foes.ToDictionary(f => f, _ => 0f);
            var extra = foes.ToDictionary(f => f, _ => 0f);
            float ownMax = 0, ownHits = 0; int triggers = 0;
            bool all = true;
            void Riders()
            {
                triggers++;
                if (serpent > 0 && hittable.Count == 1)
                {
                    var h = new Hit(HitDmg(cs, hittable[0], me.Creature, serpent, unpowered, null), true, unpowered, me.Creature, null);
                    seq[hittable[0]].Add(h); extra[hittable[0]] += h.Dmg;
                }
                else if (serpent > 0)
                    foreach (var f in hittable) extra[f] += HitDmg(cs, f, me.Creature, serpent, unpowered, null) / hittable.Count;
                foreach (var (f, amt) in strangle)
                {
                    var h = new Hit(HitDmg(cs, f, null, amt, poisonProps, null), false, poisonProps, null, null);
                    seq[f].Add(h); extra[f] += h.Dmg;
                }
            }
            void Own(Creature f, Hit h) { seq[f].Add(h); own[f] += h.Dmg; ownMax = Math.Max(ownMax, h.Dmg); }
            bool Dead(Creature f) => seq[f].Count > 0 && SeqHpLoss(f, seq[f]) >= f.CurrentHp;

            if (knife)
            {
                var shivs = pcs.ExhaustPile.Cards.Where(c => c.Tags.Contains(CardTag.Shiv)).ToList();
                bool fan = me.Creature.HasPower<FanOfKnivesPower>();
                var targets = fan ? hittable : new List<Creature> { tgt! };
                all = fan;
                int pb = me.Creature.GetPowerAmount<PhantomBladesPower>();
                bool pbFirst = pb > 0 && !CombatManager.Instance.History.CardPlaysFinished.Any(e => e.HappenedThisTurn(cs) && e.CardPlay.Card.Tags.Contains(CardTag.Shiv) && e.CardPlay.Player == me);
                var bases = shivs.Select(s =>
                {
                    decimal b = s.DynamicVars.Damage.BaseValue;
                    if (card.IsUpgraded && s.IsUpgradable && UpgradedCopy(s) is { } up) b = up.DynamicVars.Damage.BaseValue;
                    return b;
                }).ToList();
                ownHits = shivs.Count;
                for (int r = 0; r < plays; r++)
                {
                    for (int i = 0; i < shivs.Count; i++)
                    {
                        decimal b = bases[i] - (pbFirst && (r > 0 || i > 0) ? pb : 0);
                        foreach (var f in targets) Own(f, new Hit(HitDmg(cs, f, me.Creature, b, move, shivs[i]), true, move, me.Creature, shivs[i]));
                        Riders();
                    }
                    Riders();
                }
            }
            else if (outbreak)
            {
                decimal amount = card.DynamicVars.Poison.BaseValue;
                int acc = foes.Count > 0 ? cs.GetOpponentsOf(foes[0]).Where(c => c.IsAlive).Sum(a => a.GetPowerAmount<AccelerantPower>()) : 0;
                var cur = hittable.ToDictionary(f => f, f => f.GetPowerAmount<PoisonPower>());
                var canon = ModelDb.Power<PoisonPower>();
                int maxTicks = 0;
                for (int r = 0; r < plays; r++)
                {
                    foreach (var f in hittable)
                    {
                        if (!f.CanReceivePowers) continue;
                        var pw = (PowerModel?)f.GetPower<PoisonPower>() ?? canon;
                        decimal given = MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountGiven(cs, pw, me.Creature, amount, f, card, out _);
                        cur[f] += (int)MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountReceived(cs, pw, f, given, me.Creature, out _);
                    }
                    foreach (var f in hittable)
                    {
                        int ticks = Math.Max(0, Math.Min(cur[f], 1 + acc));
                        for (int i = 0; i < ticks; i++) Own(f, new Hit(HitDmg(cs, f, null, cur[f] - i, poisonProps, null), false, poisonProps, null, null));
                        cur[f] -= ticks; maxTicks = Math.Max(maxTicks, ticks);
                    }
                    Riders();
                }
                ownHits = maxTicks;
                if (poisOut != null) foreach (var f in hittable) poisOut[f] = cur[f];
            }
            else
            {
                List<Creature>? hitList = tgt != null ? new List<Creature> { tgt } : card.TargetType switch
                {
                    TargetType.AllEnemies => foes.Where(c => IsHittableM(c)).ToList(),
                    TargetType.RandomEnemy when foes.Count == 1 && IsHittableM(foes[0]) => foes,
                    TargetType.RandomEnemy when card is MementoMori && MementoTarget(me) is { } mt => new List<Creature> { mt },
                    _ => null,
                };
                all = tgt == null;
                var per = new Dictionary<Creature, (float pd, int hits)>();
                foreach (var f in hitList ?? new List<Creature>())
                {
                    var (pd, _, hits) = Preview(card, f);
                    if (pd < 0) { per.Clear(); break; }
                    per[f] = (pd, (int)hits);
                }
                Preview(card, null);
                if (per.Count == 0) all = true;
                if (!riderDmg && !extra29) return (c4, c5, c6, c7, killed, (image > 0 ? AfterimageBlock(cs, me, image) * plays : 0) + block29);
                foreach (var (_, v) in per) { ownHits = Math.Max(ownHits, v.hits); ownMax = Math.Max(ownMax, v.pd); }
                for (int r = 0; r < plays; r++)
                {
                    if (echo)
                    {
                        // 回响斩击：每打死一个敌人就对剩下的可攻击敌人再打一轮（EchoingSlash.OnPlay）
                        var alive = per.Keys.Where(f => !Dead(f)).ToList();
                        for (int round = 0; round < 20 && alive.Count > 0; round++)
                        {
                            int kills = 0;
                            foreach (var f in alive) { for (int i = 0; i < per[f].hits; i++) Own(f, new Hit(per[f].pd, true, move, me.Creature, card)); if (Dead(f)) kills++; }
                            if (kills == 0) break;
                            alive = alive.Where(f => !Dead(f)).ToList();
                        }
                    }
                    else foreach (var (f, v) in per) for (int i = 0; i < v.hits; i++) Own(f, new Hit(v.pd, true, move, me.Creature, card));
                    Riders();
                }
            }

            // G46：速行者（这张牌抽到的每一张，对全体可攻击的敌人层数点无力伤害；计算下注先弃后抽）
            for (int i = 0; i < drawn; i++)
                foreach (var f in hittable) { var h = new Hit(HitDmg(cs, f, me.Creature, speed, unpowered, null), true, unpowered, me.Creature, null); seq[f].Add(h); extra[f] += h.Dmg; }
            // G45：铜钹（每弃一张）、奇巧牌的自动打出（弃完、抽完之后）
            if (tingsha != null && discarded.Count > 0)
            {
                decimal td = tingsha.DynamicVars.Damage.BaseValue;
                for (int i = 0; i < discarded.Count; i++)
                {
                    if (hittable.Count == 1) { var h = new Hit(HitDmg(cs, hittable[0], me.Creature, td, unpowered, null), true, unpowered, me.Creature, null); seq[hittable[0]].Add(h); extra[hittable[0]] += h.Dmg; }
                    else foreach (var f in hittable) extra[f] += HitDmg(cs, f, me.Creature, td, unpowered, null) / hittable.Count;
                }
            }
            if (sly.Count > 0)
            {
                if (ap.sure != null)
                    foreach (var f in foes)
                    {
                        if (ap.sure.TryGetValue(f, out var hs)) foreach (var h in hs) if (!Dead(f)) { seq[f].Add(h); extra[f] += h.Dmg; }
                        if (ap.exp.TryGetValue(f, out var ev)) extra[f] += ev;
                    }
                foreach (var mm in memento)
                {
                    var mt = hittable.Where(f => !Dead(f)).MinBy(f => f.CurrentHp - SeqHpLoss(f, seq[f]));
                    if (mt == null) continue;
                    var (pd, _, h) = Preview(mm, mt);
                    for (int r = 0; r < PlayCount(mm, null) && pd > 0; r++)
                        for (int i = 0; i < (int)h; i++) { var hh = new Hit(pd, true, move, me.Creature, mm); seq[mt].Add(hh); extra[mt] += hh.Dmg; }
                    Preview(mm, null);
                }
                for (int i = 0; i < sly.Count; i++) Riders();
            }

            float block = (image > 0 ? AfterimageBlock(cs, me, image) * triggers : 0) + block29;
            var dead = foes.Where(Dead).ToList();
            if (hpOut != null) foreach (var f in foes) if (seq[f].Count > 0) hpOut[f] = SeqHpLoss(f, seq[f]);
            if (!all)
            {
                c4 = ownMax; c5 = ownHits; c6 = own[tgt!] + extra[tgt!]; c7 = dead.Contains(tgt!) ? 1 : 0;
            }
            else
            {
                bool hasOwn = own.Values.Any(v => v > 0) || ownHits > 0;
                c4 = hasOwn ? ownMax : foes.Select(f => seq[f].Select(h => h.Dmg).DefaultIfEmpty(0).Max()).DefaultIfEmpty(0).Max();
                c5 = hasOwn ? ownHits : triggers;
                c6 = foes.Sum(f => own[f] + extra[f]); c7 = dead.Count;
            }
            return (c4, c5, c6, c7, dead.Count > 0 ? dead : null, block);
        }
        catch { return (c4, c5, c6, c7, killed, 0); }
    }
}
