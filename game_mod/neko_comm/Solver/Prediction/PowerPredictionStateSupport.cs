using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Powers;
using CombatSolver.Engine.InCombat.Mirrors.Hooks.Attack;
using CombatSolver.Engine.InCombat.Mirrors.Hooks.Card;
using CombatSolver.Engine.InCombat.Mirrors.Hooks.Damage;
using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver;

internal static class PowerPredictionStateSupport
{
    public static void CaptureRootState(
        CombatPredictionSimulator simulator,
        PowerModel target,
        PowerModel source)
    {
        switch (target, source)
        {
            case (SkittishPower value, SkittishPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new SkittishPredictionState(original));
                break;
            case (PanachePower value, PanachePower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new PanachePredictionState(original));
                break;
            case (SoulboundPower value, SoulboundPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new SoulboundPredictionState(original));
                break;
            case (HellraiserPower value, HellraiserPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new HellraiserPredictionState(original));
                break;
            case (CacophonyPower value, CacophonyPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new CacophonyPredictionState(original));
                break;
            case (AutomationPower value, AutomationPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new AutomationPredictionState(original));
                break;
            case (JugglingPower value, JugglingPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new JugglingPredictionState(original));
                break;
            case (ChainsOfBindingPower value, ChainsOfBindingPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new ChainsOfBindingPredictionState(original));
                break;
            case (SurroundedPower value, SurroundedPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new SurroundedPredictionState(original));
                break;
            case (VoidFormPower value, VoidFormPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new VoidFormPredictionState(original));
                break;
            case (FeralPower value, FeralPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new FeralPredictionState(original));
                break;
            case (HardenedShellPower value, HardenedShellPower original):
                _ = simulator.StateStore.GetReadOnly(value, () => new HardenedShellPredictionState(original));
                break;
        }
    }
}
