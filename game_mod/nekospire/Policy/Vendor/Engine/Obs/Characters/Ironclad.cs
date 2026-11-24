using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;

namespace Sts2Headless;

// 铁甲战士（格式 22，铁甲与亡灵契约师观测调研.md）：出牌的自伤、虚无牌回合末消耗引起的无惧疼痛与卡戎之灰。
// 内容表（哪些牌会自伤）写在这里，游戏更新改了这些牌时只改这个文件（见 引擎模块与游戏更新适配.md）。
public sealed partial class Observation
{
    /// <summary>
    /// 打出时让自己掉血的牌（OnPlay 里对自己 CreatureCmd.Damage，HpLoss 数值，Unblockable | Unpowered | Move）：
    /// 放血、祭品、御血术、血墙、烙印、突破、恶魔护盾（v0.111.0 的铁甲战士卡池里全部的这类牌）。
    /// </summary>
    static readonly HashSet<Type> SelfDamageCards = new()
    {
        typeof(MegaCrit.Sts2.Core.Models.Cards.Bloodletting), typeof(MegaCrit.Sts2.Core.Models.Cards.Offering), typeof(MegaCrit.Sts2.Core.Models.Cards.Hemokinesis),
        typeof(MegaCrit.Sts2.Core.Models.Cards.BloodWall), typeof(MegaCrit.Sts2.Core.Models.Cards.Brand), typeof(MegaCrit.Sts2.Core.Models.Cards.Breakthrough),
        typeof(MegaCrit.Sts2.Core.Models.Cards.DemonicShield),
    };

