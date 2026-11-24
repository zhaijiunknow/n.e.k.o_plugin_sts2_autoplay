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

/// <summary>实体记录的类型（导出给训练端，数值只追加不改）。</summary>
public enum TokenKind { Pad = 0, Global = 1, Creature = 2, Power = 3, Card = 4, Relic = 5, Potion = 6, Orb = 7, MapNode = 8, Option = 9, Move = 10, SphereCell = 11, SelectPrompt = 12, SelectedCard = 13, RouteSummary = 14, CombatStat = 15, EncounterPool = 16, FutureContent = 17, Oracle = 20 }

/// <summary>实体所在区域。</summary>
public enum Zone { None = 0, Ally = 1, Enemy = 2, Hand = 3, Draw = 4, Discard = 5, Exhaust = 6, Deck = 7, Offer = 8, Play = 9, Pack1 = 10, Pack2 = 11, UpgradePreview = 12, RewardGroup1 = 13, RewardGroup2 = 14, RewardGroup3 = 15,
    /// <summary>格式 20：玩家知道位置的抽牌堆第 1、2 张，第 3 张及以后（见 Observation.DrawTop）。</summary>
    DrawTop1 = 16, DrawTop2 = 17, DrawTop3 = 18 }

/// <summary>
/// 观测编码：当前局面 + 候选列表 → 定长数组。格式说明见 观测格式.md，改动布局时同时改 FormatVersion 和文档。
/// 公开观测不含隐藏信息：抽牌堆按卡牌 ID 排序输出，真实顺序只出现在“特权特征”里（仅供训练期的价值网络使用）。
/// </summary>
public sealed partial class Observation
{
    /// <summary>战斗模式连打练习已经开打的第几场（RunSession 维护；实机桥接不用，恒为 0）。</summary>
    public static int Stage;
    /// <summary>战斗模式的 Boss 练习（版本 15，RunSession 维护；实机桥接不用，恒为 0）：本幕已经打赢的第一个 Boss（第二个 Boss 单打、
    /// 连打的第二场）与打赢这一场后还要接着打的 Boss（连打的第一场），词表 ID，没有为 0。</summary>
    public static int BossPrev, BossNext;
    /// <summary>地图决策时给每个现在可前往的节点加一条路线摘要（格式版本 7 起；整局 reset 带 "route_summary":true 时开，关时与版本 6 的输出相同）。</summary>
    public static bool RouteSummary;
    /// <summary>
    /// 特权特征（版本 11 起）：只有 reset 带 "privileged":true 时才输出，否则所有记录的特权特征一律为 0（Emit 统一处理）。
    /// 训练采集请求它，供价值网络用；评测、实机、搜索不请求，决策时拿不到任何随机数结果（抽牌顺序、预抽的遭遇、怪物下一次抽签）。
    /// </summary>
    public static bool Privileged;
    /// <summary>
    /// 整局预生成内容（决策记录 259）：reset 同时带 "privileged":true 与 "future_content":true 时，整局模式战斗之外的帧多出类型 17 的记录
    /// （EmitFuture），只给价值头；不带时与原来逐字节相同，格式版本不变。
    /// </summary>
    public static bool FutureContent;
    public const int TokInts = 5, TokFeats = 20, TokPriv = 2, CandInts = 5, CandFeats = 12, HeaderInts = 34;

    public readonly int[] Header = new int[HeaderInts];
    // 初始容量按第三幕 Boss 战的一帧（约 70 条记录、7 个候选）留够，免得逐次扩容（提速；容量不影响内容）
    public readonly List<int> TI = new(128 * TokInts);
    public readonly List<float> TF = new(128 * TokFeats27), TP = new(128 * TokPriv);
    public readonly List<int> CI = new(16 * CandInts);
    public readonly List<float> CF = new(16 * 14);
    public readonly List<string> Labels = new();
    /// <summary>附加信息（JSON 文本，见 Snapshot.Extra）：有就在文字之后再写一段 int32 字节数 + 内容，没有就不写，旧的读取端照常工作。</summary>
    public string? Extra;
    public int NumTokens => TI.Count / TokInts;
    public int NumCands => CI.Count / CandInts;

