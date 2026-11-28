"""Contracts across handwritten game seams (runtime identity behavior is in SafetyTests)."""
from pathlib import Path

GAME = Path(__file__).resolve().parents[1] / "game_mod" / "nekospire" / "Game"


def source(name):
    return (GAME / name).read_text(encoding="utf-8")


def test_old_save_identity_is_used_by_both_native_and_http_resume():
    session = source("CoopSaveSession.cs")
    assert 'HarmonyPatch(typeof(NMultiplayerSubmenu), "StartLoad")' in session
    assert "RunManager.CanonicalizeSave(saved, CoopSaveIdentity.HostId)" in session
    assert "NekoConfigWindow.LaunchCatgirlProcess(clientId)" in session
    assert "await CoopSaveSession.ResumeAsync(submenu, saved)" in source("GameActionService.Coop.cs")
    assert "StartSteamHost" not in session
    for forbidden in ("DeleteCurrent", "WriteAll", "SaveRun(", ".NetId ="):
        assert forbidden not in session


def test_continue_cannot_fall_back_to_start_new_session():
    resume = source("GameActionService.Coop.cs")
    assert "coop_save_not_found" in resume
    assert "ExecuteStartCoopSession" not in resume
    assert "TransitionPending = false" in resume


def test_loaded_lobby_has_client_protocol_and_ready_ui_support():
    actions = source("GameActionService.Menus.cs")
    assert "Environment.ProcessId" not in actions
    assert "RunSessionState.InLoadedLobby" in actions
    assert "load.InitializeAsClient" in actions
    assert "CoopSaveIdentity.CurrentClientId" in actions
    assert 'startInfo.Environment["STS2_COOP_NET_ID"]' in source("NekoConfigWindow.cs")
    assert 'NCharacterSelectScreen or NMultiplayerLoadGameScreen => "CHARACTER_SELECT"' in source("GameStateService.UiLookup.cs")
    assert "currentScreen is NMultiplayerLoadGameScreen" in source("GameStateService.Rooms.cs")
    assert "currentScreen is NMultiplayerLoadGameScreen && GameStateService.CanEmbark" in actions


def test_explicit_room_creation_is_not_blocked_by_existing_saves():
    actions = source("GameActionService.Menus.cs")
    assert "GuardAgainstSavedRun" not in actions
    assert '"active_coop_run_present"' not in actions
    assert '"active_run_present"' not in actions
    for method in ("ExecuteStartCoopSessionCoreAsync", "ExecuteStartMultiplayerHostAsync"):
        body = actions.split(f"private static async Task<ActionResponsePayload> {method}()", 1)[1].split("private static", 1)[0]
        for forbidden in ("HasRunSave", "HasMultiplayerRunSave", "coop_yield_to_saved_run",
                          "DeleteCurrentRun", "DeleteCurrentMultiplayerRun", "ReadLocalSave"):
            assert forbidden not in body
    assert "RunCoopTransitionAsync(ExecuteStartCoopSessionCoreAsync)" in actions
    assert "CoopSaveSession.RequireHost()" in actions


def test_coop_entry_timeout_covers_child_startup():
    import ast

    tree = ast.parse((GAME.parents[2] / "__init__.py").read_text(encoding="utf-8"))
    entry = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.AsyncFunctionDef) and node.name == "sts2_open_coop_room")
    decorator = next(node for node in entry.decorator_list
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                     and node.func.id == "plugin_entry")
    timeout = next(kw.value.value for kw in decorator.keywords if kw.arg == "timeout")
    assert 60 <= timeout < 120


def test_mod_exposes_client_address_and_applies_process_takeover():
    router = (GAME.parent / "Server" / "Router.cs").read_text(encoding="utf-8")
    read = router.split('request.Url?.AbsolutePath == "/config"', 1)[1].split('statusCode = 200;', 1)[0]
    assert "coop_client_port = cfg.coop_client_port" in read
    assert "coop_control_mode = cfg.coop_control_mode" in read
    assert "llm_api_key" not in read
    assert "cfg.coop_control_mode = cfgWrite.coop_control_mode ?? cfg.coop_control_mode" in router
    assert 'coop_control_mode = cfg.coop_control_mode' in router.split('status = "updated"', 1)[1]
    driver = source("NekoAutoplayDriver.cs")
    assert "ExternalControl.HoldAutoplay(NekoConfig.Current.coop_control_mode, state.screen, ExternalControl.IsActive)" in driver
    assert driver.index("ExternalControl.HoldAutoplay") < driver.index("await DecideAsync(state)")


def test_existing_client_is_prepared_before_hosting_and_never_duplicated():
    for name in ("GameActionService.Menus.cs", "CoopSaveSession.cs"):
        text = source(name)
        assert "CoopClientPeer.Existing" in text
        assert "await peer.PrepareAsync()" in text
        assert "await peer.RejoinAsync(" in text
        assert "else NekoConfigWindow.LaunchCatgirlProcess(" in text
    new_room = source("GameActionService.Menus.cs").split("private static async Task<ActionResponsePayload> ExecuteStartCoopSessionCoreAsync()", 1)[1]
    assert new_room.index("await peer.PrepareAsync()") < new_room.index("await ExecuteStartMultiplayerHostAsync()")
    resume = source("CoopSaveSession.cs")
    assert resume.index("await peer.PrepareAsync()") < resume.index("net.StartENetHost")
    assert "coop_client_running" not in resume


def test_rejoin_resets_run_latch_but_never_abandons_or_deletes_a_run():
    rejoin = source("GameActionService.Coop.cs")
    assert "CoopSaveIdentity.CanRejoin" in rejoin
    assert "PauseForCoopRejoinAsync" in rejoin
    assert "SetSessionClientId(request.command)" in rejoin
    assert "ResumeAfterCoopRejoin" in rejoin
    for forbidden in ("ExecuteAbandonRun", "DeleteCurrent", "Kill(", "Process.Start"):
        assert forbidden not in rejoin
    driver = source("NekoAutoplayDriver.cs")
    reset = driver.split("internal void ResumeAfterCoopRejoin()", 1)[1].split("public void Start()", 1)[0]
    assert "_runEnded = false" in reset
    assert "_openedMultiplayerMenu = false" in reset
    assert "generation != _coopSessionGeneration" in driver
