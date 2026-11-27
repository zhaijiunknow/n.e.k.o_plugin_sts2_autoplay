using System.Reflection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Powers;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Monsters;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.MonsterMoves.Intents;
using MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine;
using MegaCrit.Sts2.Core.ValueProps;

namespace Sts2Headless;

// 格式 29 的生物与招式：包 5 的文件（研究报告/2026-11-20_全角色观测缺口调研/R3-格式29规格.md 5.5）。
//   G05 招式推演（招式 22–24，睡眠类醒来那一步的招式）、G04 生物 22–23、G24 复活窗口（生物 24–26 与复活中的意图、招式、形态）、
//   G26 瀑布巨兽（生物 27，生物 0、1、17–19 记 0）、G28 / G21 / G70 玩家 28–29（值由包 1 算）。
// 只在 V29 下被调用；格式 19–28 的帧不经过这里的任何代码。
public sealed partial class Observation
{
    // ---------------------------------------------------------------- 招式表（G05）

    /// <summary>一招给自己加的力量、给每个队友加的力量、给自己挂的仪式（之后每个敌方回合末加力量）、给自己挂的活力（只加到下一次攻击）。</summary>
    readonly record struct MoveGainRow29(Func<MonsterModel, float> Self, Func<MonsterModel, float> Ally, Func<MonsterModel, float> Ritual, Func<MonsterModel, float> Vigor);

    static readonly Func<MonsterModel, float> Zero29 = _ => 0f;
    static MoveGainRow29 G29(Func<MonsterModel, float>? self = null, Func<MonsterModel, float>? ally = null, Func<MonsterModel, float>? ritual = null, Func<MonsterModel, float>? vigor = null)
        => new(self ?? Zero29, ally ?? Zero29, ritual ?? Zero29, vigor ?? Zero29);
    static Func<MonsterModel, float> N29(float v) => _ => v;

