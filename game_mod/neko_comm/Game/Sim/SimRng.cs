// Named RNG streams, mirroring the game's RunRngSet structure (12 independent streams each with its
// own counter). Pure / game-type-free. Replay verification uses outcome-tagged captures, so this does
// NOT reproduce the game's exact PRNG algorithm; it gives a deterministic, snapshotable stream the
// search uses to roll hypothetical futures (Phase 2 enemy moves, Phase 4 MCTS rollouts).
//
// Determinism contract: a (seed, streamType, sequence-of-calls) produces the same result for the
// life of one .NET runtime. Cross-runtime stability is not required (captured replay uses outcome
// tags, not re-rolling).
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    // 12 named streams (mirrors DEC RunRngType). Names kept as the game uses them.
    public enum SimRngType
    {
        UpFront, Shuffle, UnknownMapPoint, CombatCardGeneration, CombatPotionGeneration,
        CombatCardSelection, CombatEnergyCosts, CombatTargets, MonsterAi, Niche,
        CombatOrbGeneration, TreasureRoomRelics,
    }

    /// <summary>One RNG stream: seeded by (seed, streamName), tracks a call counter, snapshotable.</summary>
    public sealed class SimRng
    {
        private readonly System.Random _rng;
        private readonly int _seed;
        private readonly string _name;

        public int Counter { get; private set; }

        /// <summary>Create a stream, seeded deterministically from the run seed + stream name.</summary>
        public static SimRng Create(string runSeed, SimRngType type)
        {
            var name = StreamName(type);
            var combined = CombineSeed(runSeed, name);
            return new SimRng(combined, name);
        }

        private SimRng(int seed, string name)
        {
            _seed = seed;
            _name = name;
            _rng = new System.Random(seed);
        }

        private SimRng(SimRng other)
        {
            _seed = other._seed;
            _name = other._name;
            Counter = other.Counter;
            _rng = new System.Random(_seed);
            // Replay the same draws so the cloned stream advances identically.
            for (var i = 0; i < Counter; i++) _rng.Next();
        }

        public int NextInt(int minInclusive, int maxExclusive)
        {
            Counter++;
            return _rng.Next(minInclusive, maxExclusive);
        }

        public double NextDouble()
        {
            Counter++;
            return _rng.NextDouble();
        }

        public T Pick<T>(IReadOnlyList<T> items)
        {
            if (items.Count == 0) throw new InvalidOperationException("Pick on empty list");
            return items[NextInt(0, items.Count)];
        }

        public SimRng Clone() => new SimRng(this);

        private static string StreamName(SimRngType type)
            => type.ToString();

        // Deterministic, stable combine of the run seed string + stream name.
        private static int CombineSeed(string runSeed, string name)
        {
            unchecked
            {
                var h = 17;
                foreach (var ch in (runSeed + "::" + name))
                    h = h * 31 + ch;
                return h;
            }
        }
    }

    /// <summary>The full set of 12 streams, cloneable in one go.</summary>
    public sealed class SimRngSet
    {
        private readonly Dictionary<SimRngType, SimRng> _streams = new();

        public SimRngSet(string runSeed)
        {
            foreach (var t in Enum.GetValues<SimRngType>())
                _streams[t] = SimRng.Create(runSeed, t);
        }

        private SimRngSet(SimRngSet other)
        {
            foreach (var kv in other._streams)
                _streams[kv.Key] = kv.Value.Clone();
        }

        public SimRng Get(SimRngType type) => _streams[type];

        public SimRngSet Clone() => new SimRngSet(this);
    }
}
