using MegaCrit.Sts2.Core.Entities.Players;

namespace CombatSolver.Engine.InCombat.Simulation;

internal sealed partial class CombatPredictionSimulator
{
    // CombatManager tracks effect nesting per player. Result-pile movement and the
    // empty-hand check happen after this card's effect body has left its scope.
    private List<Player>? _activeCardOrPotionEffects;

    internal bool IsExecutingCardOrPotionEffect(Player player)
        => _activeCardOrPotionEffects?.Contains(player) == true;

    internal IDisposable BeginCardOrPotionEffect(Player player)
    {
        (_activeCardOrPotionEffects ??= []).Add(player);
        return new CardOrPotionEffectScope(this, player, _activeCardOrPotionEffects.Count);
    }

    private sealed class CardOrPotionEffectScope(CombatPredictionSimulator owner, Player player, int depth) : IDisposable
    {
        public void Dispose()
        {
            if (owner._activeCardOrPotionEffects is not { } effects
                || effects.Count != depth || !ReferenceEquals(effects[^1], player))
                throw new InvalidOperationException("Card or potion effect scopes are unbalanced.");
            effects.RemoveAt(depth - 1);
        }
    }
}