    readonly Dictionary<object, int> _index = new(128, ReferenceEqualityComparer.Instance);
    readonly Dictionary<MapPoint, int> _route = new();     // 现在可前往的节点 → 它的路线摘要记录
    float[] _f = new float[TokFeats];
    float[] _p = new float[TokPriv];
    /// <summary>格式 27 加的记录特征 20、21（V27 时 Emit 接在 _f 后面写出；手牌记录的标黄、标红，见 AddCard）。</summary>
    readonly float[] _x = new float[TokFeats27 - TokFeats];
    /// <summary>格式 29 加的记录特征 22–29（V29 时 Emit 接在 _x 后面写出；各记录类型的含义见 R3-格式29规格.md）。</summary>
    readonly float[] _y = new float[TokFeats29 - TokFeats27];
    /// <summary>格式 29 加的候选特征 14–19（每个候选开头清零，V29 时接在 12–13 后面写出；战斗内、战斗外的含义见 R3-格式29规格.md）。</summary>
    readonly float[] _cy = new float[CandFeats29 - CandFeats26];

    public static Observation Build(RunSession run, Decision d, bool labels)
    {
        var o = new Observation();
        o.Encode(run, d, labels);
        return o;
    }

    /// <summary>这一帧滑脚木桥的重抽候选池（版本 18；不在这个事件里为 null）。</summary>
    HashSet<CardModel>? _bridgePool;

    void Encode(RunSession run, Decision d, bool labels)
    {
        // 本次 Encode 的记忆化（Memo.cs）：开头启用、结尾清空，不跨帧
        MemoBegin();
        try { EncodeBody(run, d, labels); }
        finally { MemoEnd(); }
    }

