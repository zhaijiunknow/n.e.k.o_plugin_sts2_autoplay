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

// 头部 34 项（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>头部 34 项（见 观测格式.md 第二节）。</summary>
    void FillHeader(RunSession run, Decision d, RunState s, Player me, bool inCombat, int fmt)
    {
        Header[0] = fmt; Header[1] = (int)d.Kind; Header[2] = NumTokens; Header[3] = NumCands;
        Header[4] = d.Kind is DecisionKind.GameOver ? 1 : 0; Header[5] = run.IsVictory ? 1 : 0;
        Header[6] = s.CurrentActIndex; Header[7] = s.TotalFloor; Header[8] = me.Creature.CurrentHp; Header[9] = me.Creature.MaxHp;
        Header[10] = me.Gold; Header[11] = inCombat ? 1 : 0; Header[12] = UiFlows.CombatsWon; Header[13] = run.Steps;
        Header[14] = Vocab.Missing.Count; Header[15] = d.Kind == DecisionKind.Stuck ? 1 : 0;
        // 版本 16 有效抽牌（第十一份外部评审，决策记录 204）：26 现在出牌途中的抽牌会不会被放行（游戏的 Hook.ShouldDraw，fromHandDraw = false：
        // 小提琴在自己回合中途禁止一切抽牌、禁止抽牌的能力等；战斗外为 1）；27 手牌空位（CardPile.MaxCardsInHand 减当前手牌数，战斗外为 0）。
        // 卡面的抽牌数照旧在卡牌数值里：实际能抽几张 = 卡面 × 26，且不超过 27（另受抽牌堆加弃牌堆的张数限制）
        Header[26] = 1; Header[27] = 0;
        if (inCombat && me.PlayerCombatState is { } pcsD)
        {
            try { Header[26] = MegaCrit.Sts2.Core.Hooks.Hook.ShouldDraw(me.Creature.CombatState!, me, false, out _) ? 1 : 0; } catch { }
            Header[27] = Math.Max(0, CardPile.MaxCardsInHand - pcsD.Hand.Cards.Count);
        }
        // 版本 17（第十五份外部评审，决策记录 209）：28 已经在商店删过几次牌（游戏记在玩家的 ExtraFields.CardShopRemovalsUsed，离店后也在），
        // 29 下一次在商店删牌的价格（游戏的 MerchantCardRemovalEntry：基础价 + 涨价 × 已删次数，再过商店的改价，会员卡、信使等；店外按同样的改价算）
        Header[28] = me.ExtraFields.CardShopRemovalsUsed; Header[29] = RemovalPrice(me);
        // 版本 18（第十六份外部评审，决策记录 210）：30 这一组奖励里还有几个没领的卡牌奖励（奖励页：能领的全部；打开一个卡牌奖励时：除它以外的），
        // 31 现在结束回合、下一次正常抽牌阶段名义上抽几张（游戏的 Hook.ModifyHandDraw，怀表按本回合已打出的张数算；战斗外为 0）
        Header[30] = d.Kind switch
        {
            DecisionKind.Rewards when RewardsFlow.Pending is { } prw => prw.Available.Count(r => r is CardReward),
            DecisionKind.CardReward when RewardsFlow.ActiveSet is { } aset => RewardsFlow.Unclaimed(aset, RewardsFlow.ActiveAttempted, RewardsFlow.ActiveChoice).Count(r => r is CardReward),
            _ => 0,
        };
        Header[31] = inCombat ? NextHandDraw(me) : 0;
        // 版本 19（第十七份外部评审，决策记录 227）：32 现在结束回合、留到下回合的手牌张数（回合结束不丢弃的：符文金字塔一类让整手保留、
        // 带保留或本回合临时保留的牌；虚无的牌照样消耗），加上下回合抽牌前回到手里的无休手斧、流星锤；
        // 33 下回合正常抽牌实际能进手的张数（31 与手牌上限减 32 取小，不小于 0）。战斗外为 0
        if (inCombat && me.PlayerCombatState is { } pcsN)
        {
            try
            {
                var ccs = me.Creature.CombatState!;
                bool flush = MegaCrit.Sts2.Core.Hooks.Hook.ShouldFlush(ccs, me);
                // F113 第四轮：回合末在手里生效的牌（悔恨、凋萎、召唤之引等）结算后一律进弃牌堆或消耗（CombatManager.ResolveTurnEndCardEffects，早于 ShouldFlush），保留挡不住
                int stay = pcsN.Hand.Cards.Count(c => !Kw(c).Contains(CardKeyword.Ethereal) && (!flush || RetainThisTurn(c)) && !(F113 && c.HasTurnEndInHandEffect));
                int back = CombatManager.Instance.History.CardPlaysFinished.Count(e => e.HappenedThisTurn(ccs)
                    && (e.CardPlay.Card is MegaCrit.Sts2.Core.Models.Cards.ThrummingHatchet or MegaCrit.Sts2.Core.Models.Cards.Bolas || ((V24 || F113) && e.CardPlay.Card is MegaCrit.Sts2.Core.Models.Cards.Bounce))
                    && !pcsN.Hand.Cards.Contains(e.CardPlay.Card));
                // v0.113.0 修正（F113）：倦怠、神话形态在下回合抽牌前往手里塞悲伤、塔罗（BeforeHandDraw），同样占下回合的手牌空位。
                // 第三轮补上同样在抽牌前往手里加牌的能力（每回合都生效的；只管第 1 回合的遗物与下回合无关）：无尽刀刃的小刀、创意 AI、虚空呼唤、
                // 你好世界（下回合按那时的层数）、哨戒模式的扫视、梦魇的复制品、光谱偏移、定局（从抽牌堆选，最多层数张）
                int pre = 0;
                if (F113)
                    foreach (var pw in me.Creature.Powers)
                    {
                        if (pw is MegaCrit.Sts2.Core.Models.Powers.EnnuiPower) pre += pw.DynamicVars.Cards.IntValue;
                        else if (pw is MegaCrit.Sts2.Core.Models.Powers.MythicFormPower) pre += pw.Amount;
                        else if (pw is MegaCrit.Sts2.Core.Models.Powers.InfiniteBladesPower or MegaCrit.Sts2.Core.Models.Powers.CreativeAiPower
                                 or MegaCrit.Sts2.Core.Models.Powers.CallOfTheVoidPower or MegaCrit.Sts2.Core.Models.Powers.HelloWorldPower
                                 or MegaCrit.Sts2.Core.Models.Powers.SentryModePower or MegaCrit.Sts2.Core.Models.Powers.NightmarePower
                                 or MegaCrit.Sts2.Core.Models.Powers.SpectrumShiftPower) pre += Math.Max(0, pw.Amount);
                        else if (pw is MegaCrit.Sts2.Core.Models.Powers.ForegoneConclusionPower)
                            pre += Math.Min(Math.Max(0, pw.Amount), pcsN.DrawPile.Cards.Count + pcsN.DiscardPile.Cards.Count);
                    }
                // 格式 29（G08、G22）：百年积木在敌方回合抽的 3 张；侵略在回合开始前从弃牌堆拿的攻击牌；黑暗之拥、金纸在回合末（弃完手牌之后）为虚无消耗补抽的牌
                if (V29) pre += HeaderPre29(me, pcsN, ccs, flush);
                // v0.113.0 修正（F113）：敌方回合里敌人往手里塞的牌（螨虫的毒素、机甲骑士的灼伤，见 HandAdds），手牌满了进弃牌堆
                int shoved = F113 ? Math.Min(HandShoved, Math.Max(0, CardPile.MaxCardsInHand - stay - back)) : 0;
                pre = Math.Min(pre, Math.Max(0, CardPile.MaxCardsInHand - stay - back - shoved));     // 手牌满了塞不进去（进弃牌堆）
                Header[32] = stay + back + shoved + pre;
                Header[33] = Math.Max(0, Math.Min(Header[31], CardPile.MaxCardsInHand - Header[32]));
            }
            catch { }
        }
        // 本幕 Boss（游戏在地图和顶栏上明示）：整局为本幕的 Boss 与第二个 Boss（A10 最后一幕连打两个，没有为 0）；
        // 战斗模式没有真实的幕，只在打 Boss 时填这场的遭遇
        // 版本 15：24 本幕已经打赢的 Boss 数，25 本幕还要打的 Boss 数（含正在打的这一场）。
        // 战斗模式的 Boss 练习与整局对齐：16、17 为这一幕的第一个、第二个 Boss（连打两场都填两个，第二个 Boss 单打时 16 为已经打赢的第一个），
        // 24、25 按这次练习实际要打的场数算（单打一个 Boss 为 0、1；连打第一场 0、2，第二场 1、1；第二个 Boss 单打 1、1）；非 Boss 练习全为 0
        if (run.CombatOnly)
        {
            if (inCombat && s.CurrentRoom is CombatRoom { RoomType: RoomType.Boss } br)
            {
                int cur = Vocab.Id(br.Encounter);
                if (BossPrev != 0) { Header[16] = BossPrev; Header[17] = cur; Header[24] = 1; Header[25] = 1; }
                else { Header[16] = cur; Header[17] = BossNext; Header[24] = 0; Header[25] = BossNext != 0 ? 2 : 1; }
            }
            Header[23] = Stage;     // 连打练习：已经开打的第几场（0 起，决策记录 187）
        }
        else
        {
            Header[16] = Vocab.Id(s.Act.BossEncounter); Header[17] = Vocab.Id(s.Act.SecondBossEncounter);
            ActHistory(s);
            BossCount(s, inCombat);
        }
    }

    /// <summary>
    /// 格式 29：头部 32 在下回合抽牌之前多进手的牌（封顶前），R3-格式29规格.md 5.3 G08、G22：
    ///   百年积木：本场没触发、回合末手牌自伤为 0、敌方回合预计掉血 &gt; 0（_et29，包 1 的回合预演拆分）时，敌方回合第一次掉血抽 Cards 张（敌方回合小提琴不挡）；
    ///   侵略（AggressionPower.BeforeSideTurnStart）：min(层数, 弃牌堆的攻击牌 + 手里回合末进弃牌堆的攻击牌)；
    ///   黑暗之拥：层数 × 回合末虚无消耗的张数（DarkEmbracePower.AfterSideTurnEnd，弃完手牌之后才抽）；金纸：计数 + 回合末虚无消耗张数跨过 ExhaustAmount 的倍数各 1 张（JossPaper.AfterSideTurnEnd）。
    ///   这两项在玩家一方回合末抽，小提琴挡（乘现在的 Hook.ShouldDraw）。抽牌都不超过抽牌堆 + 弃牌堆（含回合末弃掉的手牌）。
    /// 愚者弹跳的重复计不做（R3 规格 G22）。
    /// </summary>
    int HeaderPre29(Player me, PlayerCombatState pcs, ICombatState cs, bool flush)
    {
        int add = 0;
        try
        {
            var hand = pcs.Hand.Cards;
            bool Eth(CardModel c) { try { return !c.HasTurnEndInHandEffect && Kw(c).Contains(CardKeyword.Ethereal) && MegaCrit.Sts2.Core.Hooks.Hook.ShouldEtherealTrigger(cs, c); } catch { return false; } }
            bool Stay(CardModel c) => !Kw(c).Contains(CardKeyword.Ethereal) && (!flush || RetainThisTurn(c)) && !c.HasTurnEndInHandEffect;
            int eth = hand.Count(Eth);
            var toDiscard = hand.Where(c => !Stay(c) && !Eth(c)).ToList();
            int pile = pcs.DrawPile.Cards.Count + pcs.DiscardPile.Cards.Count + toDiscard.Count;
            bool canDraw = true;
            try { canDraw = MegaCrit.Sts2.Core.Hooks.Hook.ShouldDraw(cs, me, false, out _); } catch { }
            int late = 0;      // 玩家一方回合末补抽的
            if (eth > 0 && canDraw)
            {
                if (me.Creature.GetPower<MegaCrit.Sts2.Core.Models.Powers.DarkEmbracePower>() is { } de && de.Amount > 0) late += de.Amount * eth;
                foreach (var r in me.Relics)
                    if (r is MegaCrit.Sts2.Core.Models.Relics.JossPaper && RelicLive(r) && r.DynamicVars["ExhaustAmount"].IntValue is var per && per > 0)
                        late += (r.DisplayAmount + eth) / per;
            }
            late = Math.Min(late, pile);
            add += late;
            if (_et29.HandLoss <= 0 && _et29.FoeLoss > 0)
                foreach (var r in me.Relics)
                    if (r is MegaCrit.Sts2.Core.Models.Relics.CentennialPuzzle && RelicLive(r) && !UsedThisCombat(r))
                        add += Math.Min(r.DynamicVars.Cards.IntValue, Math.Max(0, pile - late));
            if (me.Creature.GetPower<MegaCrit.Sts2.Core.Models.Powers.AggressionPower>() is { } ag && ag.Amount > 0)
                add += Math.Min(ag.Amount, pcs.DiscardPile.Cards.Count(c => c.Type == CardType.Attack) + toDiscard.Count(c => c.Type == CardType.Attack));
        }
        catch { }
        return add;
    }
}
