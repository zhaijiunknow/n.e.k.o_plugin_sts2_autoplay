using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Powers;
using RL = MegaCrit.Sts2.Core.Models.Relics;
using PT = MegaCrit.Sts2.Core.Models.Potions;

namespace Sts2Headless;

// 格式 29 包 3（R3-格式29规格.md 5.3）：候选 12（这一步当场得到的能量）、13（这一步之后进手的牌数）的新算法。
// 与格式 26 的 CardGains / CandGains 的区别：进手分成“抽牌”（CardPileCmd.Draw，受小提琴、不能抽牌等 Hook.ShouldDraw 约束，另受抽牌堆 + 弃牌堆张数限制）
// 与“进手”（生成、从牌堆选进手、回手，不受这两者约束）两份（G09）；名单补齐（G14、G65、G71），遗物（G07）、药水（G06、G78）、百年积木（G08）。
// 只在 V29 下调用；格式 26–28 仍走 Gains26.cs 的 CardGains / CandGains。全部只读。
public sealed partial class Observation
{
    /// <summary>这一步得失的拆分：能量、抽牌张数（未乘 ShouldDraw、未封顶）、进手张数，Back 为抽完之后从手里放回抽牌堆的张数（微光、光子切割、深谋远虑）。</summary>
    internal record struct Gain29(float En, float Draw, float Gen, float Back = 0)
    {
        public static Gain29 operator +(Gain29 a, Gain29 b) => new(a.En + b.En, a.Draw + b.Draw, a.Gen + b.Gen, a.Back + b.Back);
    }

    /// <summary>CandGains29 的结果：能量（12）、进手合计（13，已封顶）、实际抽牌张数（乘过 ShouldDraw、按空位与牌堆封顶，候选 15、算盘用）、封顶前的抽牌张数。</summary>
    internal readonly record struct Gains29Out(float Energy, float Cards, float Drawn, float DrawRaw);

    static int OthersInHand(PlayerCombatState pcs, CardModel c) => pcs.Hand.Cards.Count(x => !ReferenceEquals(x, c));

    /// <summary>本回合自己开始打出（CardPlaysStarted，含重放）的满足条件的次数。</summary>
    static int StartedThisTurn(Player me, Func<CardModel, bool> pred)
    {
        try
        {
            var cs = me.Creature.CombatState!;
            return CombatManager.Instance.History.CardPlaysStarted.Count(e => e.HappenedThisTurn(cs) && e.CardPlay.Card.Owner == me && pred(e.CardPlay.Card));
        }
        catch { return 0; }
    }

    /// <summary>本回合自己打完（CardPlaysFinished，含重放）的满足条件的次数。</summary>
    static int FinishedThisTurn(Player me, Func<CardModel, bool> pred)
    {
        try
        {
            var cs = me.Creature.CombatState!;
            return CombatManager.Instance.History.CardPlaysFinished.Count(e => e.HappenedThisTurn(cs) && e.CardPlay.Card.Owner == me && pred(e.CardPlay.Card));
        }
        catch { return 0; }
    }

    /// <summary>这张牌打出时实际花掉的能量（X 费为全部能量，免费为 0；得力助手、象牙麻将牌、威吓头盔看的 Resources.EnergyValue）。</summary>
    static int EnergySpent(CardModel c, Player me)
    {
        try { return c.EnergyCost.CostsX ? me.PlayerCombatState!.Energy : Math.Max(0, CostAll(c)); }
        catch { return 0; }
    }

    /// <summary>打出这张牌会让手里其余的牌全部离开（消耗、弃掉、洗回）：之后的手牌数按 0 算空位。</summary>
    static bool ClearsHand(CardModel c) => c is Stoke or StormOfSteel or CalculatedGamble or WheelOfFortune or FiendFire;

