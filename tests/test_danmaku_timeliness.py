"""弹幕双线：即时短句（零 LLM 延迟）+ 高风险长讲解 + 迟到丢弃。

背景（玩家原话："我都到下一个场景了才刷出上一个场景弹幕"）：点评是等 LLM 生成完才推的，
而主系统用**推理模型**，实测每条的生成间隔是

    combat 平均 5.0s（最大 39s）／reward 7.9s／map 7.1s／selection 6.2s／event 9.6s

画面早就换了，所以"及时"只能靠不调 LLM 的即时短句；长讲解是"讲得更好"，不再是"讲得及时"。
另外生成完还要检查画面有没有换 —— 换了就丢弃，否则推出去正是"上一场景的弹幕"。
"""

from __future__ import annotations

import asyncio

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


def _service() -> STS2AutoplayService:
    return STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: None)


def _combat_payload(*, hp: int = 60, max_hp: int = 80) -> dict:
    return {
        "screen": "COMBAT",
        "summary_kind": "combat",
        "trigger": "combat_turn",
        "message": "建议优先防御。",
        "player": {"current_hp": hp, "max_hp": max_hp},
        "enemies": [{"name": "树叶史莱姆（中）", "intent": "CLUMP_SHOT"}],
        "companion_evaluation": {"should_comment": True, "primary_message": "小心喵"},
    }


def _combat_snapshot(*, turn: int = 2, floor: int = 5, incoming: int = 9, hp: int = 60) -> dict:
    return {
        "screen": "COMBAT",
        "floor": floor,
        "turn": turn,
        "raw_state": {
            "combat": {
                "enemies": [
                    {
                        "index": 0,
                        "name": "树叶史莱姆（中）",
                        "intents": [{"intent_type": "Attack", "damage": incoming, "hits": 1, "total_damage": incoming}],
                    }
                ]
            }
        },
    }


# ---- 即时短句 ---------------------------------------------------------------------------


@pytest.mark.unit
def test_instant_line_carries_situation_and_is_pushed_immediately(monkeypatch) -> None:
    """即时短句必须**不经过 LLM** 就能出手：敌人意图 + 我方血量。"""
    service = _service()
    pushed: list[str] = []
    monkeypatch.setattr(service, "_schedule_catgirl_push", lambda text, **kw: pushed.append(text))

    payload = _combat_payload()
    snapshot = _combat_snapshot()
    service._emit_instant_catgirl_line(snapshot, payload["companion_evaluation"], payload)

    assert len(pushed) == 1
    line = pushed[0]
    # 意图要念**人话**（结构化 intents[].intent_type + 伤害），不是 mod 的招式 id —— 线上实测
    # 念过 `敌:树叶史莱姆（中）/CLUMP_SHOT`，那是穿帮。见 tests/test_companion_text_leaks.py。
    assert "树叶史莱姆" in line and "攻击 9" in line
    assert "CLUMP_SHOT" not in line
    assert "60/80" in line


@pytest.mark.unit
def test_instant_line_is_sent_once_per_scene_turn(monkeypatch) -> None:
    """同一场景/回合只发一次，避免每 tick 刷屏。"""
    service = _service()
    pushed: list[str] = []
    monkeypatch.setattr(service, "_schedule_catgirl_push", lambda text, **kw: pushed.append(text))
    payload = _combat_payload()
    snapshot = _combat_snapshot()

    service._emit_instant_catgirl_line(snapshot, {}, payload)
    service._emit_instant_catgirl_line(snapshot, {}, payload)

    assert len(pushed) == 1


@pytest.mark.unit
def test_instant_line_fires_again_when_the_turn_changes(monkeypatch) -> None:
    service = _service()
    pushed: list[str] = []
    monkeypatch.setattr(service, "_schedule_catgirl_push", lambda text, **kw: pushed.append(text))
    payload = _combat_payload()

    service._emit_instant_catgirl_line(_combat_snapshot(turn=2), {}, payload)
    service._emit_instant_catgirl_line(_combat_snapshot(turn=3), {}, payload)

    assert len(pushed) == 2


@pytest.mark.unit
def test_instant_line_uses_heuristic_text_off_combat(monkeypatch) -> None:
    """非战斗界面直接用启发式文本（原本就有，短而即时）。"""
    service = _service()
    pushed: list[str] = []
    monkeypatch.setattr(service, "_schedule_catgirl_push", lambda text, **kw: pushed.append(text))

    service._emit_instant_catgirl_line(
        {"screen": "MAP", "floor": 5, "turn": None},
        {"primary_message": "往右边走喵"},
        {"screen": "MAP", "summary_kind": "map", "message": "三条路"},
    )

    assert pushed == ["往右边走喵"]


# ---- 高风险判据 -------------------------------------------------------------------------


@pytest.mark.unit
def test_low_hp_combat_counts_as_high_risk() -> None:
    service = _service()
    payload = _combat_payload(hp=20, max_hp=80)  # 25% < 50%

    assert service._is_high_risk_combat(_combat_snapshot(hp=20), payload) is True


@pytest.mark.unit
def test_healthy_combat_with_small_hit_is_not_high_risk() -> None:
    service = _service()
    payload = _combat_payload(hp=70, max_hp=80)

    assert service._is_high_risk_combat(_combat_snapshot(incoming=5, hp=70), payload) is False


@pytest.mark.unit
def test_big_incoming_hit_counts_as_high_risk_even_at_full_hp() -> None:
    """满血但这一下要挨掉半条命 -> 也算高风险（要看意图总伤害，不是只看血比）。"""
    service = _service()
    payload = _combat_payload(hp=80, max_hp=80)

    assert service._is_high_risk_combat(_combat_snapshot(incoming=45, hp=80), payload) is True


