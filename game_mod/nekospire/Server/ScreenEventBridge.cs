// Real screen-transition hook. The game raises ActiveScreenContext.Updated whenever the active screen
// context changes (verified via reflection: it exposes a public `event Action Updated` + Update()).
// Subscribing here routes every screen transition into GameEventService.EvaluateNow(), so screen_changed
// (MAP/REWARD/EVENT/SHOP/...) broadcasts to /events/stream immediately instead of waiting for the fallback
// poll. EvaluateNow coalesces internally, so even if Updated fires per-frame the heavy state build is
// bounded. Combat transitions are covered by CombatEventTriggerPatch; the poll backs both up.
using System;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Logging;

namespace NekoComm.Server
{
    internal static class ScreenEventBridge
    {
        private static readonly object _gate = new();
        private static bool _installed;

        public static void Install()
        {
            lock (_gate)
            {
                if (_installed)
                    return;
                _installed = true;
            }

            // Wrapped so a failure to reach the game's screen context never breaks mod startup.
            try
            {
                ActiveScreenContext.Instance.Updated += OnScreenUpdated;
                Log.Info("[NekoComm.ScreenEvent] subscribed to ActiveScreenContext.Updated");
            }
            catch (Exception ex)
            {
                Log.Warn($"[NekoComm.ScreenEvent] subscribe failed: {ex.Message}");
            }
        }

        public static void Uninstall()
        {
            try
            {
                ActiveScreenContext.Instance.Updated -= OnScreenUpdated;
            }
            catch
            {
                // Best effort.
            }
        }

        private static void OnScreenUpdated()
        {
            try
            {
                GameEventService.Instance.EvaluateNow();
            }
            catch (Exception ex)
            {
                Log.Warn($"[NekoComm.ScreenEvent] failed to evaluate events: {ex.Message}");
            }
        }
    }
}