    /// <summary>
    /// G05：招式给的力量、仪式、活力（grep "Apply&lt;StrengthPower&gt;\|Apply&lt;VigorPower&gt;\|Apply&lt;RitualPower&gt;" Models.Monsters/*.cs，v0.113.1）。
    /// 现有 MoveGains 的 10 项（招式 15–17 仍用旧表）都在里面；女王“为我燃烧”给队友的力量在恐惧难度下是 2（旧表写死 1）。
    /// 不在表里的：仪式兽进入眩晕时挂的仪式（不是招式）、神秘骑士开场的力量（不是招式）。selfcheck 核对每一项（怪物类型，招式 ID）。
    /// </summary>
    static readonly Dictionary<(Type, string), MoveGainRow29> MoveGains29 = new()
    {
        [(typeof(Aeonglass), "INCREASING_INTENSITY_MOVE")] = G29(Prop<Aeonglass>("IncreasingIntensityTotalStrength")),
        [(typeof(Axebot), "BOOT_UP_MOVE")] = G29(AxebotBoot()),
        [(typeof(BowlbugNectar), "BUFF_MOVE")] = G29(Prop<BowlbugNectar>("BuffStrengthGain")),
        [(typeof(BruteRubyRaider), "ROAR_MOVE")] = G29(N29(3)),
        [(typeof(BygoneEffigy), "WAKE_MOVE")] = G29(N29(10)),
        [(typeof(CalcifiedCultist), "INCANTATION_MOVE")] = G29(ritual: Prop<CalcifiedCultist>("IncantationAmount")),
        [(typeof(CeremonialBeast), "PLOW_MOVE")] = G29(BeastPlow()),
        [(typeof(CeremonialBeast), "CRUSH_MOVE")] = G29(Prop<CeremonialBeast>("CrushStrength")),
        [(typeof(Crusher), "ADAPT_MOVE")] = G29(Prop<Crusher>("AdaptStrengthGain")),
        [(typeof(CubexConstruct), "CHARGE_UP_MOVE")] = G29(N29(2)),
        [(typeof(CubexConstruct), "REPEATER_BLAST_MOVE")] = G29(N29(2)),
        [(typeof(CubexConstruct), "REPEATER_BLAST_MOVE_2")] = G29(N29(2)),
        [(typeof(DampCultist), "INCANTATION_MOVE")] = G29(ritual: Prop<DampCultist>("IncantationAmount")),
        [(typeof(DecimillipedeSegment), "BULK_MOVE")] = G29(Prop<DecimillipedeSegment>("BulkStrength")),
        [(typeof(DevotedSculptor), "FORBIDDEN_INCANTATION_MOVE")] = G29(ritual: N29(9)),
        [(typeof(Entomancer), "PHEROMONE_SPIT_MOVE")] = G29(m => m.Creature.GetPower<PersonalHivePower>() is { } h && h.Amount < 3 ? 1 : 2),
        [(typeof(Exoskeleton), "ENRAGE_MOVE")] = G29(N29(2)),
        [(typeof(FakeMerchantMonster), "ENRAGE_MOVE")] = G29(N29(2)),
        [(typeof(FlailKnight), "WAR_CHANT")] = G29(N29(3)),
        [(typeof(Fogmog), "SWIPE_MOVE")] = G29(N29(1)),
        [(typeof(Fogmog), "SWIPE_RANDOM_MOVE")] = G29(N29(1)),
        [(typeof(FrogKnight), "FOR_THE_QUEEN")] = G29(N29(5)),
        [(typeof(FuzzyWurmCrawler), "INHALE")] = G29(N29(7)),
        [(typeof(GlobeHead), "GALVANIC_BURST")] = G29(N29(2)),
        [(typeof(GremlinMerc), "HEHE_MOVE")] = G29(N29(2)),
        [(typeof(KinFollower), "POWER_DANCE_MOVE")] = G29(Prop<KinFollower>("DanceStrength")),
        [(typeof(KinPriest), "RITUAL_MOVE")] = G29(Prop<KinPriest>("RitualStrength")),
        [(typeof(KnowledgeDemon), "PONDER_MOVE")] = G29(Prop<KnowledgeDemon>("PonderStrength")),
        [(typeof(LagavulinMatriarch), "SOUL_SIPHON_MOVE")] = G29(N29(2)),
        [(typeof(LivingShield), "SMASH_MOVE")] = G29(Prop<LivingShield>("EnrageStr")),
        [(typeof(LouseProgenitor), "CURL_AND_GROW_MOVE")] = G29(Prop<LouseProgenitor>("GrowStrength")),
        [(typeof(MechaKnight), "WINDUP_MOVE")] = G29(N29(5)),
        [(typeof(Myte), "SUCK_MOVE")] = G29(Prop<Myte>("SuckStrength")),
        [(typeof(Nibbit), "HISS_MOVE")] = G29(Prop<Nibbit>("HissStrengthGain")),
        [(typeof(Ovicopter), "NUTRITIONAL_PASTE_MOVE")] = G29(Prop<Ovicopter>("NutritionalPasteStrengthAmount")),
        [(typeof(PhantasmalGardener), "ENLARGE_MOVE")] = G29(Prop<PhantasmalGardener>("EnlargeStr")),
        [(typeof(Queen), "BURN_BRIGHT_FOR_ME_MOVE")] = G29(ally: _ => MegaCrit.Sts2.Core.Helpers.AscensionHelper.IsDread() ? 2 : 1),
        [(typeof(Queen), "ENRAGE_MOVE")] = G29(N29(2)),
        [(typeof(Rocket), "CHARGE_UP_MOVE")] = G29(Prop<Rocket>("ChargeUpStrengthGain")),
        [(typeof(ScrollOfBiting), "MORE_TEETH")] = G29(N29(2)),
        [(typeof(Seapunk), "BUBBLE_BURP_MOVE")] = G29(Prop<Seapunk>("BubbleStr")),
        [(typeof(SewerClam), "PRESSURIZE_MOVE")] = G29(N29(4)),
        [(typeof(SkulkingColony), "INERTIA_MOVE")] = G29(Prop<SkulkingColony>("InertiaStrengthGain")),
        [(typeof(SlimedBerserker), "LEECHING_HUG_MOVE")] = G29(N29(3)),
        [(typeof(SludgeSpinner), "RAGE_MOVE")] = G29(N29(3)),
        [(typeof(SlumberingBeetle), "ROLL_OUT_MOVE")] = G29(N29(2)),
        [(typeof(SnappingJaxfruit), "ENERGY_ORB_MOVE")] = G29(N29(2)),
        [(typeof(TerrorEel), "THRASH_MOVE")] = G29(vigor: N29(6)),
        [(typeof(TestSubject), "BURNING_GROWL_MOVE")] = G29(Prop<TestSubject>("BurningGrowlStrengthGain")),
        [(typeof(TheAdversaryMkOne), "BARRAGE_MOVE")] = G29(N29(2)),
        [(typeof(TheAdversaryMkTwo), "BARRAGE_MOVE")] = G29(N29(3)),
        [(typeof(TheAdversaryMkThree), "BARRAGE_MOVE")] = G29(N29(4)),
        [(typeof(TheInsatiable), "SALIVATE_MOVE")] = G29(Prop<TheInsatiable>("SalivateStrength")),
        [(typeof(TheLost), "DEBILITATING_SMOG")] = G29(Prop<TheLost>("DebilitatingSmogStrengthStealAmount")),
        [(typeof(TheObscura), "SAIL_MOVE")] = G29(N29(3), N29(3)),        // 扬帆：GetTeammatesOf 含自己
        // 遗忘者：瘴气偷玩家的敏捷，它的“恐惧”伤害 = 基础值 + 自己的敏捷（TheForgotten.cs:24-31,53-60），等同于给这一招的基础值加
        [(typeof(TheForgotten), "MIASMA")] = G29(Prop<TheForgotten>("DebilitatingSmogDexStealAmount")),
        [(typeof(TurretOperator), "RELOAD_MOVE")] = G29(N29(1)),
        [(typeof(Vantom), "PREPARE_MOVE")] = G29(Prop<Vantom>("PrepareStrength")),
        [(typeof(Wriggler), "WRIGGLE_MOVE")] = G29(N29(2)),
    };

