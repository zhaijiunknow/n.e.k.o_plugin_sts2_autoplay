// Harmony patch that injects an "打开 NekoSpire 设置" button into the mod's detail page in the game's
// mod manager (ModdingScreen -> NModInfoContainer.Fill), so a standalone (no N.E.K.O client) player can
// open the in-game LLM config window from the mod settings. Resolved by type name for version robustness;
// only our mod id triggers the injection; any failure is a no-op that never breaks the mod/game.
// CombatSolverRuntime.Install() calls Harmony.PatchAll() over the whole assembly, so this [HarmonyPatch]
// type is discovered automatically.
using System;
using System.Reflection;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.Modding;

namespace NekoComm.Game
{
    [HarmonyPatch]
    internal static class NekoSettingsPatch
    {
        private const string InfoContainerTypeName = "MegaCrit.Sts2.Core.Nodes.Screens.ModdingScreen.NModInfoContainer";
        private const string ModId = "neko_comm";
        private const string ButtonName = "NekoSpireSettingsButton";

        public static MethodBase? TargetMethod()
        {
            var type = AccessTools.TypeByName(InfoContainerTypeName);
            return type == null ? null : AccessTools.DeclaredMethod(type, "Fill");
        }

        public static void Postfix(object __instance, Mod mod)
        {
            try
            {
                // NModInfoContainer : Control (NOT Container). Runs on every Fill so switching mods
                // shows our entry only when this mod's detail is displayed and hides it otherwise.
                if (__instance is Control container)
                {
                    var ours = mod?.manifest?.id == ModId;
                    UpdateButton(container, ours);
                }
            }
            catch (Exception ex)
            {
                GD.PrintErr($"[NekoSpire] mod-settings entry failed: {ex.Message}");
            }
        }

        private static void UpdateButton(Control container, bool ours)
        {
            var entry = container.GetNodeOrNull<Button>(ButtonName);
            if (ours)
            {
                if (entry == null)
                {
                    entry = new Button
                    {
                        Name = ButtonName,
                        Text = "打开设置",
                        CustomMinimumSize = new Vector2(220, 48),
                    };
                    NekoUi.ApplyFont(entry, 20, bold: true);
                    NekoUi.ApplyUserButtonTexture(entry, "open_settings.png", "res://images/packed/common_ui/settings_tab_selected.png");
                    entry.Pressed += () => _ = NekoConfigWindow.OpenAsync();
                    container.AddChild(entry);
                    // Dock to the detail page's bottom-left so the button's bottom edge sits near the
                    // panel bottom (16px margin), instead of scattering at a guessed absolute Y.
                    entry.SetAnchorsAndOffsetsPreset(Control.LayoutPreset.BottomLeft, Control.LayoutPresetMode.KeepSize, 16);
                    GD.Print("[NekoSpire] injected settings button into mod detail page");
                }
                entry.Visible = true;
            }
            else if (entry != null)
            {
                // Hide on other mods' detail pages (the container is reused across Fills).
                entry.Visible = false;
            }
        }
    }
}
