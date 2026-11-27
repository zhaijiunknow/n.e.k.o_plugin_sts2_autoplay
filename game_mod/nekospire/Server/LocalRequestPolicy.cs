namespace NekoComm.Server;

// The browser UI talks to the N.E.K.O host, not directly to this game API.
// This is a browser isolation boundary, not authentication against local processes.
internal static class LocalRequestPolicy
{
    public static void Validate(string method, string? host, string? origin,
        string? contentType, string? fetchSite, int port)
    {
        var allowedHost = host == $"127.0.0.1:{port}"
            || string.Equals(host, $"localhost:{port}", StringComparison.OrdinalIgnoreCase)
            || host == $"[::1]:{port}";
        if (!allowedHost)
            throw new ApiException(403, "forbidden_host", "Host must name this loopback endpoint.");
        // Reject null/empty origins too: sandboxed/file pages may send Origin: null.
        // No CORS allow headers are emitted; even loopback browser origins are untrusted.
        if (origin != null || fetchSite != null)
            throw new ApiException(403, "forbidden_origin", "Browser requests are not supported; use the plugin host.");
        if (method.Equals("POST", StringComparison.OrdinalIgnoreCase)
            && !string.Equals(contentType?.Split(';', 2)[0].Trim(), "application/json", StringComparison.OrdinalIgnoreCase))
            throw new ApiException(415, "unsupported_media_type", "POST requests must use application/json.");
    }
}
