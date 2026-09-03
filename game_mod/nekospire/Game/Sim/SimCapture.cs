// Capture engine (game-coupled): snapshots the live combat into a SimState before/after each player
// action and writes a capture event so a later replay can assert the resolver reproduces the real
// transition exactly (the real game is truth). Serialization is delegated to the pure SimPayload.
using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Players;

namespace NekoComm.Game.Sim
{
    public static class SimCapture
    {
        // Enabled when the STS2_CAPTURE=1 env var is set at launch, or via GET /capture/on.
        public static bool Enabled = Environment.GetEnvironmentVariable("STS2_CAPTURE") == "1";

        public static string CaptureDirectory { get; set; } =
            Path.Combine(AppContext.BaseDirectory, "sim_captures");

        private static string? _file;
        private static readonly object _lock = new();
        private static SimState? _pendingPre;
        private static string? _pendingKind;
        private static object? _pendingAction;

        // Two-phase: Begin snapshots pre-state + the action; End (after the action settles) snapshots
        // post-state and writes the event. Guarded so it never breaks the live loop.
        public static void Begin(string kind, CombatState? combat, Player? me, object action)
        {
            if (!Enabled) return;
            try
            {
                if (combat == null || me == null) return;
                _pendingPre = SimBuild.FromLive(combat, me);
                _pendingKind = kind;
                _pendingAction = action;
            }
            catch
            {
                _pendingPre = null;
            }
        }

        public static void End(CombatState? combat, Player? me)
        {
            if (!Enabled || _pendingPre == null) { _pendingPre = null; return; }
            try
            {
                if (combat == null || me == null) { _pendingPre = null; return; }
                var post = SimBuild.FromLive(combat, me);
                _file ??= EnsureFile();
                var line = new Dictionary<string, object>
                {
                    ["kind"] = _pendingKind ?? "action",
                    ["action"] = _pendingAction ?? new { },
                    ["pre_state"] = SimPayload.ToPayload(_pendingPre),
                    ["post_state"] = SimPayload.ToPayload(post),
                };
                lock (_lock) File.AppendAllText(_file, JsonSerializer.Serialize(line) + "\n");
            }
            catch
            {
                // Capture must never break the live game loop.
            }
            finally
            {
                _pendingPre = null;
            }
        }

        private static string EnsureFile()
        {
            Directory.CreateDirectory(CaptureDirectory);
            var name = $"capture_{DateTime.Now:yyyyMMdd_HHmmss}.jsonl";
            return Path.Combine(CaptureDirectory, name);
        }
    }
}
