using System.Threading;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Modding;
using NekoComm.Game;
using NekoComm.Server;

namespace NekoComm;

[ModInitializer(nameof(Initialize))]
public static class ModEntry
{
    private const string LogPrefix = "[NekoComm]";

    private static int _shutdownHooksRegistered;

    public static void Initialize()
    {
        Log.Info($"{LogPrefix} Initializing");
        RegisterShutdownHooks();
        GameThread.Initialize();
        GameEventService.Instance.Start();
        HttpServer.Instance.Start();
        CombatSolver.CombatSolverRuntime.Install();
        Log.Info($"{LogPrefix} Ready");
    }

    private static void RegisterShutdownHooks()
    {
        if (Interlocked.Exchange(ref _shutdownHooksRegistered, 1) != 0)
        {
            return;
        }

        AppDomain.CurrentDomain.ProcessExit += (_, _) => Shutdown();
        AppDomain.CurrentDomain.DomainUnload += (_, _) => Shutdown();
    }

    private static void Shutdown()
    {
        try
        {
            GameEventService.Instance.Stop();
            HttpServer.Instance.Stop();
        }
        catch (Exception ex)
        {
            Log.Error($"{LogPrefix} Failed during shutdown: {ex}");
        }
    }
}
