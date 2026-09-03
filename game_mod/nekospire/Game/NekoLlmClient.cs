// Shared OpenAI-compatible chat transport for NekoSpire's standalone LLM features (catgirl danmaku and
// the autoplay out-of-combat decisions). Wraps the HTTP + Bearer auth + response parsing that
// NekoDanmakuDriver used to own, so both callers share one code path and one timeout. Returns the raw
// completion text, or null on any failure (silent, matching the standalone build's "stay quiet" idiom).
using System;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace NekoComm.Game
{
    internal static class NekoLlmClient
    {
        private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(12) };

        public static async Task<string?> ChatAsync(
            NekoConfig cfg,
            string system,
            string user,
            int maxTokens,
            double temperature)
        {
            if (string.IsNullOrWhiteSpace(cfg.llm_base_url) || string.IsNullOrWhiteSpace(cfg.llm_model))
                return null;

            var messages = new object[]
            {
                new { role = "system", content = system },
                new { role = "user", content = user },
            };
            var body = new
            {
                model = cfg.llm_model,
                messages,
                max_tokens = maxTokens,
                temperature,
            };
            var url = cfg.llm_base_url.TrimEnd('/') + "/chat/completions";

            try
            {
                using var req = new HttpRequestMessage(HttpMethod.Post, url)
                {
                    Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json"),
                };
                if (!string.IsNullOrEmpty(cfg.llm_api_key))
                    req.Headers.TryAddWithoutValidation("Authorization", "Bearer " + cfg.llm_api_key);

                using var resp = await Http.SendAsync(req).ConfigureAwait(false);
                if (!resp.IsSuccessStatusCode)
                    return null;

                using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync().ConfigureAwait(false));
                var root = doc.RootElement;
                if (root.TryGetProperty("choices", out var choices) && choices.GetArrayLength() > 0)
                {
                    var content = choices[0].GetProperty("message").GetProperty("content").GetString();
                    if (!string.IsNullOrWhiteSpace(content))
                        return content.Trim();
                }
                return null;
            }
            catch
            {
                return null;
            }
        }
    }
}
