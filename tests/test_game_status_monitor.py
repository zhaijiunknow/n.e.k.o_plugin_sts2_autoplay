"""「当前游戏信息状态」监控：触发计数 + game_status 节流推送测试。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from plugin.plugins.sts2_autoplay.danmu_spire import _load_rules
from plugin.plugins.sts2_autoplay.service import (
    STATUS_PUSH_HEARTBEAT,
    STATUS_PUSH_MIN_INTERVAL,
    STS2AutoplayService,
)


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def exception(self, *a, **k):
        return None


class FakeDanmuBridge:
    enabled = True

    def __init__(self) -> None:
        self.status_pushed: list[dict] = []

    def push_status(self, *, data: dict) -> bool:
        self.status_pushed.append(data)
        return True

    def push_text(self, *a, **k) -> bool:
        return True


def _service(bridge: FakeDanmuBridge | None = None) -> tuple[STS2AutoplayService, FakeDanmuBridge]:
    b = bridge or FakeDanmuBridge()
    svc = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **kwargs: None, danmu_bridge=b)
    svc._state.raw_state = {"run": {"gold": 120, "current_hp": 52, "max_hp": 75}, "screen": "combat"}
    svc._state.snapshot = {
        "screen": "combat",
        "floor": 3,
        "act": 1,
        "in_combat": True,
        "classification": {"screen_class": "combat"},
        "strategy_context": {"strategy_name": "defect"},
        "situation_summary": {
            "kind": "combat",
            "payload": {"player": {"current_hp": 52, "max_hp": 75, "block": 0}},
        },
    }
    return svc, b


def _snap(*, screen: str = "reward", deck: tuple = (), run_id: str = "run-1") -> dict:
    """构造 tracker 可消费的快照（raw_state + snapshot）。"""
    deck_cards = [{"id": c, "name": c, "card_id": c, "upgrade_level": 0} for c in deck]
    raw_state = {
        "run_id": run_id,
        "screen": screen,
        "in_combat": False,
        "run": {"floor": 1, "act": 1, "gold": 100, "current_hp": 60, "max_hp": 75},
        "deck": {"cards": deck_cards},
        "relics": [],
        "potions": [],
        "combat": {},
        "reward": {"cards": []},
        "selection": {"cards": []},
        "shop": {},
        "event": {"name": ""},
    }
    return {"raw_state": raw_state, "screen": screen, "in_combat": False, "floor": 1, "act": 1, "character": "DEFECT"}


# ---- 触发计数 ----

@pytest.mark.unit
def test_update_trigger_counts_accumulates_and_resets_on_run_started() -> None:
    svc, _ = _service()
    svc._update_trigger_counts([], [SimpleNamespace(trigger="AcquiredCard")])
    assert svc._trigger_counts == {"AcquiredCard": 1}
    # 同 run 继续累加
    svc._update_trigger_counts([], [SimpleNamespace(trigger="AcquiredCard")])
    assert svc._trigger_counts == {"AcquiredCard": 2}
    # run_started → 清零后累加新触发
    svc._update_trigger_counts([SimpleNamespace(type="run_started")], [SimpleNamespace(trigger="BigTurn")])
    assert svc._trigger_counts == {"BigTurn": 1}


@pytest.mark.unit
def test_trigger_counts_reset_on_run_id_change() -> None:
    svc, _ = _service()
    svc._state.raw_state = {"run_id": "run-1"}
    svc._update_trigger_counts([], [SimpleNamespace(trigger="AcquiredCard")])
    assert svc._trigger_counts == {"AcquiredCard": 1}
    # run_id 直接切换（tracker 不发 run_started）→ 清零
    svc._state.raw_state = {"run_id": "run-2"}
    svc._update_trigger_counts([], [SimpleNamespace(trigger="BigTurn")])
    assert svc._trigger_counts == {"BigTurn": 1}


@pytest.mark.unit
def test_emit_danmu_events_accumulates_acquired_card_count() -> None:
    svc, _ = _service()
    # 进入奖励空牌库（run_started）→ 拿一张普通攻击牌（card_obtained → AcquiredCard）
    snap1 = _snap(screen="reward", deck=())
    svc._state.raw_state = snap1["raw_state"]
    svc._state.snapshot = snap1
    svc._emit_danmu_events()
    snap2 = _snap(screen="reward", deck=("STRIKE_IRONCLAD",))
    svc._state.raw_state = snap2["raw_state"]
    svc._state.snapshot = snap2
    svc._emit_danmu_events()
    assert svc._trigger_counts.get("AcquiredCard", 0) >= 1


# ---- game_status 节流推送 ----

@pytest.mark.unit
def test_game_status_push_sent_with_full_payload() -> None:
    svc, bridge = _service()
    svc._maybe_push_game_status()
    assert len(bridge.status_pushed) == 1
    data = bridge.status_pushed[0]
    assert data["game"]["screen"] == "combat"
    assert data["game"]["floor"] == 3
    assert data["game"]["hp"] == 52
    assert data["game"]["gold"] == 120  # 来自 raw_state.run.gold（situation_summary.payload.player 无 gold）
    assert len(data["trigger_names"]) == len(_load_rules().keys())
    assert data["triggers"] == {}
    # 弹幕条件判定参数：features() 全量 + 内部计数
    assert "params" in data
    assert data["params"].get("gold") == 120
    assert "combat_turn_plays" in data["params"]
    assert "shop_enter_gold" in data["params"]


@pytest.mark.unit
def test_game_status_not_pushed_when_unchanged_and_within_heartbeat() -> None:
    svc, bridge = _service()
    svc._maybe_push_game_status()
    assert len(bridge.status_pushed) == 1
    # 立即再调：签名未变、心跳未到 → 不推
    svc._maybe_push_game_status()
    assert len(bridge.status_pushed) == 1


@pytest.mark.unit
def test_game_status_push_on_visible_change() -> None:
    svc, bridge = _service()
    svc._maybe_push_game_status()
    assert len(bridge.status_pushed) == 1
    # 触发计数变化（状态变化）→ 但距上次 < 最小间隔则不推；跳过最小间隔后推
    svc._trigger_counts["BigTurn"] = 1
    svc._last_status_push_at = time.time() - STATUS_PUSH_MIN_INTERVAL - 1
    svc._maybe_push_game_status()
    assert len(bridge.status_pushed) == 2
    assert bridge.status_pushed[-1]["triggers"] == {"BigTurn": 1}


@pytest.mark.unit
def test_game_status_force_bypasses_min_interval() -> None:
    """场景切换 force 推送：即使距上次 < 最小间隔也立即推（消除切换延迟）。"""
    svc, bridge = _service()
    svc._maybe_push_game_status()
    assert len(bridge.status_pushed) == 1
    # 刚推过，普通调用被节流挡
    svc._maybe_push_game_status()
    assert len(bridge.status_pushed) == 1
    # force（场景切换）→ 立即推
    svc._maybe_push_game_status(force=True)
    assert len(bridge.status_pushed) == 2


@pytest.mark.unit
def test_game_status_heartbeat_pushes_when_idle() -> None:
    svc, bridge = _service()
    svc._maybe_push_game_status()
    assert len(bridge.status_pushed) == 1
    # 心跳到期（直接改 _last_status_push_at，不 patch time）→ 即使状态未变也推
    svc._last_status_push_at = time.time() - STATUS_PUSH_HEARTBEAT - 1
    svc._maybe_push_game_status()
    assert len(bridge.status_pushed) == 2


@pytest.mark.unit
def test_game_status_skips_when_bridge_disabled() -> None:
    bridge = FakeDanmuBridge()
    bridge.enabled = False
    svc, _ = _service(bridge)
    svc._maybe_push_game_status()
    assert bridge.status_pushed == []
