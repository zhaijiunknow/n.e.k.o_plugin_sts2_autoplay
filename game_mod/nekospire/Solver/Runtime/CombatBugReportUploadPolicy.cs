using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Modding;
using MegaCrit.Sts2.Core.Models;

namespace CombatSolver;

// Upload guidance concerns this combat's content, independently of simulation admission.
internal sealed class CombatBugReportUploadPolicy
{
    public bool AllowsPlayerUploadGuidance { get; private set; } = true;

    public void Observe(CombatState combat)
    {
        if (AllowsPlayerUploadGuidance && EnumerateContent(combat).Any(IsThirdPartyContent))
            AllowsPlayerUploadGuidance = false;
    }

    private static bool IsThirdPartyContent(AbstractModel model)
    {
        var mod = AssemblyInfo.ModForType(model.GetType(), out bool isBaseGame);
        return !isBaseGame && mod != null;
    }

    private static IEnumerable<AbstractModel> EnumerateContent(CombatState combat)
    {
        foreach (var player in combat.Players)
        {
            yield return player.Character;
            foreach (var relic in player.Relics) yield return relic;
            for (int slot = 0; slot < player.PotionSlots.Count; slot++)
                if (player.GetPotionAtSlotIndex(slot) is { } potion) yield return potion;
            foreach (var card in player.Deck.Cards)
                foreach (var content in CardContent(card)) yield return content;
            if (player.PlayerCombatState is not { } state) continue;
            foreach (var card in state.AllCards)
                foreach (var content in CardContent(card)) yield return content;
            foreach (var orb in state.OrbQueue.Orbs) yield return orb;
        }
        foreach (var creature in combat.Creatures)
        {
            if (creature.Monster is { } monster) yield return monster;
            foreach (var power in creature.Powers) yield return power;
        }
    }

    private static IEnumerable<AbstractModel> CardContent(CardModel card)
    {
        yield return card;
        if (card.Enchantment is { } enchantment) yield return enchantment;
        if (card.Affliction is { } affliction) yield return affliction;
    }
}
