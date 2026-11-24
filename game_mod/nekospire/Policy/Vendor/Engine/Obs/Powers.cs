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

// 能力记录的通用读法：本回合 / 本场已触发、能力的内部数据。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    static readonly Dictionary<Type, System.Reflection.PropertyInfo[]> PowerFlags = new();

    /// <summary>这个生物本回合抽到过状态牌（同 IterationPower.AfterCardDrawn 的计数：战斗历史里本回合、这个生物抽到的状态牌）。</summary>
    static bool StatusDrawnThisTurn(Creature c, ICombatState cs)
    {
        try { return CombatManager.Instance.History.Entries.OfType<MegaCrit.Sts2.Core.Combat.History.Entries.CardDrawnEntry>().Any(e => e.HappenedThisTurn(cs) && e.Actor == c && e.Card.Type == CardType.Status); }
        catch { return false; }
    }

    static readonly FieldInfo? InternalDataField = GameCompat.Field(typeof(PowerModel), "_internalData", "能力的内部数据（格式 20 能力 8–11）");
    static readonly Dictionary<Type, FieldInfo[]> InternalScalars = new();

    /// <summary>
    /// 格式 20：能力的内部数据（PowerModel.InitInternalData，能力类自己的属性里没有，原来编不进去）。8–10 为内部数据的前三个数值字段
    /// （int、bool、decimal，按声明顺序；集合不取）：虚空形态本回合已打几张、轨道已花的能量与触发次数、暗淡蓝点本回合已触发、杂耍本回合已打几张攻击等，
    /// 都是玩家看着出牌就数得出来的。其中名字含 ThisTurn、ThisCombat 且为真的布尔字段同时记进 6（本回合 / 本场已触发）。
    /// 11 为虚空形态本回合还剩几张免费（层数减已打张数，不小于 0）。
    /// </summary>
    void PowerInternal(PowerModel pw)
    {
        try
        {
            if (InternalDataField == null || GetF(InternalDataField, pw) is not { } data) return;
            var t = data.GetType();
            if (!InternalScalars.TryGetValue(t, out var fs))
                InternalScalars[t] = fs = t.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                    .Where(f => f.FieldType == typeof(int) || f.FieldType == typeof(bool) || f.FieldType == typeof(decimal))
                    .OrderBy(f => f.MetadataToken).ToArray();
            int k = 8;
            foreach (var f in fs)
            {
                object? v = GetF(f, data);
                float x = v switch { int i => i, bool b => b ? 1 : 0, decimal m => (float)m, _ => 0 };
                if (v is true && (f.Name.Contains("ThisTurn") || f.Name.Contains("ThisCombat"))) _f[6] = 1;
                if (k <= 10) _f[k++] = x;
            }
            if (pw is MegaCrit.Sts2.Core.Models.Powers.VoidFormPower && fs.FirstOrDefault(f => f.Name == "cardsPlayedThisTurn") is { } cp && GetF(cp, data) is int played)
                _f[11] = Math.Max(0, pw.Amount - played);
        }
        catch { }
    }

    /// <summary>
    /// 格式 29（G44）：回声形态本回合还剩几次翻倍——层数减本回合已经开始打出的“第一次”的牌数（照 EchoFormPower.ModifyCardPlayCount：
    /// 战斗历史里本回合、这个生物、IsFirstInSeries 的 CardPlayStartedEntry），不小于 0。读不到为 0。
    /// </summary>
    static float EchoLeft29(PowerModel pw)
    {
        try
        {
            if (pw.Owner.CombatState is not { } cs) return 0;
            int n = CombatManager.Instance.History.CardPlaysStarted.Count(e => e.Actor == pw.Owner && e.CardPlay.IsFirstInSeries && e.HappenedThisTurn(cs));
            return Math.Max(0, pw.Amount - n);
        }
        catch { return 0; }
    }

    static bool PowerUsed(PowerModel pw)
    {
        var t = pw.GetType();
        if (!PowerFlags.TryGetValue(t, out var ps))
            PowerFlags[t] = ps = t.GetProperties(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic)
                .Where(p => p.PropertyType == typeof(bool) && p.GetIndexParameters().Length == 0 && p.DeclaringType == t && (p.Name.Contains("ThisTurn") || p.Name.Contains("ThisCombat"))).ToArray();
        foreach (var p in ps) { try { if (GetP(p, pw) is true) return true; } catch { } }
        return false;
    }
}
