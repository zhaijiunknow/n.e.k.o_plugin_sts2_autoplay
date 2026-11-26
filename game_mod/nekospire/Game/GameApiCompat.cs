using System.Reflection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.FoolArrows;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;

namespace NekoComm.Game;

/// <summary>
/// Optional 0.113.x APIs. The installed game no longer destroys arrows, whereas the pinned
/// 0.113.1 training engine does. Probe once, preserving either runtime's semantics without
/// binding the mod to members/types absent in the other assembly.
/// </summary>
internal static class GameApiCompat
{
    private static readonly PropertyInfo? DestroyAfterImpaledProperty =
        typeof(ArrowSlot).GetProperty("DestroyAfterImpaled", BindingFlags.Public | BindingFlags.Instance);
    private static readonly bool HasDestroyedStatus = Enum.TryParse<ArrowStatus>("Destroyed", out _);
    public static readonly bool SupportsArrowDestruction = HasDestroyedStatus
        && DestroyAfterImpaledProperty?.PropertyType == typeof(bool);

    // The packed kernel reserves four status bits. 15 is an unreachable comparison sentinel on
    // games without Destroyed; all writers of the destroy bit are capability-gated below.
    public static readonly ArrowStatus DestroyedArrowStatus =
        Enum.TryParse<ArrowStatus>("Destroyed", out var status) ? status : (ArrowStatus)15;

    private static readonly Type? FreeCardPowerType =
        typeof(PowerModel).Assembly.GetType("MegaCrit.Sts2.Core.Models.Powers.FreeCardPower");

    public static bool ArrowDestroyAfterImpaled(ArrowSlot arrow) =>
        SupportsArrowDestruction && (bool)DestroyAfterImpaledProperty!.GetValue(arrow)!;

    public static bool IsDestroyedArrow(ArrowStatus status) =>
        HasDestroyedStatus && status == DestroyedArrowStatus;

    public static bool IsFreeCardPower(AbstractModel model) =>
        FreeCardPowerType?.IsInstanceOfType(model) == true;

    private static readonly Action<CardModel> UpgradeCard = CreateUpgradeCard();

    // The new optional `silent` parameter changes the CLR signature even for source calls
    // with no arguments. Bind to the runtime signature so one DLL works with both games.
    public static void UpgradeInternal(CardModel card) => UpgradeCard(card);

    private static Action<CardModel> CreateUpgradeCard()
    {
        var noArgs = typeof(CardModel).GetMethod("UpgradeInternal", Type.EmptyTypes);
        if (noArgs != null)
            return noArgs.CreateDelegate<Action<CardModel>>();
        var withSilent = typeof(CardModel).GetMethod("UpgradeInternal", [typeof(bool)]);
        if (withSilent == null)
            throw new MissingMethodException(typeof(CardModel).FullName, "UpgradeInternal");
        var upgrade = withSilent.CreateDelegate<Action<CardModel, bool>>();
        return card => upgrade(card, false);
    }

    // Exact implementation of the removed MelodyCmd method in the pinned 0.113.1 assembly:
    // finished plays, this turn, this player; a multi-tag card counts once, replays count again.
    public static int MelodyCardsPlayedThisTurn(Player player) =>
        CombatManager.Instance.History.CardPlaysFinished.Count(e =>
            e.HappenedThisTurn(player.Creature.CombatState)
            && e.CardPlay.Player == player
            && (e.CardPlay.Card.Tags.Contains(CardTag.Wah)
                || e.CardPlay.Card.Tags.Contains(CardTag.Cho)
                || e.CardPlay.Card.Tags.Contains(CardTag.Ki)));
}
