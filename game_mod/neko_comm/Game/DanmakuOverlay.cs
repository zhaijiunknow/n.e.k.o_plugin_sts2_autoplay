// In-game scrolling catgirl danmaku overlay for neko_comm.
//
// neko_comm builds with Microsoft.NET.Sdk (not Godot.NET.Sdk) and the game data dir ships no
// Godot.SourceGenerators, so a C# node subclass overriding _Process is NOT wired by Godot. We instead
// use BUILT-IN nodes (CanvasLayer -> Control -> Label) attached to the SceneTree root and drive the
// per-frame scroll with the SceneTree.ProcessFrame signal (an already-proven path in GameActionService).
// All node creation/mutation happens on the game thread (see DanmakuService -> GameThread.InvokeAsync).
using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Godot;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Localization.Fonts;
using MegaCrit.Sts2.Core.Nodes;

namespace NekoComm.Game
{
    internal sealed class DanmakuOverlay
    {
        private const float SpeedPxPerSecond = 120f;
        private const int FontSize = 24;
        private const int LaneCount = 8;
        private const float LaneStep = 34f;
        private const int MaxActive = 16;
        private const float AvatarSize = 36f;
        private static readonly Color CatgirlColor = new(1f, 0.78f, 0.9f);

        private readonly List<(Control Node, float Speed)> _active = new();
        private readonly Dictionary<string, Texture2D> _avatarCache = new();
        private CanvasLayer? _layer;
        private Control? _control;
        private Font? _font;
        private int _nextLane;
        private float _viewportWidth = 1920f;
        private float _viewportHeight = 1080f;

        /// <summary>Create + attach the overlay and start the scroll loop. Must run on the game thread.</summary>
        public static DanmakuOverlay Create()
        {
            var overlay = new DanmakuOverlay();
            var layer = new CanvasLayer { Name = "NekoCommDanmakuCanvas", Layer = 64 };
            var control = new Control { Name = "NekoCommDanmakuOverlay", MouseFilter = Control.MouseFilterEnum.Ignore };
            layer.AddChild(control);
            control.SetAnchorsAndOffsetsPreset(Control.LayoutPreset.FullRect);
            control.MouseFilter = Control.MouseFilterEnum.Ignore;
            overlay._layer = layer;
            overlay._control = control;
            overlay._font = ResolveFont();

            var tree = NGame.Instance.GetTree();
            tree.Root.CallDeferred(Node.MethodName.AddChild, layer);
            _ = overlay.ScrollLoopAsync(tree);
            return overlay;
        }

        /// <summary>Add one danmaku line (optionally with a catgirl avatar). Must run on the game thread.</summary>
        public void Add(string text, string style, string placement, string? avatarBase64)
        {
            var control = _control;
            if (control == null || _active.Count >= MaxActive)
                return;

            var clean = Clean(text);
            if (clean.Length == 0)
                return;

            // The overlay is added to the tree root via CallDeferred, so on the very first POST it may not
            // be inside the tree yet; GetViewportRect would warn. Use defaults until it is.
            if (control.IsInsideTree())
            {
                var viewport = control.GetViewportRect();
                if (viewport.Size.X > 0f)
                {
                    _viewportWidth = viewport.Size.X;
                    _viewportHeight = viewport.Size.Y;
                }
            }

            int lane = _nextLane % LaneCount;
            _nextLane++;
            float y = Mathf.Max(40f, _viewportHeight * 0.12f) + lane * LaneStep;

            var avatarTex = DecodeAvatar(avatarBase64);
            float textX = 0f;
            float textY = 4f; // center a ~28px label against the 36px avatar
            var container = new Control
            {
                Name = "NekoCatgirlDanmaku",
                MouseFilter = Control.MouseFilterEnum.Ignore,
                ZIndex = 2,
            };

            if (avatarTex != null)
            {
                var rect = new TextureRect
                {
                    Texture = avatarTex,
                    Position = new Vector2(0f, 0f),
                    Size = new Vector2(AvatarSize, AvatarSize),
                    MouseFilter = Control.MouseFilterEnum.Ignore,
                    StretchMode = TextureRect.StretchModeEnum.KeepAspectCentered,
                };
                container.AddChild(rect);
                textX = AvatarSize + 4f;
                textY = (AvatarSize - (FontSize + 6f)) / 2f;
            }

            var label = new Label
            {
                Name = "NekoDanmakuText",
                Text = clean,
                Position = new Vector2(textX, textY),
                MouseFilter = Control.MouseFilterEnum.Ignore,
                HorizontalAlignment = HorizontalAlignment.Center,
                VerticalAlignment = VerticalAlignment.Center,
            };
            label.AddThemeFontSizeOverride("font_size", FontSize);
            if (_font != null)
                label.AddThemeFontOverride("font", _font);
            label.AddThemeColorOverride("font_color", CatgirlColor);

            container.AddChild(label);
            container.Position = new Vector2(_viewportWidth + 40f, y);
            control.AddChild(container);
            _active.Add((container, SpeedPxPerSecond));
        }

