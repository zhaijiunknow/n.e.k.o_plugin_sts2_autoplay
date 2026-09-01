// A faithful port of the game's RNG into the Sim so the resolver can PREDICT the exact numbers the
// live game will roll (enemy moves, draws, random targets). This is a reimplementation of the game's
// MegaRandom (xoroshiro128+ seeded by splitmix64) and the Rng wrapper (seed = Seed + XxHash64(name),
// plus a call counter), so given the run seed + a stream's counter the Sim produces the same stream.
// The game ships System.IO.Hashing.dll; XxHash64 is used verbatim so the seed combine matches 1:1.
using System;
using System.Numerics;
using System.Text;
using System.Text.RegularExpressions;
using System.IO.Hashing;

namespace NekoComm.Game.Sim
{
    /// <summary>MegaRandom — 4-state xoroshiro128+ seeded with splitmix64. Exact port of the game's.</summary>
    public sealed class SimGameRandom
    {
        private const double _incrDouble = 1.1102230246251565E-16;
        private ulong _s0, _s1, _s2, _s3;

        public SimGameRandom(ulong seed) => Reinitialise(seed);

        public static ulong Splitmix64(ref ulong x)
        {
            ulong num = (x += 11400714819323198485ul);
            num = (num ^ (num >> 30)) * 13787848793156543929ul;
            num = (num ^ (num >> 27)) * 10723151780598845931ul;
            return num ^ (num >> 31);
        }

        private void Reinitialise(ulong seed)
        {
            _s0 = Splitmix64(ref seed);
            _s1 = Splitmix64(ref seed);
            _s2 = Splitmix64(ref seed);
            _s3 = Splitmix64(ref seed);
        }

        private ulong NextULongInner()
        {
            ulong s = _s0, s2 = _s1, s3 = _s2, s4 = _s3;
            ulong result = BitOperations.RotateLeft(s2 * 5, 7) * 9;
            ulong num = s2 << 17;
            s3 ^= s; s4 ^= s2; s2 ^= s3; s ^= s4; s3 ^= num; s4 = BitOperations.RotateLeft(s4, 45);
            _s0 = s; _s1 = s2; _s2 = s3; _s3 = s4;
            return result;
        }

        public double NextDouble() => (double)(NextULongInner() >> 11) * _incrDouble;
        private int NextInner(int maxValue) => (int)(NextDouble() * (double)maxValue);
        public int Next(int maxValue) { if (maxValue < 1) throw new ArgumentOutOfRangeException(nameof(maxValue)); return NextInner(maxValue); }
        public ulong NextULong() => NextULongInner();
        public bool NextBool() => (NextULongInner() & 0x8000000000000000ul) != 0;
    }

    /// <summary>Game Rng: seed = baseSeed + XxHash64(SnakeCase(name)); a call counter tracks draws.
    /// SyncToGameCounter advances the underlying PRNG so a stream can continue from the live position.</summary>
    public sealed class GameRng
    {
        private readonly SimGameRandom _random;

        public int Counter { get; private set; }

        private GameRng(ulong seed) { _random = new SimGameRandom(seed); }

        public static GameRng Create(ulong baseSeed, string streamName, int gameCounter = 0)
        {
            var name = SnakeCase(streamName);
            var seed = baseSeed + unchecked((ulong)XxHash64.HashToUInt64(Encoding.UTF8.GetBytes(name)));
            var r = new GameRng(seed);
            if (gameCounter > 0) r.Advance(gameCounter);
            return r;
        }

        public int NextInt(int max) { Counter++; return _random.Next(max); }
        public int NextInt(int min, int max) { Counter++; return _random.Next(max - min) + min; }
        public double NextDouble() { Counter++; return _random.NextDouble(); }
        public ulong NextULong() { Counter++; return _random.NextULong(); }
        public bool NextBool() { Counter++; return _random.NextBool(); }

        private void Advance(int draws) { for (var i = 0; i < draws; i++) _random.NextULong(); Counter += draws; }

        // The game's "snake_case" of a stream name (e.g. CombatCardGeneration -> combat_card_generation).
        private static string SnakeCase(string txt)
            => Regex.Replace(txt.Trim(), "([a-z0-9])([A-Z])", "$1_$2").ToLowerInvariant();
    }
}