    static Func<MonsterModel, float> AxebotBoot()
    {
        var gain = Prop<Axebot>("BootUpStrGain"); var n = Prop<Axebot>("RespawnCount");
        return m => gain(m) * n(m);
    }
    static Func<MonsterModel, float> BeastPlow()
    {
        var s = Prop<CeremonialBeast>("PlowStrength");
        return m => m.Creature.HasPower<PlowPower>() ? s(m) : 0;      // 犁击：还挂着犁时才加
    }

    /// <summary>G05：给玩家上易伤的招式与层数（grep "Apply&lt;VulnerablePower&gt;" Models.Monsters/*.cs）。之后几步按易伤剩余层数乘倍率。</summary>
    static readonly Dictionary<(Type, string), Func<MonsterModel, float>> MoveDebuffs29 = new()
    {
        [(typeof(Flyconid), "VULNERABLE_SPORES_MOVE")] = N29(2),
        [(typeof(InfestedPrism), "PULSATE_MOVE")] = N29(1),
        [(typeof(Mawler), "ROAR_MOVE")] = N29(3),
        [(typeof(Ovicopter), "TENDERIZER_MOVE")] = N29(2),
        [(typeof(OwlMagistrate), "VERDICT")] = N29(4),
        [(typeof(Queen), "YOU_ARE_MINE_MOVE")] = N29(99),
        [(typeof(Queen), "DREAD_INITIAL_MOVE")] = N29(99),
        [(typeof(SoulFysh), "SCREAM_MOVE")] = Prop<SoulFysh>("ScreamMoveAmount"),
        [(typeof(SoulNexus), "DRAIN_LIFE_MOVE")] = N29(2),
        [(typeof(TerrorEel), "TERROR_MOVE")] = N29(99),
        [(typeof(TestSubject), "SKULL_BASH_MOVE")] = N29(1),
    };

    /// <summary>
    /// G05 睡眠类：乐加维林族母（沉睡，Models.Monsters/LagavulinMatriarch.cs:180-186）、沉睡甲虫（酣睡，SlumberingBeetle.cs:114-116）。
    /// 两种能力都在敌方回合末减一层、减到 0 醒来；下一招在玩家回合开始时按“还有没有这个能力”抽，所以第 k ≥ 1 步、层数 ≤ k 时走醒来的分支。
    /// </summary>
    static readonly (Type monster, string branch, Type power, string wake)[] Wake29 =
    {
        (typeof(LagavulinMatriarch), "SLEEP_BRANCH", typeof(AsleepPower), "SLASH_MOVE"),
        (typeof(SlumberingBeetle), "SNORE_NEXT", typeof(SlumberPower), "ROLL_OUT_MOVE"),
    };

