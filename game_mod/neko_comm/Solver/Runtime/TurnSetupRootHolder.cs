// RitsuLib-free turn-START capture. The standalone CombatSolver searches the combat at PlayerTurnPhase.Start
// (it intercepts SetupPlayerTurn / RunAutoPrePlayPhase) with IncludeTurnSetup=true, which seeds the search head
// with the turn-setup values (enemy weak turns, retained attack, persistent buff, strength suppression) — that
// is what lets it model the boss's sleep/vulnerability window and find killing lines. The ported facade only ever
// captures the live Play-phase state, so it must keep IncludeTurnSetup=false and misses that seeding. This holder
// stores the most recent Start-phase CombatRootSnapshot, captured on the game thread, so the facade can solve
// from it instead. If nothing valid is available the facade falls back to the old Play-phase + IncludeTurnSetup=false.
using System;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Players;
using CombatSolver;

namespace NekoComm.Game
{
    internal static class TurnSetupRootHolder
    {
        private sealed class Entry
        {
            public CombatRootSnapshot Snapshot = null!;
            public int Turn;
            public ulong NetId;
        }

        private static readonly object Gate = new();
        private static Entry? _entry;

        /// <summary>
        /// Called from the live combat-state change hook (game thread). If the local player is exactly in the
        /// Start phase, capture a Start-phase <see cref="CombatRootSnapshot"/> and remember it for the current turn.
        /// If combat is no longer in progress, drop any stored snapshot so it cannot leak into a later combat.
        /// </summary>
        internal static void Refresh()
        {
            try
            {
                CombatManager manager = CombatManager.Instance;
                if (manager == null)
                    return;
                CombatState? state = manager.DebugOnlyGetState();
                if (state == null || !manager.IsInProgress)
                {
                    Clear();
                    return;
                }

                Player? me = GameStateService.GetLocalPlayer(state);
                if (me?.PlayerCombatState == null)
                    return;
                // Only at the true turn-setup (Start) phase. CombatBeamSolver's IncludeTurnSetup=true guard
                // requires root.PlayerPhase == Start, and this is the only phase that satisfies it.
                if (me.PlayerCombatState.Phase != PlayerTurnPhase.Start)
                    return;

                CombatRootSnapshot root = CombatRootSnapshot.Capture(state);
                lock (Gate)
                {
                    _entry = new Entry
                    {
                        Snapshot = root,
                        Turn = me.PlayerCombatState.TurnNumber,
                        NetId = me.NetId,
                    };
                }
                MegaCrit.Sts2.Core.Logging.Log.Info(
                    $"[NekoComm.TurnSetup] captured start snapshot turn={me.PlayerCombatState.TurnNumber}");
            }
            catch (Exception ex)
            {
                MegaCrit.Sts2.Core.Logging.Log.Warn($"[NekoComm.TurnSetup] capture failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Returns the held Start-phase snapshot if it is still for the same player's current turn; otherwise
        /// null (the caller falls back to a live Play-phase capture). Called on the game thread.
        /// </summary>
        internal static CombatRootSnapshot? TryGetStartSnapshot(Player? me)
        {
            if (me?.PlayerCombatState == null)
                return null;
            lock (Gate)
            {
                Entry? e = _entry;
                if (e == null)
                    return null;
                if (e.Turn != me.PlayerCombatState.TurnNumber)
                    return null;
                if (e.NetId != me.NetId)
                    return null;
                if (e.Snapshot.PlayerPhase != PlayerTurnPhase.Start)
                    return null;
                return e.Snapshot;
            }
        }

        internal static void Clear()
        {
            lock (Gate)
                _entry = null;
        }
    }
}
