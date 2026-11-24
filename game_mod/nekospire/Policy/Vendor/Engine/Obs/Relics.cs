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

// 遗物记录（类型 5）、药水记录（类型 6）。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>
    /// 遗物：0 计数，1 是否显示计数，2 是否用完；版本 14 起 3 蜡制（玩具盒给的），4 已失效（蜡制已融化，或游戏状态为 Disabled：
    /// 蜥蜴尾巴、茶类等用掉之后），5 激活中（游戏状态为 Active：臂甲本场还没翻倍、不安油灯等着翻倍、石历等），
    /// 6 本场已触发（遗物自己记的“本场已用过”，如永冻冰晶、臂甲；界面不一定显示，玩家记得住），7 蜡制遗物的融化顺序
    /// （玩具盒每次融化遗物栏里第一个还没融化的蜡制遗物：1 为下一个，0 为不是或已融化）。
    /// </summary>
    void AddRelic(RelicModel r, Zone zone)
    {
        Begin(); _f[0] = r.DisplayAmount; _f[1] = r.ShowCounter ? 1 : 0; _f[2] = r.IsUsedUp ? 1 : 0;
        try
        {
            _f[3] = r.IsWax ? 1 : 0;
            _f[4] = r.IsMelted || r.Status == MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Disabled ? 1 : 0;
            _f[5] = r.Status == MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Active ? 1 : 0;
            _f[6] = UsedThisCombat(r) ? 1 : 0;
            if (r.IsWax && !r.IsMelted && zone == Zone.Ally && r.Owner is { } p)
                _f[7] = 1 + p.Relics.TakeWhile(x => x != r).Count(x => x.IsWax && !x.IsMelted);
            // 格式 20：8 本回合已触发（遗物自己记的“这回合已经用过”：小储君、君王石的 _usedThisTurn 等，每回合第一次花星、生成牌时触发）
            if (V20) _f[8] = UsedThisTurn(r) ? 1 : 0;
            // v0.113.0 修正（F113）：瓦皮的最大生命（30 或 60，看是“马上救”还是“谨慎”；计数 0 只是当前生命，战斗外看不到上限）
            if (F113 && r is MegaCrit.Sts2.Core.Models.Relics.Wuppy wp) _f[9] = wp.MaxHp;
            // 格式 29（G03）：律动残余 22 从现在到下个玩家回合开始最多还会掉多少血（MaxHpLoss − 本回合已挨，V29Common.RemnantLeft）；战斗外、遗物失效时 0
            if (V29 && r is MegaCrit.Sts2.Core.Models.Relics.BeatingRemnant && zone == Zone.Ally && r.Owner is { PlayerCombatState: not null } ow
                && CombatManager.Instance.IsInProgress && RemnantLeft(ow) is var left && float.IsFinite(left))
                _y[0] = left;
        }
        catch { }
        Emit(r, TokenKind.Relic, Vocab.Id(r), zone, -1, 0);
    }

    // 遗物自己记的“本场已触发”：私有布尔字段 _usedThisCombat、_wasUsedThisCombat、_activatedThisCombat、_blockGainedThisCombat、_tookDamageThisCombat 等
    static readonly Dictionary<Type, System.Reflection.FieldInfo[]> CombatFlags = new();
    static bool UsedThisCombat(RelicModel r)
    {
        var t = r.GetType();
        if (!CombatFlags.TryGetValue(t, out var fs))
            CombatFlags[t] = fs = t.GetFields(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                .Where(f => f.FieldType == typeof(bool) && f.Name.EndsWith("ThisCombat")).ToArray();
        foreach (var f in fs) if (GetF(f, r) is true) return true;
        return false;
    }

    static readonly Dictionary<Type, System.Reflection.FieldInfo[]> TurnFlags = new();
    static bool UsedThisTurn(RelicModel r)
    {
        var t = r.GetType();
        if (!TurnFlags.TryGetValue(t, out var fs))
            TurnFlags[t] = fs = t.GetFields(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                .Where(f => f.FieldType == typeof(bool) && f.Name.EndsWith("ThisTurn")).ToArray();
        foreach (var f in fs) if (GetF(f, r) is true) return true;
        return false;
    }


    void AddPotion(PotionModel p, Zone zone, int slot, float price = 0)
    {
        Begin(); _f[0] = slot; _f[2] = price;
        _f[1] = p.Usage == PotionUsage.AnyTime || (p.Usage == PotionUsage.CombatOnly && CombatManager.Instance.IsInProgress) ? 1 : 0;
        Emit(p, TokenKind.Potion, Vocab.Id(p), zone, -1, 0);
    }
}
