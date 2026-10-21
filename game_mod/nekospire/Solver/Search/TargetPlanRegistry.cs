using MegaCrit.Sts2.Core.Entities.Creatures;

namespace CombatSolver;

internal enum TargetPlanKind
{
    FocusedOpening,
    LeadingOpening,
}

internal readonly record struct TargetPlanRule(
    TargetPlanKind Kind,
    int MaximumEnemies,
    int MaximumTargetedActions,
    int MaximumRetargetedActions);

internal sealed class TargetPlanRegistry
{
    internal static TargetPlanRegistry Default { get; } = new(
    [
        new(TargetPlanKind.FocusedOpening, 3, 0, 0),
        new(TargetPlanKind.LeadingOpening, 3, 3, 2),
    ]);

    private readonly IReadOnlyDictionary<TargetPlanKind, TargetPlanRule> _rules;

    internal TargetPlanRegistry(IEnumerable<TargetPlanRule> rules)
        => _rules = rules.ToDictionary(rule => rule.Kind);

    internal IReadOnlyList<PlanAction> SelectOffensiveFollowUps(IReadOnlyList<SearchNode> followUps)
        => followUps
            .GroupBy(node => node.Action!.TargetCombatId!.Value)
            .Select(group => group
                .OrderBy(node => node.Snapshot.AliveEnemyCount)
                .ThenBy(node => node.Snapshot.EnemyHp)
                .ThenByDescending(node => node.Snapshot.FocusTargetPressure)
                .ThenByDescending(node => node.Score)
                .First().Action!)
            .OrderBy(action => action.TargetCombatId)
            .Take(3)
            .ToArray();

    internal IReadOnlyList<PlanAction[]> BuildFocusedPrefixes(
        IReadOnlyList<PlanAction> opening,
        IReadOnlyList<Creature> enemies,
        Func<Creature, string> creatureName,
        Func<IReadOnlyList<PlanAction>, bool> canReplay)
    {
        if (!opening.Any(action => action is
            { Kind: PlanActionKind.PlayCard, TargetCombatId: not null }))
            return [];

        TargetPlanRule rule = _rules[TargetPlanKind.FocusedOpening];
        List<PlanAction[]> focused = [];
        for (int targetIndex = 0; targetIndex < Math.Min(rule.MaximumEnemies, enemies.Count); targetIndex++)
        {
            Creature enemy = enemies[targetIndex];
            PlanAction[] prefix = opening.Select(action => action is
                { Kind: PlanActionKind.PlayCard, TargetCombatId: not null }
                    ? action with
                    {
                        TargetIndex = targetIndex,
                        TargetCombatId = enemy.CombatId,
                        TargetName = creatureName(enemy),
                    }
                    : action).ToArray();
            if (canReplay(prefix))
                focused.Add(prefix);
        }
        return focused;
    }

    internal IReadOnlyList<PlanAction[]> BuildLeadingPrefixes(
        IReadOnlyList<PlanAction> opening,
        IReadOnlyList<Creature> enemies,
        Func<Creature, string> creatureName,
        Func<IReadOnlyList<PlanAction>, bool> canReplay)
    {
        TargetPlanRule rule = _rules[TargetPlanKind.LeadingOpening];
        int[] targetedIndices = opening.Select((action, index) => (action, index))
            .Where(item => item.action is { Kind: PlanActionKind.PlayCard, TargetCombatId: not null })
            .Take(rule.MaximumTargetedActions).Select(item => item.index).ToArray();
        if (targetedIndices.Length < 2)
            return [];

        List<PlanAction[]> variants = [];
        for (int targetIndex = 0; targetIndex < Math.Min(rule.MaximumEnemies, enemies.Count); targetIndex++)
        {
            Creature enemy = enemies[targetIndex];
            for (int count = 1; count <= Math.Min(rule.MaximumRetargetedActions, targetedIndices.Length); count++)
            {
                PlanAction[] prefix = opening.ToArray();
                for (int i = 0; i < count; i++)
                {
                    int actionIndex = targetedIndices[i];
                    prefix[actionIndex] = prefix[actionIndex] with
                    {
                        TargetIndex = targetIndex,
                        TargetCombatId = enemy.CombatId,
                        TargetName = creatureName(enemy),
                    };
                }
                if (targetedIndices.Take(count).All(index =>
                        prefix[index].TargetCombatId == opening[index].TargetCombatId))
                    continue;
                if (canReplay(prefix))
                    variants.Add(prefix);
                if (count == 2 && targetedIndices[1] > targetedIndices[0] + 1)
                {
                    List<PlanAction> reordered = prefix.ToList();
                    PlanAction secondTargeted = reordered[targetedIndices[1]];
                    reordered.RemoveAt(targetedIndices[1]);
                    reordered.Insert(targetedIndices[0] + 1, secondTargeted);
                    if (canReplay(reordered))
                        variants.Add(reordered.ToArray());
                }
            }
        }
        return variants;
    }
}
