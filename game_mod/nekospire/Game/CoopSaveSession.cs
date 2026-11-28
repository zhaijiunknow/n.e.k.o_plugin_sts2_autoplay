using CombatSolver.Engine.Common;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Multiplayer;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Multiplayer;
using MegaCrit.Sts2.Core.Nodes.Screens.CharacterSelect;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Platform;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.Saves.Managers;
using NekoComm.Server;

namespace NekoComm.Game;

internal static class CoopSaveSession
{
    internal static bool TransitionPending { get; set; }

    // Read via the game's migration/backup-aware loader. Never write, delete or change player IDs.
    internal static SerializableRun? ReadLocalSave()
    {
        if (!SaveManager.Instance.HasMultiplayerRunSave) return null;
        var manager = GameRef.Get<RunSaveManager>(SaveManager.Instance, "_runSaveManager");
        var result = manager.LoadMultiplayerRunSave();
        if (!result.Success || result.SaveData == null)
            throw new ApiException(409, "coop_save_unreadable", "多人存档无法读取；请保留原文件和 backup，不能用新建房间替代续局。");
        var run = result.SaveData;
        return CoopSaveIdentity.TryResolve(run.PlatformType == PlatformType.None,
            run.Players.Select(p => p.NetId).ToArray(), out _) ? run : null;
    }

    internal static void RequireHost()
    {
        if (NekoAutoplayDriver.IsCatgirlProcess())
            throw new ApiException(409, "coop_host_required", "请从主机实例打开或继续联机房间。");
    }

    internal static async Task ResumeAsync(NMultiplayerSubmenu submenu, SerializableRun saved)
    {
        RequireHost();
        if (!CoopSaveIdentity.TryResolve(saved.PlatformType == PlatformType.None,
            saved.Players.Select(p => p.NetId).ToArray(), out var clientId)
            || saved.IsCustomGame || saved.GameMode == GameMode.Daily)
            throw new ApiException(409, "coop_save_unsupported", "仅支持本机双人普通联机存档；不会将其他存档转换或覆盖。");
        // Canonicalization validates game/catalog compatibility in memory, with the ENet host's actual ID.
        var run = RunManager.CanonicalizeSave(saved, CoopSaveIdentity.HostId);
        var stack = GameStateService.GetMainMenuSubmenuStack(submenu)
            ?? throw new ApiException(503, "state_unavailable", "多人菜单尚未准备好。");
        var peer = CoopClientPeer.Existing(NekoConfig.Current.coop_client_port);
        if (peer != null) await peer.PrepareAsync();
        var net = new NetHostGameService(PeerVersionInfo.LocalDefault());
        var error = net.StartENetHost(33771, 4);
        if (error.HasValue)
            throw new ApiException(409, "coop_host_failed", $"本机联机端口不可用：{error}");
        try
        {
            var screen = stack.GetSubmenuType<NMultiplayerLoadGameScreen>();
            screen.InitializeAsHost(net, run);
            stack.Push(screen);
        }
        catch
        {
            net.Disconnect(NetError.InternalError);
            throw;
        }
        NekoConfig.Current.coop_enabled = true;
        NekoConfig.Current.Save();
        if (peer != null) await peer.RejoinAsync(clientId);
        else NekoConfigWindow.LaunchCatgirlProcess(clientId);
    }
}

// Preserve the native multiplayer Continue button for local ENet saves, even when Steam is initialized.
// Steam saves and unsupported party sizes use the original game flow, never a transport conversion.
[HarmonyPatch(typeof(NMultiplayerSubmenu), "StartLoad")]
internal static class CoopNativeContinuePatch
{
    internal static bool Prefix(NMultiplayerSubmenu __instance)
    {
        if (NekoAutoplayDriver.IsCatgirlProcess()) return true;
        try
        {
            if (CoopSaveSession.TransitionPending)
                throw new ApiException(409, "coop_transition_pending", "联机房间正在打开，请勿重复提交。");
            var saved = CoopSaveSession.ReadLocalSave();
            if (saved == null) return true;
            CoopSaveSession.TransitionPending = true;
            _ = ResumeNativeAsync(__instance, saved);
        }
        catch (Exception ex) { ShowError(__instance, ex); }

        return false;
    }
    private static async Task ResumeNativeAsync(NMultiplayerSubmenu submenu, SerializableRun saved)
    {
        try { await CoopSaveSession.ResumeAsync(submenu, saved); }
        catch (Exception ex) { ShowError(submenu, ex); }
        finally { CoopSaveSession.TransitionPending = false; }
    }

    private static void ShowError(NMultiplayerSubmenu submenu, Exception ex)
    {
        GD.PrintErr("[NekoSpire] co-op resume failed (save untouched): " + ex.Message);
        var dialog = new AcceptDialog { Title = "NekoSpire 联机续局", DialogText = ex.Message };
        submenu.AddChild(dialog);
        dialog.Confirmed += dialog.QueueFree;
        dialog.Canceled += dialog.QueueFree;
        dialog.PopupCentered(new Vector2I(640, 240));
    }

}