    /// <summary>打出时先从手里消耗、弃掉几张（在抽牌、生成之前，空出的手牌位置可以再进牌）：燃烧契约、坚毅、烙印各 1 张，隐秘匕首 Cards 张。</summary>
    static int LeavesFirst(CardModel c, int others)
    {
        try
        {
            return c switch
            {
                BurningPact or TrueGrit or Brand => Math.Min(1, others),
                HiddenDaggers => Math.Min(c.DynamicVars.Cards.IntValue, others),
                _ => 0,
            };
        }
        catch { return 0; }
    }

    /// <summary>
    /// 格式 29：一张牌打出（plays 次）当场的能量、抽牌、进手（未封顶）。tgt 为目标（自动打出时为空），killed 为这一步打死的敌人。
    /// 格式 26 的 CardGains 拆成抽牌与进手两份（G09），再补上 G14（各角色、无色）、G65（疯狂科学技能型）、G71（事件牌）的名单。行号见 R3-格式29规格.md 5.3 与实现-包3.md。
    /// </summary>
    static Gain29 CardGains29(CardModel c, Creature? tgt, Player me, List<Creature>? killed)
    {
        float en = 0, draw = 0, gen = 0, back = 0;
        try
        {
            var pcs = me.PlayerCombatState!;
            var cs = me.Creature.CombatState!;
            int plays = PlayCount(c, tgt);
            int H = OthersInHand(pcs, c);                                   // 打出时手里其余的牌（出牌时这张已经移去结算牌堆）
            int space = Math.Max(0, CardPile.MaxCardsInHand - H);
            var draws = pcs.DrawPile.Cards; var discards = pcs.DiscardPile.Cards;
            int D = draws.Count, R = discards.Count;
            float Var(string name) => c.DynamicVars.TryGetValue(name, out var v) ? (float)v.BaseValue : 0;
            bool tgtDead = tgt != null && killed != null && killed.Contains(tgt);
            bool arrowed = tgt != null && NailHits(tgt);
            bool mine = tgt != null && tgt.GetPowerInstances<ImpaledPower>().Any(p => p.Applier == me.Creature && p.Arrows.Count > 0);
            if (EnergyCards.Contains(c.GetType())) en += Var("Energy") * plays;
            if (DrawCards.Contains(c.GetType())) draw += Var("Cards") * plays;
            switch (c)
            {
                // ---- 格式 26 已有的（照 CardGains，分成抽牌、进手） ----
                case DoubleEnergy:
                {
                    int cost = Math.Max(0, CostAll(c));
                    float left = Math.Max(0, pcs.Energy - cost);
                    en += left * (MathF.Pow(2, plays) - 1);
                    break;
                }
                case BowSmack when arrowed: en += Var("Energy") * plays; break;
                case Sunder when tgtDead: en += Var("Energy"); break;
                case Restlessness when pcs.Hand.Cards.Count == 1: en += Var("Energy"); draw += Var("Cards"); break;
                case ForgottenRitual when CombatManager.Instance.History.Entries.OfType<MegaCrit.Sts2.Core.Combat.History.Entries.CardExhaustedEntry>()
                                             .Any(e => e.HappenedThisTurn(cs)):
                    en += Var("Energy"); break;
                case CompileDriver or SteadyCadence:
                    if (c.DynamicVars.TryGetValue("CalculatedCards", out var cc) && cc is MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedVar cv) draw += (float)cv.Calculate(tgt) * plays;
                    break;
                case DaggerThrow or EscapePlan: draw += plays; break;
                case KiHop when arrowed: draw += Var("Cards") * plays; break;
                case Wrest when mine: draw += Var("Cards"); break;
                case KiChant when D + R > 0: gen += 1; break;
                // v0.114.0：怒放改成把手里一张牌变化成全力射击（Florescence.OnPlay：FromHand → Transform），手牌张数不变，不再算进手
                case BlotOutTheSky: gen += 1; break;
                // v0.114.0 新牌乐观：消耗手里所有悲伤，消耗几张抽几张（Optimism.OnPlay：cardCount > 0 时 CardPileCmd.Draw）；悲伤离手在 AutoPlays 里记
                case Optimism: draw += pcs.Hand.Cards.Count(x => x is Sadness && !ReferenceEquals(x, c)); break;
                // 自嘲：给自己手里塞 "Sadness" 张悲伤（SelfDeprecate.OnPlay，喜悦只给队友；旧遗漏，v0.114.0 复核时补）
                case SelfDeprecate: gen += Var("Sadness") * plays; break;
                case BrightenUp: gen += Var("Sadness") + Var("Joy"); break;
                case Jest or SorrowfulStrike: gen += Var("Cards") * plays; break;
                // v0.114.0：刻薄塞 Cards（2）张悲伤（Vitriol.OnPlay 循环 DynamicVars.Cards 次），翻找仍是 1 张
                case Vitriol: gen += Var("Cards") * plays; break;
                case Rummage: gen += plays; break;
                case MourningBlow: gen += 1; break;
                case LockedIn when draws.Any(IsShoot): gen += 1; break;
                // 微光、光子切割、深谋远虑：抽 Cards 张之后从手里选 1 张放回抽牌堆顶，净进手少 1（R1-储君；Glimmer.cs:29-34、PhotonCut.cs:35-36、ThinkingAhead.cs），在封顶之后扣
                case Glimmer or PhotonCut or ThinkingAhead: back += 1; break;
                // ---- G14 铁甲战士（R1-铁甲第 4 条） ----
                case InfernalBlade: gen += plays; break;                                    // InfernalBlade.cs:22-28
                case Pillage: draw += 1; break;                                             // 抽到非攻击为止，至少 1 张（Pillage.cs:28-33）
                case Stoke: gen += H; break;                                                // 消耗整手、生成同样张数（Stoke.cs:22-33）
                // ---- G14 无色（R1-铁甲第 20 条⑥） ----
                case Jackpot or JackOfAllTrades or BundleOfJoy: gen += Var("Cards") * plays; break;
                case Discovery or WhiteNoise or CollisionCourse or ManifestAuthority or Quasar or Severance or Distraction or Abundance or Splash or Largesse: gen += plays; break;
                case Tutor: gen += Math.Min(plays, D); break;
                case SecretTechnique: gen += Math.Min(plays, draws.Count(x => x.Type == CardType.Skill)); break;
                case SecretWeapon: gen += Math.Min(plays, draws.Count(x => x.Type == CardType.Attack)); break;
                case SeekerStrike: gen += Math.Min(plays, D); break;
                case Anointed: gen += Math.Min(space, draws.Count(x => x.Rarity == CardRarity.Rare)); break;
                case Scrawl: draw += space; break;                                          // 抽到手满（Scrawl.cs:20-21）
                case Impatience when !pcs.Hand.Cards.Any(x => !ReferenceEquals(x, c) && x.Type == CardType.Attack): draw += Var("Cards"); break;
                // ---- G14 静默猎手（R1-猎人三节）：生成小刀 ----
                case BladeDance or CloakAndDagger or UpMySleeve or BladeOfInk: gen += Var("Cards") * plays; break;
                case LeadingStrike or HiddenDaggers or FanOfKnives: gen += Var("Shivs") * plays; break;
                case StormOfSteel: gen += H; break;                                         // 弃掉整手、生成同样张数小刀
                case CalculatedGamble: draw += H; break;                                    // 弃掉整手、抽同样张数（CardCmd.DiscardAndDraw）
                // ---- G14 故障机器人（R1-故障三节） ----
                case Ftl when FinishedThisTurn(me, _ => true) < Var("PlayMax"): draw += Var("Cards"); break;
                case Hologram: gen += Math.Min(plays, R); break;
                case AllForOne:
                    gen += Math.Min(space, discards.Count(x =>
                    {
                        try { return x.EnergyCost.GetWithModifiers(CostModifiers.All) == 0 && !x.EnergyCost.CostsX && x.Type is CardType.Attack or CardType.Skill or CardType.Power; }
                        catch { return false; }
                    }));
                    break;
                // ---- G14 储君（R1-储君三、四节） ----
                case CrashLanding: gen += space; break;
                case HeirloomHammer when pcs.Hand.Cards.Any(x => !ReferenceEquals(x, c) && IsColorlessCard(x)): gen += Var("Repeat") * plays; break;
                case SummonForth:
                {
                    var blades = pcs.AllCards.OfType<SovereignBlade>().ToList();
                    gen += blades.Count(b => b.Pile == null || b.Pile.Type != PileType.Hand) + (blades.Count == 0 ? 1 : 0);
                    break;
                }
                case ParticleWall when !ExhaustsOnPlay(c, me) && !c.IsDupe: gen += 1; break;
                // ---- G14 亡灵契约师（R1-亡灵 N6） ----
                case Fetch when me.IsOstyAlive && !CombatManager.Instance.History.CardPlaysFinished.Any(e => e.HappenedThisTurn(cs) && ReferenceEquals(e.CardPlay.Card, c)):
                    draw += Var("Cards"); break;
                case Dredge: gen += Math.Min(Var("Cards"), Math.Min(space, R)); break;
                case Graveblast: gen += Math.Min(plays, R); break;
                // ---- G14 愚者塔罗（R1-愚者 G3） ----
                case TheWorld: gen += Math.Min(Var("Cards"), Math.Min(space, R)); break;
                case TheMoon or TheLovers: gen += space; break;
                case TheHierophant: gen += Var("Cards"); break;
                case WheelOfFortune: draw += Var("Cards"); break;
                // ---- G65 疯狂科学技能型（MadScience.cs:276-286；附加效果只看 Rider） ----
                case MadScience ms:
                    switch (ms.TinkerTimeRider)
                    {
                        case MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Energized: en += Var("EnergizedEnergy") * plays; break;
                        case MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Wisdom: draw += Var("WisdomCards") * plays; break;
                        case MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Chaos: gen += plays; break;
                    }
                    break;
                // ---- G71 放进手里的事件牌（R2-角色遗物第 8 条） ----
                case NeowsFury: gen += Math.Min(Var("Cards"), Math.Min(space, R)); break;
                case Wish: gen += Math.Min(1, D); break;
                case DualWield when pcs.Hand.Cards.Any(x => !ReferenceEquals(x, c) && x.Type is CardType.Attack or CardType.Power): gen += Var("Cards") * plays; break;
            }
            // 铸造：场上（消耗堆以外）没有君王之剑时先生成一把进手（ForgeCmd.Forge，ForgeCmd.cs:42-49）；召唤上前另算
            if (c is not SummonForth && c.DynamicVars.ContainsKey("Forge")
                && !pcs.AllCards.OfType<SovereignBlade>().Any(b => b.Pile == null || b.Pile.Type != PileType.Exhaust))
                gen += 1;
            // 拔箭与射箭（照 CardGains）
            var (rh, rd, shots) = c.Type == CardType.Attack || c is Recall or Uproot or Wrest ? ArrowsOut(c, tgt, me, tgtDead) : (0, 0, 0);
            if (c is EpicRetrieval) en += Var("Energy") * (rh + rd);
            if (rh + rd > 0 && me.Creature.GetPower<YahPower>() is { } yah && !InternalFlag(yah, "triggeredThisTurn")) en += yah.Amount;
            if (shots > 0) gen += discards.Count(x => x is WhistlingArrows);
            // v0.114.0：花样射击不再回手，改为射出箭时抽 Cards（2/3）张（TrickShot.OnPlay：ShootArrowAtTarget 返回真才 Draw）；
            // 箭矢戳击不再从弃牌堆拿射击牌（ArrowStab.OnPlay 改成伤害 + 瞄准）
            if (c is TrickShot) draw += Var("Cards") * shots;
            if (IsMelody(c) && me.Creature.GetPower<MotifPower>() is { } motif) draw += motif.Amount * NewMelodyTags(me, c);

            // ---- 能力带出的（G14） ----
            var pw = me.Creature;
            // 杂耍：本回合恰好第 3 张攻击时复制层数张回手（JugglingPower.cs:32-69，BeforeCardPlayed 每次重放计数）
            if (c.Type == CardType.Attack && pw.GetPower<JugglingPower>() is { } jug && jug.Amount > 0)
            {
                int n = StartedThisTurn(me, x => x.Type == CardType.Attack);
                if (n < 3 && n + plays >= 3) gen += jug.Amount;
            }
            // 横祸：之后每打一张攻击（每次重放）生成层数张攻击（CalamityPower.cs:28-55）
            if (c.Type == CardType.Attack && pw.GetPower<CalamityPower>() is { } cal && cal.Amount > 0) gen += cal.Amount * plays;
            // 恶毒：这一步给敌人上易伤（没被人工制品挡掉）就抽层数张（ViciousPower.cs:19-26）
            if (pw.GetPower<ViciousPower>() is { } vic && vic.Amount > 0 && AppliesVulnerable(c, tgt))
            {
                var hit = c.TargetType == TargetType.AllEnemies ? Hittable(cs).ToList() : tgt != null && !tgt.IsPlayer ? new List<Creature> { tgt } : new List<Creature>();
                int apps = hit.Count(f => f.GetPowerAmount<ArtifactPower>() <= 0 && !(killed?.Contains(f) ?? false));      // 打死的目标挂不上
                draw += vic.Amount * apps * plays;
            }
            // 子程序：打能力牌每次 +层数能量（SubroutinePower.cs:31-53，BeforeCardPlayed 记下当时的层数；重放时层数的增长不算）
            if (c.Type == CardType.Power && pw.GetPower<SubroutinePower>() is { } sub && sub.Amount > 0) en += sub.Amount * plays;
            // 野性：没花能量的攻击回手（FeralPower.ModifyCardPlayResultLocation）
            if (c.Type == CardType.Attack && pw.GetPower<FeralPower>() is { } fer && fer.DisplayAmount > 0 && EnergySpent(c, me) == 0 && !c.IsDupe) gen += 1;
            // 得力助手：打出花 ≥ 2 能量的牌时弃牌堆里的得力助手全部回手（RightHandHand.cs:44-53）
            int rhh = discards.Count(x => x is RightHandHand);
            if (rhh > 0 && discards.OfType<RightHandHand>().First().DynamicVars.TryGetValue("Energy", out var rv) && EnergySpent(c, me) >= rv.IntValue) gen += rhh;
            // 如此甚好：打完技能牌后本回合技能数是 Cards 的倍数时，不在手里的如此甚好回手（MakeItSo.cs:36-45）
            if (c.Type == CardType.Skill)
            {
                var mis = pcs.AllCards.OfType<MakeItSo>().Where(x => !ReferenceEquals(x, c) && (x.Pile == null || x.Pile.Type != PileType.Hand)).ToList();
                if (mis.Count > 0)
                {
                    int k = FinishedThisTurn(me, x => x.Type == CardType.Skill), per = mis[0].DynamicVars.Cards.IntValue;
                    if (per > 0 && Enumerable.Range(1, plays).Any(i => (k + i) % per == 0)) gen += mis.Count;
                }
            }
            // 等离子球激发 +2 能量（PlasmaOrb.cs:36-41）：双重释放、四重释放、多重释放激发最前面的球，碎裂每个球激发两次；生成球时球位满了挤掉最前面的
            en += PlasmaEvokeEnergy(c, me);
            // 消耗：黑暗之拥每消耗一张（非虚无）抽层数张；战鼓被消耗时得能量（DarkEmbracePower.cs:37-47、DrumOfBattle.cs:31-41）
            var (exN, drums) = ExhaustedThisStep(c, me);
            if (exN > 0 && pw.GetPower<DarkEmbracePower>() is { } de && de.Amount > 0) draw += de.Amount * exN;
            foreach (var dr in drums) en += (float)dr.DynamicVars.Energy.BaseValue * PlayCount(dr, null);
        }
        catch { }
        return new Gain29(en, draw, gen, back);
    }