    /// <summary>selfcheck 核对：MoveGains29、MoveDebuffs29 的（怪物类型，招式 ID），睡眠类的（怪物类型，醒来的招式 ID）与（怪物类型，条件分支 ID）。</summary>
    internal static IEnumerable<(Type monster, string move)> MoveGain29Keys => MoveGains29.Keys;
    internal static IEnumerable<(Type monster, string move)> MoveDebuff29Keys => MoveDebuffs29.Keys;
    internal static IEnumerable<(Type monster, string move)> Wake29Moves => Wake29.Select(w => (w.monster, w.wake));
    internal static IEnumerable<(Type monster, string branch)> Wake29Branches => Wake29.Select(w => (w.monster, w.branch));

    /// <summary>怪物行动状态机里的全部状态 ID（招式与分支；selfcheck 核对睡眠类的分支 ID）。照 Catalog.MoveKeys 生成一台状态机。</summary>
    internal static IEnumerable<string> StateIds29(MonsterModel m)
    {
        MonsterMoveStateMachine? sm = null;
        try { sm = (MonsterMoveStateMachine)GenMoves29!.Invoke(m, null)!; }
        catch { try { sm = (MonsterMoveStateMachine)GenMoves29!.Invoke(m.ToMutable(), null)!; } catch { } }
        return sm == null ? Enumerable.Empty<string>() : sm.States.Keys;
    }
    static readonly MethodInfo? GenMoves29 = GameCompat.Method(typeof(MonsterModel), "GenerateMoveStateMachine", BindingFlags.NonPublic | BindingFlags.Instance, "怪物的招式表（格式 29 睡眠类的分支，selfcheck）");

    static readonly FieldInfo? RitualJustField = GameCompat.Field(typeof(RitualPower), "_wasJustAppliedByEnemy", "仪式：本敌方回合刚挂上、回合末不加力量（格式 29 招式推演，G05）");

    /// <summary>这一招给自己、给每个队友加的力量（包 1 的 G29 用，MoveGains29 的 Self、Ally；表里没有为 0）。</summary>
    static (float self, float ally) MoveGain29(MonsterModel m, string? stateId)
    {
        try
        {
            if (Lookup29(MoveGains29, m, stateId, out var g)) return (g.Self(m), g.Ally(m));
        }
        catch { }
        return (0, 0);
    }

    static MoveGainRow29? MoveGains29Of(MonsterModel m, MoveState s) => Lookup29(MoveGains29, m, s.StateId, out var g) ? g : null;

    /// <summary>按（怪物类型，招式 ID）查表，怪物类型沿基类往上找（千足虫的三种节段共用基类 DecimillipedeSegment 的招式）。</summary>
    static bool Lookup29<T>(Dictionary<(Type, string), T> table, MonsterModel m, string? stateId, out T v)
    {
        v = default!;
        if (stateId == null) return false;
        for (var t = m.GetType(); t != null && t != typeof(MonsterModel); t = t.BaseType)
            if (table.TryGetValue((t, stateId), out v!)) return true;
        return false;
    }

    // ---------------------------------------------------------------- 计划（G05、G24）

    /// <summary>一个怪物的招式计划：第 k 步的各个候选（招式、概率、是否经过条件分支），同 AddMoves 的推法；随机分支那一步之后不再往下。Order 为出手顺序（cs.Enemies 里的下标）。</summary>
    sealed class Plan29
    {
        public readonly List<List<(MoveState s, double p, bool cond)>> Steps = new();
        public int Order = -1;
    }

    /// <summary>格式 29：这一帧每个会输出招式的怪物的计划（AddCombat 在逐个生物输出之前算好；AddMoves 按它输出，推演按它找队友的招式）。</summary>
    Dictionary<Creature, Plan29>? _plans;

    /// <summary>
    /// 格式 29（G05）：逐个生物输出之前，先给每个活着或复活中的敌人排好第 0–3 步的计划。推法与 AddMoves 相同（Resolve、临时追加出招记录、推完原样删掉），
    /// 只是睡眠类在第 k ≥ 1 步、层数 ≤ k 时按醒来的分支走（Wake29）。
    /// </summary>
    void PlanMoves(ICombatState cs)
    {
        _plans = new(ReferenceEqualityComparer.Instance);
        try
        {
            var side = RunSession.Me.Creature.Side;
            var order = cs.Enemies.ToList();
            foreach (var c in cs.Creatures)
            {
                if (c.Monster is not { } m || c.IsPlayer || c.Side == side) continue;      // 我方宠物不输出招式
                if (!c.IsAlive && !Reviving29(c)) continue;
                if (Plan1(m, c) is { } p) { p.Order = order.IndexOf(c); _plans[c] = p; }
            }
        }
        catch { }
    }