    void EncodeBody(RunSession run, Decision d, bool labels)
    {
        var s = RunSession.State;
        var me = RunSession.Me;
        int fmt = FormatOverride ?? FormatFor(me.Character);
        V20 = fmt >= 20; V21 = fmt >= 21; V22 = fmt is 22 or 28 or 29; V23 = fmt is 23 or 24 or 25 or 26 or 27 or 28 or 29; V24 = fmt is 24 or 25 or 26 or 27 or 28 or 29; V25 = fmt is 25 or 26 or 27 or 28 or 29; V26 = fmt is 26 or 27 or 28 or 29; V27 = fmt is 27 or 28 or 29;
        V29 = fmt >= 29;    // 格式 29（Format.cs FormatAll）：格式 28 的全部内容加本次修正
        F113 = fmt is 25 or 26 or 27 or 28 or 29 || (fmt != 24 && !NoFix113);     // v0.113.0 的观测修正：格式 24 冻结，其余都打开（见 Format.cs）     // 格式 23 由 21 分出，不含 22；格式 24 含 23，格式 25 含 24（见 Format.cs）
        bool inCombat = CombatManager.Instance.IsInProgress && me.PlayerCombatState != null;
        _bridgePool =!inCombat && d.Kind == DecisionKind.Event ? BridgePool(me) : null;

        int globTok = AddGlobal(s, me, d, inCombat);
        // 生物、能力、充能球、战斗中的牌
        if (inCombat) AddCombat(me);
        else AddOutOfCombat(me);

        foreach (var r in me.Relics) AddRelic(r, Zone.Ally);
        var slots = me.PotionSlots;
        for (int i = 0; i < slots.Count; i++)
            if (slots[i] is { } p) AddPotion(p, Zone.Ally, i);

        if (d.Kind == DecisionKind.Map) AddMap(s);
        else if (RouteSummary && !inCombat) AddRoutesAhead(s);     // 版本 13：战斗外的其他决策也给接下来可前往节点的路线摘要
        if (d.Kind == DecisionKind.Minigame && FakeScreens.CrystalSphere is { } sphere) AddCrystalSphere(sphere);
        if (d.Kind == DecisionKind.CardSelect && run.Selecting is { } pc) AddSelectPrompt(pc, run.SelectedIndices);

        // 看得到但不是候选的对象
        foreach (var (obj, zone, price) in d.Shown)
        {
            if (_index.ContainsKey(obj)) continue;
            switch (obj)
            {
                case CardModel c: AddCard(c, zone); break;
                case PotionReward { Potion: { } pot } pr: AddPotion(pot, zone, -1); _index[pr] = NumTokens - 1; break;
                case PotionModel p: AddPotion(p, zone, -1, price); break;
                default: Ref(obj); break;
            }
        }
        // 版本 19（第十七份外部评审，决策记录 227）：商店里买不起的商品（买得起的是候选）。每件一条能力类记录，挂在全局记录上，
        // 词表 ID 为商品本身（卡牌、遗物、药水；删牌为 SHOP_REMOVAL），0 价格，1 还差多少金币，2 类别（1 卡牌、2 遗物、3 药水、4 删牌），
        // 17 为 1（训练端按它找出这类记录，乘清零的门控）
        foreach (var (obj, price) in d.Locked)
        {
            Begin(); _f[0] = price; _f[1] = Math.Max(0, price - me.Gold); _f[17] = 1;
            _f[2] = obj switch { CardModel => 1, RelicModel => 2, PotionModel => 3, _ => 4 };
            int lid = obj switch { CardModel c => Vocab.Id(c), RelicModel r => Vocab.Id(r), PotionModel p => Vocab.Id(p), _ => Vocab.Id(Vocab.ShopRemoval) };
            Emit(null, TokenKind.Power, lid, Zone.Offer, globTok, 0);
        }

        _sel = run.Selecting;       // 格式 29：选牌候选用（V29Common.cs）
        AddChoices(d, me, inCombat, labels);
        if (!run.CombatOnly) EncounterPool(s);
        if (Privileged && FutureContent && !run.CombatOnly && !inCombat && d.Kind is not (DecisionKind.Combat or DecisionKind.GameOver or DecisionKind.Stuck)) EmitFuture(s);
        if (Privileged && Oracle && !run.CombatOnly && !inCombat && d.Kind is not (DecisionKind.Combat or DecisionKind.GameOver or DecisionKind.Stuck)) EmitOracle(s, me);     // 预知记录（决策记录 280）
        FillHeader(run, d, s, me, inCombat, fmt);
        V20 = V21 = V22 = V23 = V24 = V25 = V26 = V27 = V29 = F113 = false;
        _sel = null;
    }

    void Begin() { Array.Clear(_f); Array.Clear(_p); Array.Clear(_x); Array.Clear(_y); }

