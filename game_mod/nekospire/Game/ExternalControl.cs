using System.Diagnostics;
using System.Threading;

namespace NekoComm.Game;

internal static class ExternalControl
{
    public const string HeaderName = "X-Neko-External-Control";
    private static long _lastSeen;
    public static void Touch() => Interlocked.Exchange(ref _lastSeen, Stopwatch.GetTimestamp());
    public static bool IsActive
    {
        get
        {
            var seen = Interlocked.Read(ref _lastSeen);
            return seen != 0 && Stopwatch.GetElapsedTime(seen).TotalSeconds < 15;
        }
    }
    public static string MapPolicy(string policy, bool follow, bool active) =>
        active ? policy : follow ? "follow" : "auto";
}