    /// <summary>这张牌打出时会不会给敌人上易伤（恶毒用）：数值里有 VulnerablePower 的，以及按 "Power" 数值上易伤的上勾拳、冲击波、揭露、腐化、女皇；
    /// 熔火之拳在目标已有易伤时翻倍。钉住、蹒跚射击有条件，不算。</summary>
    static bool AppliesVulnerable(CardModel c, Creature? tgt)
    {
        try
        {
            if (c.DynamicVars.TryGetValue("VulnerablePower", out var vv) && vv.BaseValue > 0 && c is not MadScience) return true;
            return c switch
            {
                Uppercut or Shockwave or Expose or Putrefy or TheEmpress => true,
                MoltenFist => tgt != null && tgt.GetPowerAmount<VulnerablePower>() > 0,
                _ => false,
            };
        }
        catch { return false; }
    }

    static bool IsColorlessCard(CardModel c) { try { return c.VisualCardPool.IsColorless; } catch { return false; } }

    /// <summary>这一步消耗几张牌（打出的这张 + ExhaustOthers；V29 下 ExhaustOthers 由包 4 补浩劫等），其中哪些是战鼓（打出的这张、恶魔之焰与 STOKE 的整手、重振精神的非攻击牌里的）。</summary>
    static (int n, List<CardModel> drums) ExhaustedThisStep(CardModel c, Player me)
    {
        var drums = new List<CardModel>();
        int n = 0;
        try
        {
            bool self = ExhaustsOnPlay(c, me);
            n = (self ? 1 : 0) + ExhaustOthers(c, me);
            if (self && c is DrumOfBattle) drums.Add(c);
            var others = OtherHand(c, me);
            if (c is FiendFire or Stoke) drums.AddRange(others.OfType<DrumOfBattle>());
            else if (c is SecondWind) drums.AddRange(others.OfType<DrumOfBattle>());     // 战鼓是技能（非攻击）
        }
        catch { }
        return (n, drums);
    }

