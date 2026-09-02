// In-game LLM-config input window for the standalone NekoSpire build. Built purely in code (no .tscn —
// neko_comm builds with Microsoft.NET.Sdk so a scene's C# lifecycle is not wired; built-in nodes + signal
// connection at runtime is the reliable pattern). Textures come from the mod's own assets (nekospire_ui/).
using System;
using System.Threading.Tasks;
using Godot;
using MegaCrit.Sts2.Core.Nodes;

namespace NekoComm.Game
{
    internal sealed class NekoConfigWindow
    {
        public static NekoConfigWindow? Instance { get; private set; }

        private CanvasLayer? _layer;
        private Control? _overlay;
        private LineEdit? _baseUrl;
        private LineEdit? _apiKey;
        private LineEdit? _model;
        private CheckButton? _llmEnabled;

        public static async Task OpenAsync()
        {
            await GameThread.InvokeAsync(() =>
            {
                Instance ??= new NekoConfigWindow();
                Instance.Show();
                return true;
            });
        }

        private void Show()
        {
            if (_layer == null || !GodotObject.IsInstanceValid(_layer))
                Build();
            if (_overlay != null)
                _overlay.Visible = true;
        }

        private void Close()
        {
            if (_overlay != null)
                _overlay.Visible = false;
        }

