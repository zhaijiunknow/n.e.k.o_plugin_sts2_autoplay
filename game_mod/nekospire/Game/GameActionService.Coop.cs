using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using NekoComm.Server;

namespace NekoComm.Game;

internal static partial class GameActionService
{
    // GameThread serializes callbacks but awaits may interleave: reject a second lifecycle request.
    private static async Task<ActionResponsePayload> RunCoopTransitionAsync(Func<Task<ActionResponsePayload>> operation)
    {
        if (CoopSaveSession.TransitionPending)
            throw new ApiException(409, "coop_transition_pending", "联机房间正在打开，请勿重复提交。");
        CoopSaveSession.TransitionPending = true;
        try { return await operation(); }
        finally { CoopSaveSession.TransitionPending = false; }
    }

    private static Task<ActionResponsePayload> ExecuteContinueCoopSessionAsync() =>
        RunCoopTransitionAsync(ExecuteContinueCoopSessionCoreAsync);

    private static async Task<ActionResponsePayload> ExecuteContinueCoopSessionCoreAsync()
    {
        CoopSaveSession.RequireHost();
        var saved = CoopSaveSession.ReadLocalSave()
            ?? throw new ApiException(409, "coop_save_not_found", "没有可识别的本机双人联机存档；Steam 联机请使用游戏原生入口，不会自动新开一局。");
        await CloseMainMenuSubmenusAsync();
        await ExecuteOpenMultiplayerMenuAsync();
        var submenu = ActiveScreenContext.Instance.GetCurrentScreen() as NMultiplayerSubmenu
            ?? throw new ApiException(503, "state_unavailable", "多人菜单尚未准备好。");
        await CoopSaveSession.ResumeAsync(submenu, saved);
        return new ActionResponsePayload
        {
            action = "continue_coop_session", status = "completed", stable = true,
            message = "联机读档大厅已打开，猫娘将使用存档里的身份加入；请等待双方就绪。",
            state = GameStateService.BuildStatePayload()
        };
    }
    private static void RequireReusableCatgirl()
    {
        var state = GameStateService.BuildStatePayload();
        if (!CoopSaveIdentity.CanRejoin(NekoAutoplayDriver.IsCatgirlProcess(), state.screen))
            throw new ApiException(409, "coop_client_busy",
                "仅能复用主菜单或局末界面的猫娘实例；不会中断进行中的对局。");
    }

    private static async Task<ActionResponsePayload> ExecutePrepareCoopRejoinAsync()
    {
        RequireReusableCatgirl();
        var driver = NekoAutoplayDriver.Instance;
        await driver.PauseForCoopRejoinAsync();
        try
        {
            RequireReusableCatgirl();
            if (GameStateService.BuildStatePayload().screen == "GAME_OVER")
                await ExecuteReturnToMainMenuAsync();
            await CloseMainMenuSubmenusAsync();
            if (GameStateService.BuildStatePayload().screen != "MAIN_MENU")
                throw new ApiException(409, "coop_client_busy", "猫娘尚未返回主菜单，请稍后重试。");
            return new ActionResponsePayload
            {
                action = "prepare_coop_rejoin", status = "completed", stable = true,
                message = "猫娘已等待主机新大厅。", state = GameStateService.BuildStatePayload()
            };
        }
        catch { driver.CancelCoopRejoin(); throw; }
    }

    private static async Task<ActionResponsePayload> ExecuteRejoinCoopSessionAsync(ActionRequest request)
    {
        RequireReusableCatgirl();
        var driver = NekoAutoplayDriver.Instance;
        if (!driver.CoopRejoinPending)
            throw new ApiException(409, "coop_client_not_prepared", "请先由主机准备猫娘重连。");
        CoopSaveIdentity.SetSessionClientId(request.command);
        await CloseMainMenuSubmenusAsync();
        await ExecuteOpenMultiplayerMenuAsync();
        await ExecuteJoinMultiplayerDirectAsync();
        driver.ResumeAfterCoopRejoin();
        return new ActionResponsePayload
        {
            action = "rejoin_coop_session", status = "completed", stable = true,
            message = "现有猫娘已重新加入主机大厅。", state = GameStateService.BuildStatePayload()
        };
    }

}
