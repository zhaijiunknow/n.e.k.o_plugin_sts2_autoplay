from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


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
        self.pushed: list[tuple[str, str]] = []

    def push_text(self, text: str, *, style: str = "narration", placement: str | None = None, delay_seconds: float = 0.0) -> bool:
        self.pushed.append((text, style))
        return True


def _snap(
    *,
    screen: str = "map",
    in_combat: bool = False,
    enemies: tuple = (),
    hp: int = 60,
    max_hp: int = 75,
    deck: tuple = (),
    run_id: str = "run-1",
) -> dict:
    combat: dict = {}
    if in_combat or screen == "combat":
        combat = {
            "player": {"current_hp": hp, "max_hp": max_hp, "block": 0},
            "hand": [],
            "enemies": [{"id": e, "name": e, "enemy_id": e} for e in enemies],
            "turn": 0,
        }
    raw = {
        "run_id": run_id,
        "screen": screen,
        "in_combat": bool(in_combat),
        "run": {"floor": 1, "act": 1, "gold": 100, "current_hp": hp, "max_hp": max_hp},
        "deck": {"cards": [{"id": c, "name": c} for c in deck]},
        "relics": [],
        "potions": [],
        "combat": combat,
        "reward": {"cards": []},
        "selection": {"cards": []},
        "shop": {},
        "event": {"name": ""},
    }
    return {"raw_state": raw, "screen": screen, "in_combat": bool(in_combat), "floor": 1, "act": 1, "character": "DEFECT"}


def _set_state(service: STS2AutoplayService, snap: dict) -> None:
    service._state.raw_state = snap["raw_state"]
    service._state.snapshot = snap


@pytest.mark.unit
def test_event_stream_no_push_on_baseline() -> None:
    bridge = FakeDanmuBridge()
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None, danmu_bridge=bridge)
    _set_state(service, _snap(screen="map"))
    service._emit_danmu_events()
    assert bridge.pushed == []


@pytest.mark.unit
def test_event_stream_pushes_strong_monster_on_combat_start() -> None:
    bridge = FakeDanmuBridge()
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None, danmu_bridge=bridge)
    _set_state(service, _snap(screen="map"))
    service._emit_danmu_events()
    # 进入战斗且敌含 B 类强怪 → StrongMonster
    _set_state(service, _snap(screen="combat", in_combat=True, enemies=("BYRDONIS",)))
    service._emit_danmu_events()
    assert len(bridge.pushed) >= 1
    text, style = bridge.pushed[0]
    assert text
    assert style in ("catgirl", "narration")


@pytest.mark.unit
def test_event_stream_pushes_naked_hit_on_damage() -> None:
    bridge = FakeDanmuBridge()
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None, danmu_bridge=bridge)
    _set_state(service, _snap(screen="combat", in_combat=True, enemies=("LOUSE",), hp=60))
    service._emit_danmu_events()
    before = len(bridge.pushed)
    # 战斗中裸奔掉血（block=0, ≥5 点）→ NakedHit
    _set_state(service, _snap(screen="combat", in_combat=True, enemies=("LOUSE",), hp=40))
    service._emit_danmu_events()
    assert len(bridge.pushed) > before


@pytest.mark.unit
def test_event_stream_skips_when_bridge_disabled() -> None:
    bridge = FakeDanmuBridge()
    bridge.enabled = False
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None, danmu_bridge=bridge)
    _set_state(service, _snap(screen="map"))
    service._emit_danmu_events()
    _set_state(service, _snap(screen="combat", in_combat=True, enemies=("BYRDONIS",)))
    service._emit_danmu_events()
    assert bridge.pushed == []


@pytest.mark.unit
def test_event_stream_no_duplicate_push_on_idle_frames() -> None:
    bridge = FakeDanmuBridge()
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None, danmu_bridge=bridge)
    combat = _snap(screen="combat", in_combat=True, enemies=("LOUSE",), hp=60)
    _set_state(service, _snap(screen="map"))
    service._emit_danmu_events()
    _set_state(service, combat)
    service._emit_danmu_events()
    first = len(bridge.pushed)
    # 相同状态多帧 → 无新事件、无新弹幕
    for _ in range(2):
        _set_state(service, combat)
        service._emit_danmu_events()
    assert len(bridge.pushed) == first
