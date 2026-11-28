using System.Net.Http;
using System.Net.Http.Json;
using System.Net.NetworkInformation;
using System.Text.Json;
using NekoComm.Server;

namespace NekoComm.Game;

// Host-side peer handshake. A listening port alone is NOT proof that it is our catgirl.
internal sealed class CoopClientPeer(HttpClient http, Uri address)
{
    private static readonly HttpClient LocalHttp = new(new HttpClientHandler
    {
        UseProxy = false, AllowAutoRedirect = false
    }) { Timeout = TimeSpan.FromSeconds(25) };

    internal static CoopClientPeer? Existing(int port) =>
        IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners().Any(p => p.Port == port)
            ? new CoopClientPeer(LocalHttp, new Uri($"http://127.0.0.1:{port}/")) : null;

    internal async Task PrepareAsync()
    {
        using var response = await http.GetAsync(new Uri(address, "config"));
        using var doc = await ReadReplyAsync(response);
        var root = doc.RootElement;
        if (!root.TryGetProperty("data", out var data)
            || !data.TryGetProperty("coop_role", out var role) || role.GetString() != "catgirl"
            || !data.TryGetProperty("coop_rejoin_supported", out var supported) || supported.ValueKind != JsonValueKind.True)
            throw new ApiException(409, "coop_client_not_reusable",
                "猫娘端口被其他服务占用，或猫娘仍运行旧版 mod；请更新并重启一次游戏，不会重复拉起进程。");
        await ActionAsync("prepare_coop_rejoin", null);
    }

    internal Task RejoinAsync(ulong clientId) => ActionAsync("rejoin_coop_session",
        clientId.ToString(System.Globalization.CultureInfo.InvariantCulture));

    private async Task ActionAsync(string action, string? command)
    {
        using var response = await http.PostAsJsonAsync(new Uri(address, "action"), new { action, command });
        using var doc = await ReadReplyAsync(response);
    }

    private static async Task<JsonDocument> ReadReplyAsync(HttpResponseMessage response)
    {
        var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        if (response.IsSuccessStatusCode && doc.RootElement.TryGetProperty("ok", out var ok)
            && ok.ValueKind == JsonValueKind.True) return doc;
        var detail = doc.RootElement.TryGetProperty("error", out var error) ? error.ToString() : response.StatusCode.ToString();
        doc.Dispose();
        throw new ApiException(409, "coop_client_rejoin_failed", "现有猫娘实例重连失败：" + detail);
    }
}
