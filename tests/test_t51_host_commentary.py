"""t51：**关闭弹幕，但让猫娘在聊天里点评**（`push_message` 通道）。

## 玩家口径（本文件的存在理由）

> 关闭弹幕，但是可以用 push_message 让猫娘点评一下？

对，而且这正是实机症状的解法：2026-10-26 的日志里，自动代打期间插件按 `autoplay` 优先级
把游戏内弹幕**主动关掉**（8 次 `[sts2_danmaku_policy] 弹幕随模式自动关闭（reason=autoplay）`，
47 次 `[sts2_catgirl_llm] suppressed danmaku disabled`），于是玩家看到的是"猫娘全程不说话"。

弹幕该关（2026-10-19 指令：代打时没人盯屏幕，弹幕刷出去没收益还挤占 LLM 与日志），
但**"不说弹幕"不等于"不说话"** ⇒ 同一段点评改走宿主的 `push_message`
（`visibility=[]` + `ai_behavior="respond"` + `prompt_ephemeral=True`，见
`service._push_proactive_to_host` 的注释），猫娘就会在聊天窗口用自己的口吻说出来。

## 要钉死的四件事

1. **弹幕关着 + 开关开** ⇒ 点评走宿主，**不发 `/danmaku`**；
2. **弹幕开着** ⇒ 仍走 `/danmaku`，**不推宿主**（否则同一句说两遍）；
3. **开关关** ⇒ 回到老行为：既不弹幕也不说话；
4. **节流**：`ai_behavior="respond"` 每次都是一次完整 AI 回合（有 token 成本）⇒
   同场景只说一次 + 最小间隔（默认 45s）内不再推。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService

pytestmark = pytest.mark.unit


class _Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, *args: Any, **kwargs: Any) -> None:
        self.lines.append(str(args[0]) % tuple(args[1:]) if len(args) > 1 else str(args[0]))

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.info(*args, **kwargs)

    def error(self, *args: Any, **kwargs: Any) -> None:
        self.info(*args, **kwargs)

    def debug(self, *args: Any, **kwargs: Any) -> None: ...


class _Client:
    """只记录 `/danmaku` 推送次数。"""

    def __init__(self) -> None:
        self.danmaku: list[str] = []

    async def push_danmaku(self, text: str, **kwargs: Any) -> dict[str, Any]:
        self.danmaku.append(text)
        return {"ok": True}


def _service(cfg: dict[str, Any] | None = None) -> tuple[STS2AutoplayService, _Logger, list[dict[str, Any]]]:
    logger = _Logger()
    pushes: list[dict[str, Any]] = []

    def _notifier(**kwargs: Any) -> None:
        pushes.append(kwargs)

    service = STS2AutoplayService(logger, lambda payload: None, _notifier)
    service._cfg.update(cfg or {})
    return service, logger, pushes


def _push(service: STS2AutoplayService, text: str) -> None:
    asyncio.run(service._push_catgirl(text, fetch_avatar=False))


# ══════════════════════════════════════════════════════════════════════════════
# ① 弹幕关着 + 开关开 ⇒ 改走宿主
# ══════════════════════════════════════════════════════════════════════════════


def test_danmaku_off_routes_commentary_to_host() -> None:
    """★ 核心：弹幕关着时不再静音，而是把这段点评交给宿主让猫娘开口。"""
    service, logger, pushes = _service({"host_commentary_when_danmaku_off": True})
    service.set_danmaku_enabled(False)
    client = _Client()
    service._client = client

    _push(service, "这波敌人意图不客气，先垫格挡喵。")

    assert client.danmaku == [], "弹幕关着时绝不该再 POST /danmaku"
    assert len(pushes) == 1, f"应推一次宿主消息，实际 {pushes}"
    payload = pushes[0]
    assert payload["ai_behavior"] == "respond", "只有 respond 才会让本体主动开口"
    assert payload["visibility"] == [], "正文不给用户看，用户只看猫娘自己的回复"
    assert payload["metadata"]["prompt_ephemeral"] is True
    assert payload["metadata"]["kind"] == "autoplay_commentary"
    assert "先垫格挡" in payload["content"]
    assert any("[sts2_catgirl_host] pushed commentary to host" in line for line in logger.lines), logger.lines


# ══════════════════════════════════════════════════════════════════════════════
# ② 弹幕开着 ⇒ 仍走弹幕，不推宿主（不许一句话说两遍）
# ══════════════════════════════════════════════════════════════════════════════


def test_danmaku_on_does_not_also_push_to_host() -> None:
    service, _, pushes = _service({"host_commentary_when_danmaku_off": True})
    service.set_danmaku_enabled(True)
    client = _Client()
    service._client = client

    _push(service, "打这张打击，收掉小怪。")

    assert client.danmaku == ["打这张打击，收掉小怪。"]
    assert pushes == [], "弹幕已经说了，不许再让本体重复一次"


# ══════════════════════════════════════════════════════════════════════════════
# ③ 开关关 ⇒ 回到老行为（与 0.7.8 之前逐字一致）
# ══════════════════════════════════════════════════════════════════════════════


def test_switch_off_keeps_the_old_silent_behaviour() -> None:
    service, _, pushes = _service({"host_commentary_when_danmaku_off": False})
    service.set_danmaku_enabled(False)
    client = _Client()
    service._client = client

    _push(service, "这句话不该出现在任何地方。")

    assert client.danmaku == []
    assert pushes == []


# ══════════════════════════════════════════════════════════════════════════════
# ④ 节流：同场景只说一次；最小间隔内不再推
# ══════════════════════════════════════════════════════════════════════════════


def test_same_scene_is_only_spoken_once() -> None:
    """同一场景键（`last_push_scene_key`）只讲一次 —— 每推一次都是一次 AI 回合。"""
    service, _, pushes = _service({"host_commentary_when_danmaku_off": True})
    service.set_danmaku_enabled(False)
    service._client = _Client()
    service._state.last_push_scene_key = "0:7:combat:3"

    _push(service, "第一句。")
    _push(service, "同一回合的第二句。")

    assert len(pushes) == 1, f"同一场景只应说一次，实际 {len(pushes)} 次"


def test_min_interval_throttles_different_scenes() -> None:
    """不同场景但间隔不够 ⇒ 也不推（默认 45s 一次，有 token 成本）。"""
    service, logger, pushes = _service(
        {"host_commentary_when_danmaku_off": True, "host_commentary_min_interval_seconds": 45}
    )
    service.set_danmaku_enabled(False)
    service._client = _Client()

    service._state.last_push_scene_key = "A"
    _push(service, "第一句。")
    service._state.last_push_scene_key = "B"  # 换场景
    _push(service, "第二句。")

    assert len(pushes) == 1
    assert any("[sts2_catgirl_host] throttled" in line for line in logger.lines), logger.lines


def test_interval_is_configurable_and_has_a_floor() -> None:
    """`host_commentary_min_interval_seconds` 可调，但下限 5s（防"零间隔"把本体刷爆）。"""
    service, _, _ = _service({"host_commentary_min_interval_seconds": 0})
    assert service._host_commentary_min_interval() == pytest.approx(5.0)
    service, _, _ = _service({"host_commentary_min_interval_seconds": 90})
    assert service._host_commentary_min_interval() == pytest.approx(90.0)


# ══════════════════════════════════════════════════════════════════════════════
# ⑤ 生成闸门：弹幕关着但开了宿主点评 ⇒ 仍要生成文本（否则没得说）
# ══════════════════════════════════════════════════════════════════════════════


def test_generator_still_runs_when_only_host_commentary_is_on() -> None:
    service, logger, _ = _service({"host_commentary_when_danmaku_off": True})
    service.set_danmaku_enabled(False)
    service._catgirl_llm_enabled = False  # 走启发式兜底，避开真 LLM

    scheduled: list[str] = []
    service._push_catgirl_fallback = lambda ce, pl: scheduled.append("fallback")  # type: ignore[assignment]

    service._maybe_emit_catgirl_llm(
        snapshot={"screen": "COMBAT"},
        companion_evaluation={"primary_message": "打这张。"},
        payload={"summary_kind": "combat", "screen": "COMBAT"},
    )

    assert scheduled == ["fallback"], "开了宿主点评就必须继续产出文本"
    assert not any("suppressed danmaku disabled" in line for line in logger.lines), logger.lines


def test_generator_is_silent_when_both_are_off() -> None:
    service, logger, _ = _service({"host_commentary_when_danmaku_off": False})
    service.set_danmaku_enabled(False)

    called: list[int] = []
    service._push_catgirl_fallback = lambda ce, pl: called.append(1)  # type: ignore[assignment]

    service._maybe_emit_catgirl_llm(
        snapshot={"screen": "COMBAT"},
        companion_evaluation={"primary_message": "打这张。"},
        payload={"summary_kind": "combat", "screen": "COMBAT"},
    )

    assert called == [], "两路都关着时不许生成（省一次 LLM/头像开销）"
    assert any("suppressed danmaku disabled" in line for line in logger.lines), logger.lines
