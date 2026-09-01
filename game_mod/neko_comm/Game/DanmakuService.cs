// Facade for the in-game catgirl danmaku overlay behind the mod HTTP API.
// POST /danmaku -> DanmakuService.PushAsync -> lazily create the overlay on the game thread and render one
// line. Created lazily so no CanvasLayer node is added if the catgirl never posts.
using System;
using System.Threading.Tasks;

namespace NekoComm.Game
{
    internal static class DanmakuService
    {
        private static DanmakuOverlay? _overlay;

        public static async Task<string> PushAsync(string text, string? style, string? placement, string? avatar)
        {
            if (string.IsNullOrWhiteSpace(text))
                return "empty";

            return await GameThread.InvokeAsync(() =>
            {
                try
                {
                    _overlay ??= DanmakuOverlay.Create();
                    _overlay.Add(text, style ?? "catgirl", placement ?? "scrolling", avatar);
                    return "ok";
                }
                catch (Exception ex)
                {
                    return $"error:{ex.Message}";
                }
            });
        }
    }

    /// <summary>Payload for POST /danmaku.</summary>
    internal sealed class DanmakuRequest
    {
        public string text { get; set; } = "";
        public string? style { get; set; }
        public string? placement { get; set; }
        public string? avatar { get; set; }
    }
}
