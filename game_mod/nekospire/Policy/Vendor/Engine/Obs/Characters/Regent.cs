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

// 储君（格式 20）：星的战斗统计、已知的抽牌堆顶部。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>
    /// 格式 20：星的战斗统计，一条能力类记录（词表 ID 为 STAT:STARS），所属记录为玩家：0 本回合得到的星，1 本回合失去的星（花掉的与被扣的），
    /// 8 本场得到的星，9 本场失去的星。来自战斗历史的 StarsModifiedEntry（游戏的星数每次变化记一条），都是玩家数得出来的。
    /// 战斗计数记录（类型 15）的 20 列已经用满，所以另起一条；用能力类、避开 17–19（买不起的商品、木桥候选池、回手资格的门控标记）。
    /// </summary>
    void AddStarStats(ICombatState cs, Player me, int playerTok)
    {
        try
        {
            Begin();
            foreach (var e in CombatManager.Instance.History.Entries)
            {
                if (e is not MegaCrit.Sts2.Core.Combat.History.Entries.StarsModifiedEntry sm || sm.Actor != me.Creature) continue;
                bool now = e.HappenedThisTurn(cs);
                if (sm.Amount > 0) { _f[8] += sm.Amount; if (now) _f[0] += sm.Amount; }
                else { _f[9] -= sm.Amount; if (now) _f[1] -= sm.Amount; }
            }
            Emit(null, TokenKind.Power, Vocab.Id("STAT:STARS"), Zone.Ally, playerTok, 0);
        }
        catch { }
    }

    /// <summary>
    /// 已知的抽牌堆顶部（格式 20，决策记录 129 的做法）：游戏把牌放到抽牌堆顶（闪光、光子切割、宇宙冷漠、闪耀打击打出后、思考在前、头槌等，
    /// CardPileCmd.Add 的位置为 Top）时玩家知道它在哪；之后被抽走、打出、挪走就不再在顶部，洗牌（STS2 的洗牌把抽牌堆本身也一起洗）、
    /// 随机插入一张牌（位置为 Random，玩家不知道插在哪）之后整段作废。这里在游戏的牌堆操作上记下这段从顶部起连续、玩家知道位置的牌，
    /// 只用玩家看得到的事件，不看随机数结果：
    ///   CardPileCmd.Add 的前缀记下每张牌要去的位置（Top / Random / Bottom），CardPile.AddInternal 的后缀按它更新：Top 插到最前，Random 整段作废，
    ///   Bottom 不变；不是经 Add 来的按下标插入（变化 Transform 的替换牌插回原位）只保留插入点以上的部分；
    ///   RemoveInternal（抽走、打出、选走）从中去掉这张牌，MoveToTopInternal（固有牌）插到最前，MoveToBottomInternal 去掉，RandomizeOrderInternal、Clear 作废。
    /// 编码时再核对记下的与牌堆实际顶部是否一致，只取一致的前缀（核对不一致记在 Mismatch，正常应为 0）。
    /// 补丁只在无头引擎里装（GameShim.Install）；实机桥接没装时这里恒为空，等于没有已知的顶部。
    /// </summary>
    public static class DrawTop
    {
        sealed class Pending { public CardPilePosition Pos; public CardPile? Pile; }
        static readonly System.Runtime.CompilerServices.ConditionalWeakTable<CardPile, List<CardModel>> KnownTop = new();
        static readonly System.Runtime.CompilerServices.ConditionalWeakTable<CardModel, Pending> PendingAdd = new();
        /// <summary>编码时发现记下的顶部与牌堆实际顶部不一致的次数（没跟踪到的牌堆操作；核对用，正常为 0）。</summary>
        public static int Mismatch;

        public static void Install(HarmonyLib.Harmony h)
        {
            var t = typeof(CardPile);
            HarmonyLib.HarmonyMethod M(string name) => GameCompat.Own(typeof(DrawTop), name);
            const string use = "已知的抽牌堆顶部（格式 20）";
            GameCompat.Patch(h, GameCompat.Method(t, nameof(CardPile.AddInternal), use), "CardPile.AddInternal", postfix: M(nameof(AfterAdd)));
            GameCompat.Patch(h, GameCompat.Method(t, nameof(CardPile.RemoveInternal), use), "CardPile.RemoveInternal", prefix: M(nameof(BeforeRemove)));
            GameCompat.Patch(h, GameCompat.Method(t, nameof(CardPile.MoveToTopInternal), use), "CardPile.MoveToTopInternal", postfix: M(nameof(AfterMoveTop)));
            GameCompat.Patch(h, GameCompat.Method(t, nameof(CardPile.MoveToBottomInternal), use), "CardPile.MoveToBottomInternal", postfix: M(nameof(AfterMoveBottom)));
            GameCompat.Patch(h, GameCompat.Method(t, nameof(CardPile.RandomizeOrderInternal), use), "CardPile.RandomizeOrderInternal", postfix: M(nameof(AfterReset)));
            GameCompat.Patch(h, GameCompat.Method(t, nameof(CardPile.Clear), use), "CardPile.Clear", postfix: M(nameof(AfterReset)));
            var add = GameCompat.Method(typeof(MegaCrit.Sts2.Core.Commands.CardPileCmd), nameof(MegaCrit.Sts2.Core.Commands.CardPileCmd.Add),
                new[] { typeof(IEnumerable<CardModel>), typeof(CardPile), typeof(CardPilePosition), typeof(AbstractModel), typeof(bool), typeof(bool) }, use, exact: true);
            GameCompat.Patch(h, add, "CardPileCmd.Add(IEnumerable<CardModel>, CardPile, ...)", prefix: M(nameof(BeforeCmdAdd)));
        }

        static bool IsDraw(CardPile p) => p.Type == PileType.Draw;
        static List<CardModel> Of(CardPile p) => KnownTop.GetOrCreateValue(p);

        // CardPileCmd.Add 是异步方法，这里是它同步执行的入口：只记下这一批牌各自要去的位置，不改游戏状态。
        // 只认已经物化的集合（数组、列表）：惰性序列重复枚举可能有副作用（如每次都新建牌），这时不记（按“不是经 Add 来的”处理，只会少报已知）
        static void BeforeCmdAdd(IEnumerable<CardModel> cards, CardPile newPile, CardPilePosition position)
        {
            try
            {
                if (newPile == null || !IsDraw(newPile) || cards is not (ICollection<CardModel> or IReadOnlyCollection<CardModel>)) return;
                foreach (var c in cards)
                    if (c != null) PendingAdd.AddOrUpdate(c, new Pending { Pos = position, Pile = newPile });
            }
            catch { }
        }

        static void AfterAdd(CardPile __instance, CardModel card, int index)
        {
            try
            {
                if (!IsDraw(__instance)) return;
                var k = Of(__instance);
                if (PendingAdd.TryGetValue(card, out var p) && ReferenceEquals(p.Pile, __instance))
                {
                    PendingAdd.Remove(card);
                    switch (p.Pos)
                    {
                        case CardPilePosition.Top: k.Remove(card); k.Insert(0, card); return;
                        case CardPilePosition.Random: k.Clear(); return;
                        default: if (index < 0) return; break;      // Bottom：顶部不变
                    }
                }
                if (index >= 0 && index < k.Count) k.RemoveRange(index, k.Count - index);   // 不是经 Add 来的按下标插入：只留插入点以上
            }
            catch { }
        }

        static void BeforeRemove(CardPile __instance, CardModel card)
        {
            try { if (IsDraw(__instance) && KnownTop.TryGetValue(__instance, out var k)) k.Remove(card); } catch { }
        }

        static void AfterMoveTop(CardPile __instance, CardModel card)
        {
            try { if (IsDraw(__instance)) { var k = Of(__instance); k.Remove(card); k.Insert(0, card); } } catch { }
        }

        static void AfterMoveBottom(CardPile __instance, CardModel card)
        {
            try { if (IsDraw(__instance) && KnownTop.TryGetValue(__instance, out var k)) k.Remove(card); } catch { }
        }

        static void AfterReset(CardPile __instance)
        {
            try { if (IsDraw(__instance) && KnownTop.TryGetValue(__instance, out var k)) k.Clear(); } catch { }
        }

        /// <summary>抽牌堆顶部有几张是玩家知道位置的（记下的与实际顶部逐张一致的前缀长度）。</summary>
        public static int Known(CardPile draw)
        {
            if (!KnownTop.TryGetValue(draw, out var k) || k.Count == 0) return 0;
            var cards = draw.Cards;
            int n = 0;
            while (n < k.Count && n < cards.Count && ReferenceEquals(k[n], cards[n])) n++;
            if (n < k.Count)
            {
                Mismatch++;
                if (Mismatch <= 5) Console.Error.WriteLine($"[drawtop] 记下的顶部与牌堆不一致：记下 {string.Join(",", k.Select(c => c.Id.Entry))}，实际前 {k.Count} 张 {string.Join(",", cards.Take(k.Count).Select(c => c.Id.Entry))}");
                k.RemoveRange(n, k.Count - n);
            }
            return n;
        }
    }
}
