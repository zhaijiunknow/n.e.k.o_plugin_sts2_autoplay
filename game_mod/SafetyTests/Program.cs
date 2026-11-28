using System.Text;
using System.Text.Json;
using NekoComm.Game;
using NekoComm.Server;

int checks = 0;
void Check(bool condition, string label) { if (!condition) throw new Exception(label); checks++; }
void Reject(Action action, int status)
{
    try { action(); throw new Exception("Expected rejection"); }
    catch (ApiException ex) { Check(ex.StatusCode == status, ex.Code); }
}
void Validate(string method = "POST", string? host = "127.0.0.1:18080", string? origin = null,
    string? content = "application/json", string? fetchSite = null, int port = 18080) =>
    LocalRequestPolicy.Validate(method, host, origin, content, fetchSite, port);
Validate(); checks++;
Validate(content: "Application/JSON; charset=utf-8"); checks++;
Validate(host: "127.0.0.1:18081", port: 18081); checks++;
Validate(method: "GET", content: null); checks++;
foreach (var host in new string?[] { null, "evil.test:18080", "127.0.0.1.evil.test:18080", "127.0.0.1:18081", "localhost", "user@127.0.0.1:18080" })
    Reject(() => Validate(host: host), 403);
foreach (var origin in new[] { "https://evil.test", "null", "", "http://127.0.0.1:48916", "http://localhost:18080" })
    Reject(() => Validate(origin: origin), 403);
Reject(() => Validate(method: "GET", fetchSite: "cross-site"), 403);
Reject(() => Validate(method: "GET", fetchSite: "none"), 403);
foreach (var content in new string?[] { null, "", "text/plain", "application/x-www-form-urlencoded", "multipart/form-data", "application/jsonp", "application/json, text/plain" })
    Reject(() => Validate(content: content), 415);

async Task<ConfigWriteRequest> Parse(string json)
{
    using var stream = new MemoryStream(Encoding.UTF8.GetBytes(json));
    return (await JsonHelper.DeserializeAsync<ConfigWriteRequest>(stream))!;
}
foreach (var key in new[] { "llm_base_url", "llm_api_key", "LLM_API_KEY" })
{
    var req = await Parse($"{{\"{key}\":null,\"llm_enabled\":false}}");
    Reject(req.Validate, 403);
}
foreach (var value in new[] { -1, 86401 })
    Reject((await Parse($"{{\"coop_map_vote_wait_seconds\":{value}}}")).Validate, 400);
Reject((await Parse("{\"coop_map_policy\":\"garbage\"}")).Validate, 400);
foreach (var policy in new[] { "wait", "follow", "auto" })
{
    var req = await Parse($"{{\"coop_map_policy\":\"{policy}\",\"coop_map_vote_wait_seconds\":0}}");
    req.Validate(); Check(req.coop_map_vote_wait_seconds == 0, "zero preserved");
    Check(ExternalControl.MapPolicy(policy, true, true) == policy, "live plugin policy");
    Check(ExternalControl.MapPolicy(policy, true, false) == "follow", "offline standalone follow");
    Check(ExternalControl.MapPolicy(policy, false, false) == "auto", "offline standalone auto");
}
foreach (var mode in new[] { "auto", "hold" })
{
    var req = await Parse($"{{\"coop_control_mode\":\"{mode}\"}}");
    req.Validate(); Check(req.coop_control_mode == mode, "control mode parsed, not ignored");
}
Reject((await Parse("{\"coop_control_mode\":\"garbage\"}")).Validate, 400);
foreach (var screen in new[] { "MAIN_MENU", "MULTIPLAYER_LOBBY", "CHARACTER_SELECT" })
    Check(!ExternalControl.HoldAutoplay("hold", screen, true), "takeover still allows bootstrap");
foreach (var screen in new[] { "MAP", "COMBAT", "EVENT", "REWARD", "GAME_OVER", "UNKNOWN" })
{
    Check(ExternalControl.HoldAutoplay("hold", screen, true), "plugin owns run decisions");
    Check(!ExternalControl.HoldAutoplay("hold", screen, false), "expired lease restores standalone");
    Check(!ExternalControl.HoldAutoplay("auto", screen, true), "auto remains standalone");
}
Check(!ExternalControl.IsActive, "no lease initially");
ExternalControl.Touch(); Check(ExternalControl.IsActive, "request activates lease");

