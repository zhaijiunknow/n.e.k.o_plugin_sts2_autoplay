using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;

namespace Sts2Headless;

// v0.113.0 观测修正第四轮（F113，游戏更新v0.113.0/观测独立核查.md 第十一节）：候选 8–10 里“这一步还会引起什么”。
// 出牌时遗物附带的格挡与伤害、敌人对出牌的反应（荆棘反伤、打过阈值或死了队友就眩晕）、药水的击杀与格挡、这一步给玩家的防御能力、球首的通电。
// 都只读；只在 F113 下调用。
public sealed partial class Observation
{
    static float BlockOf(ICombatState cs, Player me, decimal amount)
    {
        try { return MathF.Floor((float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyBlock(cs, me.Creature, amount, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null, null, out _)); }
        catch { return (float)amount; }
    }

    /// <summary>
    /// 出牌时遗物附带的格挡与伤害（每次打出各结算一遍，计数取遗物界面上的数 DisplayAmount）：
    /// 装饰扇（本回合第 3、6…张攻击，4 格挡）、音叉（累计 10 张技能，7 格挡）、永冻（本场第一张能力，7 格挡）、威吓头盔（这张牌花了 ≥ 2 能量，4 格挡）；
    /// 锁镰（本回合第 3、6…张攻击，随机一个可攻击的敌人 6 点；只有一个可攻击的敌人时才算）、开信刀（本回合第 3、6…张技能，全体 5 点）。
    /// 返回格挡（进候选 8）与这些伤害打死的敌人（进候选 10 的不算名单；候选 4–7 不变）。
    /// </summary>
    (float block, List<Creature>? killed) RelicRiders(CardModel card, Creature? tgt, Player me, Dictionary<Creature, float>? hl, List<Creature>? killed0)
    {
        try
        {
            var cs = me.Creature.CombatState!;
            int plays = PlayCount(card, tgt);
            bool atk = card.Type == CardType.Attack, skill = card.Type == CardType.Skill, power = card.Type == CardType.Power;
            float blk = 0, all = 0, one = 0;
            foreach (var r in me.Relics)
            {
                if (r.IsMelted || r.Status == MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Disabled) continue;
                switch (r)
                {
                    case MegaCrit.Sts2.Core.Models.Relics.OrnamentalFan when atk:
                        for (int i = 1; i <= plays; i++) if ((r.DisplayAmount + i) % r.DynamicVars.Cards.IntValue == 0) blk += BlockOf(cs, me, r.DynamicVars.Block.BaseValue);
                        break;
                    case MegaCrit.Sts2.Core.Models.Relics.TuningFork when skill:
                        { int n = r.DisplayAmount; for (int i = 0; i < plays; i++) { n++; if (n >= r.DynamicVars.Cards.IntValue) { blk += BlockOf(cs, me, r.DynamicVars.Block.BaseValue); n -= r.DynamicVars.Cards.IntValue; } } }
                        break;
                    case MegaCrit.Sts2.Core.Models.Relics.Permafrost when power:
                        if (!CombatManager.Instance.History.CardPlaysFinished.Any(e => e.CardPlay.Card.Owner == me && e.CardPlay.Card.Type == CardType.Power))
                            blk += BlockOf(cs, me, r.DynamicVars.Block.BaseValue);
                        break;
                    case MegaCrit.Sts2.Core.Models.Relics.IntimidatingHelmet:
                        {
                            int spent = card.EnergyCost.CostsX ? me.PlayerCombatState!.Energy : CostAll(card);
                            if (spent >= r.DynamicVars.Energy.IntValue) blk += BlockOf(cs, me, r.DynamicVars.Block.BaseValue);
                        }
                        break;
                    case MegaCrit.Sts2.Core.Models.Relics.Kusarigama when atk:
                        for (int i = 1; i <= plays; i++) if ((r.DisplayAmount + i) % r.DynamicVars.Cards.IntValue == 0) one += (float)r.DynamicVars.Damage.BaseValue;
                        break;
                    case MegaCrit.Sts2.Core.Models.Relics.LetterOpener when skill:
                        for (int i = 1; i <= plays; i++) if ((r.DisplayAmount + i) % r.DynamicVars.Cards.IntValue == 0) all += (float)r.DynamicVars.Damage.BaseValue;
                        break;
                    // 格式 29（G31，R1-遗物药水 G8）：风的女儿（每打出一张攻击 Block 点 Unpowered 格挡）、迷失鬼火（打出能力牌对全体 Damage 点）、
                    // 遗忘之魂（每消耗一张牌对随机一个可攻击的敌人 1 点；只有一个可攻击的敌人时才算，消耗张数同 Riders22）
                    case MegaCrit.Sts2.Core.Models.Relics.DaughterOfTheWind when V29 && atk:
                        blk += plays * BlockOf(cs, me, r.DynamicVars.Block.BaseValue);
                        break;
                    case MegaCrit.Sts2.Core.Models.Relics.LostWisp when V29 && power:
                        all += plays * (float)r.DynamicVars.Damage.BaseValue;
                        break;
                    case MegaCrit.Sts2.Core.Models.Relics.ForgottenSoul when V29:
                        {
                            int ex = (ExhaustsOnPlay(card, me) ? 1 : 0) + ExhaustOthers(card, me);
                            one += ex * (float)r.DynamicVars.Damage.BaseValue;
                        }
                        break;
                }
            }
            List<Creature>? killed = null;
            if (all > 0 || one > 0)
            {
                var hittable = Hittable(cs).ToList();
                foreach (var f in hittable)
                {
                    if (killed0 != null && killed0.Contains(f)) continue;
                    float d = (all > 0 ? HitDmg(cs, f, me.Creature, (decimal)all, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null) : 0)
                            + (one > 0 && hittable.Count == 1 ? HitDmg(cs, f, me.Creature, (decimal)one, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null) : 0);
                    if (d <= 0) continue;
                    float lost = hl != null && hl.TryGetValue(f, out var l0) ? l0 : 0;
                    float hpLeft = f.CurrentHp - lost, blkLeft = lost > 0 ? 0 : f.Block;      // 牌已经打掉的血；牌打到了就当格挡已经打穿（近似）
                    float more = Math.Min(Math.Max(0, d - blkLeft), ShellLeft(f));
                    if (hl != null) hl[f] = lost + more;
                    if (more >= hpLeft) (killed ??= new()).Add(f);
                }
            }
            return (blk, killed);
        }
        catch { return (0, null); }
    }

    /// <summary>
    /// 攻击牌打到敌人身上引起的反应（逐段算到目标死为止）：
    /// 荆棘（ThornsPower.BeforeDamageReceived：每段有力攻击先对攻击者造成层数点 Unpowered 伤害，可被格挡；蝌蚪、尖刺蟾蜍）→ 玩家当场挨的伤害；
    /// 振翅（FlutterPower：没被挡住的有力攻击每段减一层，减到 0 眩晕；窃贼跳蚤）、钻地（BurrowedPower.AfterBlockBroken：格挡被打破就眩晕；掘地者）→ 这回合不出手。
    /// 只算有目标的、打全体的、只剩一个可攻击敌人时打随机的攻击。
    /// </summary>
    (float thorns, List<Creature>? stunned) AttackReactions(CardModel card, Creature? tgt, Player me)
    {
        float th = 0; List<Creature>? stunned = null;
        try
        {
            if (card.Type != CardType.Attack) return (0, null);
            var cs = me.Creature.CombatState!;
            var hittable = Hittable(cs).ToList();
            var targets = tgt != null && !tgt.IsPlayer ? new List<Creature> { tgt }
                : card.TargetType == TargetType.AllEnemies ? hittable
                : card.TargetType == TargetType.RandomEnemy && hittable.Count == 1 ? hittable : new List<Creature>();
            foreach (var f in targets)
            {
                var (pd, _, hits) = Preview(card, f);
                if (pd < 0) continue;
                int n = (int)hits * PlayCount(card, f), landed = 0, unblocked = 0;
                float hp = f.CurrentHp, blk = f.Block;
                for (int i = 0; i < n && hp > 0; i++)
                {
                    landed++;
                    float d = pd, b = Math.Min(blk, d); blk -= b; d -= b;
                    if (d > 0) { unblocked++; hp -= d; }
                }
                int thorns = f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.ThornsPower>();
                // 格式 29（G41，R1-亡灵 N11）：奥斯提出手的牌，荆棘打的是攻击者奥斯提（宠物挨打先扣主人的格挡），这里只把被玩家现有格挡挡下的部分
                // 记进当场挨的伤害（扣回合结束前的格挡），余下奥斯提掉的血在 Riders29 里记进 ostyEx（见 OstyThorns29）
                bool ostyHit29 = V29 && card.Tags.Contains(CardTag.OstyAttack);
                if (thorns > 0 && landed > 0 && !ostyHit29) th += landed * HitDmg(cs, me.Creature, f, thorns, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered | MegaCrit.Sts2.Core.ValueProps.ValueProp.SkipHurtAnim, null);
                // 格式 29（G67）：口哨打完目标没死就眩晕它（Whistle.OnPlay：CreatureCmd.Stun），这回合不出手
                if (V29 && hp > 0 && card is MegaCrit.Sts2.Core.Models.Cards.Whistle && ReferenceEquals(f, tgt)) { (stunned ??= new()).Add(f); continue; }
                if (hp > 0)
                {
                    int flutter = f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.FlutterPower>();
                    if (flutter > 0 && unblocked >= flutter) (stunned ??= new()).Add(f);
                    else if (f.Block > 0 && blk <= 0 && f.HasPower<MegaCrit.Sts2.Core.Models.Powers.BurrowedPower>()) (stunned ??= new()).Add(f);
                }
            }
            Preview(card, null);
            if (V29 && card.Tags.Contains(CardTag.OstyAttack)) th += Math.Min(OstyThorns29(card, tgt, me), me.Creature.Block);
        }
        catch { }
        return (th, stunned);
    }

    /// <summary>格式 29（G41）：奥斯提出手的这张牌打到带荆棘的敌人时，荆棘反伤打奥斯提的总量（逐段算到目标死为止，同 AttackReactions；奥斯提不在为 0）。</summary>
    static float OstyThorns29(CardModel card, Creature? tgt, Player me)
    {
        float th = 0;
        try
        {
            if (card.Type != CardType.Attack || !card.Tags.Contains(CardTag.OstyAttack) || !me.IsOstyAlive) return 0;
            var cs = me.Creature.CombatState!;
            var hittable = Hittable(cs).ToList();
            var targets = tgt != null && !tgt.IsPlayer ? new List<Creature> { tgt }
                : card.TargetType == TargetType.AllEnemies ? hittable
                : card.TargetType == TargetType.RandomEnemy && hittable.Count == 1 ? hittable : new List<Creature>();
            foreach (var f in targets)
            {
                int thorns = f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.ThornsPower>();
                if (thorns <= 0) continue;
                var (pd, _, hits) = Preview(card, f);
                if (pd < 0) continue;
                int n = (int)hits * PlayCount(card, f), landed = 0;
                float hp = f.CurrentHp, blk = f.Block;
                for (int i = 0; i < n && hp > 0; i++)
                {
                    landed++;
                    float d = pd, b = Math.Min(blk, d); blk -= b; d -= b;
                    if (d > 0) hp -= d;
                }
                if (landed > 0) th += landed * HitDmg(cs, me.Osty!, f, thorns, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered | MegaCrit.Sts2.Core.ValueProps.ValueProp.SkipHurtAnim, null);
            }
            Preview(card, null);
        }
        catch { }
        return th;
    }

    /// <summary>
    /// 这一步之后打过阈值、或死了队友就眩晕的敌人（这回合不出手，候选 10 不算它们的攻击）：仪式兽的犁击（PlowPower）、恐怖鳗的尖啸（ShriekPower）：
    /// 没被挡住的伤害之后生命 ≤ 层数；腐尸蛞蝓的贪食（RavenousPower.AfterDeath：同一边别的敌人死了，它眩晕并加力量）。hl 为这一步对每个敌人造成的掉血，
    /// dead 为这一步打死（或之后毒死）的敌人。凯撒蟹的蟹怒（CrabRagePower：另一只钳子死了，它 +6 力量）方向相反，记进 crab（格式 23、25 的 mod 用）。
    /// </summary>
    static (List<Creature>? stunned, Dictionary<Creature, decimal>? crab) ThresholdReactions(ICombatState cs, Player me, Dictionary<Creature, float>? hl, IReadOnlyCollection<Creature>? dead)
    {
        List<Creature>? stunned = null; Dictionary<Creature, decimal>? crab = null;
        try
        {
            foreach (var f in cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive))
            {
                if (dead != null && dead.Contains(f)) continue;
                float lost = hl != null && hl.TryGetValue(f, out var l0) ? l0 : 0;
                if (lost > 0 && ((f.GetPower<MegaCrit.Sts2.Core.Models.Powers.PlowPower>() is { } pl && f.CurrentHp - lost <= pl.Amount)
                                 || (f.GetPower<MegaCrit.Sts2.Core.Models.Powers.ShriekPower>() is { } sh && f.CurrentHp - lost <= sh.Amount)))
                { (stunned ??= new()).Add(f); continue; }
                bool allyDied = dead != null && dead.Any(g => !ReferenceEquals(g, f) && g.Side == f.Side);
                if (allyDied && f.HasPower<MegaCrit.Sts2.Core.Models.Powers.RavenousPower>()) { (stunned ??= new()).Add(f); continue; }
                if (allyDied && f.GetPower<MegaCrit.Sts2.Core.Models.Powers.CrabRagePower>() is { } cr) (crab ??= new())[f] = cr.DynamicVars.Strength.BaseValue;
            }
        }
        catch { }
        return (stunned, crab);
    }

