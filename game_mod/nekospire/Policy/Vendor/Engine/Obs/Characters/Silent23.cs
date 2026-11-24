using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Potions;
using MegaCrit.Sts2.Core.Models.Powers;

namespace Sts2Headless;

// 静默猎手（格式 23，猎人观测调研.md 第七节第一档）：候选 10 算上这一步给敌人上的虚弱与减力量、让敌人涨的伤害、加毒之后毒得死的敌人。
public sealed partial class Observation
{
    /// <summary>
    /// 格式 23：出牌、用药水候选做完之后，敌人的攻击怎么变（给回合结束预演的“假设”版本用，见 EndTurnPreview 的 AtkPerHit），哪些敌人会在敌方回合开始被毒死。
    /// 照各牌、各药水 OnPlay 的结算顺序，每次打出（PlayCount，爆发等）各结算一遍：
    ///   虚弱（敌人原来没有时，这回合的攻击乘虚弱倍率；WeakPower 到敌方回合结束才减层，所以当回合就生效）：中和、钩拳（先打伤害）、扫腿，
    ///     烟雾（先给全体加毒、再给全体上虚弱），萎靡（先减 X 层力量、再上 X 层虚弱；X = 现有能量经 Hook.ModifyXValue，升级 +1），虚弱药水，束缚药水（全体）。
    ///   减力量：萎靡；尖啸（PiercingWailPower，TemporaryStrengthPower 的减力量，敌方回合结束才恢复，所以敌人这回合的攻击少这么多）。
    ///   人工制品：每层挡掉一次减益（毒也算），按结算顺序扣（ArtifactPower.TryModifyPowerAmountReceived 判断、AfterModifyingPowerAmountReceived 扣层）；
    ///     挡完之后的照常过加减层钩子（Hook.ModifyPowerAmountGiven / Received：异蛇头骨等）。
    ///   敌人涨伤害：实验体的激怒（EnragePower：玩家打出技能，它加层数点力量）；感染棱柱的活力火花（VitalSparkPower：打出被感染的牌，
    ///     玩家挂上层数点感染 TaintedPower，本回合每段有力攻击加这么多，所有敌人都算）。
    ///   加毒：致命毒药、蛇咬、带毒刺击（先打伤害）、咕嘟冒泡（目标已中毒才加）、烟雾、弹跳药瓶（只剩一个可攻击的敌人时全打它；多个时随机，不算）、毒药水；
    ///     触媒（玩家加触媒层数，毒多结算几次）；毒性爆发结算之后剩下的毒（SilentPreview 的 poisOut）。
    /// 毒得死：这一步之后毒、触媒或生命变了的敌人，按 PoisonNextWith（新的层数与触媒）算下一个敌方回合开始的毒伤，
    /// 不小于“现在的生命 − 这一步造成的掉血 − 回合结束被动的确定掉血”时算毒得死，候选 10 不算它的攻击（同回合结束预演的口径）。
    /// 不算的：涂毒等打中后才加的毒、腐蚀波、毒雾（玩家回合开始才加毒）、奇巧自动打出与铜钹、抽到的牌、药水的伤害。只读。
    /// </summary>
    (Dictionary<Creature, FoeMod>? mod, List<Creature>? pskip) Fx23(Choice ch, Player me, Dictionary<Creature, float> hl, Dictionary<Creature, int> pout, List<Creature>? killed)
    {
        if (V29) return Fx29(ch, me, hl, pout, killed);      // 格式 29 的新口径（Silent29.cs），本函数原样留给旧格式
        try
        {
            var cs = me.Creature.CombatState!;
            var pcs = me.PlayerCombatState!;
            var foes = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
            var hittable = foes.Where(c => Hittable(cs).Contains(c)).ToList();
            var card = ch.A as CardModel;
            var pot = ch.A as PotionModel;
            var tgt = ch.B as Creature;
            int plays = card != null ? PlayCount(card, tgt) : 1;
            var add = new Dictionary<Creature, decimal>();
            var weak = new HashSet<Creature>();
            var shrink = new HashSet<Creature>();      // F113 第四轮：甲虫汁的缩小（ShrinkPower：它的有力攻击 ×0.7）
            var pois = new Dictionary<Creature, int>();
            var art = foes.ToDictionary(f => f, f => f.GetPowerAmount<ArtifactPower>());
            int accel = 0;

            // 给敌人上一次减益（数值为能力层数；萎靡的减力量为负数）
            void Debuff<T>(Creature f, decimal amount) where T : PowerModel
            {
                if (amount == 0 || !art.ContainsKey(f) || !f.CanReceivePowers) return;
                if (art[f] > 0) { art[f]--; return; }
                var pw = (PowerModel?)f.GetPower<T>() ?? ModelDb.Power<T>();
                decimal got = MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountGiven(cs, pw, me.Creature, amount, f, card, out _);
                // 真实的人工制品还在（按次数已经挡完）时钩子仍会挡，这里不再过接收钩子
                if (f.GetPowerAmount<ArtifactPower>() <= 0) got = MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountReceived(cs, pw, f, got, me.Creature, out _);
                if (got == 0) return;
                switch (pw)
                {
                    case WeakPower: if (got > 0 && !f.HasPower<WeakPower>()) weak.Add(f); break;
                    case StrengthPower: add[f] = add.GetValueOrDefault(f) + got; break;
                    case PiercingWailPower: add[f] = add.GetValueOrDefault(f) - got; break;
                    case ShacklingPotionPower when F113: add[f] = add.GetValueOrDefault(f) - got; break;      // 第四轮：束缚药水（临时减力量，敌方回合结束才恢复）
                    case ShrinkPower when F113: if (got != 0) shrink.Add(f); break;
                    // 第七轮（F113）：其余临时减力量（黑暗镣铐、弱化之触、凌虐、下砸、星灭等，TemporaryStrengthPower 的减益，敌方回合结束才恢复）
                    case TemporaryStrengthPower when F113 && pw.Type == MegaCrit.Sts2.Core.Entities.Powers.PowerType.Debuff: add[f] = add.GetValueOrDefault(f) - got; break;
                    case PoisonPower: pois[f] = pois.GetValueOrDefault(f) + (int)got; break;
                }
            }

            for (int r = 0; r < plays && card != null; r++)
            {
                switch (card)
                {
                    case Neutralize or SuckerPunch or LegSweep when tgt != null:
                        Debuff<WeakPower>(tgt, card.DynamicVars.Weak.BaseValue); break;
                    case Haze:
                        foreach (var f in hittable) Debuff<PoisonPower>(f, card.DynamicVars.Poison.BaseValue);
                        foreach (var f in hittable) Debuff<WeakPower>(f, card.DynamicVars.Weak.BaseValue);
                        break;
                    case Malaise when tgt != null:
                        int x = Math.Max(0, MegaCrit.Sts2.Core.Hooks.Hook.ModifyXValue(cs, card, pcs.Energy)) + (card.IsUpgraded ? 1 : 0);
                        if (x > 0) { Debuff<StrengthPower>(tgt, -x); Debuff<WeakPower>(tgt, x); }
                        break;
                    case PiercingWail:
                        foreach (var f in hittable) Debuff<PiercingWailPower>(f, card.DynamicVars["StrengthLoss"].BaseValue); break;
                    case DeadlyPoison or Snakebite or PoisonedStab when tgt != null:
                        Debuff<PoisonPower>(tgt, card.DynamicVars.Poison.BaseValue); break;
                    case BubbleBubble when tgt != null:
                        if (tgt.HasPower<PoisonPower>() || pois.GetValueOrDefault(tgt) > 0) Debuff<PoisonPower>(tgt, card.DynamicVars.Poison.BaseValue);
                        break;
                    case BouncingFlask when hittable.Count == 1:
                        for (int i = 0; i < card.DynamicVars.Repeat.IntValue; i++) Debuff<PoisonPower>(hittable[0], card.DynamicVars.Poison.BaseValue);
                        break;
                    case Accelerant:
                        accel += (int)card.DynamicVars["Accelerant"].BaseValue; break;
                    // v0.113.0 修正（F113，Fx23 所在的格式 23、25）：刻薄（Vitriol.OnPlay：目标上虚弱）、女皇（塔罗：虚弱 + 易伤）、蹒跚射击（射出箭才上，ShootArrowAtTarget 返回真；
                    // 只有前“可用箭数”次打出有箭）、钉住（目标被扎穿才生效，Nail.OnPlay）。易伤不改敌人的攻击，这里只算虚弱
                    case Vitriol when F113 && tgt != null:
                        Debuff<WeakPower>(tgt, card.DynamicVars.Weak.BaseValue); break;
                    case TheEmpress when F113 && tgt != null:
                        Debuff<WeakPower>(tgt, card.DynamicVars["Power"].BaseValue); break;
                    case HobblingShot when F113 && tgt != null && r < pcs.AvailableArrowCount:
                        Debuff<WeakPower>(tgt, card.DynamicVars["Power"].BaseValue); break;
                    case Nail when F113 && tgt != null && NailHits(tgt):
                        Debuff<WeakPower>(tgt, card.DynamicVars["Power"].BaseValue); break;
                    // v0.113.0 修正第四轮（F113）：压制（古老牙齿把中和换成它，先打后上虚弱）、冲击波（全体虚弱 + 易伤）、疯狂科学的“削弱”附加效果（目标虚弱）
                    case Suppress when F113 && tgt != null:
                        Debuff<WeakPower>(tgt, card.DynamicVars.Weak.BaseValue); break;
                    case Shockwave when F113:
                        foreach (var f in hittable) Debuff<WeakPower>(f, card.DynamicVars["Power"].BaseValue);
                        break;
                    case MadScience ms when F113 && tgt != null && ms.TinkerTimeRider == MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Sapping:
                        Debuff<WeakPower>(tgt, card.DynamicVars["SappingWeak"].BaseValue); break;
                    // 第七轮（F113，Fx23 起用于所有格式）：其余给敌人上虚弱、减力量的牌（各牌 OnPlay；先上的别的减益照样吃人工制品）
                    case GoForTheEyes when F113 && tgt != null:
                        if (tgt.Monster is { IntendsToAttack: true }) Debuff<WeakPower>(tgt, card.DynamicVars.Weak.BaseValue);
                        break;
                    case Null or Comet or FallingStar or GammaBlast or Defy or KnowThyPlace when F113 && tgt != null:
                        Debuff<WeakPower>(tgt, card.DynamicVars.Weak.BaseValue);
                        if (card is Comet or FallingStar or GammaBlast or KnowThyPlace) Debuff<VulnerablePower>(tgt, card.DynamicVars.Vulnerable.BaseValue);
                        break;
                    case Putrefy or Uppercut when F113 && tgt != null:
                        Debuff<WeakPower>(tgt, card.DynamicVars["Power"].BaseValue); Debuff<VulnerablePower>(tgt, card.DynamicVars["Power"].BaseValue); break;
                    case Deathbringer when F113:
                        foreach (var f in hittable) Debuff<DoomPower>(f, card.DynamicVars.Doom.BaseValue);
                        foreach (var f in hittable) Debuff<WeakPower>(f, card.DynamicVars.Weak.BaseValue);
                        break;
                    case MeteorShower when F113:
                        foreach (var f in hittable) Debuff<WeakPower>(f, card.DynamicVars.Weak.BaseValue);
                        foreach (var f in hittable) Debuff<VulnerablePower>(f, card.DynamicVars.Vulnerable.BaseValue);
                        break;
                    case Resonance when F113:
                        foreach (var f in hittable) Debuff<StrengthPower>(f, -1m); break;
                    case SharedFate when F113 && tgt != null:
                        Debuff<StrengthPower>(tgt, -card.DynamicVars["EnemyStrengthLoss"].BaseValue); break;
                    case DarkShackles when F113 && tgt != null:
                        Debuff<DarkShacklesPower>(tgt, card.DynamicVars["StrengthLoss"].BaseValue); break;
                    case EnfeeblingTouch when F113 && tgt != null:
                        Debuff<EnfeeblingTouchPower>(tgt, card.DynamicVars["StrengthLoss"].BaseValue); break;
                    case Mangle when F113 && tgt != null:
                        Debuff<ManglePower>(tgt, card.DynamicVars["StrengthLoss"].BaseValue); break;
                    case CrushUnder when F113:
                        foreach (var f in hittable) Debuff<CrushUnderPower>(f, card.DynamicVars["StrengthLoss"].BaseValue); break;
                    case DyingStar when F113:
                        foreach (var f in hittable) Debuff<DyingStarPower>(f, card.DynamicVars["StrengthLoss"].BaseValue); break;
                }
            }
            switch (pot)
            {
                case PoisonPotion when tgt != null: Debuff<PoisonPower>(tgt, pot.DynamicVars.Poison.BaseValue); break;
                case WeakPotion when tgt != null: Debuff<WeakPower>(tgt, pot.DynamicVars.Weak.BaseValue); break;
                // 束缚药水：虚弱的层数取的是 "VulnerablePower" 这个数值（游戏代码如此，两个都是 1），先虚弱后易伤
                case PotionOfBinding: foreach (var f in hittable) Debuff<WeakPower>(f, pot.DynamicVars["VulnerablePower"].IntValue); break;
                // v0.113.0 修正第四轮（F113）：束缚药水（全体临时减力量）、甲虫汁（目标缩小：它的有力攻击 ×0.7）
                case ShacklingPotion when F113: foreach (var f in hittable) Debuff<ShacklingPotionPower>(f, pot.DynamicVars.Strength.BaseValue); break;
                case BeetleJuice when F113 && tgt != null: Debuff<ShrinkPower>(tgt, pot.DynamicVars.Repeat.BaseValue); break;
            }
            // 敌人因为这张牌涨的伤害
            if (card != null)
            {
                decimal tainted = 0;
                foreach (var f in foes)
                {
                    if (card.Type == CardType.Skill && f.GetPower<EnragePower>() is { } en) add[f] = add.GetValueOrDefault(f) + en.Amount * plays;
                    if (card.Affliction is MegaCrit.Sts2.Core.Models.Afflictions.Tainted && f.GetPower<VitalSparkPower>() is { } vs) tainted += vs.Amount * plays;
                }
                if (tainted != 0) foreach (var f in foes) add[f] = add.GetValueOrDefault(f) + tainted;
            }

            // v0.113.0 修正（F113）：这张牌打完本回合三种旋律都有、指挥棒本回合还没触发时，给玩家挂指挥棒能力：到下回合开始受到的有力攻击 ×0.75
            // （ConductorsBaton.AfterCardPlayed：MelodiesThisTurn ≥ 3；ConductorsBatonPower 的 DamageDecrease），敌人这回合的每段攻击都乘
            decimal mult = 1m;
            if (F113 && card != null && !me.Creature.HasPower<ConductorsBatonPower>()
                && me.Relics.FirstOrDefault(x => x is MegaCrit.Sts2.Core.Models.Relics.ConductorsBaton) is { } baton && !baton.IsMelted && !UsedThisTurn(baton)
                && (PlayedWah(me) || card.Tags.Contains(CardTag.Wah))
                && (PlayedCho(me) || card.Tags.Contains(CardTag.Cho))
                && (PlayedKi(me) || card.Tags.Contains(CardTag.Ki)))
                mult = ModelDb.Power<ConductorsBatonPower>().DynamicVars["DamageDecrease"].BaseValue;
            Dictionary<Creature, FoeMod>? mod = null;
            foreach (var f in foes)
            {
                decimal a = add.GetValueOrDefault(f);
                bool w = weak.Contains(f);
                decimal fm = mult * (shrink.Contains(f) ? (100m - ModelDb.Power<ShrinkPower>().DynamicVars["DamageDecrease"].BaseValue) / 100m : 1m);
                if (a != 0 || w || fm != 1m) (mod ??= new())[f] = new FoeMod(a, w, fm);
            }

            List<Creature>? pskip = null;
            foreach (var f in foes)
            {
                if (killed != null && killed.Contains(f)) continue;
                int added = pois.GetValueOrDefault(f);
                bool burst = pout.TryGetValue(f, out int left);
                float lost = hl.GetValueOrDefault(f);
                if (added == 0 && accel == 0 && lost <= 0 && !burst) continue;      // 毒、触媒、生命都没变：回合结束预演已经算过
                int stacks = (burst ? left : f.GetPowerAmount<PoisonPower>()) + added;
                if (stacks <= 0) continue;
                float pn = PoisonNextWith(f, stacks, accel == 0 ? null : AccelOf(f) + accel);
                float sure = _sureHp != null && _sureHp.TryGetValue(f, out var s) ? s : 0;
                if (pn > 0 && pn >= f.CurrentHp - lost - sure) (pskip ??= new()).Add(f);
            }
            return (mod, pskip);
        }
        catch { return (null, null); }
    }
}
