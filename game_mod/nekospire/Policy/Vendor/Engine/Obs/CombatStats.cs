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

// 战斗计数记录（类型 15）：玩家的本回合、本场计数，怪物的形态。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>
    /// 战斗计数记录（类型 15，版本 11）：所属记录为对应生物。特征 0 子类型：0 玩家的本回合、本场计数，1 怪物的形态，2 玩家的弃牌与抽牌（格式 21，AddSilentStats）。
    /// 玩家：1 本回合打出的牌数，2–4 其中攻击、技能、能力，5 本回合花的能量，6 本回合失去的生命（未被格挡的伤害），7 本场失去的生命，
    /// 8 上一轮（上个玩家回合与敌人回合）失去的生命，9 本回合抽的牌数，10 本场消耗的牌数，11 本场生成的充能球数，12 本回合消耗的牌数。
    /// 都来自战斗历史（CombatManager.History），游戏里都可数出来，不含随机数结果。
    /// </summary>
    void AddCombatStats(ICombatState cs, Player me, int playerTok)
    {
        try
        {
            Begin();
            int round = cs.RoundNumber;
            foreach (var e in CombatManager.Instance.History.Entries)
            {
                bool now = e.HappenedThisTurn(cs);
                switch (e)
                {
                    case MegaCrit.Sts2.Core.Combat.History.Entries.CardPlayFinishedEntry cp when now && cp.CardPlay.Player == me:
                        _f[1]++;
                        var t = cp.CardPlay.Card.Type;
                        if (t == CardType.Attack) _f[2]++; else if (t == CardType.Skill) _f[3]++; else if (t == CardType.Power) _f[4]++;
                        break;
                    case MegaCrit.Sts2.Core.Combat.History.Entries.EnergySpentEntry es when now && es.Actor.Player == me:
                        _f[5] += es.Amount; break;
                    case MegaCrit.Sts2.Core.Combat.History.Entries.DamageReceivedEntry dr when dr.Result.Receiver == me.Creature:
                        float u = dr.Result.UnblockedDamage;
                        _f[7] += u;
                        if (now) _f[6] += u;
                        else if (EntryRound != null && GetP(EntryRound, e) is int r && r == round - 1) _f[8] += u;
                        break;
                    case MegaCrit.Sts2.Core.Combat.History.Entries.CardDrawnEntry cd when now && cd.Card.Owner == me:
                        _f[9]++; break;
                    case MegaCrit.Sts2.Core.Combat.History.Entries.CardExhaustedEntry ce when ce.Card.Owner == me:
                        _f[10]++; if (now) _f[12]++; break;
                    case MegaCrit.Sts2.Core.Combat.History.Entries.OrbChanneledEntry oc when oc.Actor.Player == me:
                        _f[11]++; break;
                }
            }
            // 版本 13：13 随机目标的伤害一点也没打到攻击者时的预计掉血（同玩家生物特征 17 的算法，只是被动伤害只算目标确定的），
            // 14 按期望算打得死、按目标确定的伤害打不死的敌人数；充能球（都是公开状态推出来的）：15 球位已满（再生成一个就会挤出最前面的球），
            // 16 满时被挤出的最前面那个球的激发值，17、18 循环在下回合开始时触发最前面的球被动：冰霜给的格挡、闪电与黑暗等的数值
            _f[13] = _worstLoss; _f[14] = _luckyKills;
            var pcs = me.PlayerCombatState!;
            var orbs = pcs.OrbQueue.Orbs;
            if (orbs.Count > 0)
            {
                var front = orbs[0];
                bool full = pcs.OrbQueue.Capacity > 0 && orbs.Count >= pcs.OrbQueue.Capacity;
                _f[15] = full ? 1 : 0; _f[16] = full ? (float)front.EvokeVal : 0;
                float loop = 0;
                foreach (var pw in me.Creature.Powers) if (pw is MegaCrit.Sts2.Core.Models.Powers.LoopPower) loop += pw.Amount;
                if (front is MegaCrit.Sts2.Core.Models.Orbs.FrostOrb) _f[17] = loop * (float)front.PassiveVal; else _f[18] = loop * (float)front.PassiveVal;
            }
            // 版本 14：19 再打出一张牌会新增的回合结束自伤（目前只有永世沙漏的凋萎存在：倒数到 1 时下一张牌往手里塞一张凋萎，
            // 回合结束在手里打 3 点，沙漏每升级一次凋萎多 3 点）
            foreach (var f in cs.Creatures)
                foreach (var pw in f.Powers)
                    if (pw is MegaCrit.Sts2.Core.Models.Powers.WitheringPresencePower wp && wp.DisplayAmount <= 1)
                        _f[19] += 3 + 3 * (f.Monster is MegaCrit.Sts2.Core.Models.Monsters.Aeonglass ag ? ag.WitherUpgradeCount : 0);
            _nextPlaySelfDmg = _f[19];
            Emit(null, TokenKind.CombatStat, 0, Zone.Ally, playerTok, 0);
        }
        catch { }
    }

    /// <summary>格式 22：再打出一张牌会新增的回合结束自伤（战斗计数 19，永世沙漏的凋萎存在），出牌候选的 10 用。</summary>
    float _nextPlaySelfDmg;


    /// <summary>
    /// 格式 22：玩家的第三条战斗计数（战斗计数记录，特征 0 子类型为 3，所属记录为玩家），都来自战斗历史，玩家数得出来：
    /// 1 本场受到没被挡住的伤害的次数（扯碎 TearAsunder 的段数 = 1 + 它），2 本回合不是回合开始抽到的牌数（死亡行军 DeathMarch 的加成按它算），
    /// 3 本场打出过的虚无牌数（亡魂牵引 PullFromBelow 的段数），4 奥斯提本回合攻击了几次（猛晃 Rattle 的段数 = 1 + 它），5 本场召唤的总量。
    /// 这几张牌在手里时卡面预览已经对了，不在手里时模型要自己从历史里数，这里直接给。
    /// </summary>
    void AddStats22(ICombatState cs, Player me, int playerTok)
    {
        try
        {
            Begin(); _f[0] = 3;
            var osty = me.Osty;
            foreach (var e in CombatManager.Instance.History.Entries)
            {
                switch (e)
                {
                    case MegaCrit.Sts2.Core.Combat.History.Entries.DamageReceivedEntry dr when dr.Result.Receiver == me.Creature && dr.Result.UnblockedDamage > 0:
                        _f[1]++; break;
                    case MegaCrit.Sts2.Core.Combat.History.Entries.CardDrawnEntry cd when cd.Actor == me.Creature && !cd.FromHandDraw && e.HappenedThisTurn(cs):
                        _f[2]++; break;
                    case MegaCrit.Sts2.Core.Combat.History.Entries.CardPlayFinishedEntry cp when cp.CardPlay.Player == me && cp.WasEthereal:
                        _f[3]++; break;
                    case MegaCrit.Sts2.Core.Combat.History.Entries.CreatureAttackedEntry ca when osty != null && ca.Actor == osty && e.HappenedThisTurn(cs):
                        _f[4]++; break;
                    case MegaCrit.Sts2.Core.Combat.History.Entries.SummonedEntry se when se.Actor == me.Creature:
                        _f[5] += se.Amount; break;
                }
            }
            Emit(null, TokenKind.CombatStat, 0, Zone.Ally, playerTok, 0);
        }
        catch { }
    }

    /// <summary>
    /// 怪物的形态（战斗计数记录，子类型 1，版本 11）：有几条命的怪（目前只有实验体：111 → 212 → 313 血，A10 数值）给出
    /// 1 当前是第几形态（0 起），2 后面还有几个形态，3 后面形态的血量合计，4 剩余总血量（当前生命 + 后面形态）。数值来自怪物定义，不含随机数。
    /// </summary>
    void AddForms(MonsterModel m, Creature c, int tok)
    {
        try
        {
            if (m is not MegaCrit.Sts2.Core.Models.Monsters.TestSubject ts || TestSubjectRespawns?.GetValue(ts) is not int k) return;
            int[] hp = { ts.FirstFormHp, ts.SecondFormHp, ts.ThirdFormHp };
            float later = 0;
            for (int i = k + 1; i < hp.Length; i++) later += hp[i];
            Begin();
            _f[0] = 1; _f[1] = k; _f[2] = Math.Max(0, hp.Length - 1 - k); _f[3] = later; _f[4] = c.CurrentHp + later;
            Emit(null, TokenKind.CombatStat, 0, Zone.Enemy, tok, 0);
        }
        catch { }
    }
}