    /// <summary>全局记录（类型 1）：幕、层数、进阶、金币、生命、能量、星、回合、球位、药水栏、牌组张数、房间、问号房与药水几率；特权的下一场遭遇。返回它的下标。</summary>
    int AddGlobal(RunState s, Player me, Decision d, bool inCombat)
    {
        // 全局
        Begin();
        _f[0] = s.CurrentActIndex; _f[1] = s.TotalFloor; _f[2] = s.AscensionLevel; _f[3] = me.Gold;
        _f[4] = me.Creature.CurrentHp; _f[5] = me.Creature.MaxHp;
        if (me.PlayerCombatState is { } pcs0 && inCombat)
        {
            _f[6] = pcs0.Energy; _f[7] = pcs0.MaxEnergy; _f[8] = pcs0.Stars; _f[9] = pcs0.TurnNumber; _f[10] = pcs0.OrbQueue.Capacity;
            if (V29) _y[0] = NextEnergy29(me, pcs0, me.Creature.CombatState!);     // 格式 29（G21/G72）：现在结束回合，下个玩家回合开始时的能量（Run29.cs）
        }
        else if (V29) _y[1] = MaxEnergyOut29(me);      // 格式 29（G62 J5）：战斗外的能量上限（Run29.cs）
        _f[11] = me.PotionSlots.Count; _f[12] = me.Deck.Cards.Count; _f[13] = inCombat ? 1 : 0;
        _f[14] = (int)(s.CurrentRoom?.RoomType ?? 0); _f[15] = d.Selected;
        // 版本 9：问号房此刻遇到普通战、宝箱、商店的几率（其余为事件；游戏不显示，但规则公开、可按经历推算），药水掉落几率
        try
        {
            var uo = s.Odds.UnknownMapPoint;
            _f[16] = Math.Max(0, uo.MonsterOdds); _f[17] = Math.Max(0, uo.TreasureOdds); _f[18] = Math.Max(0, uo.ShopOdds);
            _f[19] = me.PlayerOdds.PotionReward.CurrentValue;
        }
        catch { }
        if (Privileged)     // 版本 11 特权：本幕开局时预抽好的下一场普通战、精英战（遭遇词表 ID；游戏不显示）
        {
            try
            {
                if (RoomsField.GetValue(s.Act) is MegaCrit.Sts2.Core.Rooms.RoomSet rs)
                {
                    if (rs.normalEncounters.Count > 0) _p[0] = Vocab.Id(rs.NextNormalEncounter);
                    if (rs.eliteEncounters.Count > 0) _p[1] = Vocab.Id(rs.NextEliteEncounter);
                }
            }
            catch { }
        }
        return Emit(null, TokenKind.Global, Vocab.Id(me.Character), Zone.None, -1, Vocab.Id(s.Act));
    }

    /// <summary>战斗中：生物与能力、招式（先算回合结束预演）、战斗计数、充能球、手牌、抽牌堆（已知顶部单列，其余按 ID 排序）、弃牌堆、消耗堆、打出区。</summary>
    void AddCombat(Player me)
    {
        var cs = me.Creature.CombatState!;
        int slot = 0;
        _sureHits = F113 ? new() : null;
        Et29Last = default;
        _endTurn = EndTurnPreview(cs, me, out _worstLoss, out _luckyKills, out _lossAfter, out _devourer, out _sureHp, sureHitsOut: _sureHits);
        _et29 = Et29Last;       // 格式 29：主调用的拆分输出（V29Common.cs；之后的转身、无额外回合两次调用会覆盖 Et29Last）
        // 格式 20：凯撒蟹战里（玩家身上有被包围）另按转身之后的朝向算一份，给会让玩家转身的候选用（见 SurroundTurns）
        _surround = V20 ? me.Creature.GetPower<MegaCrit.Sts2.Core.Models.Powers.SurroundedPower>() : null;
        _lossAfterFlip = _surround != null ? FlippedLoss(cs, me, _surround) : null;
        // F113 第四轮：佩尔之眼的额外回合在出牌之后就没有了，另算一份不带额外回合的预演给出牌候选（头部 32 用的敌人塞牌数照现在的）
        _lossAfterNoExtra = null;
        if (PaelsExtraTurn)
        {
            int shoved = HandShoved;
            try { EndTurnPreview(cs, me, out _, out _, out var noEx, out _, out _, noExtraTurn: true); _lossAfterNoExtra = noEx; } catch { }
            HandShoved = shoved;
        }
        _nextRoll = Privileged ? SimulateNextRolls(cs) : null;
        if (V29) PlanMoves(cs);     // 格式 29（G05）：逐个生物输出之前先排好各怪物的招式计划（Creatures29.cs）
        foreach (var c in cs.Creatures) AddCreature(c, slot++, cs);
        _endTurn = null; _nextRoll = null; _plans = null;
        var pcs = me.PlayerCombatState!;
        int playerTok = _index[me.Creature];
        AddCombatStats(cs, me, playerTok);
        if (V20) AddStarStats(cs, me, playerTok);
        if (V21) AddSilentStats(cs, me, playerTok);
        if (V22) AddStats22(cs, me, playerTok);
        if (V24) AddFoolStats(cs, me, playerTok);
        for (int i = 0; i < pcs.OrbQueue.Orbs.Count; i++)
        {
            var orb = pcs.OrbQueue.Orbs[i];
            Begin(); _f[0] = i; _f[1] = (float)orb.PassiveVal; _f[2] = (float)orb.EvokeVal;
            _f[3] = PassiveTriggers(cs, orb);      // 版本 15：回合结束时被动结算几次（镀金缆线让最前面的球多一次）
            Emit(orb, TokenKind.Orb, Vocab.Id(orb), Zone.Ally, playerTok, 0);
        }
        foreach (var c in pcs.Hand.Cards) AddCard(c, Zone.Hand, inHand: true);
        var draw = pcs.DrawPile.Cards;
        int n = draw.Count;
        // 格式 20：玩家知道位置的顶部几张单独放在区域 16–18（第 1、2 张，第 3 张及以后），不进抽牌堆的汇总
        int known = V20 ? DrawTop.Known(pcs.DrawPile) : 0;
        for (int i = 0; i < known; i++)
            AddCard(draw[i], (Zone)((int)Zone.DrawTop1 + Math.Min(i, 2)), priv0: n > 1 ? i / (float)(n - 1) : 0);
        // 公开观测按 ID 排序，真实位置放进特权特征
        foreach (var (c, pos) in draw.Select((c, i) => (c, i)).Skip(known).OrderBy(t => Vocab.Model(t.c), StringComparer.Ordinal).ThenBy(t => t.c.CurrentUpgradeLevel))
            AddCard(c, Zone.Draw, priv0: n > 1 ? pos / (float)(n - 1) : 0);
        foreach (var c in pcs.DiscardPile.Cards) AddCard(c, Zone.Discard);
        foreach (var c in pcs.ExhaustPile.Cards) AddCard(c, Zone.Exhaust);
        // 版本 14：打出牌堆里正在结算的牌（选牌、选目标等打到一半的决策时才有；如内存清理、燃烧契约让你选一张消耗的牌）
        foreach (var c in pcs.PlayPile.Cards) AddCard(c, Zone.Play);
    }

