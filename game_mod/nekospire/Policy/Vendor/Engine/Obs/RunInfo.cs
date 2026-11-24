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

// 整局信息：出怪池、整局预生成内容、本幕历史与 Boss 数、奖励分组、下回合抽牌数、商店删牌价。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    static readonly System.Reflection.FieldInfo RoomsField = GameCompat.Field(typeof(ActModel), "_rooms", "本幕的遭遇表（出怪池、本幕历史、预生成内容、特权的下一场）")!;
    static readonly System.Reflection.PropertyInfo? EntryRound = GameCompat.Property(typeof(MegaCrit.Sts2.Core.Combat.History.CombatHistoryEntry), "RoundNumber", "战斗历史的轮次（上一轮失去的生命）");
    static readonly System.Reflection.FieldInfo? TestSubjectRespawns = GameCompat.Field(typeof(MegaCrit.Sts2.Core.Models.Monsters.TestSubject), "_respawns", "实验体的形态（战斗计数子类型 1）");
    Dictionary<MonsterModel, string>? _nextRoll;

    /// <summary>卡牌奖励所在的组（版本 18）：按它在这组奖励里排第几个卡牌奖励，分到区域 RewardGroup1–3（第 3 个以后都算第 3 组）。</summary>
    public static Zone RewardZone(RewardsSet? set, CardReward cr)
    {
        int k = set == null ? 0 : Math.Max(0, set.Rewards.OfType<CardReward>().ToList().IndexOf(cr));
        return (Zone)((int)Zone.RewardGroup1 + Math.Min(k, 2));
    }

    /// <summary>现在结束回合、下一次正常抽牌名义上抽几张（版本 18）：游戏的抽牌修正钩子；怀表按“上回合打出的张数”判断，
    /// 结束回合后那就是本回合已打出的张数（界面上显示的数），这里换成按它算。不保证都能进手（手牌上限、抽牌堆张数）。</summary>
    static int NextHandDraw(Player me)
    {
        try
        {
            var cs = me.Creature.CombatState!;
            decimal n = MegaCrit.Sts2.Core.Hooks.Hook.ModifyHandDraw(cs, me, 5m, out _);
            foreach (var r in me.Relics.OfType<MegaCrit.Sts2.Core.Models.Relics.Pocketwatch>())
            {
                if (r.IsMelted || r.Status == MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Disabled) continue;
                n -= r.ModifyHandDraw(me, 0m);      // 去掉按上回合算的那部分
                if (r.DisplayAmount <= r.DynamicVars["CardThreshold"].BaseValue) n += r.DynamicVars.Cards.BaseValue;
            }
            // v0.113.0 修正（F113）：本回合刚挂上的“下回合抽牌”（DrawCardsNextTurnPower）现在的 AmountOnTurnStart 为 0，钩子不加；到下回合开始才更新、照加 Amount
            // （哇 叽、塔罗星星，储君的指引之星、闪耀、谋划、暗淡蓝点，放松、掠食者等；原来头部 31、33 少算）
            if (F113)
                foreach (var pw in me.Creature.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.DrawCardsNextTurnPower>())
                    if (pw.AmountOnTurnStart == 0) n += pw.Amount;
            // v0.113.0 修正（F113）：按回合数、计数生效的抽牌遗物，钩子读的是这一回合的回合数和计数，要换成下一回合的：
            // 蛇之戒、准备背包、轰鸣海螺（精英战）只管第 1 回合，大蘑菇第 1 回合少抽；龙之戒管前“Turns”回合；
            // 钟摆的计数在抽牌前（BeforeHandDraw）变成 (计数 + 1) % Turns，为 0 时多抽；花粉核心的计数在抽牌前加 1，到 Turns 时多抽
            if (F113)
            {
                int t = me.PlayerCombatState!.TurnNumber;
                foreach (var r in me.Relics)
                {
                    if (r.IsMelted || r.Status == MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Disabled) continue;
                    switch (r)
                    {
                        case MegaCrit.Sts2.Core.Models.Relics.RingOfTheSnake or MegaCrit.Sts2.Core.Models.Relics.BagOfPreparation
                            or MegaCrit.Sts2.Core.Models.Relics.BoomingConch or MegaCrit.Sts2.Core.Models.Relics.BigMushroom:
                            n -= r.ModifyHandDraw(me, 0m); break;
                        case MegaCrit.Sts2.Core.Models.Relics.RingOfTheDrake:
                            n -= r.ModifyHandDraw(me, 0m);
                            if (t + 1 <= r.DynamicVars["Turns"].BaseValue) n += r.DynamicVars.Cards.BaseValue;
                            break;
                        case MegaCrit.Sts2.Core.Models.Relics.Pendulum pd:
                            n -= pd.ModifyHandDraw(me, 0m);
                            if ((pd.TurnsSeen + 1) % pd.DynamicVars["Turns"].IntValue == 0) n += pd.DynamicVars.Cards.BaseValue;
                            break;
                        case MegaCrit.Sts2.Core.Models.Relics.PollinousCore pc:
                            n -= pc.ModifyHandDraw(me, 0m);
                            if (pc.TurnsSeen + 1 >= pc.DynamicVars["Turns"].IntValue) n += pc.DynamicVars.Cards.BaseValue;
                            break;
                    }
                }
            }
            return (int)Math.Max(0, n);
        }
        catch { return 0; }
    }


    /// <summary>下一次在商店删牌的价格（版本 17）：游戏的删牌条目按已删次数定基础价，商店里再过 Hook.ModifyMerchantPrice；
    /// 条目的 Cost 只在商店房间里改价，店外照同样的钩子算，与进店后看到的价格一致。</summary>
    static int RemovalPrice(Player me)
    {
        try
        {
            var entry = new MegaCrit.Sts2.Core.Entities.Merchant.MerchantCardRemovalEntry(me);
            int p = entry.Cost;
            if (me.RunState.CurrentRoom is not MegaCrit.Sts2.Core.Rooms.MerchantRoom)
                p = (int)MegaCrit.Sts2.Core.Hooks.Hook.ModifyMerchantPrice(me.RunState, me, entry, p);
            return p;
        }
        catch { return 0; }
    }

    static readonly System.Reflection.PropertyInfo? WeakCount = GameCompat.Property(typeof(ActModel), "NumberOfWeakEncounters", "本幕弱池的场数（出怪池、头部 20）");

    /// <summary>
    /// 本幕的出怪池（版本 15，决策记录 194）：本幕普通战（弱池、常规池）与精英战的每种遭遇各一条“出怪池”记录（类型 16，词表 ID 为遭遇）。
    /// 游戏在每幕开头按规则排好整幕的遭遇（ActModel.GenerateRooms）：普通战前几场从弱池抽（第一幕 3 场，第二、三幕 2 场），之后从常规池抽，
    /// 精英战从精英池抽；每个池子是一个抽取袋，抽过的不放回、抽空了重新装满，每次只在“与上一场不同、也没有相同标签”的遭遇里等概率抽。
    /// 这里只用已经打过的场次（列表的前“已进入场数”项，玩家都见过）按同样的规则推出袋里还剩什么、下一场各是什么的概率；
    /// 列表里还没打到的项（随机数的结果）不看。
    /// 特征：0–2 弱池、常规池、精英池（独热），3 本幕打过几次，4 还在这一轮的袋子里，5 下一场同类战斗（普通战或精英战）是它的概率。
    /// </summary>
    void EncounterPool(RunState s)
    {
        try
        {
            if (RoomsField.GetValue(s.Act) is not MegaCrit.Sts2.Core.Rooms.RoomSet rs) return;
            int nWeak = WeakCount?.GetValue(s.Act) is int w ? w : 0;
            var normal = rs.normalEncounters.Take(Math.Min(rs.normalEncountersVisited, rs.normalEncounters.Count)).ToList();
            var elite = rs.eliteEncounters.Take(Math.Min(rs.eliteEncountersVisited, rs.eliteEncounters.Count)).ToList();
            var weakPool = s.Act.AllWeakEncounters.ToList(); var regPool = s.Act.AllRegularEncounters.ToList(); var elitePool = s.Act.AllEliteEncounters.ToList();
            // 抽取袋：从这一池开始抽的那一场起，按已经抽出的依次拿掉，空了重新装满；返回现在袋里剩下的
            static List<EncounterModel> Bag(List<EncounterModel> pool, IEnumerable<EncounterModel> drawn)
            {
                var bag = new List<EncounterModel>();
                foreach (var e in drawn)
                {
                    if (bag.Count == 0) bag.AddRange(pool);
                    int i = bag.FindIndex(x => x.Id == e.Id);
                    if (i >= 0) bag.RemoveAt(i);
                }
                if (bag.Count == 0) bag.AddRange(pool);
                return bag;
            }
            void Emit3(List<EncounterModel> pool, int slot, List<EncounterModel> drawn, List<EncounterModel>? bag, EncounterModel? last)
            {
                var next = bag?.Where(e => e.Id != last?.Id && !e.SharesTagsWith(last)).ToList();
                foreach (var e in pool)
                {
                    Begin(); _f[slot] = 1;
                    _f[3] = drawn.Count(x => x.Id == e.Id);
                    if (bag != null)
                    {
                        _f[4] = bag.Any(x => x.Id == e.Id) ? 1 : 0;
                        _f[5] = next!.Count > 0 && next.Any(x => x.Id == e.Id) ? 1f / next.Count : 0;
                    }
                    Emit(null, TokenKind.EncounterPool, Vocab.Id(e), Zone.None, -1, 0);
                }
            }
            var lastN = normal.LastOrDefault();
            bool inWeak = normal.Count < nWeak;
            Emit3(weakPool, 0, normal, inWeak ? Bag(weakPool, normal.Take(nWeak)) : null, lastN);
            Emit3(regPool, 1, normal, inWeak ? null : Bag(regPool, normal.Skip(nWeak)), lastN);
            Emit3(elitePool, 2, elite, Bag(elitePool, elite), elite.LastOrDefault());
        }
        catch { }
    }

    /// <summary>
    /// 整局预生成内容（类型 17，特权，只进价值头；决策记录 259）。游戏开局时用 UpFront 随机数把三幕的普通战、精英、Boss、先古之民与事件顺序
    /// 一次抽好，存在各幕的 RoomSet（RunManager.GenerateRooms → ActModel.GenerateRooms），之后的任何动作都不改它们，是与动作无关的外生输入：
    /// 价值头以它为条件不会让策略梯度有偏（Mao 等 2019 的输入相关基线）。哪些会真的遇到取决于路线，但表本身不变。
    /// 本幕：还没走到的普通战前 3 场、精英前 2 场、事件前 2 个（本幕 Boss 与先古之民玩家已看到，不给）。
    /// 后面每一幕：Boss（最后一幕还有第二个）、先古之民、普通战前 4 场、精英前 2 场、事件前 3 个。
    /// 词表 ID 为遭遇或事件；特征 0 第几幕之后（0 本幕、1 下一幕、2 再下一幕），1 类别（0 普通战、1 精英、2 Boss、3 事件、4 先古之民），
    /// 2 本类里的先后（0 为最先遇到；第二个 Boss 为 1），3 这场普通战排在弱池的位置上（按该幕的 NumberOfWeakEncounters）。
    /// 事件表里的项到时可能因条件不满足被跳过（RoomSet.EnsureNextEventIsValid），这里按表的原样给。
    /// </summary>
    void EmitFuture(RunState s)
    {
        try
        {
            for (int ai = s.CurrentActIndex; ai < s.Acts.Count; ai++)
            {
                var act = s.Acts[ai];
                if (RoomsField.GetValue(act) is not MegaCrit.Sts2.Core.Rooms.RoomSet rs) continue;
                int off = ai - s.CurrentActIndex;
                bool cur = off == 0;
                int nWeak = WeakCount?.GetValue(act) is int w ? w : 0;
                void Rec(AbstractModel m, int cat, int order, bool weak)
                {
                    Begin(); _f[0] = off; _f[1] = cat; _f[2] = order; _f[3] = weak ? 1 : 0;
                    Emit(null, TokenKind.FutureContent, Vocab.Id(m), Zone.None, -1, 0);
                }
                int nv = cur ? rs.normalEncountersVisited : 0, lv = cur ? rs.eliteEncountersVisited : 0, xv = cur ? rs.eventsVisited : 0;
                for (int k = 0; k < (cur ? 3 : 4) && nv + k < rs.normalEncounters.Count; k++) Rec(rs.normalEncounters[nv + k], 0, k, nv + k < nWeak);
                for (int k = 0; k < 2 && lv + k < rs.eliteEncounters.Count; k++) Rec(rs.eliteEncounters[lv + k], 1, k, false);
                if (!F113)
                    for (int k = 0; k < (cur ? 2 : 3) && xv + k < rs.events.Count; k++) Rec(rs.events[xv + k], 3, k, false);
                else
                {
                    // v0.113.0 修正（F113）：① 每幕第一间是先古之民，它进的是事件房，也让 eventsVisited 加 1（RunManager 进房时 MarkRoomVisited(Event)），
                    // 所以后面各幕第一个真正遇到的事件是 events[1]（原来从 events[0] 起，后几幕整体错一位，实测 21 万次比对 0 次对上）；
                    // ② 照 RoomSet.EnsureNextEventIsValid 跳过不能出现（IsAllowed）或已经遇到过（VisitedEventIds）的事件，模拟里已经排到的也算遇到过。
                    // 后面各幕的 IsAllowed 按那一幕的幕号与 Act 判断（ActView），金币、生命等仍按现在的（到时会变，近似）
                    int xs = cur ? xv : (rs.HasAncient ? 1 : 0);
                    var seen = new HashSet<ModelId>(s.VisitedEventIds);
                    IRunState view = cur ? s : ActView.Of(s, ai);
                    for (int i = xs, k = 0; i < rs.events.Count && k < (cur ? 2 : 3); i++)
                    {
                        var ev = rs.events[i];
                        bool ok;
                        try { ok = ev.IsAllowed(view) && !seen.Contains(ev.Id); } catch { ok = !seen.Contains(ev.Id); }
                        if (!ok) continue;
                        seen.Add(ev.Id); Rec(ev, 3, k++, false);
                    }
                }
                if (cur) continue;
                Rec(rs.Boss, 2, 0, false);
                if (rs.SecondBoss is { } b2) Rec(b2, 2, 1, false);
                if (rs.HasAncient) Rec(rs.Ancient, 4, 0, false);
            }
        }
        catch { }
    }

    /// <summary>F113：给事件的 IsAllowed 看的“到了第 ai 幕”的局面：幕号与 Act 换成那一幕，其余照转发到真实的局面（只读）。</summary>
    public class ActView : System.Reflection.DispatchProxy
    {
        RunState _s = null!; int _act;
        public static IRunState Of(RunState s, int act)
        {
            var p = Create<IRunState, ActView>();
            var v = (ActView)(object)p; v._s = s; v._act = act;
            return p;
        }
        protected override object? Invoke(System.Reflection.MethodInfo? m, object?[]? args)
        {
            if (m == null) return null;
            if (m.Name == "get_CurrentActIndex") return _act;
            if (m.Name == "get_Act") return _s.Acts[_act];
            try { return m.Invoke(_s, args); }
            catch (System.Reflection.TargetInvocationException e) when (e.InnerException != null) { throw e.InnerException; }
        }
    }

    /// <summary>整局的头部 24、25（版本 15）：本幕已经打赢的 Boss 数（本幕走过的 Boss 房间数，正在打的那一场不算）与还要打的 Boss 数（含正在打的）。</summary>
    void BossCount(RunState s, bool inCombat)
    {
        try
        {
            int total = s.Act.SecondBossEncounter != null ? 2 : 1, rooms = 0;
            if (s.CurrentActIndex < s.MapPointHistory.Count)
                foreach (var e in s.MapPointHistory[s.CurrentActIndex])
                    foreach (var room in e.Rooms)
                        if (room.RoomType == RoomType.Boss) rooms++;
            int done = Math.Max(0, rooms - (inCombat && s.CurrentRoom is CombatRoom { RoomType: RoomType.Boss } ? 1 : 0));
            Header[24] = Math.Min(done, total); Header[25] = Math.Max(0, total - done);
        }
        catch { }
    }

    /// <summary>
    /// 版本 14：本幕已经发生的遭遇（头部 18–22）：18 本幕进过几场普通战，19 几场精英战，20 本幕还剩几场普通战从弱池里出
    /// （游戏每幕开头几场普通战从弱池抽，之后换常规池：第一幕 3 场等，规则公开），21 本幕最近一场普通战、22 最近一场精英战的遭遇（词表 ID，没有为 0）。
    /// 只记已发生的；预抽好的下一场见特权特征。
    /// </summary>
    void ActHistory(RunState s)
    {
        try
        {
            if (RoomsField.GetValue(s.Act) is MegaCrit.Sts2.Core.Rooms.RoomSet rs)
            {
                Header[18] = rs.normalEncountersVisited; Header[19] = rs.eliteEncountersVisited;
                if (WeakCount?.GetValue(s.Act) is int weak) Header[20] = Math.Max(0, weak - rs.normalEncountersVisited);
            }
            if (s.CurrentActIndex < s.MapPointHistory.Count)
                foreach (var e in s.MapPointHistory[s.CurrentActIndex])
                    foreach (var room in e.Rooms)
                    {
                        if (room.ModelId is not { } id) continue;
                        if (room.RoomType == RoomType.Monster) Header[21] = Vocab.Id(ModelDb.GetById<EncounterModel>(id));
                        else if (room.RoomType == RoomType.Elite) Header[22] = Vocab.Id(ModelDb.GetById<EncounterModel>(id));
                    }
        }
        catch { }
    }
}