@pytest.mark.unit
def test_long_commentary_is_gated_in_combat_but_kept_off_combat() -> None:
    """战斗里平稳就只发短句；非战斗界面（地图/商店/奖励）照旧让本体讲解。"""
    service = _service()

    assert service._should_emit_long_commentary(_combat_snapshot(incoming=3, hp=75), _combat_payload(hp=75, max_hp=80)) is False
    assert service._should_emit_long_commentary({"turn": None}, {"summary_kind": "map", "screen": "MAP"}) is True
    assert service._should_emit_long_commentary({"turn": None}, {"summary_kind": "shop", "screen": "SHOP"}) is True


# ---- 迟到丢弃 ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stale_commentary_is_dropped_instead_of_shown_in_the_next_scene() -> None:
    """**"到下一个场景才刷出上一个场景弹幕"的回归测试。**

    生成完发现画面已经换了场景/回合 -> 直接丢弃，别推出去。
    """
    service = _service()
    pushed: list[str] = []

    class Gen:
        available = True

        async def generate(self, **kw):
            return "该打左边那只喵！"

    async def fake_push(text):
        pushed.append(text)

    async def fake_combat_prompt(snapshot, payload):
        return "本轮: 打击"

    service._catgirl_llm = Gen()
    service._danmaku_enabled = True
    service._push_catgirl = fake_push
    service._build_combat_prompt = fake_combat_prompt

    # 下发时是 turn 2 的场景，生成期间画面已经走到了 turn 3。
    service._state.last_instant_line_key = "COMBAT|5|3"
    asyncio.run(
        service._catgirl_llm_async(
            {"situation_summary": {"text": "局面"}},
            {"summary_kind": "combat", "message": "局面"},
            {"primary_message": "小心喵"},
            "COMBAT|5|2",
        )
    )

    assert pushed == [], "迟到的讲解不能推出（否则就是上一场景的弹幕）"


@pytest.mark.unit
def test_fresh_commentary_is_pushed() -> None:
    service = _service()
    pushed: list[str] = []

    class Gen:
        available = True

        async def generate(self, **kw):
            return "该打左边那只喵！"

    async def fake_push(text):
        pushed.append(text)

    async def fake_combat_prompt(snapshot, payload):
        return "本轮: 打击"

    service._catgirl_llm = Gen()
    service._danmaku_enabled = True
    service._push_catgirl = fake_push
    service._build_combat_prompt = fake_combat_prompt
    service._state.last_instant_line_key = "COMBAT|5|2"

    asyncio.run(
        service._catgirl_llm_async(
            {"situation_summary": {"text": "局面"}},
            {"summary_kind": "combat", "message": "局面"},
            {"primary_message": "小心喵"},
            "COMBAT|5|2",
        )
    )

    assert pushed == ["该打左边那只喵！"]


# ---- 给本体的局势速报 -------------------------------------------------------------------


@pytest.mark.unit
def test_host_digest_is_invisible_ephemeral_and_proactive() -> None:
    """**"不可见 + 不进记忆 + 本体主动讲"的回归测试。**

    玩家口径：「需要改成不进记忆、不可见、本体主动讲，不然没有直播的互动味」。对应三个开关：

    - `visibility=[]` → 用户看不到这些 parts 本身（SDK 文档：空列表且 AI 也 respond 时，
      用户**只看到 AI 的回复**）；
    - `ai_behavior="respond"` → 喂上下文**并触发一次 AI 回合**，所以是本体主动开口
      （原来的 `"read"` 不会开口，那正是"没有互动味"的根因）；
    - `metadata["prompt_ephemeral"] = True` → 不进历史/记忆（宿主认的临时提示开关）。
    """
    calls: list[dict] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: calls.append(k))

    service._push_host_situation_digest(_combat_snapshot(), _combat_payload())

    assert len(calls) == 1
    call = calls[0]
    assert call["visibility"] == [], "速报本身不能出现在聊天里"
    assert call["ai_behavior"] == "respond", "要触发本体主动开口"
    assert call["metadata"]["prompt_ephemeral"] is True, "不能进历史/记忆"


@pytest.mark.unit
def test_host_digest_needs_a_scene_change_and_is_throttled() -> None:
    """同一局面只讲一次，且两次之间要有时间间隔（每次都会让本体开一次口）。"""
    calls: list[dict] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: calls.append(k))
    payload = _combat_payload()

    service._push_host_situation_digest(_combat_snapshot(turn=2), payload)
    service._push_host_situation_digest(_combat_snapshot(turn=2), payload)  # 同局面 -> 挡
    assert len(calls) == 1

    service._push_host_situation_digest(_combat_snapshot(turn=3), payload)  # 换了回合但仍在节流窗内 -> 仍挡
    assert len(calls) == 1

    service._state.last_host_digest_at = 0.0  # 越过节流窗
    service._push_host_situation_digest(_combat_snapshot(turn=4), payload)
    assert len(calls) == 1  # 只变回合号，不应重报同一组事实。
    service._push_host_situation_digest(_combat_snapshot(turn=4), _combat_payload(hp=45))
    assert len(calls) == 2


@pytest.mark.unit
def test_host_digest_carries_enough_for_the_host_to_talk_about() -> None:
    """速报要给足内容（敌人 + 意图 + 我方血量），否则本体无话可讲。

    意图同样要念人话：结构化 `intents[]` 在快照里，所以这里应该是「攻击 9」而不是招式 id。
    """
    service = _service()
    text = service._situation_digest_text(_combat_snapshot(), _combat_payload())

    assert "树叶史莱姆（中）" in text and "攻击 9" in text
    assert "CLUMP_SHOT" not in text
    assert "60/80" in text
    assert len(text) <= 160
