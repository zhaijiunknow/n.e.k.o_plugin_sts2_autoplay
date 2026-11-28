namespace NekoComm.Game;

// ENet's host is always 1. New local clients use 2, never an OS process ID.
// Old two-player saves retain their original ID: no save-file migration is necessary.
internal static class CoopSaveIdentity
{
    internal const ulong HostId = 1;
    internal const ulong NewClientId = 2;

    // Process-local override for a reused client. Never rewrite save-file player IDs.
    private static ulong? _sessionClientId;
    internal static ulong CurrentClientId => _sessionClientId
        ?? ParseClientId(Environment.GetEnvironmentVariable("STS2_COOP_NET_ID"));
    internal static void SetSessionClientId(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) throw new InvalidOperationException("Missing co-op client identity.");
        _sessionClientId = ParseClientId(value);
    }

    internal static bool CanRejoin(bool isCatgirl, string screen) =>
        isCatgirl && screen is "MAIN_MENU" or "GAME_OVER";

    internal static bool TryResolve(bool localTransport, IReadOnlyList<ulong> ids, out ulong clientId)
    {
        clientId = 0;
        if (!localTransport || ids.Count != 2 || ids.Count(id => id == HostId) != 1)
            return false;
        clientId = ids.First(id => id != HostId);
        return clientId > HostId;
    }

    internal static ulong ParseClientId(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return NewClientId;
        if (ulong.TryParse(value, out var id) && id > HostId) return id;
        throw new InvalidOperationException("Invalid STS2_COOP_NET_ID; refusing to use a different player identity.");
    }
}
