// Shared UI style helpers so the standalone NekoSpire UI matches the game's look: the game's localized
// font (via FontManager, with a bundled CJK fallback) and a DanmakuSpire-style panel (dark bg, gold
// border, rounded, soft shadow). Uses built-in Godot node styling (no source-generator subclass).
using System;
using System.IO;
using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Localization.Fonts;
using MegaCrit.Sts2.Core.Nodes;

namespace NekoComm.Game
{
    internal static class NekoUi
    {
        public static Font? ResolveGameFont()
        {
            try
            {
                if (LocManager.Instance != null && FontManager.NeedsFontSubstitution(LocManager.Instance.Language))
                    return FontManager.GetSubstituteFont(LocManager.Instance.Language, (FontType)0);
            }
            catch
            {
                // fall through to the bundled default
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

        private static Font? ResolveBoldFont()
        {
            try
            {
                if (LocManager.Instance != null)
                    return FontManager.GetSubstituteFont(LocManager.Instance.Language, (FontType)1);
            }
            catch
            {
                // fall through
            }
            try
            {
                return GD.Load<Font>("res://themes/kreon_bold_shared.tres");
            }
            catch
            {
                return null;
            }
        }

        public static void ApplyFont(Control control, int size, bool bold = false)
        {
            var font = bold ? ResolveBoldFont() ?? ResolveGameFont() : ResolveGameFont();
            if (font != null)
                control.AddThemeFontOverride("font", font);
            control.AddThemeFontSizeOverride("font_size", size);
        }

        public static StyleBoxFlat CreatePanelStyle()
        {
            var style = new StyleBoxFlat
            {
                BgColor = new Color(0.075f, 0.065f, 0.055f, 0.985f),
                BorderColor = new Color(0.76f, 0.6f, 0.3f, 0.95f),
                ShadowColor = new Color(0f, 0f, 0f, 0.7f),
                ShadowSize = 16,
            };
            style.SetBorderWidthAll(3);
            style.SetCornerRadiusAll(10);
            return style;
        }

        /// <summary>Panel background: the author's panel.png (9-patch) if provided, else the gold StyleBoxFlat.</summary>
        public static StyleBox BuildPanelBackground()
        {
            var tex = LoadUserTexture("panel.png");
            if (tex != null)
            {
                var style = new StyleBoxTexture { Texture = tex };
                // Right/Bottom content margins 0 so the button bar (Close) can sit flush against the
                // panel's right/bottom edges; left/top keep the title/text inset.
                style.TextureMarginLeft = 20;
                style.TextureMarginTop = 20;
                style.TextureMarginRight = -12;
                style.TextureMarginBottom = 20;
                return style;
            }
            return CreatePanelStyle();
        }

        // The game packs its UI resources (button sprites, themes) into SlayTheSpire2.pck, but a mod can
        // load them via their res:// paths (DanmakuSpire loads res://themes/... the same way). The main-menu
        // text-button Theme is what styles the mod-menu / settings buttons, so apply it to a plain Godot
        // Button to render with the genuine game button textures.
        private const string GameButtonThemePath = "res://themes/main_menu_text_button.tres";

        /// <summary>Apply the game's main-menu text-button Theme so a plain Godot Button looks native.</summary>
        public static void StyleLikeGameButton(Button target)
        {
            try
            {
                var theme = GD.Load<Theme>(GameButtonThemePath);
                if (theme != null)
                    target.Theme = theme;
                else
                    GD.Print("[NekoSpire] game button theme not found: " + GameButtonThemePath);
            }
            catch (Exception ex)
            {
                GD.PrintErr("[NekoSpire] apply game button theme failed: " + ex.Message);
            }
        }

        /// <summary>Set a Button's icon to the game's close (back-x) sprite — for a native close button.</summary>
        public static void StyleAsGameCloseButton(Button target)
        {
            try
            {
                var tex = GD.Load<Texture2D>("res://images/atlases/compressed.sprites/back_button_x.tres");
                if (tex != null)
                    target.Icon = tex;
                target.ExpandIcon = false;
            }
            catch (Exception ex)
            {
                GD.PrintErr("[NekoSpire] apply game close icon failed: " + ex.Message);
            }
        }

        /// <summary>Build a StyleBoxTexture from a game atlas sprite (the res:// path of a *.tres atlas
        /// region) so a Button renders with the genuine game button image.</summary>
        public static void ApplyGameButtonSprite(Button target, string resPath, float padding = 14f)
        {
            try
            {
                var tex = GD.Load<Texture2D>(resPath);
                if (tex == null)
                {
                    GD.Print("[NekoSpire] game button sprite not found: " + resPath);
                    return;
                }
                var normal = new StyleBoxTexture { Texture = tex };
                normal.SetTextureMarginAll(padding);
                var hover = new StyleBoxTexture { Texture = tex, ModulateColor = new Color(1.15f, 1.15f, 1.15f, 1f) };
                hover.SetTextureMarginAll(padding);
                target.AddThemeStyleboxOverride("normal", normal);
                target.AddThemeStyleboxOverride("hover", hover);
                target.AddThemeStyleboxOverride("pressed", hover);
            }
            catch (Exception ex)
            {
                GD.PrintErr("[NekoSpire] apply game button sprite failed: " + ex.Message);
            }
        }

        /// <summary>Apply a user-supplied button texture from user://NekoSpire/<filename> (a PNG the author
        /// drops in the user-data dir). Falls back to a game sprite if the file is absent.</summary>
        public static void ApplyUserButtonTexture(Button target, string filename, string fallbackResPath, float padding = 14f)
        {
            var tex = LoadUserTexture(filename);
            if (tex == null)
            {
                ApplyGameButtonSprite(target, fallbackResPath, padding);
                return;
            }
            var normal = new StyleBoxTexture { Texture = tex };
            normal.SetTextureMarginAll(padding);
            var hover = new StyleBoxTexture { Texture = tex, ModulateColor = new Color(1.15f, 1.15f, 1.15f, 1f) };
            hover.SetTextureMarginAll(padding);
            target.AddThemeStyleboxOverride("normal", normal);
            target.AddThemeStyleboxOverride("hover", hover);
            target.AddThemeStyleboxOverride("pressed", hover);
        }

        public static Texture2D? LoadUserTexture(string filename)
        {
            // 1) Mod-packed asset (res://neko_comm/ui/<file> — when the .tscn/ui are packed into the mod's
            //    pck, the author's textures ship with the mod and resolve here).
            try
            {
                var packed = GD.Load<Texture2D>("res://neko_comm/ui/" + filename);
                if (packed != null)
                    return packed;
            }
            catch
            {
                // fall through
            }
            // 2) Loose folder next to the mod's dll (<mods>/nekospire_ui/), for dev without repacking.
            try
            {
                var dllDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location) ?? "";
                var path = Path.Combine(dllDir, "nekospire_ui", filename);
                if (!File.Exists(path))
                    return null;
                var image = new Image();
                if (image.LoadPngFromBuffer(File.ReadAllBytes(path)) != Error.Ok)
                    return null;
                return ImageTexture.CreateFromImage(image);
            }
            catch
            {
                return null;
            }
        }
    }
}