var directory = Path.Combine(Path.GetTempPath(), "nekospire-safety-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(directory);
try
{
    var path = Path.Combine(directory, "settings.json");
    File.WriteAllText(path, "{\"coop_map_vote_wait_seconds\":0,\"custom\":{\"keep\":true}}");
    NekoConfig.LoadFromPath(path);
    Check(NekoConfig.Current.coop_map_vote_wait_seconds == 0, "legacy config loaded");
    NekoConfig.Current.coop_map_policy = "wait";
    NekoConfig.Current.coop_control_mode = "hold";
    NekoConfig.Current.SaveToPath(path);
    using (var doc = JsonDocument.Parse(File.ReadAllText(path)))
    {
        Check(doc.RootElement.GetProperty("schema_version").GetInt32() == 1, "legacy migration");
        Check(doc.RootElement.GetProperty("custom").GetProperty("keep").GetBoolean(), "unknown fields preserved");
        Check(!doc.RootElement.TryGetProperty("coop_map_policy", out _), "runtime policy not persisted");
        Check(!doc.RootElement.TryGetProperty("coop_control_mode", out _), "process hold not persisted");
    }
    foreach (var json in new[] { "{\"schema_version\":99}", "{\"SCHEMA_VERSION\":99}", "{\"schema_version\":\"new\"}" })
    {
        File.WriteAllText(path, json); NekoConfig.LoadFromPath(path); NekoConfig.Current.SaveToPath(path);
        Check(File.ReadAllText(path) == json, "future/invalid version not overwritten");
    }
    foreach (var json in new[] { "{broken", "null", "[]", "{\"coop_map_vote_wait_seconds\":\"bad\"}" })
    {
        File.WriteAllText(path, json); NekoConfig.LoadFromPath(path);
        Check(Directory.GetFiles(directory, "*.bak").Any(p => File.ReadAllText(p) == json), "corrupt original backed up");
        NekoConfig.Current.SaveToPath(path);
        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        Check(doc.RootElement.GetProperty("schema_version").GetInt32() == 1, "safe defaults after backup");
    }
    File.WriteAllText(path, "{\"llm_model\":\"keep-on-io-error\"}");
    var original = File.ReadAllText(path);
    using (var locked = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
        NekoConfig.LoadFromPath(path);
    NekoConfig.Current.SaveToPath(path);
    Check(File.ReadAllText(path) == original, "IO failure blocks a later automatic save");
    NekoConfig.LoadFromPath(path); NekoConfig.Current.SaveToPath(path);
    Check(NekoConfig.Current.llm_model == "keep-on-io-error", "explicit reload after repair");
    Check(!Directory.GetFiles(directory, "*.new").Any(), "no temp files remain");
}
finally
{
    var cleanupPath = Path.GetFullPath(directory);
    var tempRoot = Path.GetFullPath(Path.GetTempPath());
    if (!string.Equals(Path.GetDirectoryName(cleanupPath)?.TrimEnd(Path.DirectorySeparatorChar),
            tempRoot.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase)
        || !Path.GetFileName(cleanupPath).StartsWith("nekospire-safety-", StringComparison.Ordinal))
        throw new InvalidOperationException("Refusing cleanup outside the safety test temp directory.");
    Directory.Delete(cleanupPath, recursive: true);
}
var pacing = new DanmakuPacing();
Check(pacing.TryAccept("第一句", 0, 0), "danmaku first line at time zero");
Check(!pacing.TryAccept("第二句", 1, 1), "danmaku burst dropped");
Check(pacing.TryAccept("第二句", 1, 6), "danmaku new line after cooldown");
Check(!pacing.TryAccept("第三句", 2, 12), "danmaku active cap");
Check(pacing.TryAccept("第三句", 1, 12), "full screen rejection does not consume slot");
Check(!pacing.TryAccept("第一句", 0, 18), "danmaku recent text dedup");
Check(pacing.TryAccept("第一句", 0, 60), "danmaku dedup expires");
Check(!pacing.TryAccept(" ", 0, 66), "danmaku empty text");
Check(DanmakuPacing.Clean(" 第一条！\n第二条 ") == "第一条！", "danmaku one sentence");
Check(DanmakuPacing.Clean(new string('长', 100)).Length == 32, "danmaku length budget");
Check(System.Globalization.StringInfo.ParseCombiningCharacters(DanmakuPacing.Clean(string.Concat(Enumerable.Repeat("🐱", 50)))).Length == 32,
    "danmaku truncation preserves unicode");
Check(DanmakuPacing.LifetimeSeconds < 2 * DanmakuPacing.MinIntervalSeconds, "danmaku lifetime fits two lanes");
Check(CoopSaveIdentity.TryResolve(true, new ulong[] { 1, 25324 }, out var oldId) && oldId == 25324,
    "old process-derived identity survives restart without editing save");
Check(CoopSaveIdentity.TryResolve(true, new ulong[] { 8832, 1 }, out oldId) && oldId == 8832,
    "saved player order does not determine host");
Check(CoopSaveIdentity.TryResolve(true, new ulong[] { 1, 2 }, out oldId) && oldId == 2, "new stable ID");
Check(!CoopSaveIdentity.TryResolve(false, new ulong[] { 1, 2 }, out _), "never convert Steam saves");
foreach (var ids in new[] { new ulong[] { 1 }, new ulong[] { 1, 2, 3 }, new ulong[] { 2, 3 },
    new ulong[] { 1, 1 }, new ulong[] { 1, 0 }, Array.Empty<ulong>() })
    Check(!CoopSaveIdentity.TryResolve(true, ids, out _), "reject ambiguous party identity");
Check(CoopSaveIdentity.ParseClientId(null) == 2, "new ID not based on PID");
Check(CoopSaveIdentity.ParseClientId("25324") == 25324, "launch preserves old identity");
foreach (var value in new[] { "0", "1", "-2", "oops", "18446744073709551616" })
{
    bool rejected = false;
    try { CoopSaveIdentity.ParseClientId(value); } catch (InvalidOperationException) { rejected = true; }
    Check(rejected, "bad launch identity fails closed");
}
await CoopPeerChecks.RunAsync(Check);
Console.WriteLine($"ALL PASS ({checks} checks)");