    /// <summary>这一步激发等离子球得到的能量（每次 EvokeVal，固定 2）。只算最前面的球确定被激发的几种情况；重放不另算。</summary>
    static float PlasmaEvokeEnergy(CardModel c, Player me)
    {
        try
        {
            var q = me.PlayerCombatState!.OrbQueue;
            var orbs = q.Orbs.ToList();
            static bool P(MegaCrit.Sts2.Core.Models.OrbModel o) => o is MegaCrit.Sts2.Core.Models.Orbs.PlasmaOrb;
            float ev(MegaCrit.Sts2.Core.Models.OrbModel o) => (float)o.EvokeVal;
            switch (c)
            {
                case Dualcast when orbs.Count > 0 && P(orbs[0]): return 2 * ev(orbs[0]);
                case Quadcast when orbs.Count > 0 && P(orbs[0]): return c.DynamicVars.Repeat.IntValue * ev(orbs[0]);
                case MultiCast when orbs.Count > 0 && P(orbs[0]):
                {
                    int x = Math.Max(0, MegaCrit.Sts2.Core.Hooks.Hook.ModifyXValue(me.Creature.CombatState!, c, me.PlayerCombatState!.Energy)) + (c.IsUpgraded ? 1 : 0);
                    return x * ev(orbs[0]);
                }
                case Shatter: return orbs.Where(P).Sum(o => 2 * ev(o));
            }
            // 生成球：球位满了挤掉最前面的（OrbCmd.cs:81-84）
            int k = OrbChannels(c, me).Length;
            int over = orbs.Count + k - q.Capacity;
            if (k > 0 && over > 0) return orbs.Take(Math.Min(over, orbs.Count)).Where(P).Sum(ev);
        }
        catch { }
        return 0;
    }

