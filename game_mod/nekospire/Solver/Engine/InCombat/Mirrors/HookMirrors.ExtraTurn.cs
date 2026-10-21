using CombatSolver.Engine.Common;
using CombatSolver.Engine.InCombat.Mirrors.Hooks.TurnEnd;
using CombatSolver.Engine.InCombat.Simulation;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;

namespace CombatSolver.Engine.InCombat.Mirrors;

internal static partial class HookMirrors
{
    /// <summary>
    /// 按原生监听顺序判断额外回合，在第一个返回 true 的来源处结束。
    /// </summary>
    public static bool ShouldTakeExtraTurn(
        CombatPredictionSimulator simulator, SimulatedCombatState combat, Player player)
    {
        ExtraTurnMirrors.Seal();
        ExtraTurnMirrorContext? context = null;
        foreach (AbstractModel listener in IterateCombatHookListeners(simulator, MirroredHookMask.ExtraTurnCallbacks))
        {
            context ??= new ExtraTurnMirrorContext { Simulator = simulator, Player = player };
            if (ExtraTurnMirrors.ShouldTakeExtraTurn(listener, context))
                return true;
        }
        return false;
    }

    /// <summary>
    /// 在固定监听快照上按原生顺序结算每个来源，选择暂停由动作重放恢复。
    /// </summary>
    public static bool AfterTakingExtraTurn(
        CombatPredictionSimulator simulator, SimulatedCombatState combat, Player player)
    {
        ExtraTurnMirrors.Seal();
        if (simulator.HasPendingChoice)
            return false;
        List<CardHookReceiver>? receivers = null;
        foreach (AbstractModel listener in IterateCombatHookListeners(simulator, MirroredHookMask.ExtraTurnCallbacks))
        {
            PredictedCard? card = listener is CardModel model
                ? simulator.State.GetPlayerCombatState(model.Owner).FindCard(model) : null;
            (receivers ??= []).Add(new CardHookReceiver(listener, card));
        }
        if (receivers is null)
            return true;

        var context = new ExtraTurnMirrorContext { Simulator = simulator, Player = player };
        foreach (CardHookReceiver receiver in receivers)
        {
            ExtraTurnMirrors.AfterTakingExtraTurn(receiver.Current, context);
            if (simulator.HasPendingChoice)
                return false;
        }
        return true;
    }
}