    Plan29? Plan1(MonsterModel m, Creature c)
    {
        var sm = m.MoveStateMachine;
        if (NoMoves || sm == null || m.NextMove is not { StateId: not null } cur) return null;
        var plan = new Plan29();
        plan.Steps.Add(new() { (cur, 1.0, false) });
        var log = sm.StateLog;
        int added = 0;
        MemoPause();
        try
        {
            var move = cur;
            bool cond = false;
            for (int k = 1; k <= MovesAhead; k++)
            {
                var outs = new List<(MoveState s, double p)>();
                if (!Resolve29(sm, c, sm.States[move.GetNextState(c, null!)], 1.0, ref cond, outs, 0, k) || outs.Count == 0) break;
                bool cd = cond;
                plan.Steps.Add(outs.Select(o => (o.s, o.p, cd)).ToList());
                if (outs.Count > 1) break;
                move = outs[0].s;
                log.Add(move); added++;
            }
        }
        catch { }
        finally { log.RemoveRange(log.Count - added, added); MemoResume(); }
        return plan;
    }

    /// <summary>同 Resolve，加睡眠类醒来那一步（Wake29）。step 为正在推的第几步。</summary>
    static bool Resolve29(MonsterMoveStateMachine sm, Creature c, MonsterState st, double p, ref bool cond, List<(MoveState, double)> outs, int depth, int step)
    {
        if (depth > 4) return false;
        switch (st)
        {
            case MoveState ms:
                outs.Add((ms, p)); return true;
            case ConditionalBranchState cb:
                cond = true;
                string? next = WakeOf(c, cb, step) ?? cb.GetNextState(c, null!);
                return next != null && sm.States.TryGetValue(next, out var ns) && Resolve29(sm, c, ns, p, ref cond, outs, depth + 1, step);
            case RandomBranchState rb:
                var ws = rb.States.Select(w => (w.stateId, wt: (double)(float)StateWeight.Invoke(null, new object[] { w, c })!)).Where(x => x.wt > 0).ToList();
                double sum = ws.Sum(x => x.wt);
                if (sum <= 0) return false;
                foreach (var (sid, wt) in ws)
                    if (!sm.States.TryGetValue(sid, out var bs) || !Resolve29(sm, c, bs, p * wt / sum, ref cond, outs, depth + 1, step)) return false;
                return true;
            default:
                return false;
        }
    }

    static string? WakeOf(Creature c, ConditionalBranchState cb, int step)
    {
        if (step < 1 || c.Monster is not { } m) return null;
        foreach (var w in Wake29)
            if (w.monster.IsInstanceOfType(m) && w.branch == cb.Id && c.Powers.FirstOrDefault(x => x.GetType() == w.power) is { } pw && pw.Amount <= step)
                return w.wake;
        return null;
    }

    /// <summary>格式 29：按计划输出招式记录（同 AddMoves：第 0 步，之后逐步输出、确定的一步临时追加进出招记录，随机分支那一步输出各候选后停）。</summary>
    void AddMoves29(MonsterModel m, Creature c, int tok, IEnumerable<Creature> targets, Plan29 plan)
    {
        var sm = m.MoveStateMachine!;
        EmitMove(m, c, tok, targets, plan.Steps[0][0].s, 0, 1f, false);
        var log = sm.StateLog;
        int added = 0;
        MemoPause();
        try
        {
            for (int k = 1; k < plan.Steps.Count; k++)
            {
                var outs = plan.Steps[k];
                if (outs.Count > 1)
                {
                    foreach (var (s, p, cd) in outs) EmitMove(m, c, tok, targets, s, k, (float)p, cd);
                    break;
                }
                EmitMove(m, c, tok, targets, outs[0].s, k, 1f, outs[0].cond);
                log.Add(outs[0].s); added++;
            }
        }
        catch { }
        finally { log.RemoveRange(log.Count - added, added); MemoResume(); }
    }

