using System.Reflection;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Relics;
using MegaCrit.Sts2.Core.Factories;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Odds;
using MegaCrit.Sts2.Core.Random;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;

namespace Sts2Headless;

/// <summary>
/// 超级特权价值头的“预知”记录（2026-11-06，决策记录 280）：在整局预生成内容（类型 17）之外，再把玩家看不到、但由当前随机数状态
/// 已经决定的近期结果推算出来，只给训练期的价值网络。reset 同时带 "privileged":true 与 "oracle":true 时，整局模式战斗外的决策帧
/// 多出类型 20 的记录；不带时帧逐字节不变，格式版本不变。
///
/// 推算一律在副本上做，不改真实对局：随机数用 ToSerializable 复制一份；需要调用游戏自己的有状态方法时（问号房），先存下随机数与概率表，
/// 调完原样还原。遗物袋只读不取。没有调用任何会登记新卡、新遗物或触发钩子副作用的路径（卡牌奖励按 CardFactory.CreateForReward 的
/// 抽法在副本上重做，不经 RunState.CreateCard、不跑奖励修改钩子），所以个别遗物、诅咒改动奖励时预览会和实际不同。
///
/// 记录特征：0 类别、1 本类里的先后、2 子类（稀有度、是否升级、房间类型等）、3 附加数值；词表 ID 为对应的牌、遗物、药水（没有为 0）。
///   类别 0/1/2：下一场普通战 / 精英 / Boss 打完的卡牌奖励（各 3 张；子类 = 稀有度 × 2 + 是否升级）。三种房间各自从当前状态独立推算，
///               按游戏的顺序先掷药水（概率表副本）、金币，再抽卡。
///   类别 3/4/5：同上三种房间的药水掉落（有掉落时一条，词表为药水；附加数值 = 1）。
///   类别 6：精英战的遗物奖励（稀有度按奖励随机数副本掷，再从遗物袋里只读取最前面那件可用的）。
///   类别 7：遗物袋里普通 / 罕见 / 稀有各自最前面的 3 件（子类 = 稀有度）。
///   类别 8：接下来两个问号房各会变成什么（子类 = 房间类型）。
/// </summary>
public sealed partial class Observation
{
    /// <summary>reset 带 "oracle":true（且 privileged）时输出类型 20 的预知记录。</summary>
    public static bool Oracle;

    static readonly MethodInfo? AvailableDeque = GameCompat.Method(typeof(RelicGrabBag), "GetAvailableDeque", BindingFlags.Instance | BindingFlags.NonPublic, "预知记录：遗物袋");
    static readonly MethodInfo? NextAllowedRarity = GameCompat.Method(typeof(CardFactory), "GetNextAllowedRarity", BindingFlags.Static | BindingFlags.NonPublic, "预知记录：卡牌奖励稀有度");
    static readonly FieldInfo? OddsRng = GameCompat.Field(typeof(AbstractOdds), "_rng", BindingFlags.Instance | BindingFlags.NonPublic, "预知记录：概率表的随机数");
    static readonly FieldInfo? NonEventOdds = GameCompat.Field(typeof(UnknownMapPointOdds), "_nonEventOdds", BindingFlags.Instance | BindingFlags.NonPublic, "预知记录：问号房概率");
    static readonly PropertyInfo? UpgradeOddScaling = GameCompat.Property(typeof(CardFactory), "UpgradedCardOddScaling", BindingFlags.Static | BindingFlags.NonPublic, "预知记录：卡牌奖励的升级概率");

    void OracleRec(int id, int cat, int order, int sub, float val = 0)
    {
        Begin(); _f[0] = cat; _f[1] = order; _f[2] = sub; _f[3] = val;
        Emit(null, TokenKind.Oracle, id, Zone.None, -1, 0);
    }

    void EmitOracle(RunState s, Player me)
    {
        try { OracleRewards(me, RoomType.Monster, 0); } catch { }
        try { OracleRewards(me, RoomType.Elite, 1); } catch { }
        // F113：最后一幕的 Boss 不给奖励（RewardsSet：最后一幕 Boss 房不生成），原来照样输出它的奖励预测
        if (!(F113 && s.CurrentActIndex >= s.Acts.Count - 1)) try { OracleRewards(me, RoomType.Boss, 2); } catch { }
        try { OracleRelicBag(s, me); } catch { }
        try { OracleUnknownRooms(s); } catch { }
        if (Environment.GetEnvironmentVariable("STS2_ORACLE_DEBUG") == "1")       // 调试：奖励随机数与稀有度概率的当前状态
            try { OracleRec(0, 99, me.PlayerRng.Rewards.ToSerializable().counter, 0, me.PlayerOdds.CardRarity.CurrentValue); } catch { }
    }