    /// <summary>战斗外：玩家生物记录与牌组。</summary>
    void AddOutOfCombat(Player me)
    {
        Begin(); _f[0] = me.Creature.CurrentHp; _f[1] = me.Creature.MaxHp; _f[3] = 1; _f[5] = 1;
        Emit(me.Creature, TokenKind.Creature, Vocab.Id(me.Character), Zone.Ally, -1, 0);
        foreach (var c in me.Deck.Cards) AddCard(c, Zone.Deck);
    }

    int Emit(object? key, TokenKind kind, int id, Zone zone, int owner, int aux)
    {
        int idx = NumTokens;
        TI.Add((int)kind); TI.Add(id); TI.Add((int)zone); TI.Add(owner); TI.Add(aux);
        TF.AddRange(_f);
        if (V27) TF.AddRange(_x);       // 格式 27：每条记录 22 项特征
        if (V29) TF.AddRange(_y);       // 格式 29：每条记录 30 项特征
        if (Privileged) TP.AddRange(_p); else for (int i = 0; i < TokPriv; i++) TP.Add(0f);
        if (key != null) _index[key] = idx;
        return idx;
    }

    /// <summary>候选引用的对象 → 记录下标；未编码过的对象补一条“供选择”记录。</summary>
    int Ref(object? o)
    {
        if (o == null) return -1;
        if (_index.TryGetValue(o, out int idx)) return idx;
        switch (o)
        {
            case CardModel c: AddCard(c, Zone.Offer); break;
            case RelicModel r: AddRelic(r, Zone.Offer); break;
            case PotionModel p: AddPotion(p, Zone.Offer, -1); break;
            case Creature c: AddCreature(c, -1, c.CombatState!); break;
            case MapPoint mp:
                Begin(); _f[0] = mp.coord.row; _f[1] = mp.coord.col; _f[3] = 1;
                Emit(mp, TokenKind.MapNode, Vocab.Id(Vocab.MapNode(mp.PointType.ToString())), Zone.None, -1, 0);
                break;
            case EventOption ev: AddEventOption(ev); break;
            case RestSiteOption ro: AddRestOption(ro); break;
            case MerchantCardRemovalEntry: AddOption(o, Vocab.ShopRemoval, 0); break;
            case GoldReward g: AddOption(g, Vocab.Reward(nameof(GoldReward)), 0, g.Amount); break;
            case PotionReward { Potion: { } pot } pr: AddPotion(pot, Zone.Offer, -1); _index[pr] = NumTokens - 1; break;
            case RelicReward { Relic: { } rel } rr: AddRelic(rel, Zone.Offer); _index[rr] = NumTokens - 1; break;
            // 版本 18：卡牌奖励的选项记录放在它所在组的区域，与它亮出来的牌同区（决策记录 210）
            case CardReward cr: AddOption(cr, Vocab.Reward(nameof(CardReward)), 0, zone: RewardZone(RewardsFlow.Pending?.Set ?? RewardsFlow.ActiveSet, cr)); break;
            // 格式 29（G34 J3）：特殊卡牌奖励（偷窃跳蚤还回的牌、灯笼钥匙）输出它的牌（供选择区域的卡牌记录），奖励索引到这张牌（同药水、遗物奖励）
            case SpecialCardReward scr when V29 && SpecialCard29(scr) is { } sc:
                if (!_index.ContainsKey(sc)) AddCard(sc, Zone.Offer);
                _index[scr] = _index[sc]; break;
            case Reward r: AddOption(r, Vocab.Reward(r.GetType().Name), 0); break;
            default: AddOption(o, "OBJ:" + o.GetType().Name, 0); break;
        }
        return _index[o];
    }

