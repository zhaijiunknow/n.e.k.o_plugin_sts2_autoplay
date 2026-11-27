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

// 静默猎手（格式 21）：弃牌与抽牌计数、中毒的下回合毒伤、刀刃陷阱与毒性爆发的预览、出牌附带效果。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>
    /// 格式 21：玩家的第二条战斗计数（战斗计数记录，特征 0 子类型为 2，所属记录为玩家）：1 本回合弃掉的牌数，2 本场抽到的牌数。
    /// 数法同铭记死亡、谋杀的 CalculatedDamage：战斗历史里 Actor 为玩家的 CardDiscardedEntry（本回合）、CardDrawnEntry（整场）。
    /// 这两张牌在手里时卡面预览已经对了，不在手里时模型要自己从历史里数，这里直接给。子类型 0 的 20 列已经用满，所以另起一条（不加词表）。
    /// </summary>
    void AddSilentStats(ICombatState cs, Player me, int playerTok)
    {
        try
        {
            Begin(); _f[0] = 2;
            foreach (var e in CombatManager.Instance.History.Entries)
            {
                if (e is MegaCrit.Sts2.Core.Combat.History.Entries.CardDiscardedEntry cd && cd.Actor == me.Creature && e.HappenedThisTurn(cs)) _f[1]++;
                else if (e is MegaCrit.Sts2.Core.Combat.History.Entries.CardDrawnEntry dr && dr.Actor == me.Creature) _f[2]++;
            }
            Emit(null, TokenKind.CombatStat, 0, Zone.Ally, playerTok, 0);
        }
        catch { }
    }


    /// <summary>
    /// 格式 21（静默猎手，猎人观测调研.md 第三节第 3、4 项）：出牌候选的 4–7 补上原来算不出或漏掉的伤害，另返回余像的格挡（算进候选 8）。
    /// 照游戏的结算顺序逐次打出（CardModel.OnPlayWrapper：每次打出先 OnPlay，再 AfterCardPlayed 结算附带效果），一次打出的伤害段之后接着这次打出的附带伤害：
    ///   这张牌自己的伤害：
    ///     刀刃陷阱：消耗堆里的小刀逐张对目标自动打出（KnifeTrap.OnPlay → CardCmd.AutoPlay），每张小刀按它自己的伤害（升级过的刀刃陷阱先把小刀升级）
    ///       经伤害钩子算（精准、力量、虚弱、易伤；幻影之刃只加本回合第一张小刀：它数本回合打完的小刀，第一张打完之后就不加了）；有刀扇时小刀打全体。
    ///       每张小刀的自动打出本身也是一次打出，各触发一次附带效果。
    ///     毒性爆发：先给每个可攻击的敌人加毒（加毒钩子：异蛇头骨；人工制品挡掉），再让每个敌人的毒立刻结算一次（PoisonPower.Trigger：
    ///       min(层数, 1 + 触媒层数) 次，第 i 次按层数 − i 打，不吃格挡）。
    ///     其余：原来的口径（有目标的攻击按界面预览，打全体、只剩一个敌人的打随机逐个敌人按界面预览）；别的牌（技能、能力、多个敌人时的打随机）不算自己的伤害。
    ///   附带效果（BeforeCardPlayed 记下层数、AfterCardPlayed 结算，所以这张牌自己加的层数这一次不算）：
    ///     紧勒：被紧勒的敌人每次掉层数点血（不吃格挡、不吃力量与易伤），与这张牌打谁无关；
    ///     群蛇形态：每次对随机一个可攻击的敌人造成层数点伤害（吃格挡、不吃力量与易伤）：只剩一个可攻击的敌人时是确定的，多个时只按期望摊进 6、不算击杀；
    ///     余像：每次获得层数点格挡（不吃敏捷与脆弱）。
    /// 4–7 的口径：有目标、自己有伤害（含刀刃陷阱，没有刀扇时）：4 单次伤害，5 每次打出的段数，6 对目标的总伤害（自己的伤害加上落在目标上的附带伤害，不扣格挡），
    /// 7 能不能打死目标；其余（打全体、毒性爆发、有刀扇的刀刃陷阱、只有附带伤害的牌）：4 各敌人中最大的单次伤害，5 段数（只有附带伤害时为触发次数），
    /// 6 各敌人总伤害之和，7 打得死几个。附带伤害打死的其他敌人也记进 killed（候选 10 不算它们的攻击）。没有新情况时原样返回。
    /// 不算的：弃牌引起的奇巧自动打出与铜钹、刀刃陷阱自动打出时目标已死的改投（游戏里小刀照原目标打出、落空）、涂毒等打中后才加的毒。
    /// </summary>
    /// <remarks>格式 23：hpOut、poisOut 不为空时另记下每个敌人这一步的掉血（逐段算到的才记，覆盖原来的口径）与毒性爆发结算之后剩下的毒（Fx23 用）。</remarks>
    (float c4, float c5, float c6, float c7, List<Creature>? killed, float block) SilentPreview(CardModel card, Creature? tgt, Player me,
                                                                                              float c4, float c5, float c6, float c7, List<Creature>? killed,
                                                                                              Dictionary<Creature, float>? hpOut = null, Dictionary<Creature, int>? poisOut = null)
    {
        if (V29) return SilentPreview29(card, tgt, me, c4, c5, c6, c7, killed, hpOut, poisOut);      // 格式 29 的新口径（Silent29.cs）
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
            // 附带效果的层数（打出前记下的）
            var strangle = foes.Select(f => (f, amt: f.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.StranglePower>().Where(p => p.Applier?.Player == me).Sum(p => p.Amount)))
                .Where(x => x.amt > 0).ToList();
            int serpent = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.SerpentFormPower>();
            int image = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.AfterimagePower>();
            bool riderDmg = strangle.Count > 0 || (serpent > 0 && hittable.Count > 0);
            bool knife = card is MegaCrit.Sts2.Core.Models.Cards.KnifeTrap && tgt != null;
            bool outbreak = card is MegaCrit.Sts2.Core.Models.Cards.Outbreak;
            if (!riderDmg && !knife && !outbreak && image <= 0) return (c4, c5, c6, c7, killed, 0);

            var seq = foes.ToDictionary(f => f, _ => new List<Hit>());
            var own = foes.ToDictionary(f => f, _ => 0f);        // 自己的伤害合计（不扣格挡）
            var extra = foes.ToDictionary(f => f, _ => 0f);      // 附带伤害合计（含群蛇形态的期望）
            float ownMax = 0, ownHits = 0; int triggers = 0;
            bool all = true;                                     // 4–7 按“打全体”的口径
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

            if (knife)
            {
                // 刀刃陷阱：消耗堆里的小刀；有刀扇时每张打全体
                var shivs = pcs.ExhaustPile.Cards.Where(c => c.Tags.Contains(CardTag.Shiv)).ToList();
                bool fan = me.Creature.HasPower<MegaCrit.Sts2.Core.Models.Powers.FanOfKnivesPower>();
                var targets = fan ? hittable : new List<Creature> { tgt! };
                all = fan;
                int pb = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.PhantomBladesPower>();
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
                        // 幻影之刃的加成是常数加法、排在乘法之前：不是第一张时把它先从基础伤害里减掉，钩子再加回来，结果就是不带它的伤害
                        decimal b = bases[i] - (pbFirst && (r > 0 || i > 0) ? pb : 0);
                        foreach (var f in targets) Own(f, new Hit(HitDmg(cs, f, me.Creature, b, move, shivs[i]), true, move, me.Creature, shivs[i]));
                        Riders();
                    }
                    Riders();
                }
            }
            else if (outbreak)
            {
                // 毒性爆发：每次打出先给全体加毒，再让每个敌人的毒结算一次（结算一次层数减一）
                decimal amount = card.DynamicVars.Poison.BaseValue;
                int acc = foes.Count > 0 ? cs.GetOpponentsOf(foes[0]).Where(c => c.IsAlive).Sum(a => a.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.AccelerantPower>()) : 0;
                var cur = hittable.ToDictionary(f => f, f => f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.PoisonPower>());
                var canon = ModelDb.Power<MegaCrit.Sts2.Core.Models.Powers.PoisonPower>();
                int maxTicks = 0;
                for (int r = 0; r < plays; r++)
                {
                    foreach (var f in hittable)
                    {
                        if (!f.CanReceivePowers) continue;
                        var pw = (PowerModel?)f.GetPower<MegaCrit.Sts2.Core.Models.Powers.PoisonPower>() ?? canon;
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
                // 原来的口径：有目标的攻击、打全体、只剩一个敌人的打随机，各按界面预览的单次伤害与段数
                List<Creature>? hitList = tgt != null ? new List<Creature> { tgt } : card.TargetType switch
                {
                    TargetType.AllEnemies => foes.Where(c => IsHittableM(c)).ToList(),
                    TargetType.RandomEnemy when foes.Count == 1 && IsHittableM(foes[0]) => foes,
                    TargetType.RandomEnemy when card is MegaCrit.Sts2.Core.Models.Cards.MementoMori && MementoTarget(me) is { } mt => new List<Creature> { mt },     // v0.113.0，见 MementoTarget
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
                if (per.Count == 0) all = true;      // 自己没有伤害：只算附带伤害，按“打全体”的口径
                if (!riderDmg) return (c4, c5, c6, c7, killed, image > 0 ? AfterimageBlock(cs, me, image) * plays : 0);
                // 4、5 同原来的口径：单次伤害取界面预览（X 费 0 段时也照给），段数取最大
                foreach (var (_, v) in per) { ownHits = Math.Max(ownHits, v.hits); ownMax = Math.Max(ownMax, v.pd); }
                for (int r = 0; r < plays; r++)
                {
                    foreach (var (f, v) in per) for (int i = 0; i < v.hits; i++) Own(f, new Hit(v.pd, true, move, me.Creature, card));
                    Riders();
                }
            }

            float block = image > 0 ? AfterimageBlock(cs, me, image) * triggers : 0;
            var dead = foes.Where(f => seq[f].Count > 0 && SeqHpLoss(f, seq[f]) >= f.CurrentHp).ToList();
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

    /// <summary>
    /// 格式 21：这个敌人在下一个敌方回合开始时被毒实际打掉多少血。血条上的预告（NHealthBar 用 PoisonPower.CalculateTotalDamageNextTurn）是：
    /// 结算 min(层数, 1 + 对手的触媒层数) 次，第 i 次按层数 − i 过伤害钩子（无实体在钩子的伤害上限里已经压成 1）。预告不管掉血修正，这里补上
    /// （v0.111.0 里改掉血的 7 种效果中对敌人的毒有效的三种，都按状态逐次算）：滑溜每次把 ≥ 1 的掉血压成 1、用掉一层；缓冲每层抵掉一次掉血；
    /// 硬化外壳在敌方回合开始前重置（BeforeSideTurnStart），按满额封顶。每次截掉小数。只读。
    /// </summary>
    static float PoisonNext(Creature f, MegaCrit.Sts2.Core.Models.Powers.PoisonPower pp) => PoisonNextWith(f, pp.Amount, null);

    /// <summary>对手（玩家一方）活着的触媒层数合计（毒每回合多结算几次）。</summary>
    /// <summary>格式 19/20/22 的毒伤预告：游戏的 PoisonPower.CalculateTotalDamageNextTurn（只过 Hook.ModifyDamage）。v0.114.0 起难杀从伤害上限改成掉血钩子
    /// （HardToKillPower.ModifyHpLostBeforeOstyLate），这个函数对外骨骼虫不再封顶（游戏界面的血条预告也是）；有难杀时照 v0.113.1 的口径逐次封顶，
    /// 没有时原样调游戏的函数（帧不变）。</summary>
    static float PoisonGameLike(Creature f, MegaCrit.Sts2.Core.Models.Powers.PoisonPower pp)
    {
        int htk = f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.HardToKillPower>();
        if (htk <= 0) return pp.CalculateTotalDamageNextTurn();
        var props = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unblockable | MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered;
        int n = Math.Min(pp.Amount, 1 + AccelOf(f));
        decimal sum = 0;
        for (int i = 0; i < n; i++)
            sum += Math.Min(MegaCrit.Sts2.Core.Hooks.Hook.ModifyDamage(f.CombatState!.RunState, f.CombatState, f, null, pp.Amount - i, props, null, null,
                MegaCrit.Sts2.Core.Hooks.ModifyDamageHookType.All, CardPreviewMode.None, out _), htk);
        return (int)sum;
    }

    static int AccelOf(Creature f) => f.CombatState!.GetOpponentsOf(f).Where(c => c.IsAlive).Sum(a => a.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.AccelerantPower>());

    /// <summary>同 PoisonNext，只是毒的层数与触媒层数可以另给（格式 23：这一步加毒、打出触媒之后；accel 为空时按现在的触媒）。</summary>
    static float PoisonNextWith(Creature f, int amount, int? accel)
    {
        try
        {
            var cs = f.CombatState!;
            int n = Math.Min(amount, 1 + (accel ?? AccelOf(f)));
            int slip = f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.SlipperyPower>(), buf = f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.BufferPower>();
            int htk = f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.HardToKillPower>();      // v0.114.0：难杀改成掉血钩子（ModifyHpLostBeforeOstyLate，先于滑溜），HitDmg 里不再封顶
            float total = 0;
            var props = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unblockable | MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered;
            for (int i = 0; i < n; i++)
            {
                float d = HitDmg(cs, f, null, amount - i, props, null);
                if (htk > 0 && d > 0) d = Math.Min(d, htk);
                bool slipped = slip > 0 && d >= 1;
                if (slipped) d = 1;
                if (d > 0 && buf > 0) { buf--; continue; }       // 缓冲抵掉的这次没掉血，滑溜也不减层
                if (slipped) slip--;
                total += d;
            }
            float cap = f.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.HardenedShellPower>().Select(x => (float)x.Amount).DefaultIfEmpty(float.MaxValue).Min();
            return Math.Min(total, cap);
        }
        catch { return 0; }
    }

    /// <summary>格式 21：余像每次给的格挡（AfterimagePower.AfterCardPlayed：GainBlock 层数点，Unpowered，经 Hook.ModifyBlock，截掉小数）。</summary>
    static float AfterimageBlock(ICombatState cs, Player me, int amount)
    {
        try { return MathF.Floor((float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyBlock(cs, me.Creature, amount, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, null, null, out _)); }
        catch { return amount; }
    }

    /// <summary>v0.113.0 的铭记死亡（静默猎手，标着打随机敌人）实际打当前生命最低的可攻击敌人：MementoMori.OnPlay 的
    /// CombatState.HittableEnemies.MinBy(CurrentHp)（并列取靠前的）。没有可攻击的敌人时为 null。</summary>
    static Creature? MementoTarget(Player me)
    {
        try
        {
            var hs = Hittable(me.Creature.CombatState!);
            return hs.Count == 0 ? null : hs.MinBy(c => c.CurrentHp);
        }
        catch { return null; }
    }
}
