// Stand-ins for the parts of upstream's OfflineSearchHarness / standalone-mod shell that the linked
// bootstrap sources mention but this smoke test does not use.
namespace OfflineSearchHarness
{
    internal sealed record HarnessOptions;

    internal static class ModRuntime
    {
        public static IDisposable? Session { get; set; }

        public static string Initialize(HarnessOptions options) => "not_used";
    }
}

namespace CombatSolver
{
    // GameBootstrap.ApplyGodotBypasses assigns this injection point of the standalone mod's controller.
    internal static class SolverController
    {
        public static Func<string>? DisplayServerNameProvider { get; set; }
    }
}
