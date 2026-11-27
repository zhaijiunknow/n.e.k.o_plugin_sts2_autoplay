// Shared display budget for HTTP pushes and standalone commentary. No backlog is kept.
using System;
using System.Collections.Generic;
using System.Globalization;

namespace NekoComm.Game
{
    internal sealed class DanmakuPacing
    {
        public const int MaxActive = 2;
        public const int MaxChars = 32;
        public const double MinIntervalSeconds = 6;
        public const double LifetimeSeconds = 10;
        private double? _lastAt;
        private readonly Dictionary<string, double> _recent = new();

        public bool TryAccept(string text, int activeCount, double now)
        {
            if (string.IsNullOrWhiteSpace(text) || activeCount >= MaxActive
                || (_lastAt.HasValue && now - _lastAt.Value < MinIntervalSeconds))
                return false;
            foreach (var key in new List<string>(_recent.Keys))
                if (now - _recent[key] >= 60) _recent.Remove(key);
            if (_recent.ContainsKey(text)) return false;
            _lastAt = now;
            _recent[text] = now;
            return true;
        }

        public static string Clean(string? text)
        {
            var clean = string.Join(" ", (text ?? "").Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
            for (int i = 1; i < clean.Length; i++)
                if ("。！？!?".Contains(clean[i])) { clean = clean[..(i + 1)]; break; }
            var starts = StringInfo.ParseCombiningCharacters(clean);
            return starts.Length > MaxChars ? clean[..starts[MaxChars - 1]] + "…" : clean;
        }
    }
}
