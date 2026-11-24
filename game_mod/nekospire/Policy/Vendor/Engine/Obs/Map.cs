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

// 地图节点（类型 8）、路线摘要（类型 14）、问号房的实际概率。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>问号房按游戏的抽取规则算出的实际概率（版本 18）：普通战、宝箱、商店（其余为事件）。
    /// 照搬 UnknownMapPointOdds.Roll：上一个房间是商店、或这个节点的下一层全是商店时不出商店（RunManager.BuildRoomTypeBlacklist），
    /// 再过 Hook.ModifyUnknownMapPointRoomTypes（念珠去掉普通战，黄金罗盘本幕只出事件，灯笼钥匙等）；新手前三个问号房固定。</summary>
    static (float m, float t, float sh) UnknownRoomProbs(RunState s, MapPoint p)
    {
        try
        {
            if (s.UnlockState is { NumberOfRuns: 0 })
            {
                int seen = s.MapPointHistory.SelectMany(l => l).Count(h => h.MapPointType == MapPointType.Unknown);
                if (seen < 2) return (0, 0, 0);
                if (seen == 2) return (1, 0, 0);
            }
            var odds = s.Odds.UnknownMapPoint;
            var black = RunManager.BuildRoomTypeBlacklist(s.CurrentMapPointHistoryEntry, p.Children);
            IReadOnlySet<RoomType> types = new[] { RoomType.Monster, RoomType.Elite, RoomType.Treasure, RoomType.Shop, RoomType.Event }.Except(black).ToHashSet();
            types = MegaCrit.Sts2.Core.Hooks.Hook.ModifyUnknownMapPointRoomTypes(s, types);
            var fallback = types.Contains(RoomType.Event) ? RoomType.Event : types.Order().First();
            var pr = new Dictionary<RoomType, float>();
            float cum = 0;
            foreach (var (rt, o) in new[] { (RoomType.Monster, odds.MonsterOdds), (RoomType.Elite, odds.EliteOdds), (RoomType.Treasure, odds.TreasureOdds), (RoomType.Shop, odds.ShopOdds) })
            {
                if (!types.Contains(rt) || o < 0) continue;
                float hi = Math.Min(1f, cum + o);
                pr[rt] = Math.Max(0, hi - Math.Min(1f, cum)); cum += o;
            }
            float rest = Math.Max(0, 1f - Math.Min(1f, cum));
            pr[fallback] = (pr.TryGetValue(fallback, out var f0) ? f0 : 0) + rest;
            return (pr.GetValueOrDefault(RoomType.Monster), pr.GetValueOrDefault(RoomType.Treasure), pr.GetValueOrDefault(RoomType.Shop));
        }
        catch { return (-1, -1, -1); }
    }

    /// <summary>
    /// 整张当前幕地图；可达性与路线摘要都按地图边，从 Boss 往上逐行递推（地图是分层的有向无环图，连线只连到下一行），
    /// 一遍算出每个节点出发的结果，开销与节点数 + 边数成正比，不逐条枚举路线。
    /// </summary>
    void AddMap(RunState s)
    {
        var map = s.Map;
        var travelable = new HashSet<MapPoint>(RunSessionMap.Travelable(s));
        var visited = new HashSet<MapCoord>(s.VisitedMapCoords);
        var points = map.GetAllMapPoints().Append(map.StartingMapPoint).Append(map.BossMapPoint);
        if (map.SecondBossMapPoint != null) points = points.Append(map.SecondBossMapPoint);
        var all = points.Distinct().ToList();
        var idx = new Dictionary<MapPoint, int>();
        for (int i = 0; i < all.Count; i++) idx[all[i]] = i;
        // desc[p]：从 p 出发能到的节点（含自己）= {p} ∪ 各子节点的 desc；按行从下往上算，子节点总在下一行
        var desc = new Dictionary<MapPoint, BitArray>();
        foreach (var p in all.OrderByDescending(q => q.coord.row))
        {
            var bits = new BitArray(all.Count); bits[idx[p]] = true;
            foreach (var ch in p.Children) bits.Or(desc.TryGetValue(ch, out var cb) ? cb : Desc(ch));
            desc[p] = bits;
        }
        BitArray Desc(MapPoint q)    // 子节点不在 all 里（不应发生）时现算
        {
            if (desc.TryGetValue(q, out var b)) return b;
            b = new BitArray(all.Count); if (idx.TryGetValue(q, out int qi)) b[qi] = true;
            foreach (var ch in q.Children) b.Or(Desc(ch));
            return desc[q] = b;
        }
        var reachable = new BitArray(all.Count);
        foreach (var t in travelable) reachable.Or(Desc(t));
        // 从每个现在可前往的节点（按列排序，第 k 个）出发能到的节点，对应特征 9 + k
        var next = travelable.OrderBy(p => p.coord.col).ThenBy(p => p.coord.row).Take(7).ToList();
        var from = next.Select(Desc).ToList();
        foreach (var p in all)
        {
            Begin();
            int pi = idx[p];
            _f[0] = p.coord.row; _f[1] = p.coord.col; _f[2] = visited.Contains(p.coord) ? 1 : 0; _f[3] = travelable.Contains(p) ? 1 : 0;
            _f[4] = s.CurrentMapCoord == p.coord ? 1 : 0; _f[5] = reachable[pi] ? 1 : 0;
            // 连线（界面上画出的路径）：子节点的列号 + 1，按列排序，最多 3 个，没有为 0
            int c = 0;
            foreach (var ch in p.Children.OrderBy(q => q.coord.col)) if (c < 3) _f[6 + c++] = ch.coord.col + 1;
            for (int k = 0; k < from.Count; k++) _f[9 + k] = from[k][pi] ? 1 : 0;
            // 版本 18（第十六份评审）：现在可前往的问号节点，16–18 按游戏的抽取规则算出的普通战、宝箱、商店的实际概率（其余为事件；
            // 全局记录 16–18 仍是底层计数）
            if (p.PointType == MapPointType.Unknown && travelable.Contains(p)) (_f[16], _f[17], _f[18]) = UnknownRoomProbs(s, p);
            // 任务标记（FurCoat 标记的战斗、SpoilsMap 标记的宝箱），地图上画在节点旁：附加编号为标记它的遗物或卡牌
            Emit(p, TokenKind.MapNode, Vocab.Id(Vocab.MapNode(p.PointType.ToString())), Zone.None, -1, p.Quests.Count > 0 ? Vocab.Id(p.Quests[0]) : 0);
        }
        if (!RouteSummary) return;
        EmitRoutes(s, next);
    }

    /// <summary>战斗外、非选路线的决策（版本 13）：只给接下来可前往的节点（按列排序，最多 7 个）的路线摘要，不输出整张地图。</summary>
    void AddRoutesAhead(RunState s)
    {
        try
        {
            if (s.Map == null) return;
            var next = RunSessionMap.Travelable(s).OrderBy(p => p.coord.col).ThenBy(p => p.coord.row).Take(7).ToList();
            if (next.Count > 0) EmitRoutes(s, next);
        }
        catch { }
    }

    void EmitRoutes(RunState s, List<MapPoint> next)
    {
        var map = s.Map;
        // 本幕已走过的路上，上一个精英之后是否经过了休息处（本幕开头算没有）：决定下一个精英“前面有没有火堆”
        int fire = 0;
        foreach (var vc in s.VisitedMapCoords)
            fire = map.GetPoint(vc)?.PointType switch { MapPointType.RestSite => 1, MapPointType.Elite => 0, _ => fire };
        var memo = new Dictionary<MapPoint, Route>();
        foreach (var t in next)
        {
            var r = RouteFrom(t, map, memo);
            Begin();
            _f[0] = map.BossMapPoint.coord.row - t.coord.row; _f[1] = (float)Math.Log2(r.Paths + 1);
            for (int k = 0; k < Route.Kinds; k++) _f[2 + k] = r.Max[k];
            _f[7] = r.Min[0]; _f[8] = r.Min[1]; _f[9] = r.Min[4];
            _f[10] = r.Covered[fire]; _f[11] = r.AllCovered[fire];
            for (int e = 0; e < 4; e++) _f[12 + e] = r.RestByElite[e];
            // 版本 13：16、17 最少再走几格到休息处、商店（含这一格；到 Boss 前到不了为 −1），18、19 到下一个休息处（没有就到 Boss）之前
            // 最少要打几个精英、几场战斗（普通战 + 精英，未知不算）
            _f[16] = r.ToRest; _f[17] = r.ToShop; _f[18] = r.ElitesToRest; _f[19] = r.FightsToRest;
            Emit(new RouteKey(t), TokenKind.RouteSummary, Vocab.Id(Vocab.MapNode(t.PointType.ToString())), Zone.None, -1, 0);
            _route[t] = NumTokens - 1;
        }
    }

    sealed record RouteKey(MapPoint Point);

    /// <summary>
    /// 从某节点（含）到本幕 Boss（不含）的全部路线的汇总：路线条数，普通战、精英、未知、商店、休息处各自最少、最多几个，
    /// 以及走 0、1、2、3 个及以上精英时最多几个休息处（走不到为 −1）；
    /// “前面有火堆”的精英（上一个精英或本幕开头之后经过了休息处）最多几个，每个精英前面都有火堆时最多几个精英（做不到为 −1），
    /// 这两项按进入这个节点时“上一个精英之后是否已经过休息处”分两种情况各算一份（下标 0 没有、1 有）。按子节点递推：条数相加、最少最多取子节点的最小最大再加上自己这一格、
    /// 取舍表取子节点的逐项最大再按自己的类型平移（计数用（加，乘），最少最多用热带半环）。
    /// </summary>
    sealed class Route
    {
        public const int Kinds = 5;
        public double Paths;
        public readonly int[] Min = new int[Kinds], Max = new int[Kinds];
        public readonly int[] RestByElite = { 0, -1, -1, -1 };
        public readonly int[] Covered = new int[2], AllCovered = new int[2];
        public int ToRest = -1, ToShop = -1, ElitesToRest, FightsToRest;
    }

    static int RouteKind(MapPointType t) => t switch
    {
        MapPointType.Monster => 0, MapPointType.Elite => 1, MapPointType.Unknown => 2, MapPointType.Shop => 3, MapPointType.RestSite => 4, _ => -1,
    };

    static Route RouteFrom(MapPoint p, ActMap map, Dictionary<MapPoint, Route> memo)
    {
        if (memo.TryGetValue(p, out var r)) return r;
        r = new Route();
        if (p == map.BossMapPoint || p == map.SecondBossMapPoint || p.Children.Count == 0)
        {
            r.Paths = 1;    // 到 Boss 为止：空路线一条
            return memo[p] = r;
        }
        bool first = true;
        var tab = new[] { -1, -1, -1, -1 };
        foreach (var ch in p.Children)
        {
            var c = RouteFrom(ch, map, memo);
            r.Paths += c.Paths;
            for (int k = 0; k < Route.Kinds; k++)
            {
                r.Min[k] = first ? c.Min[k] : Math.Min(r.Min[k], c.Min[k]);
                r.Max[k] = first ? c.Max[k] : Math.Max(r.Max[k], c.Max[k]);
            }
            for (int e = 0; e < 4; e++) tab[e] = Math.Max(tab[e], c.RestByElite[e]);
            first = false;
        }
        int own = RouteKind(p.PointType);
        for (int f = 0; f < 2; f++)
        {
            int fin = own == 4 ? 1 : own == 1 ? 0 : f, cov = -1, all = -1;
            foreach (var ch in p.Children) { var c = memo[ch]; cov = Math.Max(cov, c.Covered[fin]); all = Math.Max(all, c.AllCovered[fin]); }
            bool elite = own == 1;
            r.Covered[f] = cov + (elite && f == 1 ? 1 : 0);
            r.AllCovered[f] = elite && f == 0 || all < 0 ? -1 : all + (elite ? 1 : 0);
        }
        if (own >= 0) { r.Min[own]++; r.Max[own]++; }
        int Nearest(Func<Route, int> get) { int best = -1; foreach (var ch in p.Children) { int v = get(memo[ch]); if (v >= 0 && (best < 0 || v < best)) best = v; } return best < 0 ? -1 : best + 1; }
        r.ToRest = own == 4 ? 1 : Nearest(c => c.ToRest);
        r.ToShop = own == 3 ? 1 : Nearest(c => c.ToShop);
        if (own == 4) { r.ElitesToRest = 0; r.FightsToRest = 0; }
        else
        {
            r.ElitesToRest = p.Children.Min(ch => memo[ch].ElitesToRest) + (own == 1 ? 1 : 0);
            r.FightsToRest = p.Children.Min(ch => memo[ch].FightsToRest) + (own is 0 or 1 ? 1 : 0);
        }
        for (int e = 0; e < 4; e++) r.RestByElite[e] = -1;
        for (int e = 0; e < 4; e++)
        {
            if (tab[e] < 0) continue;
            int ne = own == 1 ? Math.Min(e + 1, 3) : e, nr = tab[e] + (own == 4 ? 1 : 0);
            r.RestByElite[ne] = Math.Max(r.RestByElite[ne], nr);
        }
        return memo[p] = r;
    }
}
