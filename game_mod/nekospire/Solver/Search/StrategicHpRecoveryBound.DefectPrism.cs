using System.Collections.Frozen;
using CombatSolver.Engine.Common;
using CombatSolver.Engine.InCombat.Simulation;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Characters;
using MegaCrit.Sts2.Core.Models.Monsters;
using MegaCrit.Sts2.Core.Models.Orbs;
using MegaCrit.Sts2.Core.Models.Potions;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.Models.Relics;

namespace CombatSolver;

internal static partial class StrategicHpRecoveryBound
{
    // Closed native actions: Turbo generates Void in combat, Compact transforms
    // existing statuses into Fuel in their combat piles, and Scavenge exhausts
    // an existing hand card. No accepted action adds a card to the run Deck.
    private static readonly FrozenSet<Type> DefectPrismCards = new Type[]
    {
        typeof(Compact), typeof(SporeMind), typeof(Zap), typeof(Turbo),
        typeof(DefendDefect), typeof(Skim), typeof(Quadcast), typeof(FlashOfSteel),
        typeof(Coolheaded), typeof(AscendersBane), typeof(Greed), typeof(Scavenge),
        typeof(Darkness), typeof(Fuel), typeof(MegaCrit.Sts2.Core.Models.Cards.Void),
    }.ToFrozenSet();

    private static readonly FrozenSet<Type> DefectPrismPowers = new Type[]
    {
        typeof(StrengthPower), typeof(DexterityPower), typeof(WeakPower),
        typeof(VulnerablePower), typeof(FrailPower), typeof(ArtifactPower),
        typeof(PlatingPower), typeof(VitalSparkPower), typeof(TaintedPower),
        typeof(EnergyNextTurnPower), typeof(NoDrawPower), typeof(FocusPower),
    }.ToFrozenSet();

    private static readonly FrozenSet<Type> DefectPrismRelics = new Type[]
    {
        typeof(CrackedCore), typeof(CursedPearl), typeof(MiniatureTent),
        typeof(BookOfFiveRings), typeof(TinyMailbox), typeof(StrikeDummy),
        typeof(Pendulum), typeof(ArchaicTooth), typeof(Gorget),
    }.ToFrozenSet();

    internal static bool CanCertifyDefectPrismHealingEnvironment(
        CombatPredictionSimulator simulator, Player player)
    {
        SimulatedCombatState combat = (SimulatedCombatState)simulator.State.CombatState;
        return player.Character.GetType() == typeof(Defect) && combat.Players.Count == 1
            && combat.KnownEnemies.Count > 0
            && combat.KnownEnemies.All(enemy => enemy.Monster?.GetType() == typeof(InfestedPrism))
            && combat.Modifiers.Count == 0
            && combat.RootHasOnlyNonHealingLoadoutSubscribers
            && !combat.RootHasBaseLibCardModifiers && combat.AdaptedOnPlay is null
            // Book of Five Rings heals only when a card enters the run Deck.
            // The closed card, potion and enemy actions cannot trigger that event.
            // Cursed Pearl / Archaic Tooth act on acquisition, which is also absent.
            && combat.RelicsOf(player).All(relic => DefectPrismRelics.Contains(relic.GetType()))
            && DefectPrismHealingUpperBound(simulator, player, 0) == 0;
    }

    internal static int DefectPrismHealingUpperBound(
        CombatPredictionSimulator simulator, Player player, int postCombatHeal)
    {
        if (simulator.HasPendingChoice)
            return int.MaxValue;
        SimulatedCombatState combat = (SimulatedCombatState)simulator.State.CombatState;
        var state = simulator.State.GetPlayerCombatState(player);
        if (state.AllCards.Any(card => !DefectPrismCards.Contains(card.Preview.GetType())
                || !HasCertifiedRemainingAttachments(card.Preview)
                || card.Preview.Type == CardType.Status && card.Preview.DeckVersion is not null)
            || combat.PendingReturningCards.Any(card => !DefectPrismCards.Contains(card.Preview.GetType())
                || !HasCertifiedRemainingAttachments(card.Preview)
                || card.Preview.Type == CardType.Status && card.Preview.DeckVersion is not null)
            || state.OrbQueue.Orbs.Any(orb => orb.GetType() != typeof(LightningOrb)
                && orb.GetType() != typeof(FrostOrb) && orb.GetType() != typeof(DarkOrb))
            || combat.EffectivePowers().Any(power => !DefectPrismPowers.Contains(power.GetType())))
            return int.MaxValue;
        int slots = ((ICombatPredictionPlayerLimits)combat).GetPotionSlotCount(player);
        for (int slot = 0; slot < slots; slot++)
        {
            PotionModel? potion = combat.GetPotionAtSlot(player, slot);
            // Foul Potion damages combat creatures, including the player. Its
            // Merchant/Event gold branch cannot run in this native encounter.
            if (potion is not null && potion.GetType() != typeof(FoulPotion))
                return int.MaxValue;
        }
        return Math.Max(0, postCombatHeal);
    }
}