    // ---------------------------------------------------------------- 推演（G05、G26）

    static bool IsAttack29(MoveState s) => s.Intents.Any(i => i is AttackIntent);

    /// <summary>
    /// G05：招式记录 22–24（第 k 步这一招推演后的单段伤害、段数、各攻击意图单段 × 段数之和）。按已知变化重算：
    /// 力量（本怪第 0…k−1 步的自加与仪式；队友第 j &lt; k 步、出手在它前面的队友第 k 步给的力量；仪式、领地、高压每个敌方回合末加的力量；玩家的硫磺石；
    /// 临时减力量在 k ≥ 1 时恢复）、活力（下一次攻击用掉）、怪身上的虚弱与玩家身上的易伤、无实体到期、怪物新给玩家上的易伤（MoveDebuffs29）、
    /// 实验体多次爪击的段数、瀑布巨兽倒地后“爆炸”的伤害（蒸汽喷发的层数，G26）。
    /// 重算照游戏的伤害钩子（MoveDmgRecalc）；同一意图不加任何变化的重算值与招式 5 对不上时返回 null（照抄 5、4、6）。只读。
    /// </summary>
    (float one, float hits, float total)? Move29(MonsterModel m, Creature c, MoveState s, int k, IEnumerable<Creature> targets)
    {
        var atks = s.Intents.OfType<AttackIntent>().ToList();
        if (atks.Count == 0) return (0, 0, 0);
        if (_plans == null || !_plans.TryGetValue(c, out var plan)) return null;
        var cs = c.CombatState!;
        var player = RunSession.Me.Creature;
        // 本怪第 0…k−1 步（推到第 k 步，前面每一步都只有一招）
        var mine = new List<MoveState>();
        for (int j = 0; j < k; j++)
        {
            if (j >= plan.Steps.Count || plan.Steps[j].Count != 1) return null;
            mine.Add(plan.Steps[j][0].s);
        }
        bool Before(Plan29 op) => op.Order >= 0 && plan.Order >= 0 && op.Order < plan.Order;
        decimal str = 0;
        for (int j = 0; j < k; j++)
            if (MoveGains29Of(m, mine[j]) is { } g) str += (decimal)g.Self(m) + (decimal)g.Ritual(m) * Math.Max(0, k - j - 1);
        // 队友给的力量：第 j < k 步，与出手在它前面的队友的第 k 步（随机分支那一步取各候选里最少的，即不管抽到哪一招都会加的）
        foreach (var (o, op) in _plans)
        {
            if (ReferenceEquals(o, c) || o.Monster is not { } om) continue;
            for (int j = 0; j <= k && j < op.Steps.Count; j++)
            {
                if (j == k && !Before(op)) continue;
                float least = float.MaxValue;
                foreach (var (st, _, _) in op.Steps[j]) least = Math.Min(least, MoveGains29Of(om, st) is { } g ? g.Ally(om) : 0);
                if (least != float.MaxValue) str += (decimal)least;
            }
        }
        var skip = new HashSet<AbstractModel>(ReferenceEqualityComparer.Instance);
        foreach (var pw in c.Powers)
        {
            switch (pw)
            {
                case RitualPower:
                    bool just = RitualJustField?.GetValue(pw) is true;
                    str += pw.Amount * Math.Max(0, k - (just ? 1 : 0)); break;
                case TerritorialPower or HighVoltagePower:
                    str += pw.Amount * k; break;
                case TemporaryStrengthPower when k >= 1:
                    str += pw.Type == PowerType.Debuff ? pw.Amount : -pw.Amount; break;     // 临时力量在敌方回合末撤掉
                case WeakPower when k >= 1 && pw.Amount - k + (pw.SkipNextDurationTick ? 1 : 0) <= 0:
                    skip.Add(pw); break;
                case VigorPower when k >= 1:
                    skip.Add(pw); break;        // 活力另算（下面）
            }
        }
        // 玩家的硫磺石：每个玩家回合开始给所有活着的敌人加力量（Brimstone.AfterSideTurnStart），到第 k 步之前有 k 次
        if (k >= 1 && c.IsAlive && RunSession.Me.Relics.FirstOrDefault(r => r is MegaCrit.Sts2.Core.Models.Relics.Brimstone && RelicLive(r)) is { } brim)
            str += k * brim.DynamicVars["EnemyStrength"].BaseValue;
        if (k >= 1)
        {
            decimal vig = c.GetPower<VigorPower>()?.Amount ?? 0;
            for (int j = 0; j < k; j++)
            {
                if (IsAttack29(mine[j])) vig = 0;                                   // 攻击用掉活力
                if (MoveGains29Of(m, mine[j]) is { } g) vig += (decimal)g.Vigor(m);         // 同一招里先攻击、后挂活力（骇鳗痛打）
            }
            str += vig;
        }
        // 玩家身上的易伤：敌方回合末减一层；敌方回合里新上的这回合末不减（SkipNextDurationTick）。玩家有人工制品时不算新上的
        decimal extra = 1m;
        var vp = player.GetPower<VulnerablePower>();
        int v = vp?.Amount ?? 0; bool sk = vp?.SkipNextDurationTick ?? false;
        bool art = player.HasPower<ArtifactPower>();
        for (int t = 0; t <= k; t++)
        {
            int add = 0;
            if (!art)
                foreach (var (o, op) in _plans)
                {
                    if (t >= op.Steps.Count || o.Monster is not { } om) continue;
                    if (t == k && (ReferenceEquals(o, c) || !Before(op))) continue;
                    int least = int.MaxValue;
                    foreach (var (st, _, _) in op.Steps[t])
                        least = Math.Min(least, Lookup29(MoveDebuffs29, om, st.StateId, out var f) ? (int)f(om) : 0);
                    if (least != int.MaxValue && least > 0) add += least;
                }
            if (add > 0) { v += add; sk = true; }
            if (t == k) break;
            if (sk) sk = false; else if (v > 0) v--;
        }
        if (vp != null && v <= 0) skip.Add(vp);
        else if (vp == null && v > 0) extra = VulnMult29(player, c);
        // 玩家身上的无实体（伤害上限 1）也在敌方回合末减一层（IntangiblePower.AfterSideTurnEnd）
        if (k >= 1 && player.GetPower<IntangiblePower>() is { } ip && ip.Amount - k <= 0) skip.Add(ip);

        float one = 0, hits = 0, total = 0;
        foreach (var atk in atks)
        {
            if (atk.DamageCalc is not { } calc) return null;
            decimal b = calc();
            if (MoveDmgRecalc(cs, c, player, b, null, 1m) != AtkSingle(atk, targets, c)) return null;
            // G26：瀑布巨兽倒地后，“爆炸”的伤害在“即将爆炸”那一招才从蒸汽喷发的层数取出（WaterfallGiant.cs:217,296-300），之前按层数算
            if (m is WaterfallGiant && s.StateId == "EXPLODE_MOVE" && c.GetPower<SteamEruptionPower>() is { } se) b = se.Amount;
            int h = Math.Max(1, atk.Repeats);
            if (m is TestSubject && s.StateId == "MULTI_CLAW_MOVE") h += mine.Count(x => x.StateId == "MULTI_CLAW_MOVE");     // 多次爪击每用一次多一段（TestSubject.cs:208,313）
            one = MoveDmgRecalc(cs, c, player, b + str, skip, extra);
            hits = h;
            total += one * h;
        }
        return (one, hits, total);
    }