    /// <summary>某种房间打完的奖励：照 RewardsSet.WithRewardsFromRoom 与 GenerateWithoutOffering 的顺序，在奖励随机数副本上掷。</summary>
    void OracleRewards(Player me, RoomType room, int cat)
    {
        var rng = new Rng(me.PlayerRng.Rewards.ToSerializable());
        var potionOdds = new PotionRewardOdds(me.PlayerOdds.PotionReward.CurrentValue, rng);
        var cardOdds = new CardRarityOdds(me.PlayerOdds.CardRarity.CurrentValue, rng);
        // 1. 建奖励表时掷药水（PotionRewardOdds.Roll，不跑强制掉落的钩子）
        float chance = potionOdds.CurrentValue + (room == RoomType.Elite ? 0.25f : 0f) * 0.5f;
        // F113：强制掉药水时（白兽雕像，Hook.ShouldForcePotionReward）游戏直接返回、不掷（PotionRewardOdds.Roll），原来多掷一次，后面全部错位
        bool forced = false;
        if (F113) try { forced = MegaCrit.Sts2.Core.Hooks.Hook.ShouldForcePotionReward(me.RunState, me, room); } catch { }
        bool potion = forced || rng.NextFloat() < chance;
        // 2. 逐项生成：金币、药水、卡牌、（精英）遗物
        rng.NextInt(0, 2);
        if (potion)
        {
            var p = PotionFactory.CreateRandomPotionOutOfCombat(me, rng);
            OracleRec(Vocab.Id(p), cat + 3, 0, 0, 1);
        }
        var opts = CardCreationOptions.ForRoom(me, room);
        // 同 CardFactory.FilterForPlayerCount：单人局去掉只限多人的牌（漏了这一步，可选的牌多出几张，抽到的就全错位）
        var pool = opts.GetPossibleCards(me).Where(c => c.MultiplayerConstraint != CardMultiplayerConstraint.MultiplayerOnly).ToList();
        var taken = new List<CardModel>();
        var oddsType = opts.RarityOdds;
        for (int i = 0; i < 3; i++)
        {
            var cand = pool.Except(taken).ToList();      // CreateForReward 每张牌都重新 Except 已抽的（顺序不变）
            var allowed = cand.Select(c => c.Rarity).ToHashSet();
            if (allowed.Count == 0) break;
            var rolled = cardOdds.Roll(oddsType);
            var rarity = NextAllowedRarity?.Invoke(null, new object[] { rolled, (Func<CardRarity, bool>)allowed.Contains }) is CardRarity r ? r : rolled;
            var card = rng.NextItem(cand.Where(c => c.Rarity == rarity));
            if (card == null) break;
            taken.Add(card);
            // 升级（CardFactory.RollForUpgrade，基础概率 0，非稀有牌每幕加 UpgradedCardOddScaling；不跑修改升级概率的钩子）
            float roll = rng.NextFloat();
            decimal odds = card.Rarity != CardRarity.Rare ? me.RunState.CurrentActIndex * UpgradeScaling() : 0m;
            bool up = card.IsUpgradable && (decimal)roll <= odds;
            OracleRec(Vocab.Id(card), cat, i, (int)card.Rarity * 2 + (up ? 1 : 0));
        }
        if (room == RoomType.Elite)
        {
            var rr = RelicFactory.RollRarity(rng);
            if (PeekRelic(me.RelicGrabBag, rr, me.RunState, 0) is { } relic) OracleRec(Vocab.Id(relic), 6, 0, (int)rr);
        }
    }

    static decimal UpgradeScaling()
    {
        var p = UpgradeOddScaling;
        return p?.GetValue(null) is decimal d ? d : 0.25m;
    }

    /// <summary>遗物袋里某个稀有度第 k 件可用的遗物（只读，不从袋里取走）。</summary>
    static RelicModel? PeekRelic(RelicGrabBag bag, RelicRarity rarity, IRunState rs, int k)
    {
        if (AvailableDeque?.Invoke(bag, new object[] { rarity, rs, (Func<RelicModel, bool>)(_ => true) }) is not List<RelicModel> dq) return null;
        return k < dq.Count ? dq[k] : null;
    }

    void OracleRelicBag(RunState s, Player me)
    {
        foreach (var rarity in new[] { RelicRarity.Common, RelicRarity.Uncommon, RelicRarity.Rare })
            for (int k = 0; k < 3; k++)
                if (PeekRelic(me.RelicGrabBag, rarity, s, k) is { } r) OracleRec(Vocab.Id(r), 7, k, (int)rarity);
    }

    /// <summary>接下来两个问号房：在真实的问号概率对象上掷，掷完把随机数与概率表原样还原。</summary>
    void OracleUnknownRooms(RunState s)
    {
        var odds = s.Odds.UnknownMapPoint;
        if (OddsRng?.GetValue(odds) is not Rng rng || NonEventOdds?.GetValue(odds) is not Dictionary<RoomType, float> table) return;
        var saveRng = rng.ToSerializable();
        var saveTable = new Dictionary<RoomType, float>(table);
        try
        {
            for (int k = 0; k < 2; k++)
            {
                var t = odds.Roll(Array.Empty<RoomType>(), s);
                OracleRec(0, 8, k, (int)t);
            }
        }
        finally
        {
            rng.LoadFromSerializable(saveRng);
            table.Clear();
            foreach (var kv in saveTable) table[kv.Key] = kv.Value;
        }
    }
}
