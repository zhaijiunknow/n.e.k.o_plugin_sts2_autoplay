using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using NekoComm.Game;
using NekoComm.Server;

internal static class CoopPeerChecks
{
    private const string Config = """{"ok":true,"data":{"coop_role":"catgirl","coop_rejoin_supported":true}}""";
    private const string Ok = """{"ok":true,"data":{"status":"completed"}}""";

    internal static async Task RunAsync(Action<bool, string> check)
    {
        foreach (var screen in new[] { "MAIN_MENU", "GAME_OVER" })
        {
            check(CoopSaveIdentity.CanRejoin(true, screen), "reuse idle catgirl");
            check(!CoopSaveIdentity.CanRejoin(false, screen), "never repurpose host");
        }
        foreach (var screen in new[] { "COMBAT", "MAP", "EVENT", "CHARACTER_SELECT", "UNKNOWN" })
            check(!CoopSaveIdentity.CanRejoin(true, screen), "never interrupt a live run/lobby");
        CoopSaveIdentity.SetSessionClientId("25324");
        check(CoopSaveIdentity.CurrentClientId == 25324, "reuse retains old save identity");
        CoopSaveIdentity.SetSessionClientId("2");
        check(CoopSaveIdentity.CurrentClientId == 2, "next new run restores stable identity");
        foreach (var value in new string?[] { null, "", "1", "0", "oops" })
        {
            bool rejected = false;
            try { CoopSaveIdentity.SetSessionClientId(value); }
            catch (InvalidOperationException) { rejected = true; }
            check(rejected && CoopSaveIdentity.CurrentClientId == 2, "bad identity does not mutate session");
        }
        foreach (var id in new ulong[] { 2, 25324, ulong.MaxValue })
        {
            using var handler = new Handler(Config, Ok, Ok);
            using var http = new HttpClient(handler);
            var peer = new CoopClientPeer(http, new Uri("http://127.0.0.1:18081/"));
            await peer.PrepareAsync();
            await peer.RejoinAsync(id);
            check(handler.Paths.SequenceEqual(new[] { "/config", "/action", "/action" }), "verify then prepare then rejoin");
            using var prepare = JsonDocument.Parse(handler.Bodies[1]);
            using var rejoin = JsonDocument.Parse(handler.Bodies[2]);
            check(prepare.RootElement.GetProperty("action").GetString() == "prepare_coop_rejoin", "pause before host creates lobby");
            check(rejoin.RootElement.GetProperty("action").GetString() == "rejoin_coop_session", "reuse instead of spawning");
            check(rejoin.RootElement.GetProperty("command").GetString() == id.ToString(), "identity round-trips without precision loss");
        }
        foreach (var config in new[] {
            """{"ok":true,"data":{"coop_role":"host","coop_rejoin_supported":true}}""",
            """{"ok":true,"data":{"coop_role":"catgirl"}}""",
            """{"ok":true,"data":{"coop_role":"catgirl","coop_rejoin_supported":false}}""",
            """{"ok":false,"error":{"message":"old mod"}}""" })
        {
            using var handler = new Handler(config);
            using var http = new HttpClient(handler);
            var peer = new CoopClientPeer(http, new Uri("http://127.0.0.1:18081/"));
            bool rejected = false;
            try { await peer.PrepareAsync(); } catch (ApiException) { rejected = true; }
            check(rejected && handler.Paths.Count == 1, "unknown peer gets no actions");
        }
        using (var handler = new Handler(Config, """{"ok":false,"error":{"code":"coop_client_busy"}}"""))
        using (var http = new HttpClient(handler))
        {
            var peer = new CoopClientPeer(http, new Uri("http://127.0.0.1:18081/"));
            bool rejected = false;
            try { await peer.PrepareAsync(); } catch (ApiException) { rejected = true; }
            check(rejected && handler.Paths.Count == 2, "busy peer rejects preparation without reconnecting");
        }
        using (var handler = new Handler(Config, Ok, """{"ok":false,"error":{"code":"join_failed"}}"""))
        using (var http = new HttpClient(handler))
        {
            var peer = new CoopClientPeer(http, new Uri("http://127.0.0.1:18081/"));
            await peer.PrepareAsync();
            bool rejected = false;
            try { await peer.RejoinAsync(25324); } catch (ApiException) { rejected = true; }
            check(rejected && handler.Paths.Count == 3, "failed resume is not retried as a new identity");
        }
    }

    private sealed class Handler(params string[] replies) : HttpMessageHandler
    {
        internal readonly List<string> Paths = [];
        internal readonly List<string> Bodies = [];
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Paths.Add(request.RequestUri!.AbsolutePath);
            Bodies.Add(request.Content == null ? "" : await request.Content.ReadAsStringAsync(token));
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(replies[Paths.Count - 1], Encoding.UTF8, "application/json")
            };
        }
    }
}
