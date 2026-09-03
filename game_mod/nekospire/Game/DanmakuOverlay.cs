// In-game scrolling catgirl danmaku overlay for nekospire. Style modeled on the "弹幕尖塔 DanmakuSpire" mod:
// each line is a horizontal "smoke" strip (rounded translucent panel tinted with the catgirl color) with the
// character icon on the left and centered light text with a soft drop shadow — instead of bare pink text.
//
// nekospire builds with Microsoft.NET.Sdk (not Godot.NET.Sdk) and the game data dir ships no
// Godot.SourceGenerators, so a C# node subclass overriding _Process is NOT wired by Godot. We instead
// use BUILT-IN nodes (CanvasLayer -> Control -> PanelContainer -> Label) attached to the SceneTree root and
// drive the per-frame scroll with the SceneTree.ProcessFrame signal. All node creation/mutation happens on the
// game thread (see DanmakuService -> GameThread.InvokeAsync).
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
        private const int LaneCount = 8;
        private const int MaxActive = 16;
        private const float AvatarGap = 6f;
        private const float Padding = 14f;

        // DanmakuSpire-style palette: light champagne text + dark soft shadow; the catgirl color tints the smoke.
        private static readonly Color TextColor = new(0.96f, 0.93f, 0.87f, 1f);
        private static readonly Color TextShadowColor = new(0f, 0f, 0f, 0.6f);
        private static readonly Color CatgirlColor = new(1f, 0.72f, 0.88f);
        private static readonly Color SmokeBase = new(0.05f, 0.035f, 0.06f, 0.78f);

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

            // Size follows the configurable font size (like DanmakuSpire: font 24 -> icon 2x, ~56px tall).
            int fontSize = Math.Clamp(NekoConfig.Current.danmaku_font_size, 16, 40);
            float avatar = fontSize * 2f;
            float laneStep = fontSize * 1.9f;
            float y = Mathf.Max(40f, _viewportHeight * 0.12f) + lane * laneStep;

            var avatarTex = ResolveAvatar(avatarBase64);

            // container: just a position/scroll holder.
            var container = new Control
            {
                Name = "NekoCatgirlDanmaku",
                MouseFilter = Control.MouseFilterEnum.Ignore,
                ZIndex = 2,
            };

            // Smoke strip: a rounded translucent panel tinted with the catgirl color, with a soft drop shadow —
            // the DanmakuSpire "smoke ninepatch" look, approximated with a StyleBoxFlat.
            var smoke = new PanelContainer
            {
                Name = "NekoDanmakuSmoke",
                MouseFilter = Control.MouseFilterEnum.Ignore,
            };
            var smokeStyle = new StyleBoxFlat
            {
                BgColor = SmokeBase.Lerp(CatgirlColor, 0.16f),
                ShadowColor = new Color(0f, 0f, 0f, 0.55f),
                ShadowSize = 8,
                CornerRadiusTopLeft = 20,
                CornerRadiusTopRight = 20,
                CornerRadiusBottomLeft = 20,
                CornerRadiusBottomRight = 20,
                ContentMarginLeft = Padding,
                ContentMarginRight = Padding,
                ContentMarginTop = 6f,
                ContentMarginBottom = 6f,
            };
            smoke.AddThemeStyleboxOverride("panel", smokeStyle);

            var row = new HBoxContainer
            {
                Name = "NekoDanmakuRow",
                MouseFilter = Control.MouseFilterEnum.Ignore,
            };
            row.AddThemeConstantOverride("separation", (int)AvatarGap);
            smoke.AddChild(row);

            if (avatarTex != null)
            {
                var rect = new TextureRect
                {
                    Texture = avatarTex,
                    CustomMinimumSize = new Vector2(avatar, avatar),
                    MouseFilter = Control.MouseFilterEnum.Ignore,
                    StretchMode = TextureRect.StretchModeEnum.KeepAspectCentered,
                    ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize,
                };
                row.AddChild(rect);
            }

            var label = new Label
            {
                Name = "NekoDanmakuText",
                Text = clean,
                MouseFilter = Control.MouseFilterEnum.Ignore,
                VerticalAlignment = VerticalAlignment.Center,
            };
            label.AddThemeFontSizeOverride("font_size", fontSize);
            if (_font != null)
                label.AddThemeFontOverride("font", _font);
            label.AddThemeColorOverride("font_color", TextColor);
            // Soft drop shadow (DanmakuSpire uses a shadow, not an outline).
            label.AddThemeColorOverride("font_shadow_color", TextShadowColor);
            label.AddThemeConstantOverride("shadow_offset_x", 2);
            label.AddThemeConstantOverride("shadow_offset_y", 2);
            label.AddThemeConstantOverride("shadow_outline_size", 0);
            row.AddChild(label);

            container.AddChild(smoke);
            container.Position = new Vector2(_viewportWidth + 60f, y);
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
                    if (node.Position.X < -700f)
                    {
                        node.QueueFree();
                        _active.RemoveAt(i);
                    }
                }
            }
        }

        // Prefer the ACTUAL catgirl avatar: (1) caller-supplied base64 (the catgirl image from the N.E.K.O
        // client/main server), (2) the custom avatar configured via NekoConfig.danmaku_avatar (a file in
        // mods/nekospire_ui/, default catgirl.png — the user's own catgirl portrait). No icon if missing.
        private Texture2D? ResolveAvatar(string? avatarBase64)
        {
            var fromBase64 = DecodeAvatar(avatarBase64);
            if (fromBase64 != null)
                return fromBase64;
            var filename = NekoConfig.Current.danmaku_avatar;
            if (!string.IsNullOrWhiteSpace(filename))
                return NekoUi.LoadUserTexture(filename);
            return null;
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
                bool ok;
                if (bytes.Length >= 4 && bytes[0] == 0x89 && bytes[1] == 0x50 && bytes[2] == 0x4E && bytes[3] == 0x47)
                    ok = image.LoadPngFromBuffer(bytes) == Error.Ok;
                else if (bytes.Length >= 3 && bytes[0] == 0xFF && bytes[1] == 0xD8 && bytes[2] == 0xFF)
                    ok = image.LoadJpgFromBuffer(bytes) == Error.Ok;
                else
                    return null;
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
                return null;
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
