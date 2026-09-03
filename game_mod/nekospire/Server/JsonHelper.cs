using System.IO;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace NekoComm.Server;

internal static class JsonHelper
{
    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = null,
        PropertyNameCaseInsensitive = true,
        WriteIndented = true,
        // Keep CJK/中文 literal in the JSON body instead of escaping to \uXXXX, so raw API responses are
        // human-readable when curled. JSON semantics are unchanged for the plugin (it decodes anyway).
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    public static string Serialize<T>(T value)
    {
        return JsonSerializer.Serialize(value, Options);
    }

    public static ValueTask<T?> DeserializeAsync<T>(Stream stream, CancellationToken cancellationToken = default)
    {
        return JsonSerializer.DeserializeAsync<T>(stream, Options, cancellationToken);
    }
}
