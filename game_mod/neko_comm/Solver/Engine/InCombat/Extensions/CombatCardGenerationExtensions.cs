using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Extensions;
using MegaCrit.Sts2.Core.Factories;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Random;
using CombatSolver.Engine.Common;

namespace CombatSolver.Engine.InCombat.Extensions;

internal static class CombatCardGenerationExtensions
{
    public static IEnumerable<CardModel> FilterForCombatAndPlayerCount(
        this IEnumerable<CardModel> cards,
        CardMultiplayerConstraint multiplayerConstraint)
    {
        IEnumerable<CardModel> combatCards = CardFactory.FilterForCombat(cards);
        return multiplayerConstraint == CardMultiplayerConstraint.SingleplayerOnly
            ? combatCards.Where(card => card.MultiplayerConstraint != CardMultiplayerConstraint.MultiplayerOnly)
            : combatCards.Where(card => card.MultiplayerConstraint != CardMultiplayerConstraint.SingleplayerOnly);
    }

    // Mirrors CardFactory.GetDistinctForCombat, but does not create cards for the player.
    public static IEnumerable<CardModel> TakeRandomDistinctForCombat(
        this IEnumerable<CardModel> cards,
        Player player,
        int count,
        Rng rng,
        CardMultiplayerConstraint multiplayerConstraint)
    {
        return cards.FilterForCombatAndPlayerCount(multiplayerConstraint).TakeRandom(count, rng);
    }

    // Mirrors CardFactory.GetForCombat, but does not create cards for the player.
    public static IEnumerable<CardModel> TakeRandomForCombat(
        this IEnumerable<CardModel> cards,
        Player player,
        int count,
        Rng rng,
        CardMultiplayerConstraint multiplayerConstraint)
    {
        var options = cards.FilterForCombatAndPlayerCount(multiplayerConstraint).ToList();
        if (options.Count == 0)
        {
            return [];
        }

        List<CardModel> results = [];
        for (var i = 0; i < count; i++)
        {
            results.Add(rng.NextItem(options)!);
        }

        return results;
    }

    // Mirrors CardFactory.GetDistinctForCombat, but returns PredictedCard instead of CardModel.
    public static IEnumerable<PredictedCard> GetDistinctForCombat(
        this IEnumerable<CardModel> cards,
        Player player,
        int count,
        Rng rng,
        CardMultiplayerConstraint multiplayerConstraint)
    {
        return cards
            .TakeRandomDistinctForCombat(player, count, rng, multiplayerConstraint)
            .Select(card => PredictedCard.Create(card, player));
    }

    // Mirrors CardFactory.GetForCombat, but returns PredictedCard instead of CardModel.
    public static IEnumerable<PredictedCard> GetForCombat(
        this IEnumerable<CardModel> cards,
        Player player,
        int count,
        Rng rng,
        CardMultiplayerConstraint multiplayerConstraint)
    {
        return cards
            .TakeRandomForCombat(player, count, rng, multiplayerConstraint)
            .Select(card => PredictedCard.Create(card, player));
    }
}