        private void Build()
        {
            var cfg = NekoConfig.Current;
            _layer = new CanvasLayer { Name = "NekoSpireConfigLayer", Layer = 120 };
            var overlay = new Control { Name = "NekoSpireConfigOverlay", Visible = true };
            overlay.SetAnchorsAndOffsetsPreset(Control.LayoutPreset.FullRect);
            _overlay = overlay;

            var center = new CenterContainer();
            center.SetAnchorsAndOffsetsPreset(Control.LayoutPreset.FullRect);
            overlay.AddChild(center);

            var panel = new PanelContainer { CustomMinimumSize = new Vector2(540, 0) };
            panel.AddThemeStyleboxOverride("panel", NekoUi.BuildPanelBackground());
            center.AddChild(panel);

            // Outer vbox fills the panel; field rows stay inside a margin, the button row sits directly in
            // the outer vbox so Save/Close are flush against the panel's right edge.
            var vbox = new VBoxContainer();
            vbox.AddThemeConstantOverride("separation", 12);
            panel.AddChild(vbox);

            var margin = new MarginContainer();
            margin.AddThemeConstantOverride("margin_left", 24);
            margin.AddThemeConstantOverride("margin_top", 5);
            margin.AddThemeConstantOverride("margin_right", 24);
            vbox.AddChild(margin);
            var fields = new VBoxContainer();
            fields.AddThemeConstantOverride("separation", 12);
            margin.AddChild(fields);

            var title = new Label { Text = "NekoSpire LLM 设置", HorizontalAlignment = HorizontalAlignment.Left };
            NekoUi.ApplyFont(title, 24, bold: true);
            fields.AddChild(title);
            var subtitle = new Label { Text = "填写你的 LLM API(base_url / model / api_key)。" };
            NekoUi.ApplyFont(subtitle, 16);
            fields.AddChild(subtitle);

            _baseUrl = AddRow(fields, "Base URL", cfg.llm_base_url);
            _apiKey = AddRow(fields, "API Key", cfg.llm_api_key);
            _model = AddRow(fields, "Model", cfg.llm_model);

            _llmEnabled = new CheckButton { Text = "启用 LLM 弹幕（直接调用该 LLM 生成,给出猫娘建议）", ButtonPressed = cfg.llm_enabled };
            NekoUi.ApplyFont(_llmEnabled, 18);
            fields.AddChild(_llmEnabled);

            // One-click host-side co-op start (replaces the old coop_enabled checkbox): enable coop so the
            // catgirl autoplay activates, then open an ENet multiplayer room. Requires being at the main menu.
            var hostCoop = new Button { Text = "开始 co-op 房间", CustomMinimumSize = new Vector2(0, 44), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
            NekoUi.ApplyFont(hostCoop, 20, bold: true);
            NekoUi.ApplyUserButtonTexture(hostCoop, "open_settings.png", "res://images/packed/common_ui/settings_tab_selected.png");
            hostCoop.Pressed += OnHostCoopPressed;
            fields.AddChild(hostCoop);

            // Button row flush to the panel's right: right-aligned + fill-width, no right margin.
            var btnRow = new HBoxContainer();
            btnRow.AddThemeConstantOverride("separation", 12);
            btnRow.Alignment = BoxContainer.AlignmentMode.End;
            btnRow.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;

            var save = new Button { Text = "保存", CustomMinimumSize = new Vector2(120, 44) };
            NekoUi.ApplyFont(save, 20);
            NekoUi.ApplyUserButtonTexture(save, "save.png", "res://images/packed/common_ui/submenu_compendium_button.png");
            save.Pressed += OnSave;

            var close = new Button { Text = "关闭", CustomMinimumSize = new Vector2(120, 44) };
            NekoUi.ApplyFont(close, 20);
            NekoUi.ApplyUserButtonTexture(close, "close.png", "res://images/packed/common_ui/submenu_compendium_button.png");
            close.Pressed += Close;

            btnRow.AddChild(save);
            btnRow.AddChild(close);
            vbox.AddChild(btnRow);

            _layer.AddChild(overlay);
            NGame.Instance.GetTree().Root.CallDeferred(Node.MethodName.AddChild, _layer);
        }

        private static LineEdit AddRow(Container parent, string name, string value)
        {
            var row = new HBoxContainer();
            row.AddThemeConstantOverride("separation", 10);
            var label = new Label { Text = name, CustomMinimumSize = new Vector2(140, 0) };
            NekoUi.ApplyFont(label, 18);
            var edit = new LineEdit { Text = value, SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
            NekoUi.ApplyFont(edit, 18);
            row.AddChild(label);
            row.AddChild(edit);
            parent.AddChild(row);
            return edit;
        }

        private void OnSave()
        {
            if (_baseUrl == null || _apiKey == null || _model == null || _llmEnabled == null)
                return;
            var cfg = NekoConfig.Current;
            cfg.llm_base_url = _baseUrl.Text.Trim();
            cfg.llm_api_key = _apiKey.Text.Trim();
            cfg.llm_model = _model.Text.Trim();
            cfg.llm_enabled = _llmEnabled.ButtonPressed;
            cfg.Save();
            Close();
        }

        private void OnHostCoopPressed()
        {
            // Host-side: enable coop (so the catgirl autoplay activates when launched as the client) and open
            // an ENet multiplayer room. The config window overlays the main menu, so open_multiplayer_menu then
            // start_multiplayer_host work while the player is at the main menu.
            var cfg = NekoConfig.Current;
            cfg.coop_enabled = true;
            cfg.Save();
            Close();
            _ = StartCoopSessionAsync();
        }

        private async Task StartCoopSessionAsync()
        {
            try
            {
                // The config window is opened from the mods submenu; pop back to the actual main menu first so
                // the multiplayer actions (open_multiplayer_menu -> start_multiplayer_host) are valid (they
                // require currentScreen to be NMainMenu, not the mods submenu).
                await CloseMainMenuSubmenusAsync();
                await GameThread.InvokeAsync(() => GameActionService.ExecuteAsync(new ActionRequest { action = "open_multiplayer_menu" }));
                await GameThread.InvokeAsync(() => GameActionService.ExecuteAsync(new ActionRequest { action = "start_multiplayer_host" }));
                GD.Print("[NekoSpire] host co-op room opened; launching catgirl...");
                LaunchCatgirlProcess();
            }
            catch (Exception ex)
            {
                GD.PrintErr("[NekoSpire] co-op session start failed: " + ex.Message);
            }
        }

        // Pop the main-menu submenu stack back to NMainMenu (only the mods submenu is open when the config
        // window's co-op button is clicked, but loop defensively for nested submenus).
        private static async Task CloseMainMenuSubmenusAsync()
        {
            for (var i = 0; i < 6; i++)
            {
                var hasClose = await GameThread.InvokeAsync(() =>
                {
                    var st = GameStateService.BuildStatePayload();
                    return st.available_actions.Contains("close_main_menu_submenu");
                });
                if (!hasClose)
                    return;
                await GameThread.InvokeAsync(() => GameActionService.ExecuteAsync(new ActionRequest { action = "close_main_menu_submenu" }));
                await Task.Delay(150);
            }
        }

        private static void LaunchCatgirlProcess()
        {
            try
            {
                // Same exe as the host game. Catgirl = client on coop_client_port with debug actions on;
                // no +connect_lobby, so its autoplay uses the ENet path (open_multiplayer_menu + join_multiplayer_direct).
                var exe = System.Environment.ProcessPath;
                var startInfo = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = exe ?? "SlayTheSpire2.exe",
                    WorkingDirectory = exe != null ? System.IO.Path.GetDirectoryName(exe) ?? "" : "",
                    UseShellExecute = false,
                };
                var catgirlPort = NekoConfig.Current.coop_client_port;
                startInfo.Environment["STS2_API_PORT"] = catgirlPort.ToString();
                startInfo.Environment["STS2_ENABLE_DEBUG_ACTIONS"] = "1";
                System.Diagnostics.Process.Start(startInfo);
                GD.Print($"[NekoSpire] catgirl process launched (port {catgirlPort}, debug on)");
            }
            catch (Exception ex)
            {
                GD.PrintErr("[NekoSpire] catgirl launch failed: " + ex.Message);
            }
        }
    }
}
