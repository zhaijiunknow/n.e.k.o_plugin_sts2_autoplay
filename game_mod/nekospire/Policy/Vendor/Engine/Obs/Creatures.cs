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

// 生物记录（类型 2）与其能力记录、意图、招式预告（类型 10）。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    void AddCreature(Creature c, int slot, ICombatState cs)
    {
        Begin();
        _f[0] = c.CurrentHp; _f[1] = c.MaxHp; _f[2] = c.Block; _f[3] = c.IsAlive ? 1 : 0; _f[4] = IsHittableM(c) ? 1 : 0;
        _f[5] = c.IsPlayer ? 1 : 0;
        bool reviving = V29 && Reviving29(c);      // 格式 29（G24）：复活中的怪照活着的输出意图、招式、形态
        if (c.Monster is { } m && (c.IsAlive || reviving))
        {
            var targets = cs.PlayerCreatures;
            foreach (AbstractIntent intent in m.NextMove.Intents)
            {
                if (intent is AttackIntent atk)
                {
                    try { _f[6] = AtkSingle(atk, targets, c); _f[7] = Math.Max(1, atk.Repeats); _f[8] += AtkTotal(atk, targets, c); } catch { }
                }
                _f[9 + IntentFlag(intent.IntentType)] = 1;
            }
        }
        if (_endTurn != null && _endTurn.TryGetValue(c, out var et)) { _f[16] = et.a; _f[17] = et.b; _f[18] = et.c; _f[19] = et.d; }
        int id = c.Monster != null ? Vocab.Id(c.Monster) : c.Player != null ? Vocab.Id(c.Player.Character) : Vocab.Unknown;
        bool ally = c.IsPlayer || c.Side == RunSession.Me.Creature.Side;
        if (V29) Creature29(c, cs, ally, reviving);        // 格式 29：生物 22–29（Creatures29.cs）
        int tok = Emit(c, TokenKind.Creature, id, ally ? Zone.Ally : Zone.Enemy, -1, slot);
        int pwRank = 0;
        foreach (var pw in c.Powers)
        {
            Begin(); _f[0] = pw.Amount; _f[1] = pw.Type == PowerType.Debuff ? 1 : 0;
            try { _f[2] = pw.DisplayAmount; } catch { _f[2] = pw.Amount; }     // 版本 11：界面上显示的数（凋萎存在的倒数、懒惰已出牌数等，13 种能力与层数不同）
            // 版本 15：能力自己的前两个数值（悬浮提示里的数，同一种能力的不同实例可以不同：坚韧之环每回合给几点格挡、层数只是剩几回合）
            try { int k = 4; foreach (var (_, v) in pw.DynamicVars) { if (k > 5) break; _f[k++] = (float)v.BaseValue; } } catch { }
            // 第七份评审：6 本回合 / 本场已触发（能力自己记的“这回合已经用过”，如胆小 HasGainedBlockThisTurn；按名字含 ThisTurn、ThisCombat 的布尔属性与字段）
            _f[6] = PowerUsed(pw) ? 1 : 0;
            // 第十四份评审（决策记录 207）：迭代只在本回合第一次抽到状态牌时追加抽牌（游戏数战斗历史里本回合抽到的状态牌），
            // 6 记本回合已经抽到过状态牌，即再抽到也不会触发；先抽到状态牌再打出迭代同样算用掉，生成到手里的不算抽到
            if (pw is MegaCrit.Sts2.Core.Models.Powers.IterationPower && StatusDrawnThisTurn(c, cs)) _f[6] = 1;
            // 版本 17（第十五份评审）：7 这条能力在所属生物能力列表里的先后位置（0 起）。回合开始、结束等时机的能力按这个顺序结算，
            // 如热修复的临时集中排在吞噬暗影前面时，先撤掉临时集中再激发
            _f[7] = pwRank++;
            if (V20) PowerInternal(pw);
            // 格式 20：凯撒蟹的被包围与背刺（储君决策记录第 14 条，见 _surround）
            if (V20 && pw is MegaCrit.Sts2.Core.Models.Powers.SurroundedPower sp)
                _f[8] = sp.Facing == MegaCrit.Sts2.Core.Models.Powers.SurroundedPower.Direction.Left ? 1 : 0;
            else if (V20 && pw is MegaCrit.Sts2.Core.Models.Powers.BackAttackLeftPower or MegaCrit.Sts2.Core.Models.Powers.BackAttackRightPower
                     && RunSession.Me.Creature.GetPower<MegaCrit.Sts2.Core.Models.Powers.SurroundedPower>() is { } sur)
                _f[8] = Behind(sur.Facing, c) ? 1 : 0;
            // 格式 21：中毒 12 下一个敌方回合开始时毒实际让它掉多少血（见 PoisonNext），13 毒不毒得死（12 ≥ 当前生命）。敌方回合开始先结算毒，毒得死的敌人不会出手
            if (V21 && pw is MegaCrit.Sts2.Core.Models.Powers.PoisonPower pois)
            {
                _f[12] = PoisonNext(c, pois);
                _f[13] = _f[12] > 0 && _f[12] >= c.CurrentHp ? 1 : 0;
            }
            // 格式 22：灾厄 12 敌方回合结束时会不会被灾厄处决，13 离处决还差多少血（见 DoomInfo）
            if ((V22 || V24) && pw is MegaCrit.Sts2.Core.Models.Powers.DoomPower)      // 格式 24 也有（塔罗“死亡”）
                (_f[12], _f[13]) = DoomInfo(c, pw.Amount, ally);
            // 格式 24：扎穿 12 扎着几支箭，13 全部拔出还要打几段有力攻击（见 ImpaledInfo）
            if (V24 && pw is MegaCrit.Sts2.Core.Models.Powers.ImpaledPower imp)
                (_f[12], _f[13]) = ImpaledInfo(imp);
            // 格式 29（G44）：回声形态 22 本回合还剩几次翻倍（见 EchoLeft29）
            if (V29 && pw is MegaCrit.Sts2.Core.Models.Powers.EchoFormPower) _y[0] = EchoLeft29(pw);
            // 格式 22：我方的宠物（奥斯提）的能力放在我方区域（原来只有玩家自己的能力在我方区域，奥斯提的被当成敌方的）；v0.113.0 修正（F113）所有格式同（瓦皮、异鸟宝宝、小异鸟、佩尔的士兵）
            Emit(pw, TokenKind.Power, Vocab.Id(pw), c.IsPlayer || ((V22 || F113) && ally) ? Zone.Ally : Zone.Enemy, tok, 0);
        }
        // 格式 22：我方的宠物（奥斯提）由玩家的牌指挥，自己的“招式”只是占位（NOTHING_MOVE），不输出；F113 同（宠物从不抽招式，NextMove 停在 UNSET_MOVE，原来输出成词表外、头部 14 加 1）
        if (c.Monster is { } mm && (c.IsAlive || reviving) && !((V22 || F113) && ally))
        {
            // 格式 29：按 PlanMoves 排好的计划输出（同 AddMoves，加睡眠类醒来那一步、招式 22–24）；没有计划时（Ref 补的记录等）照旧
            if (V29 && _plans != null && _plans.TryGetValue(c, out var plan)) AddMoves29(mm, c, tok, cs.PlayerCreatures, plan);
            else AddMoves(mm, c, tok, cs.PlayerCreatures);
            AddForms(mm, c, tok);
        }
    }

    /// <summary>意图类型 → 7 个标志之一：攻击、增益、减益（含塞牌）、防御、治疗/召唤、眩晕/睡眠/逃跑、其他。</summary>
    static int IntentFlag(IntentType t) => t switch
    {
        IntentType.Attack or IntentType.DeathBlow => 0,
        IntentType.Buff => 1,
        IntentType.Debuff or IntentType.DebuffStrong or IntentType.CardDebuff or IntentType.StatusCard => 2,
        IntentType.Defend => 3,
        IntentType.Heal or IntentType.Summon => 4,
        IntentType.Stun or IntentType.Sleep or IntentType.Escape => 5,
        _ => 6,
    };

    const int MovesAhead = 3;
    /// <summary>核对用：STS2_NO_MOVES=1 时不输出招式预告，用来确认加不加预告，同一种子、同样的操作打出的对局完全一样。</summary>
    static readonly bool NoMoves = Environment.GetEnvironmentVariable("STS2_NO_MOVES") == "1";
    static readonly MethodInfo StateWeight = GameCompat.Method(typeof(RandomBranchState), "GetStateWeight", BindingFlags.NonPublic | BindingFlags.Static, "招式预告的随机分支权重")
        ?? throw new MissingMethodException("RandomBranchState.GetStateWeight");

    /// <summary>
    /// 招式预告（格式版本 3 起）：从怪物自己的行动状态机推出当前这一招之后的几招，挂在怪物记录下面。
    /// 当前这一招记为第 0 步；固定的下一招概率为 1，接着往后推，最多推 MovesAhead 步；
    /// 遇到随机分支，按游戏自己的权重函数（只看出招记录和权重，不碰随机数）给出各候选招式的概率，推到这里为止；
    /// 遇到条件分支，按当前局面判断，这一步及之后标“看条件”。不读随机数的抽签结果，所以不是预知。
    /// 推算中途要把假想出过的招式临时追加到出招记录里（权重函数按记录判断能否重复、冷却），推完原样删掉。
    /// </summary>
    void AddMoves(MonsterModel m, Creature c, int tok, IEnumerable<Creature> targets)
    {
        var sm = m.MoveStateMachine;
        if (NoMoves || sm == null || m.NextMove is not { StateId: not null } cur) return;
        EmitMove(m, c, tok, targets, cur, 0, 1f, false);
        var log = sm.StateLog;
        int added = 0;
        MemoPause();      // 下面会临时往出招记录里加假想的招式：期间不用、不记 Encode 的记忆（Memo.cs）
        try
        {
            var move = cur;
            bool cond = false;
            for (int k = 1; k <= MovesAhead; k++)
            {
                var outs = new List<(MoveState s, double p)>();
                if (!Resolve(sm, c, sm.States[move.GetNextState(c, null!)], 1.0, ref cond, outs, 0) || outs.Count == 0) break;
                if (outs.Count > 1)
                {
                    foreach (var (s, p) in outs) EmitMove(m, c, tok, targets, s, k, (float)p, cond);
                    break;
                }
                move = outs[0].s;
                EmitMove(m, c, tok, targets, move, k, 1f, cond);
                log.Add(move); added++;
            }
        }
        catch { }
        finally { log.RemoveRange(log.Count - added, added); MemoResume(); }
    }

    /// <summary>把一个状态解析成具体招式（及概率）；随机分支后不再往下推（返回多个候选，或只剩一个可选时照常继续）。</summary>
    static bool Resolve(MonsterMoveStateMachine sm, Creature c, MonsterState st, double p, ref bool cond, List<(MoveState, double)> outs, int depth)
    {
        if (depth > 4) return false;
        switch (st)
        {
            case MoveState ms:
                outs.Add((ms, p)); return true;
            case ConditionalBranchState cb:
                cond = true;
                string? next = cb.GetNextState(c, null!);
                return next != null && sm.States.TryGetValue(next, out var ns) && Resolve(sm, c, ns, p, ref cond, outs, depth + 1);
            case RandomBranchState rb:
                var ws = rb.States.Select(w => (w.stateId, wt: (double)(float)StateWeight.Invoke(null, new object[] { w, c })!)).Where(x => x.wt > 0).ToList();
                double sum = ws.Sum(x => x.wt);
                if (sum <= 0) return false;
                foreach (var (sid, wt) in ws)
                    if (!sm.States.TryGetValue(sid, out var bs) || !Resolve(sm, c, bs, p * wt / sum, ref cond, outs, depth + 1)) return false;
                return true;
            default:
                return false;
        }
    }

    static Func<MonsterModel, float> Prop<T>(string name)
    {
        // 游戏更新后属性改名时不让整个观测编码失效（静态初始化抛异常会让每一帧都出错）：报一次错，这一项填 0
        var get = GameCompat.Getter(typeof(T), name, "招式给的力量、格挡（MoveGains）");
        if (get == null) { Console.Error.WriteLine($"MoveGains：{typeof(T).Name} 没有属性 {name}，这一项填 0"); return _ => 0; }
        return m => Convert.ToSingle(get.Invoke(m, null));
    }
    static Func<MonsterModel, float> Num(float v) => _ => v;
    static readonly Func<MonsterModel, float> None = _ => 0;

    /// <summary>
    /// 版本 12：招式记录 15–17 为这一招给自己加的力量、给每个队友加的力量、自己获得的格挡。游戏界面不显示这些数（增益、防御意图没有数字），
    /// 但都是招式代码里固定的（同招式预告，相当于老玩家背下来的数）。目前只覆盖第二、三幕的 Boss（死亡的大头）；
    /// 随进阶或局面变化的按游戏代码里的属性现取（永恒玻璃“愈演愈烈”每用一次多 1 点，取的是现在的值，三步之后的那一次会少算 1），
    /// 写死的数和游戏 0.111.0 的代码核对过（tools/check_obs_v12.py 按实际出招核对）。
    /// </summary>
    /// <summary>
    /// v0.113.0 修正（F113）：敌人在敌方回合往玩家手里塞牌的招式，塞几张（头部 32、33 用）。游戏的意图只显示“塞状态牌”和张数，不说塞到哪；
    /// 塞进手牌的只有这两招（CardPileCmd.AddToCombatAndPreview 的 PileType.Hand）：螨虫“毒素”2 张毒素、机甲骑士“喷火”4 张灼伤。
    /// 咀嚼者的晕眩等塞进弃牌堆、抽牌堆的不算（不改下回合手牌的张数）。
    /// </summary>
    static readonly Dictionary<(Type, string), Func<MonsterModel, int>> HandAdds = new()
    {
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.Myte), "TOXIC_MOVE")] = _ => 2,
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.MechaKnight), "FLAMETHROWER_MOVE")] = _ => 4,
    };
    /// <summary>HandAdds 里写死的（怪物类型，招式 ID），selfcheck 核对。</summary>
    internal static IEnumerable<(Type monster, string move)> HandAddKeys => HandAdds.Keys;
    /// <summary>这个敌人这一招往玩家手里塞几张牌（见 HandAdds；不塞为 0）。</summary>
    static int HandAddsOf(MonsterModel m)
    {
        try { return m.NextMove is MoveState mv && HandAdds.TryGetValue((m.GetType(), mv.StateId), out var f) ? f(m) : 0; }
        catch { return 0; }
    }
    /// <summary>F113：回合结束预演算出的、敌方回合会往玩家手里塞的牌数（会出手的敌人才算；FillHeader 用）。</summary>
    static int HandShoved;

    /// <summary>MoveGains 里写死的（怪物类型，招式 ID）：selfcheck 核对这些招式在新版游戏里还在不在（招式改名时这里会静默失配）。</summary>
    internal static IEnumerable<(Type monster, string move)> MoveGainKeys => MoveGains.Keys;

    static readonly Dictionary<(Type, string), (Func<MonsterModel, float> self, Func<MonsterModel, float> ally, Func<MonsterModel, float> block)> MoveGains = new()
    {
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.Aeonglass), "EBB_MOVE")] = (None, None, Prop<MegaCrit.Sts2.Core.Models.Monsters.Aeonglass>("EbbBlock")),
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.Aeonglass), "INCREASING_INTENSITY_MOVE")] = (Prop<MegaCrit.Sts2.Core.Models.Monsters.Aeonglass>("IncreasingIntensityTotalStrength"), None, None),
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.Queen), "BURN_BRIGHT_FOR_ME_MOVE")] = (None, Num(1), Num(20)),
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.Queen), "ENRAGE_MOVE")] = (Num(2), None, None),
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.TestSubject), "BURNING_GROWL_MOVE")] = (Prop<MegaCrit.Sts2.Core.Models.Monsters.TestSubject>("BurningGrowlStrengthGain"), None, None),
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.KnowledgeDemon), "PONDER_MOVE")] = (Prop<MegaCrit.Sts2.Core.Models.Monsters.KnowledgeDemon>("PonderStrength"), None, None),
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.TheInsatiable), "SALIVATE_MOVE")] = (Prop<MegaCrit.Sts2.Core.Models.Monsters.TheInsatiable>("SalivateStrength"), None, None),
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.Crusher), "ADAPT_MOVE")] = (Prop<MegaCrit.Sts2.Core.Models.Monsters.Crusher>("AdaptStrengthGain"), None, None),
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.Crusher), "GUARDED_STRIKE_MOVE")] = (None, None, Num(18)),
        [(typeof(MegaCrit.Sts2.Core.Models.Monsters.Rocket), "CHARGE_UP_MOVE")] = (Prop<MegaCrit.Sts2.Core.Models.Monsters.Rocket>("ChargeUpStrengthGain"), None, None),
    };

    /// <summary>
    /// 一条招式记录：0 第几步（0 为当前这一招），1 概率，2 是否经过条件分支，3 单次基准伤害（招式本身的数值，未计力量、虚弱、易伤等），
    /// 4 次数，5 按当前状态算的单次伤害，6 按当前状态算的总伤害，7–13 意图标志（同生物记录 9–15），14 塞几张牌（版本 11），
    /// 15–17 给自己加的力量、给每个队友加的力量、获得的格挡（版本 12，见 MoveGains）。
    /// </summary>
    void EmitMove(MonsterModel m, Creature c, int tok, IEnumerable<Creature> targets, MoveState s, int step, float prob, bool cond)
    {
        Begin();
        _f[0] = step; _f[1] = prob; _f[2] = cond ? 1 : 0;
        foreach (AbstractIntent intent in s.Intents)
        {
            if (intent is AttackIntent atk)
            {
                try
                {
                    _f[3] = atk.DamageCalc != null ? (float)atk.DamageCalc() : 0; _f[4] = Math.Max(1, atk.Repeats);
                    _f[5] = AtkSingle(atk, targets, c); _f[6] += AtkTotal(atk, targets, c);
                }
                catch { }
            }
            if (intent is StatusIntent si) _f[14] += si.CardCount;     // 版本 11：往牌堆里塞几张牌（界面上显示）
            _f[7 + IntentFlag(intent.IntentType)] = 1;
        }
        if (MoveGains.TryGetValue((m.GetType(), s.StateId), out var g)) { try { _f[15] = g.self(m); _f[16] = g.ally(m); _f[17] = g.block(m); } catch { } }
        // 版本 11 特权：第 1 步遇到随机分支时，按复制的随机数流推演出的实际抽签结果（只给价值网络；局面到时可能变，是推演不是保证）
        if (step == 1 && prob < 1f && _nextRoll != null && _nextRoll.TryGetValue(m, out var rolled)) { _p[0] = rolled == s.StateId ? 1 : 0; _p[1] = 1; }
        // 格式 29（G05）：22–24 推演后的单段伤害、段数、总伤害（Move29；算不了时照抄 5、4、6）
        if (V29)
        {
            (float, float, float)? pj = null;
            try { pj = Move29(m, c, s, step, targets); } catch { }
            (_y[0], _y[1], _y[2]) = pj ?? (_f[5], _f[4], _f[6]);
        }
        // F113：通用眩晕状态 STUNNED（Creature.StunInternal 临时插的招式，任何怪物都可能有；两版都不在词表里）照旧记 <unk>、不记进词表外计数，眩晕本身在意图标志 5 里
        // 格式 29（G24）：幻影复活的 REVIVE_MOVE（IllusionPower.AfterDeath 运行时新建，不在词表）同样记 <unk>、不计词表外
        Emit(null, TokenKind.Move, (F113 && s.StateId == "STUNNED") || (V29 && s.StateId == "REVIVE_MOVE") ? Vocab.IdQuiet(Vocab.Move(m, s.StateId)) : Vocab.Id(Vocab.Move(m, s.StateId)), Zone.Enemy, tok, 0);
    }
}
