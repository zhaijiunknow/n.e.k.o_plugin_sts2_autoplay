"""Small integration guards; executable policies are tested by game_mod/SafetyTests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "game_mod" / "nekospire"


def test_security_boundary_precedes_every_route():
    source = (ROOT / "Server/Router.cs").read_text(encoding="utf-8")
    assert source.index("LocalRequestPolicy.Validate(") < source.index('== "/health"')
    assert source.index("LocalRequestPolicy.Validate(") < source.index("ExternalControl.Touch()")
    assert source.index("cfgWrite.Validate()") < source.index("cfg.llm_enabled =")
    assert "cfg.llm_base_url =" not in source
    assert "cfg.llm_api_key =" not in source


def test_coop_map_policy_reaches_driver_and_cannot_be_bypassed_by_llm():
    router = (ROOT / "Server/Router.cs").read_text(encoding="utf-8")
    driver = (ROOT / "Game/NekoAutoplayDriver.cs").read_text(encoding="utf-8")
    assert "cfg.coop_map_policy = cfgWrite.coop_map_policy" in router
    method = driver.split("private ActionRequest? DecideMap(", 1)[1].split("private static bool IsMultiplayerRun", 1)[0]
    assert 'if (policy == "wait")' in method
    assert 'if (policy == "follow")' in method
    assert "ExternalControl.IsActive" in method
    bypass = driver.split("private static bool ShouldBypassLlm(", 1)[1].split("private static bool LlmReady", 1)[0]
    assert 'if (s.screen == "MAP"' in bypass
    assert 'ExternalControl.IsActive) != "auto"' in bypass
