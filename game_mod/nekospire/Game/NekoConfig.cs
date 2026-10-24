// Standalone LLM config for NekoSpire: the mod can call a user-supplied OpenAI-compatible LLM API to
// generate catgirl danmaku without the N.E.K.O client. Persisted to user://NekoSpire/settings.json
// (System.Text.Json, atomic write). Loaded on mod init; edited via NekoConfigWindow.
using System;
using System.IO;
using System.Text;
using System.Text.Json;
using Godot;

namespace NekoComm.Game
{
    internal sealed class NekoConfig
    {
        public bool coop_enabled { get; set; }
        // Port of the catgirl (co-op client) instance. Autoplay drives the local player when this
        // process's own API port (STS2_API_PORT, else HttpServer.DefaultPort) equals coop_client_port.
        // Override with the STS2_COOP_PORT env var; see NekoAutoplayDriver.ResolveCoopClientPort().
        public int coop_client_port { get; set; } = 18081;
        // Danmaku font size (px). Icon / smoke panel scale off this (DanmakuSpire uses 24, 16-40 adjustable).
        public int danmaku_font_size { get; set; } = 24;
        // Custom catgirl-avatar image for the danmaku: a filename under mods/nekospire_ui/ (default catgirl.png),
        // used when no base64 avatar is supplied (the N.E.K.O client). Icon shows none if missing.
        public string danmaku_avatar { get; set; } = "catgirl.png";
        public bool llm_enabled { get; set; } = true;
        // Danmaku (catgirl commentary) is decoupled from llm_enabled (which gates the autoplay LLM decisions
        // for MAP/reward/event/deck). Turn this off to suppress commentary while keeping decision-LLM on.
        // The catgirl (co-op autoplay client) process also skips danmaku automatically.
        // Scope: this mod's OWN standalone LLM danmaku driver (NekoDanmakuDriver) only.
        public bool danmaku_enabled { get; set; } = true;
        // Runtime gate for POST /danmaku — the path the N.E.K.O plugin pushes catgirl commentary through.
        // Independent of danmaku_enabled on purpose: the plugin hardwrites danmaku_enabled=false on every
        // connect (to silence this mod's own driver), so reusing that key as the overlay switch would make
        // the plugin turn its own danmaku off. Set false to stop rendering pushes from any local caller.
        public bool danmaku_push_enabled { get; set; } = true;
        // Co-op map votes: when true the catgirl follows a vote the other player already cast instead of
        // racing to vote its own preferred node; it only votes on its own after vote_wait_seconds.
        public bool coop_map_follow_player { get; set; } = true;
        public int coop_map_vote_wait_seconds { get; set; } = 20;
        // When the main menu shows a resumable run (Continue enabled), the catgirl refuses to click
        // Continue / start a new room on its own. Both game processes share user://, so resuming the run in
        // the catgirl process makes the co-op save unreachable for the host.
        public bool coop_yield_to_saved_run { get; set; } = true;
        public string llm_base_url { get; set; } = "https://api.openai.com/v1";
        public string llm_api_key { get; set; } = "";
        public string llm_model { get; set; } = "";
        public int llm_max_tokens { get; set; } = 80;

        // ★★★ t46：`GET /solver/plan` 的搜索时间预算（**秒**）。0 或负数 = 不覆盖（回到上游默认）。
        //
        // 为什么必须由本模组自己限流（实机铁证，2026-10-22/24 三份 `godot.log`）：
        //   `/solver/plan` 是**同步**端点 —— 插件要等到响应回来才出牌。而
        //   `CombatSolverFacade.SolverFacadeOptions.Default` 的 Deep 预算是 **120000 ms**，
        //   再加上同一 profile 预算下的补充审计（`SUPPLEMENTAL_AUDIT_BUDGET budget_ms=120000`），
        //   单次请求实测 **78 s / 137 s（均值）/ 155 s，最长 329 s**；插件旧实现 15 s 读超时后
        //   放弃并重发，实测**最多 21 个搜索同时在飞**，越拖越慢。
        //
        // 取值 12 s 的依据：t45 真机取证里 208 次请求是 min 11 ms / **median 64 ms** / max 12.2 s
        //   ⇒ 绝大多数回合远低于 12 s，只有病态局面会被这个上限截断（截断时引擎按上游既有语义
        //   发布已算出的前沿 = `search_status=budget_exceeded`，不会退化成"没有计划"）。
        //   ⇒ 尾部从"几分钟"变成"最多十几秒"，而正常回合的计划质量不受影响。
        public double solver_budget_seconds { get; set; } = 12;

        private static readonly JsonSerializerOptions Options = new()
        {
            WriteIndented = true,
            PropertyNameCaseInsensitive = true,
        };

        public static NekoConfig Current { get; private set; } = new();

        private static string ConfigPath => ProjectSettings.GlobalizePath("user://NekoSpire/settings.json");

        public static void Load()
        {
            try
            {
                var path = ConfigPath;
                if (File.Exists(path))
                    Current = JsonSerializer.Deserialize<NekoConfig>(File.ReadAllText(path), Options) ?? new NekoConfig();
            }
            catch
            {
                Current = new NekoConfig();
            }
        }

        public void Save()
        {
            try
            {
                var path = ConfigPath;
                var dir = Path.GetDirectoryName(path);
                if (!string.IsNullOrEmpty(dir))
                    Directory.CreateDirectory(dir);
                var tmp = path + ".new";
                File.WriteAllText(tmp, JsonSerializer.Serialize(this, Options), new UTF8Encoding(false));
                File.Move(tmp, path, overwrite: true);
            }
            catch (Exception ex)
            {
                GD.PrintErr("[NekoSpire] config save failed: " + ex.Message);
            }
        }
    }
}