    /// <summary>
    /// G07：出牌时遗物带出的能量、抽牌、进手（计数取 DisplayAmount，plays 次逐次累加；R1-遗物药水 G5 的行号）。handAfter 为这一步之后、进手之前的手牌数（不休陀螺另在 CandGains29 判断）。
    /// 地精之角：killed 里每个敌人 +1 能量 +1 抽；双截棍：攻击牌、(计数 + i) % 10 == 0 时 +1 能量；铁棒：(计数 + i) % 4 == 0 时 +1 抽；棋子：能力牌每次 +1 抽；
    /// 金纸：这一步消耗张数跨过 5 的倍数各 +1 抽；音乐盒：攻击牌、本回合没触发时 +1 进手；燃烧木棍：本场没触发、这一步消耗了技能牌时 +1 进手；象牙麻将牌：花 ≥ 3 能量时每次 +1 能量。
    /// </summary>
    static Gain29 RelicGains29(CardModel c, Creature? tgt, Player me, List<Creature>? killed, int plays)
    {
        float en = 0, draw = 0, gen = 0;
        try
        {
            bool atk = c.Type == CardType.Attack;
            var (exN, _) = ExhaustedThisStep(c, me);
            foreach (var r in me.Relics)
            {
                if (!RelicLive(r)) continue;
                switch (r)
                {
                    case RL.GremlinHorn when killed != null && killed.Count > 0:
                        en += (float)r.DynamicVars.Energy.BaseValue * killed.Count; draw += (float)r.DynamicVars.Cards.BaseValue * killed.Count; break;
                    case RL.Nunchaku when atk:
                        for (int i = 1; i <= plays; i++) if ((r.DisplayAmount + i) % r.DynamicVars.Cards.IntValue == 0) en += (float)r.DynamicVars.Energy.BaseValue;
                        break;
                    case RL.IronClub:
                        for (int i = 1; i <= plays; i++) if ((r.DisplayAmount + i) % r.DynamicVars.Cards.IntValue == 0) draw += 1;
                        break;
                    case RL.GamePiece when c.Type == CardType.Power: draw += (float)r.DynamicVars.Cards.BaseValue * plays; break;
                    case RL.JossPaper when exN > 0:
                    {
                        int per = r.DynamicVars["ExhaustAmount"].IntValue;
                        if (per > 0) draw += (r.DisplayAmount + exN) / per;
                        break;
                    }
                    case RL.MusicBox when atk && !UsedThisTurn(r): gen += 1; break;
                    case RL.BurningSticks when !UsedThisCombat(r) && ExhaustsSkill(c, me): gen += 1; break;
                    case RL.IvoryTile when r.DynamicVars.TryGetValue("EnergyThreshold", out var th) && EnergySpent(c, me) >= th.IntValue:
                        en += (float)r.DynamicVars.Energy.BaseValue * plays; break;
                }
            }
        }
        catch { }
        return new Gain29(en, draw, gen);
    }