    /// <summary>怪物一次攻击的单段伤害（同 AttackIntent.GetSingleDamage → Hook.ModifyDamage 的 All：加法、乘法、晚乘法、上限，不小于 0，取整），
    /// 跳过 skip 里的监听者，乘法这一步另乘 extraMult。只读。</summary>
    static int MoveDmgRecalc(ICombatState cs, Creature dealer, Creature target, decimal n, HashSet<AbstractModel>? skip, decimal extraMult)
    {
        var props = ValueProp.Move;
        var ls = cs.RunState.IterateHookListeners(cs).Where(l => skip == null || !skip.Contains(l)).ToList();
        foreach (var l in ls) n += l.ModifyDamageAdditive(target, n, props, dealer, null, null);
        foreach (var l in ls) n *= l.ModifyDamageMultiplicative(target, n, props, dealer, null, null);
        n *= extraMult;
        foreach (var l in ls) n *= l.ModifyDamageMultiplicativeLate(target, n, props, dealer, null, null);
        foreach (var l in ls) { var x = l.ModifyDamageCap(target, props, dealer, null, null); if (n > x) n = x; } n = Math.Min(n, OldHtkCap(target));      // v0.114.0：难杀照旧口径封顶（见 Damage.cs OldHtkCap）
        return Math.Max(0, (int)Math.Max(0m, n));
    }