    /// <summary>
    /// 药水的击杀与格挡：火焰药水、爆裂安瓿这类带 Damage 的（单体或全体，Unpowered）打死的敌人进候选 10 的不算名单；强化剂（Fortifier：获得当前格挡 × 2）的格挡进候选 8。
    /// </summary>
    (float block, List<Creature>? killed) PotionEffects(PotionModel pot, Creature? tgt, Player me, Dictionary<Creature, float>? hl)
    {
        float blk = 0; List<Creature>? killed = null;
        try
        {
            var cs = me.Creature.CombatState!;
            if (pot is MegaCrit.Sts2.Core.Models.Potions.Fortifier) blk = BlockOf(cs, me, me.Creature.Block * 2m);
            if (pot.DynamicVars.TryGetValue("Damage", out var dv) && dv.BaseValue > 0)
            {
                var targets = pot.TargetType == TargetType.AnyEnemy && tgt != null ? new List<Creature> { tgt }
                    : pot.TargetType == TargetType.AllEnemies ? Hittable(cs).ToList() : new List<Creature>();
                foreach (var f in targets)
                {
                    float d = HitDmg(cs, f, me.Creature, dv.BaseValue, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null);
                    float lost = FoeHpLoss(f, null, d, 1, f.Block);
                    if (hl != null) hl[f] = hl.GetValueOrDefault(f) + lost;
                    if (lost >= f.CurrentHp) (killed ??= new()).Add(f);
                }
            }
        }
        catch { }
        return (blk, killed);
    }