    /// <summary>这一步消耗了技能牌（燃烧木棍）：打出的这张是技能且被消耗，或恶魔之焰、STOKE、重振精神消耗的手牌里有技能。</summary>
    static bool ExhaustsSkill(CardModel c, Player me)
    {
        try
        {
            if (c.Type == CardType.Skill && ExhaustsOnPlay(c, me)) return true;
            if (c is FiendFire or Stoke or SecondWind) return OtherHand(c, me).Any(x => x.Type == CardType.Skill);
        }
        catch { }
        return false;
    }

    /// <summary>
    /// G06、G78：用药水当场的能量、抽牌、进手（R1-遗物药水 G3、R1-愚者 G6 的行号）。能量：痊愈药水、明耀酊剂、能量药水；
    /// 抽牌：痊愈药水、明晰提取物、异蛇之油、迅捷药水、发光水（先消耗整手）、瓶装潜能（整手洗回）；进手：攻击、技能、能力、无色药水各 1，欧洛巴斯之酸 3，
    /// 液态记忆（弃牌堆不空）、预知之滴（抽牌堆不空）各 1，狡诈药水、食尸鬼之罐、宇宙调和按 Cards，结晶欣快填满手牌，瓶装歌曲 3。赌徒特酿要选牌，记 0。
    /// handAfter 为药水结算之后、进手之前的手牌数（发光水、瓶装潜能为 0）。
    /// </summary>
    static Gain29 PotionGains29(PotionModel p, Player me, int handAfter)
    {
        float en = 0, draw = 0, gen = 0;
        try
        {
            var pcs = me.PlayerCombatState!;
            float Var(string name) => p.DynamicVars.TryGetValue(name, out var v) ? (float)v.BaseValue : 0;
            int space = Math.Max(0, CardPile.MaxCardsInHand - handAfter);
            switch (p)
            {
                case PT.EnergyPotion or PT.RadiantTincture: en += Var("Energy"); break;
                case PT.CureAll: en += Var("Energy"); draw += Var("Cards"); break;
                case PT.SwiftPotion or PT.Clarity or PT.SneckoOil or PT.GlowwaterPotion or PT.BottledPotential: draw += Var("Cards"); break;
                case PT.AttackPotion or PT.SkillPotion or PT.PowerPotion or PT.ColorlessPotion: gen += 1; break;
                case PT.OrobicAcid: gen += 3; break;
                case PT.LiquidMemories when pcs.DiscardPile.Cards.Count > 0: gen += 1; break;
                case PT.DropletOfPrecognition when pcs.DrawPile.Cards.Count > 0: gen += 1; break;
                case PT.CunningPotion or PT.PotOfGhouls or PT.CosmicConcoction: gen += Var("Cards"); break;
                case PT.CrystallineEuphoria: gen += space; break;
                case PT.BottledSong: gen += 3; break;
            }
        }
        catch { }
        return new Gain29(en, draw, gen);
    }

