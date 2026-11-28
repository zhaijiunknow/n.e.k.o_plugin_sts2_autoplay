using System.Text.Json;
using System.Text.Json.Serialization;

namespace NekoComm.Server;

// Credentials can only be edited in the local game settings window.
internal sealed record ConfigWriteRequest(
    bool? llm_enabled,
    bool? danmaku_enabled,
    bool? danmaku_push_enabled,
    bool? coop_enabled,
    string? coop_control_mode,
    bool? livestream_enabled,
    int? coop_client_port,
    string? coop_map_policy,
    bool? coop_map_follow_player,
    int? coop_map_vote_wait_seconds,
    string? llm_model,
    int? llm_max_tokens)
{
    [JsonExtensionData]
    public Dictionary<string, JsonElement>? Extra { get; init; }

    public void Validate()
    {
        if (Extra?.Keys.Any(key => key.Equals("llm_base_url", StringComparison.OrdinalIgnoreCase)
            || key.Equals("llm_api_key", StringComparison.OrdinalIgnoreCase)) == true)
            throw new ApiException(403, "local_config_only", "Edit LLM credentials and endpoint in the game settings window.");
        if (coop_control_mode != null && coop_control_mode is not ("auto" or "hold"))
            throw new ApiException(400, "invalid_config", "coop_control_mode must be auto or hold.");
        if (coop_map_policy != null && coop_map_policy is not ("wait" or "follow" or "auto"))
            throw new ApiException(400, "invalid_config", "coop_map_policy must be wait, follow, or auto.");
        if (coop_map_vote_wait_seconds is < 0 or > 86400)
            throw new ApiException(400, "invalid_config", "Map wait must be between 0 and 86400 seconds.");
        if (coop_client_port is < 1 or > 65535)
            throw new ApiException(400, "invalid_config", "Invalid co-op client port.");
    }
}
