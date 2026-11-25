"""两个"跟本体说话"的开关：尖塔速报 / 尖塔复盘。

玩家口径：「给尖塔速报和尖塔复盘设置两个开关控制是否开启这个」。

- **尖塔速报**（`host_digest_enabled`）：是否把局势悄悄交给本体、由它主动讲一句。
- **尖塔复盘**（`run_reflection_enabled`）：局末是否把玩家的选择与推荐差得最多的几步交给本体讲。

两者都**只影响"跟本体说话"这一路**：关掉之后游戏内弹幕、自动游玩、结构化记录
（损血归因 / 路线图 / 历史页）照旧 —— 这是刻意的，别把开关做成"顺便把记录也停了"。
"""

from __future__ import annotations

import asyncio

import pytest
from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore
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


def _service() -> STS2AutoplayService:
    return STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: None)


_COMBAT_PAYLOAD = {
    "screen": "COMBAT",
    "summary_kind": "combat",
    "player": {"current_hp": 60, "max_hp": 80},
    "enemies": [{"name": "史莱姆", "intent": "GOOP"}],
}
_COMBAT_SNAPSHOT = {"screen": "COMBAT", "floor": 5, "turn": 2, "raw_state": {"combat": {"enemies": []}}, "in_combat": True}


def _terminal_snapshot() -> dict:
    return {
        "classification": {"screen_class": "terminal"},
        "screen": "GAME_OVER",
        "run_id": "run-a",
        "character": "C",
        "floor": 7,
        "act": 1,
        "raw_state": {},
    }


# ---- 默认值与开关本身 -------------------------------------------------------------------


@pytest.mark.unit
def test_both_switches_default_to_on() -> None:
    service = _service()

    assert service._state.host_digest_enabled is True
    assert service._state.run_reflection_enabled is True


@pytest.mark.unit
def test_setters_toggle_state_and_config() -> None:
    service = _service()

    off = service.set_host_digest_enabled(False)
    assert off["status"] == "ok" and off["enabled"] is False
    assert service._state.host_digest_enabled is False
    assert service._cfg["host_digest_enabled"] is False

    off2 = service.set_run_reflection_enabled(False)
    assert off2["enabled"] is False
    assert service._state.run_reflection_enabled is False
    assert service._cfg["run_reflection_enabled"] is False


# ---- 尖塔速报 ---------------------------------------------------------------------------


@pytest.mark.unit
def test_situation_digest_is_silent_when_the_switch_is_off() -> None:
    calls: list[dict] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: calls.append(k))
    service.set_host_digest_enabled(False)

    service._push_host_situation_digest(_COMBAT_SNAPSHOT, _COMBAT_PAYLOAD)

    assert calls == [], "速报关掉后不该再打扰本体"


@pytest.mark.unit
def test_character_announcement_keeps_danmaku_but_drops_the_host_push() -> None:
    """速报关掉时：游戏内弹幕照旧（那是弹幕开关管的），只是不再推给本体。"""
    calls: list[dict] = []
    pushed: list[str] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: calls.append(k))
    service.set_host_digest_enabled(False)
    service._schedule_catgirl_push = lambda text, **kw: pushed.append(text)

    service._maybe_announce_character(
        {"run_id": "run-a", "raw_state": {"run": {"character_name": "储君", "character_id": "REGENT"}}}
    )

    assert pushed == ["这局我抽到储君喵，开打！"], "弹幕不受速报开关影响"
    assert calls == [], "不该推给本体"


# ---- 尖塔复盘 ---------------------------------------------------------------------------


_DIVERGENT_STEP = {
        "source": "network", "decision": "COMBAT", "floor": 6, "act": 1, "round": 2, "room": "NIBBIT",
        "situation": "血 30/80", "hp": 30, "max_hp": 80, "chosen": "play 打击 -> 小啃兽", "recommended": "play 防御",
        "same": False, "p_chosen": 0.05, "p_recommended": 0.8, "gap": 0.75,
    }


def _run_end(service: STS2AutoplayService) -> None:
    async def drive() -> None:
        service._maybe_record_run_memory(dict(_terminal_snapshot()))
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(drive())


@pytest.mark.unit
def test_run_end_records_and_never_pauses_autoplay(tmp_path) -> None:
    """局末只写记录：没有可对照的步就没有复盘，自动玩的状态不被动（"复盘暂停"已删除）。"""
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    service._state.autoplay_state = "running"

    _run_end(service)

    assert len(service._catgirl_memory.runs) == 1, "记录必须照写（历史页/路线图靠它）"
    assert "review" not in service._catgirl_memory.runs[0], "纯自动玩的局没有可对照的步"
    assert service._state.autoplay_state == "running"
    assert service._state.pause_reason == ""


@pytest.mark.unit
def test_review_is_recorded_but_not_explained_when_the_switch_is_off(tmp_path) -> None:
    """复盘关掉时：对照本身照写（它是结构化记录），只是不请模型讲、也不推给本体。"""
    calls: list[dict] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: calls.append(k))
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    service.set_run_reflection_enabled(False)
    service._choice_review.steps = [dict(_DIVERGENT_STEP)]

    class Gen:
        available = True

        async def generate(self, **kw):
            raise AssertionError("开关关掉时不该调用模型")

    service._catgirl_llm = Gen()

    _run_end(service)

    review = service._catgirl_memory.runs[0]["review"]
    assert review["compared"] == 1 and review["agreed"] == 0
    assert review["steps"][0]["recommended"] == "play 防御"
    assert review["explanation"] == ""
    assert not [c for c in calls if (c.get("metadata") or {}).get("kind") == "run_review"]


# ---- 启动时读配置 -----------------------------------------------------------------------


@pytest.mark.unit
def test_startup_reads_the_switch_config() -> None:
    service = _service()

    asyncio.run(service.startup({"host_digest_enabled": False, "run_reflection_enabled": False}))

    assert service._state.host_digest_enabled is False
    assert service._state.run_reflection_enabled is False
