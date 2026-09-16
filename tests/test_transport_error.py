from __future__ import annotations

import pytest

from plugin.plugins.sts2_autoplay.transport_client import STS2TransportClient


@pytest.mark.unit
def test_describe_error_includes_action_screen_and_code() -> None:
    """mod 端多个动作共用同一句 message，靠 error.details 才能定位是哪个动作挂的。"""
    payload = {
        "ok": False,
        "error": {
            "code": "invalid_action",
            "message": "Action is not available in the current state.",
            "details": {"action": "host_multiplayer_lobby", "screen": "MAIN_MENU"},
        },
    }

    text = STS2TransportClient._describe_error(payload, 409)

    assert "Action is not available in the current state." in text
    assert "action=host_multiplayer_lobby" in text
    assert "screen=MAIN_MENU" in text
    assert "code=invalid_action" in text


@pytest.mark.unit
def test_describe_error_keeps_message_when_details_absent() -> None:
    payload = {"ok": False, "error": {"code": "internal_error", "message": "boom"}}

    assert STS2TransportClient._describe_error(payload, 500) == "boom (code=internal_error)"


@pytest.mark.unit
def test_describe_error_falls_back_to_status_code() -> None:
    assert STS2TransportClient._describe_error({"ok": False}, 502) == "STS2-Agent 请求失败: HTTP 502"