    /// <summary>玩家新挂上易伤时怪物的攻击乘的倍率（同 VulnerablePower.ModifyDamageMultiplicative：DamageIncrease 1.5，玩家身上有衰弱时再改；
    /// 纸蛙、残暴看攻击者的玩家与能力，怪物没有）。</summary>
    static decimal VulnMult29(Creature player, Creature dealer)
    {
        decimal num = ModelDb.Power<VulnerablePower>().DynamicVars["DamageIncrease"].BaseValue;
        if (player.GetPower<DebilitatePower>() is { } deb) num = deb.ModifyVulnerableMultiplier(player, num, ValueProp.Move, dealer, null);
        return num;
    }

    // ---------------------------------------------------------------- 生物记录的新列（G04、G24、G26、G21/G28/G70）

    /// <summary>G24：复活中——已死、仍在战斗里，身上的重新接上（千足虫）、适者生存（实验体）的内部数据 isReviving 为真，或幻影（IllusionPower.IsReviving）。</summary>
    static bool Reviving29(Creature c)
    {
        if (c.IsAlive || c.Monster == null) return false;
        try
        {
            foreach (var pw in c.Powers)
            {
                if (pw is ReattachPower or AdaptablePower && InternalFlag(pw, "isReviving")) return true;
                if (pw is IllusionPower ip && ip.IsReviving) return true;
            }
        }
        catch { }
        return false;
    }

    /// <summary>
    /// 格式 29 生物记录的新列（AddCreature 在 Emit 之前调；R3 规格 3.1）。敌人：22 是次要敌人（活着或复活中），23 打死它战斗当场结束，
    /// 24 复活中，25 下个敌方回合就复活，26 复活后的生命，27 瀑布巨兽倒地（打不死、下个敌方回合之后自爆；同时生物 0、1、17–19 记 0）。
    /// 玩家：28 现在结束回合预计失去的最大生命（纸片割伤），29 下个玩家回合开始时确定有的格挡（两项都由包 1 的回合预演算，见 _et29）。宠物全为 0。
    /// </summary>
    void Creature29(Creature c, ICombatState cs, bool ally, bool reviving)
    {
        try
        {
            if (c.IsPlayer)
            {
                _y[6] = _et29.MaxHpLoss; _y[7] = _et29.NextTurnBlock;
                return;
            }
            if (ally) return;
            _y[0] = c.IsSecondaryEnemy && (c.IsAlive || reviving) ? 1 : 0;
            _y[1] = c.IsAlive && c.IsPrimaryEnemy && CombatEndsWithout(cs, new[] { c }) ? 1 : 0;
            if (reviving)
            {
                _y[2] = 1;
                string? mv = c.Monster?.NextMove?.StateId;
                _y[3] = mv is "REATTACH_MOVE" or "RESPAWN_MOVE" or "REVIVE_MOVE" ? 1 : 0;
                _y[4] = ReviveHp29(c);
            }
            if (c.Monster is WaterfallGiant && c.HpDisplay == HpDisplay.InfiniteWithoutNumbers)
            {
                _f[0] = 0; _f[1] = 0; _f[17] = 0; _f[18] = 0; _f[19] = 0;
                _y[5] = 1;
            }
        }
        catch { }
    }

    /// <summary>G24 生物 26：复活后的生命。千足虫节段为重新接上的层数（ReattachPower.DoReattach 回这么多血），实验体为下一形态的生命，幻影为最大生命（回满）。</summary>
    static float ReviveHp29(Creature c)
    {
        if (c.GetPower<ReattachPower>() is { } ra) return ra.Amount;
        if (c.Monster is TestSubject ts && TestSubjectRespawns?.GetValue(ts) is int k)
        {
            int[] hp = { ts.FirstFormHp, ts.SecondFormHp, ts.ThirdFormHp };
            return k + 1 < hp.Length ? hp[k + 1] : 0;
        }
        if (c.HasPower<IllusionPower>()) return c.MaxHp;
        return 0;
    }
}