    /// <summary>
    /// 格式 29：候选这一步当场得到的能量（12）与进手的牌数（13），以及实际抽的张数（候选 15、算盘用）。
    /// g 为出牌、药水、选牌各自的得失（未封顶）；handAfter 为这一步之后、进手之前的手牌数；extraDiscard 为这一步弃进弃牌堆、可供洗牌再抽的张数（计算下注、命运之轮）。
    /// G08 百年积木：本场没触发、这一步当场会掉没被挡住的血（hurtNow）时抽 Cards 张。G09：抽牌 × Hook.ShouldDraw（小提琴、不能抽牌），进手不受影响；
    /// 抽牌不超过抽牌堆 + 弃牌堆；合计按手牌空位封顶。不休陀螺：这一步之后手牌为 0 时再抽 1 张。有“不能加能量”时能量为 0。
    /// </summary>
    static Gains29Out CandGains29(Player me, Gain29 g, int handAfter, bool hurtNow, int extraDiscard = 0)
    {
        try
        {
            var pcs = me.PlayerCombatState!;
            var cs = me.Creature.CombatState!;
            float en = g.En, draw = g.Draw, gen = g.Gen;
            foreach (var r in me.Relics)
                if (r is RL.CentennialPuzzle && RelicLive(r) && !UsedThisCombat(r) && hurtNow) draw += (float)r.DynamicVars.Cards.BaseValue;
            bool canDraw = true;
            try { canDraw = MegaCrit.Sts2.Core.Hooks.Hook.ShouldDraw(cs, me, false, out _); } catch { }
            if (!canDraw) draw = 0;
            if (me.Creature.HasPower<NoEnergyGainPower>()) en = 0;
            int pile = pcs.DrawPile.Cards.Count + pcs.DiscardPile.Cards.Count + Math.Max(0, extraDiscard);
            float rawDraw = draw;
            draw = Math.Min(draw, pile);
            float space = Math.Max(0, CardPile.MaxCardsInHand - handAfter);
            float drawn = Math.Min(draw, space);
            float total = Math.Min(draw + gen, space);
            total -= Math.Min(g.Back, handAfter + total);      // 抽完之后放回一张（手里有牌才放）
            if (handAfter + total <= 0 && canDraw && pile > draw && me.Relics.Any(r => r is RL.UnceasingTop && RelicLive(r))) { total += 1; drawn += 1; }
            return new Gains29Out(en, total, drawn, rawDraw);
        }
        catch { return default; }
    }
}
