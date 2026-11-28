"""Cards-result overlays must offer a safe close, not a card-selection action.

Source contracts cover the Godot-specific button lookup; planner tests exercise
its consumer without a running game. Live visibility/clicks still need in-game QA.
"""
from pathlib import Path

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner

ROOT = Path(__file__).resolve().parents[1]


def test_simple_cards_view_uses_confirm_with_the_existing_safety_guards():
    source = (ROOT / "game_mod/nekospire/Game/GameStateService.cs").read_text(encoding="utf-8")
    method = source.split("public static NButton? GetCardsViewBackButton(", 1)[1].split(
        "\n    }", 1
    )[0]
    assert "currentScreen is not NCardsViewScreen" in method
    assert 'cardsViewScreen is NSimpleCardsViewScreen ? "ConfirmButton" : "BackButton"' in method
    assert "GetNodeOrNull<NButton>(buttonPath)" in method
    for guard in ("backButton != null", "GodotObject.IsInstanceValid(backButton)",
                  "backButton.IsVisibleInTree()", "backButton.IsEnabled"):
        assert guard in method
    assert "MissingCardsViewButtons.Add(diagnosticKey)" in method
    assert "Log.Warn" in method and "GetType().FullName" in method
    assert "ForceClick" not in method  # reading the state never dismisses the overlay


def _plan(actions):
    return STS2HeuristicPlanner().plan({
        "classification": {"state_name": "cards_view"},
        "summary_context": {"payload": {}},
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "screen": "CARDS_VIEW",
            "available_actions": [{"type": name, "raw": {"name": name}} for name in actions],
        },
    })


def test_cards_results_close_without_selecting_a_card():
    plan = _plan(["save_and_quit", "discard_potion", "close_cards_view"])
    assert plan is not None
    assert plan.action_type == "close_cards_view"
    assert plan.kwargs == {}


def test_disabled_results_button_does_not_trigger_an_unrelated_action():
    assert _plan(["save_and_quit", "discard_potion"]) is None