    /// <summary>这一步给玩家的覆甲、无实体、缓冲（打出自己用的牌、用给自己的药水；按数值名 PlatingPower、IntangiblePower、BufferPower 找）。</summary>
    static (float plating, bool intangible, int buffer) DefensiveGains(Choice ch, Player me)
    {
        try
        {
            MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVarSet? vars = null; int plays = 1;
            if (ch.Kind == ActionKind.PlayCard && ch.A is CardModel c && c.TargetType == TargetType.Self) { vars = c.DynamicVars; plays = PlayCount(c, null); }
            else if (ch.Kind == ActionKind.UsePotion && ch.A is PotionModel p && p.TargetType is TargetType.AnyPlayer or TargetType.Self) vars = p.DynamicVars;
            if (vars == null) return (0, false, 0);
            float plat = vars.TryGetValue("PlatingPower", out var a) ? (float)a.BaseValue * plays : 0;
            bool intg = vars.TryGetValue("IntangiblePower", out var b) && b.BaseValue > 0;
            int buf = vars.TryGetValue("BufferPower", out var d) ? (int)d.BaseValue * plays : 0;
            return (plat, intg, buf);
        }
        catch { return (0, false, 0); }
    }

    /// <summary>球首的通电（GalvanicPower：给玩家的能力牌挂“通电”苦痛，打出就对自己造成层数点 Unpowered | Move 伤害，可被格挡）。</summary>
    static float GalvanizedSelfDamage(CardModel card, Player me)
    {
        try
        {
            if (card.Affliction is not MegaCrit.Sts2.Core.Models.Afflictions.Galvanized) return 0;
            var cs = me.Creature.CombatState!;
            int plays = PlayCount(card, null);
            float t = 0;
            foreach (var f in cs.Creatures)
                if (f.GetPower<MegaCrit.Sts2.Core.Models.Powers.GalvanicPower>() is { } g)
                    t += plays * HitDmg(cs, me.Creature, null, g.Amount, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered | MegaCrit.Sts2.Core.ValueProps.ValueProp.Move, null);
            return t;
        }
        catch { return 0; }
    }
}
