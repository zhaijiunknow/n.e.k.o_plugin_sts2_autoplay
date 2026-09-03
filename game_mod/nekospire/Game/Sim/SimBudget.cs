// Hard compute budget for the win-probability search. The solver runs off the main game thread only
// after building a pure SimState, but it must still respect a wall-clock + node cap so /solver/plan
// stays responsive and never stalls the game. Budget-exceeded is reported honestly in the payload.
// Pure / game-type-free.
using System;

namespace NekoComm.Game.Sim
{
    public sealed class SimBudget
    {
        private long? _startMs;
        private readonly int _maxMs;

        public int MaxNodes { get; }
        public int MaxRollouts { get; }
        public int Nodes { get; private set; }
        public int Rollouts { get; private set; }
        public bool Exceeded { get; private set; }

        // The wall-clock budget begins at the first search tick — NOT at construction. Otherwise the
        // snapshot build (SimBuild, which reads live game objects) eats the budget before the search
        // even starts, starving it to an early budget_exceeded with a garbage result.
        public bool TimedOut => _startMs.HasValue && Environment.TickCount64 - _startMs.Value >= _maxMs;

        public SimBudget(int maxMs, int maxNodes, int maxRollouts)
        {
            _maxMs = maxMs;
            MaxNodes = maxNodes;
            MaxRollouts = maxRollouts;
        }

        public void TickNode()
        {
            _startMs ??= Environment.TickCount64;
            Nodes++;
            if (Nodes >= MaxNodes || TimedOut) Exceeded = true;
        }

        public void TickRollout()
        {
            _startMs ??= Environment.TickCount64;
            Rollouts++;
            if (Rollouts >= MaxRollouts || TimedOut) Exceeded = true;
        }
    }
}