    // ---------------------------------------------------------------- 序列化

    /// <summary>
    /// 二进制帧：int32 头部[16]，int32 记录整数[n×5]，float32 记录特征[n×16]，float32 特权特征[n×2]，
    /// int32 候选整数[m×5]，float32 候选特征[m×4]，int32 标签字节数 + UTF-8 JSON 标签数组（可为 0）。全部小端。
    /// </summary>
    public byte[] Serialize()
    {
        byte[] labelBytes = Labels.Count > 0 ? System.Text.Encoding.UTF8.GetBytes(System.Text.Json.JsonSerializer.Serialize(Labels)) : Array.Empty<byte>();
        byte[]? extraBytes = Extra != null ? System.Text.Encoding.UTF8.GetBytes(Extra) : null;
        int size = 4 * (HeaderInts + TI.Count + TF.Count + TP.Count + CI.Count + CF.Count + 1) + labelBytes.Length + (extraBytes != null ? 4 + extraBytes.Length : 0);
        var buf = new byte[size];
        int off = 0;
        void Copy<T>(ReadOnlySpan<T> xs) where T : unmanaged
        {
            var bytes = System.Runtime.InteropServices.MemoryMarshal.AsBytes(xs);
            bytes.CopyTo(buf.AsSpan(off));
            off += bytes.Length;
        }
        Copy<int>(Header);
        Copy<int>(System.Runtime.InteropServices.CollectionsMarshal.AsSpan(TI));
        Copy<float>(System.Runtime.InteropServices.CollectionsMarshal.AsSpan(TF));
        Copy<float>(System.Runtime.InteropServices.CollectionsMarshal.AsSpan(TP));
        Copy<int>(System.Runtime.InteropServices.CollectionsMarshal.AsSpan(CI));
        Copy<float>(System.Runtime.InteropServices.CollectionsMarshal.AsSpan(CF));
        Copy<int>(stackalloc int[] { labelBytes.Length });
        labelBytes.CopyTo(buf, off);
        off += labelBytes.Length;
        if (extraBytes != null)
        {
            Copy<int>(stackalloc int[] { extraBytes.Length });
            extraBytes.CopyTo(buf, off);
        }
        return buf;
    }
}