        private async Task ScrollLoopAsync(SceneTree tree)
        {
            var last = (double)Time.GetTicksMsec() / 1000.0;
            while (_layer != null && GodotObject.IsInstanceValid(_layer))
            {
                await NGame.Instance.ToSignal(tree, SceneTree.SignalName.ProcessFrame);
                var now = (double)Time.GetTicksMsec() / 1000.0;
                var delta = (float)(now - last);
                last = now;
                if (delta <= 0f || delta > 0.5f)
                    continue;

                for (int i = _active.Count - 1; i >= 0; i--)
                {
                    var (node, speed) = _active[i];
                    if (!GodotObject.IsInstanceValid(node))
                    {
                        _active.RemoveAt(i);
                        continue;
                    }
                    node.Position += new Vector2(-speed * delta, 0f);
                    // Catgirl line (<=60 chars + one 36px avatar) is far narrower than 500px, so this
                    // cleanly drops it once it has fully scrolled off the left edge.
                    if (node.Position.X < -500f)
                    {
                        node.QueueFree();
                        _active.RemoveAt(i);
                    }
                }
            }
        }

        private Texture2D? DecodeAvatar(string? avatarBase64)
        {
            if (string.IsNullOrWhiteSpace(avatarBase64))
                return null;
            var raw = StripDataPrefix(avatarBase64);
            if (raw.Length == 0)
                return null;
            if (_avatarCache.TryGetValue(raw, out var cached))
                return cached;
            try
            {
                var bytes = Convert.FromBase64String(raw);
                var image = new Image();
                // Sniff format by magic bytes so the correct loader is used without trying the wrong one
                // (avoids Godot's noisy LoadPng/LoadJpg push-errors on a mismatched image). Any failure
                // -> null -> the caller renders text-only (per "if it doesn't render, fall back to text").
                bool ok;
                if (bytes.Length >= 4 && bytes[0] == 0x89 && bytes[1] == 0x50 && bytes[2] == 0x4E && bytes[3] == 0x47)
                    ok = image.LoadPngFromBuffer(bytes) == Error.Ok;
                else if (bytes.Length >= 3 && bytes[0] == 0xFF && bytes[1] == 0xD8 && bytes[2] == 0xFF)
                    ok = image.LoadJpgFromBuffer(bytes) == Error.Ok;
                else
                    return null; // unknown/unsupported -> plain text
                if (!ok)
                    return null;
                var texture = ImageTexture.CreateFromImage(image);
                if (texture == null || texture.GetWidth() <= 0 || texture.GetHeight() <= 0)
                    return null;
                _avatarCache[raw] = texture;
                return texture;
            }
            catch
            {
                return null; // decode/allocation failure -> plain text
            }
        }

        private static string StripDataPrefix(string avatarBase64)
        {
            var value = avatarBase64 ?? "";
            int idx = value.IndexOf("base64,", StringComparison.OrdinalIgnoreCase);
            return idx >= 0 ? value.Substring(idx + 7) : value;
        }

        private static string Clean(string text)
        {
            var t = (text ?? "").Replace("\r", " ").Replace("\n", " ").Trim();
            return t.Length > 60 ? t.Substring(0, 60) : t;
        }

        private static Font? ResolveFont()
        {
            // Mirrors DanmakuSpire's LoadLocalizedFont + LoadFallbackFont, which compiles against sts2.
            try
            {
                if (LocManager.Instance != null && FontManager.NeedsFontSubstitution(LocManager.Instance.Language))
                    return FontManager.GetSubstituteFont(LocManager.Instance.Language, (FontType)0);
            }
            catch
            {
                // fall through to bundled default
            }
            try
            {
                return GD.Load<Font>("res://themes/kreon_regular_shared.tres");
            }
            catch
            {
                return null;
            }
        }
    }
}