    /// <summary>
    /// 格式 22：打出一次这张牌自己掉多少血（不在 SelfDamageCards 里为 0）。同游戏的结算：伤害钩子（无实体压成 1 等）→ 不吃格挡 → 掉血钩子（钨棒、缓冲等），截掉小数。只读。
    /// </summary>
    static float SelfHpLoss(ICombatState cs, Player me, CardModel card)
    {
        try
        {
            if (!SelfDamageCards.Contains(card.GetType()) || !card.DynamicVars.TryGetValue("HpLoss", out var hv)) return 0;
            var props = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unblockable | MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered | MegaCrit.Sts2.Core.ValueProps.ValueProp.Move;
            float d = HitDmg(cs, me.Creature, me.Creature, hv.BaseValue, props, card);
            if (d <= 0) return 0;
            try { d = (float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyHpLost(cs.RunState, cs, me.Creature, (decimal)d, props, me.Creature, card, MegaCrit.Sts2.Core.Hooks.HpLossHookPhase.All, out _); } catch { }
            return MathF.Floor(Math.Max(0, d));
        }
        catch { return 0; }
    }

    /// <summary>这张牌打出后会不会被消耗（自己带消耗；或腐化在身时的技能牌）。打出过程中消耗别的牌（真正的勇气、燃烧契约等）不算，那要看选了哪些。</summary>
    static bool ExhaustsOnPlay(CardModel card, Player me)
    {
        try { return Kw(card).Contains(CardKeyword.Exhaust) || (card.Type == CardType.Skill && me.Creature.HasPower<MegaCrit.Sts2.Core.Models.Powers.CorruptionPower>()); }
        catch { return false; }
    }

    /// <summary>手里除这张牌以外的牌（这张牌打出时已经离开手牌，OnPlay 里看到的手牌没有它）。</summary>
    static List<CardModel> OtherHand(CardModel card, Player me)
    {
        try { return me.PlayerCombatState!.Hand.Cards.Where(c => !ReferenceEquals(c, card)).ToList(); }
        catch { return new List<CardModel>(); }
    }

    /// <summary>
    /// 格式 22：打出这张牌时另外消耗几张别的牌（张数确定，消耗哪张可能是随机或要选，这里只要张数，给无惧疼痛、卡戎之灰用）：
    /// 恶魔之焰消耗整手、重振精神消耗手里全部非攻击牌、坚毅（没升级随机一张，升级了选一张）、燃烧契约、烙印、余烬各一张（手里有牌时），
    /// 洁净从抽牌堆选一张。
    /// </summary>
    static int ExhaustOthers(CardModel card, Player me)
    {
        try
        {
            var hand = OtherHand(card, me);
            return card switch
            {
                MegaCrit.Sts2.Core.Models.Cards.FiendFire => hand.Count,
                MegaCrit.Sts2.Core.Models.Cards.SecondWind => hand.Count(c => c.Type != CardType.Attack),
                MegaCrit.Sts2.Core.Models.Cards.TrueGrit or MegaCrit.Sts2.Core.Models.Cards.BurningPact or MegaCrit.Sts2.Core.Models.Cards.Brand
                    or MegaCrit.Sts2.Core.Models.Cards.Cinder => Math.Min(1, hand.Count),
                MegaCrit.Sts2.Core.Models.Cards.Cleanse => Math.Min(1, me.PlayerCombatState!.DrawPile.Cards.Count),
                // 格式 29（G01，R3-格式29规格.md 5.4）：浩劫自动打出抽牌堆顶一张并强制消耗（抽牌堆空时先洗弃牌堆；顶牌已知是能打出的能力牌时不进消耗堆）；
                // THRASH 先打伤害、再随机消耗手里一张攻击牌；STOKE 消耗整手
                MegaCrit.Sts2.Core.Models.Cards.Havoc when V29 => HavocExhausts(me),
                MegaCrit.Sts2.Core.Models.Cards.Thrash when V29 => hand.Any(c => c.Type == CardType.Attack) ? 1 : 0,
                MegaCrit.Sts2.Core.Models.Cards.Stoke when V29 => hand.Count,
                _ => 0,
            };
        }
        catch { return 0; }
    }

    /// <summary>格式 29（G01）：浩劫这一次打出消耗几张（CardPileCmd.AutoPlayFromDrawPile，forceExhaust）：抽牌堆加弃牌堆没牌为 0；
    /// 顶牌已知（KnownTop）且是能打出的能力牌时为 0（能力牌打出后不进结算牌堆，GetResultLocationForCardPlay）；其余 1（不能打出的牌也照样消耗）。
    /// 顶牌已知是浩劫时它又自动打出下一张，连锁算下去（HavocChain）。</summary>
    static int HavocExhausts(Player me) => HavocChain(me).exhausts;

    /// <summary>格式 29（G01）：浩劫打出一次的连锁：消耗几张、确定自动打出的已知顶牌（不含连锁里的浩劫自己）。顶牌未知时只算消耗张数。</summary>
    static (int exhausts, List<CardModel> played) HavocChain(Player me)
    {
        var played = new List<CardModel>();
        try
        {
            var pcs = me.PlayerCombatState!;
            int avail = pcs.DrawPile.Cards.Count + pcs.DiscardPile.Cards.Count, n = 0;
            var top = KnownTop(pcs, pcs.DrawPile.Cards.Count);
            for (int i = 0; avail > 0 && i < 20; i++)
            {
                avail--;
                var t = i < top.Count ? top[i] : null;
                bool playable = t != null && !Kw(t).Contains(CardKeyword.Unplayable);
                if (!(playable && t!.Type == CardType.Power)) n++;
                if (t is MegaCrit.Sts2.Core.Models.Cards.Havoc && playable) continue;      // 连锁：它再打出下一张
                if (playable) played.Add(t!);
                break;
            }
            return (n, played);
        }
        catch { return (0, played); }
    }

    /// <summary>
    /// 格式 22：卡面数值里没有、写在出牌代码里的段数与获得格挡的次数：恶魔之焰的段数 = 消耗的张数（整手）；
    /// 重振精神每消耗一张获得一次格挡；邪眼本回合已经消耗过牌时获得两次格挡。返回（段数，每次打出获得格挡的次数），不是这几张牌时原样返回。
    /// </summary>
    static (float hits, int gains) IroncladCounts(CardModel card, Player? me, float hits)
    {
        try
        {
            if (me == null) return (hits, 1);
            switch (card)
            {
                // v0.113.0 修正（F113）：恶魔之焰的卡面有了 CalculatedHits（预览已过拇指夹），不再用手牌张数覆盖
                case MegaCrit.Sts2.Core.Models.Cards.FiendFire when !F113: return (OtherHand(card, me).Count, 1);
                case MegaCrit.Sts2.Core.Models.Cards.SecondWind: return (hits, OtherHand(card, me).Count(c => c.Type != CardType.Attack));
                case MegaCrit.Sts2.Core.Models.Cards.EvilEye when card.CombatState is { } cs
                    && CombatManager.Instance.History.Entries.OfType<MegaCrit.Sts2.Core.Combat.History.Entries.CardExhaustedEntry>().Any(e => e.HappenedThisTurn(cs) && e.Actor == me.Creature):
                    return (hits, 2);
            }
        }
        catch { }
        return (hits, 1);
    }

    /// <summary>遗物还在起作用（没融化、没失效）。</summary>
    static bool RelicLive(RelicModel r) => !r.IsMelted && r.Status != MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Disabled;

    /// <summary>
    /// 格式 22：回合结束时虚无牌被消耗（CombatManager.DoTurnEnd：充能球被动之后、手牌的回合结束效果之前；有回合结束效果的牌不算，
    /// 经 Hook.ShouldEtherealTrigger）。每消耗一张：无惧疼痛给层数点格挡（Unpowered，算进回合结束前的格挡 extra），卡戎之灰对全体造成伤害（all）。
    /// </summary>
    static void EtherealExhaust(ICombatState cs, Player me, ref float extra, Action<float> all)
    {
        try
        {
            var pcs = me.PlayerCombatState!;
            int n = pcs.Hand.Cards.Count(c =>
            {
                try { return !c.HasTurnEndInHandEffect && Kw(c).Contains(CardKeyword.Ethereal) && MegaCrit.Sts2.Core.Hooks.Hook.ShouldEtherealTrigger(cs, c); }
                catch { return false; }
            });
            if (n == 0) return;
            int fnp = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.FeelNoPainPower>();
            if (fnp > 0) extra += n * AfterimageBlock(cs, me, fnp);
            foreach (var r in me.Relics)
                if (r is MegaCrit.Sts2.Core.Models.Relics.CharonsAshes && RelicLive(r) && r.DynamicVars.TryGetValue("Damage", out var dv))
                    all(n * (float)dv.BaseValue);
        }
        catch { }
    }
}
