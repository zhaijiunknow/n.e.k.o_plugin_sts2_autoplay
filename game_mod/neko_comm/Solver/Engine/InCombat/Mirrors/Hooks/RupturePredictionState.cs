using MegaCrit.Sts2.Core.Models;
using CombatSolver.Engine.Common;

namespace CombatSolver.Engine.InCombat.Mirrors.Hooks;

internal sealed class RupturePredictionState : IPredictionStateForkable
{
    public Dictionary<CardModel, int> StrengthByCard { get; private set; } = [];

    public object Fork(PredictionForkContext context)
        => new RupturePredictionState
        {
            StrengthByCard = new Dictionary<CardModel, int>(StrengthByCard),
        };
}
