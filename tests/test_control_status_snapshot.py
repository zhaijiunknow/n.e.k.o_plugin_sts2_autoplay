"""Static UI snapshots: bounded writes, no host task creation, no full game/chat data."""
import asyncio
import json

from plugin.plugins.sts2_autoplay import service_status
from plugin.plugins.sts2_autoplay.tests.test_map_wait_settings import SettingsPlugin


def test_status_snapshot_is_throttled_atomic_and_minimal(tmp_path, monkeypatch):
    service = SettingsPlugin()._service
    service._control_status_path = tmp_path / "control_status.json"
    service._state.latest_sync_packet = {"private": "not-for-ui"}
    monkeypatch.setattr(service._livestream, "status", lambda: {
        "enabled": True, "profile": "balanced", "wait_scale": 1,
        "waiting": True, "remaining_seconds": 12, "decision_key": "private",
    })
    clock = [100.0]
    monkeypatch.setattr(service_status, "monotonic", lambda: clock[0])
    service._publish_control_status()
    first = service._control_status_path.read_text("utf-8")
    payload = json.loads(first)
    assert "private" not in first and "latest_sync_packet" not in first
    assert "base_url" not in payload["server"]
    assert payload["instance"] == {"active": "host"}
    assert payload["host_digest"]["enabled"] is True
    assert payload["livestream"]["waiting"] is True
    assert payload["livestream"]["remaining_seconds"] == 12
    assert "decision_key" not in payload["livestream"]
    service._state.host_digest_enabled = False
    service._publish_control_status()
    assert service._control_status_path.read_text("utf-8") == first
    clock[0] += 2
    service._publish_control_status()
    assert json.loads(service._control_status_path.read_text("utf-8"))["host_digest"]["enabled"] is False
    assert list(tmp_path.iterdir()) == [service._control_status_path]


def test_status_entry_is_read_only_and_has_a_timestamp(tmp_path):
    service = SettingsPlugin()._service
    service._control_status_path = tmp_path / "control_status.json"
    assert asyncio.run(service.get_status())["updated_at"] > 0
    assert not service._control_status_path.exists()


def test_snapshot_write_failure_does_not_break_reporting(tmp_path, monkeypatch):
    service = SettingsPlugin()._service
    service._control_status_path = tmp_path / "control_status.json"
    service._control_status_path.write_text('{"previous":true}', encoding="utf-8")
    def fail(*args):
        raise PermissionError("busy")
    monkeypatch.setattr(service_status.os, "replace", fail)
    service._publish_control_status()
    assert json.loads(service._control_status_path.read_text("utf-8")) == {"previous": True}
    assert len(list(tmp_path.iterdir())) == 1
