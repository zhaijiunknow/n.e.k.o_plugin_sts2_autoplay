namespace CombatSolver;

internal sealed partial class CombatBeamSolver
{
    private interface IExpansionExecutor
    {
        int MaximumQueuedParents { get; }

        ExpansionWorkerOutcome[] Execute(
            IReadOnlyList<SearchNode> nodes,
            Action<SearchNode, SearchNode> acceptChild,
            Action<SearchNode> finishParent,
            Action<int, ExpansionBatch>? beforeCommit = null);
    }

    private sealed class SerialExpansionExecutor(CombatBeamSolver coordinator) : IExpansionExecutor
    {
        public int MaximumQueuedParents => 1;

        public ExpansionWorkerOutcome[] Execute(
            IReadOnlyList<SearchNode> nodes,
            Action<SearchNode, SearchNode> acceptChild,
            Action<SearchNode> finishParent,
            Action<int, ExpansionBatch>? beforeCommit = null)
        {
            if (nodes.Count != 1 || beforeCommit != null)
                throw new ArgumentException("串行展开只接收一个父节点，不接收批次提交回调。");
            SearchNode node = nodes[0];
            foreach (SearchNode child in coordinator.Expand(node))
            {
                acceptChild(node, child);
                if (coordinator._run.Expanded >= coordinator._profile.MaxExpandedNodes)
                    break;
            }
            finishParent(node);
            return [];
        }
    }
}
