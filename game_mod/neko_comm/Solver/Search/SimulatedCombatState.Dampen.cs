using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Models.Powers;
using CombatSolver.Engine.Common;
using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver;

internal sealed partial class SimulatedCombatState
{
    private ForkableSet<(Creature Target, Creature Caster)>? _dampenCasters;
    private Dictionary<PredictedCard, int>? _dampenOriginalUpgrades;

    public void ApplyDampen(
        CombatPredictionSimulator simulator,
        Creature target,
        Creature caster)
    {
        if ((_dampenCasters ??= []).Contains((target, caster)))
            return;
        bool firstCaster = !_dampenCasters.Any(entry => entry.Target == target);
        _dampenCasters.Add((target, caster));
        if (!firstCaster)
            return;

        Apply<DampenPower>(target, 1, caster);
        if (target.Player is not { } player)
            return;
        _dampenOriginalUpgrades ??= new Dictionary<PredictedCard, int>();
        foreach (PredictedCard card in simulator.State.GetPlayerCombatState(player).AllCards)
        {
            if (!card.Preview.IsUpgraded)
                continue;
            _dampenOriginalUpgrades.Add(card, card.Preview.CurrentUpgradeLevel);
            card.MutablePreview.DowngradeInternal();
        }
    }

    public void RemoveDampenCaster(Creature caster)
    {
        if (_dampenCasters == null)
            return;
        Creature[] affectedTargets = _dampenCasters
            .Where(entry => entry.Caster == caster)
            .Select(entry => entry.Target)
            .Distinct()
            .ToArray();
        foreach ((Creature target, Creature existingCaster) in _dampenCasters
                     .Where(entry => entry.Caster == caster)
                     .ToArray())
        {
            _dampenCasters.Remove((target, existingCaster));
        }
        foreach (Creature target in affectedTargets)
        {
            if (_dampenCasters.Any(entry => entry.Target == target))
                continue;
            SetAmount<DampenPower>(target, 0);
            RestoreDampenedCards();
        }
    }

    private void RestoreDampenedCards()
    {
        if (_dampenOriginalUpgrades == null)
            return;
        foreach ((PredictedCard card, int level) in _dampenOriginalUpgrades)
        {
            while (card.Preview.CurrentUpgradeLevel < level)
            {
                card.MutablePreview.UpgradeInternal();
                card.MutablePreview.FinalizeUpgradeInternal();
            }
        }
        _dampenOriginalUpgrades.Clear();
    }

    private void AppendDampenFingerprint(ref StateFingerprintBuilder fingerprint)
    {
        if (_dampenCasters != null)
        {
            foreach ((Creature target, Creature caster) in _dampenCasters
                         .OrderBy(entry => entry.Target.CombatId)
                         .ThenBy(entry => entry.Caster.CombatId))
            {
                fingerprint.Add('c');
                fingerprint.Add(target.CombatId ?? uint.MaxValue);
                fingerprint.Add(caster.CombatId ?? uint.MaxValue);
            }
        }
        if (_dampenOriginalUpgrades != null)
        {
            foreach ((PredictedCard card, int level) in _dampenOriginalUpgrades
                         .OrderBy(entry => entry.Key.Preview.Id.Entry, StringComparer.Ordinal))
            {
                fingerprint.Add('g');
                fingerprint.Add(card.Preview.Id.Entry);
                fingerprint.Add(level);
            }
        }
    }

    private Dictionary<PredictedCard, int>? ForkDampenCards(PredictionForkContext context)
    {
        if (_dampenOriginalUpgrades == null)
            return null;
        Dictionary<PredictedCard, int> result = new(_dampenOriginalUpgrades.Count);
        foreach ((PredictedCard card, int level) in _dampenOriginalUpgrades)
            result.Add(ForkCard(card, context), level);
        return result;
    }
}
