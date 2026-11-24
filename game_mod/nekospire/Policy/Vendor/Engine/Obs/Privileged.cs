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

// 特权特征：怪物下一次抽签的推演（只给价值网络）。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>
    /// 特权（版本 11）：怪物下一次抽签的推演。游戏在玩家回合开始时按敌人顺序依次给每只怪抽下一招（CombatManager → PrepareForNextTurn →
    /// RollMove，用整局共用的 MonsterAi 随机数流）。这里复制那条流（不动原来的），按同样顺序、按现在的局面替每只活着的怪走一遍状态机，
    /// 返回每只怪推演出的下一招。敌人回合里若有别的消耗（制造者召唤、扑翼等）或局面变了，结果可能不对；只给价值网络。
    /// </summary>
    static Dictionary<MonsterModel, string>? SimulateNextRolls(ICombatState cs)
    {
        try
        {
            var rng = new MegaCrit.Sts2.Core.Random.Rng(RunSession.State.Rng.GetRng(MegaCrit.Sts2.Core.Entities.Rngs.RunRngType.MonsterAi).ToSerializable());
            var res = new Dictionary<MonsterModel, string>(ReferenceEqualityComparer.Instance);
            foreach (var c in cs.Enemies)
            {
                if (!c.IsAlive || c.Monster is not { MoveStateMachine: { } sm, NextMove: { StateId: not null } cur } m) continue;
                MonsterState st = cur;
                for (int i = 0; i < 8; i++)
                {
                    string? next = st.GetNextState(c, rng);
                    if (string.IsNullOrEmpty(next) || !sm.States.TryGetValue(next, out var ns)) break;
                    st = ns;
                    if (st is MoveState) { res[m] = st.Id; break; }
                }
            }
            return res;
        }
        catch { return null; }
    }
}
