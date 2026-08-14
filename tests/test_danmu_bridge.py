from __future__ import annotations

import asyncio
import json

import pytest
from plugin.plugins.sts2_autoplay.danmu_bridge import STS2DanmuBridge
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


async def _noop_post(payload: dict) -> None:
    return None


@pytest.mark.unit
def test_push_text_empty_or_disabled_returns_false() -> None:
    bridge = STS2DanmuBridge(DummyLogger(), post_async=_noop_post)
    assert bridge.push_text("") is False
    assert bridge.push_text("   ") is False
    assert bridge.push_text("\n\t") is False
    bridge.enabled = False
    assert bridge.push_text("有内容") is False


@pytest.mark.unit
def test_push_text_dedup_same_content_within_ttl() -> None:
    # top_mode=none 保证 placement 确定（否则随机 placement 会让去重 key 不同）
    bridge = STS2DanmuBridge(DummyLogger(), post_async=_noop_post, top_mode="none", dedup_enabled=True)
    assert bridge.push_text("重复弹幕") is True
    # TTL 窗口内同一文本 → 第二次忽略
    assert bridge.push_text("重复弹幕") is False


@pytest.mark.unit
def test_push_text_normalizes_whitespace() -> None:
    bridge = STS2DanmuBridge(DummyLogger(), post_async=_noop_post, top_mode="none", dedup_enabled=True)
    assert bridge.push_text("  你好  弹幕  ") is True
    # 归一化后与上一条相同 → 命中去重
    assert bridge.push_text("你好 弹幕") is False


@pytest.mark.unit
def test_push_text_no_dedup_by_default() -> None:
    # 默认不去重：同一文本可重复推送（弹幕更密）
    bridge = STS2DanmuBridge(DummyLogger(), post_async=_noop_post, top_mode="none")
    assert bridge.push_text("重复弹幕") is True
    assert bridge.push_text("重复弹幕") is True


@pytest.mark.unit
def test_push_text_dedup_distinguishes_style() -> None:
    bridge = STS2DanmuBridge(DummyLogger(), post_async=_noop_post)
    assert bridge.push_text("重复", style="narration") is True
    # 同文本不同 style → 不命中
    assert bridge.push_text("重复", style="catgirl") is True


@pytest.mark.unit
async def test_push_text_schedules_broadcast_in_async_context() -> None:
    posted: list[dict] = []

    async def fake_post(payload: dict) -> None:
        posted.append(payload)

    bridge = STS2DanmuBridge(DummyLogger(), post_async=fake_post)
    assert bridge.push_text(" 测试弹幕 ") is True
    await asyncio.sleep(0.05)  # 让 create_task 调度出的广播跑完
    assert len(posted) == 1
    assert posted[0]["text"] == "测试弹幕"
    assert posted[0]["style"] == "narration"


@pytest.mark.unit
async def test_push_status_payload_shape() -> None:
    """game_status：type 区分，text 直接承载游戏信息 JSON（前端 JSON.parse(msg.text)）。"""
    posted: list[dict] = []

    async def fake_post(payload: dict) -> None:
        posted.append(payload)

    bridge = STS2DanmuBridge(DummyLogger(), post_async=fake_post)
    data = {"game": {"screen": "combat"}, "trigger_names": ["BigTurn"], "triggers": {"BigTurn": 1}}
    assert bridge.push_status(data=data) is True
    await asyncio.sleep(0.05)  # 让 create_task 调度出的广播跑完
    assert len(posted) == 1
    payload = posted[0]
    assert payload["type"] == "game_status"
    assert "style" not in payload  # 非弹幕，不带弹幕扩展字段
    assert json.loads(payload["text"]) == data  # text 承载的 JSON 可被前端还原


@pytest.mark.unit
def test_push_status_disabled_or_empty_returns_false() -> None:
    bridge = STS2DanmuBridge(DummyLogger(), post_async=_noop_post)
    assert bridge.push_status(data={}) is False
    assert bridge.push_status(data=None) is False
    bridge.enabled = False
    assert bridge.push_status(data={"a": 1}) is False


@pytest.mark.unit
async def test_broadcast_narration_payload_has_style() -> None:
    posted: list[dict] = []

    async def fake_post(payload: dict) -> None:
        posted.append(payload)

    bridge = STS2DanmuBridge(DummyLogger(), post_async=fake_post)
    await bridge._broadcast("你好，弹幕。", "narration", "scrolling")
    assert posted == [
        {"type": "danmu", "text": "你好，弹幕。", "style": "narration", "placement": "scrolling"}
    ]


@pytest.mark.unit
async def test_broadcast_catgirl_style_keeps_style_when_avatar_unavailable() -> None:
    posted: list[dict] = []

    async def fake_post(payload: dict) -> None:
        posted.append(payload)

    bridge = STS2DanmuBridge(DummyLogger(), post_async=fake_post)
    # 强制头像获取返回空 → 仍带 style=catgirl/placement，但无 avatar 字段
    async def _no_avatar() -> str:
        return ""

    bridge.get_avatar = _no_avatar
    await bridge._broadcast("建议优先防御。", "catgirl", "scrolling")
    assert posted[0]["text"] == "建议优先防御。"
    assert posted[0]["style"] == "catgirl"
    assert posted[0]["placement"] == "scrolling"
    assert "avatar" not in posted[0]


@pytest.mark.unit
def test_decide_placement_none_scrolls() -> None:
    bridge = STS2DanmuBridge(DummyLogger(), top_mode="none")
    assert bridge._decide_placement("narration") == "scrolling"
    assert bridge._decide_placement("catgirl") == "scrolling"


@pytest.mark.unit
def test_decide_placement_all_tops() -> None:
    bridge = STS2DanmuBridge(DummyLogger(), top_mode="all")
    assert bridge._decide_placement("narration") == "top"
    assert bridge._decide_placement("catgirl") == "top"


@pytest.mark.unit
def test_decide_placement_standard_probability() -> None:
    # catgirl 永不置顶；narration 按概率
    bridge0 = STS2DanmuBridge(DummyLogger(), top_mode="standard", top_probability=0.0)
    assert bridge0._decide_placement("catgirl") == "scrolling"
    assert bridge0._decide_placement("narration") == "scrolling"
    bridge1 = STS2DanmuBridge(DummyLogger(), top_mode="standard", top_probability=1.0)
    assert bridge1._decide_placement("narration") == "top"


class FakeDanmuBridge:
    enabled = True

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    def push_text(self, text: str, *, style: str = "narration", placement: str | None = None, delay_seconds: float = 0.0) -> bool:
        self.pushed.append((text, style))
        return True


def _companion_snapshot(*, commentary: str = "", message: str = "", screen: str = "combat") -> dict:
    companion_evaluation: dict = {"should_comment": True}
    if commentary:
        companion_evaluation["commentary"] = commentary
    payload: dict = {
        "screen": screen,
        "summary_kind": screen,
        "trigger": "player_operation",
        "message": message,
        "ai_behavior": "respond",
        "companion_evaluation": companion_evaluation,
    }
    return {
        "catgirl_sync": {
            "should_sync": True,
            "should_comment": True,
            "fingerprint": "danmu-test-fp",
            "reason": "screen_class:combat",
            "min_interval_seconds": 0.0,
            "force": True,
            "payload": payload,
        }
    }


@pytest.mark.unit
def test_deliver_catgirl_sync_pushes_catgirl_only() -> None:
    """catgirl 轨道推猫娘点评；narration 无规则命中则不推（只保留条件弹幕）。"""
    bridge = FakeDanmuBridge()
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **kwargs: None, danmu_bridge=bridge)
    service._cfg["companion_mode_enabled"] = True
    service._cfg["neko_commentary_enabled"] = True

    service._deliver_catgirl_sync(
        _companion_snapshot(commentary="当前局势偏危险，建议优先防御。", message="建议优先防御。")
    )

    # 只推 catgirl（narration 无规则命中不推）
    assert len(bridge.pushed) == 1
    catgirl_text, catgirl_style = bridge.pushed[0]
    assert catgirl_style == "catgirl"
    assert catgirl_text == "建议优先防御。"


@pytest.mark.unit
def test_deliver_catgirl_sync_pushes_for_unknown_screen() -> None:
    bridge = FakeDanmuBridge()
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **kwargs: None, danmu_bridge=bridge)
    service._cfg["companion_mode_enabled"] = True
    service._cfg["neko_commentary_enabled"] = True

    service._deliver_catgirl_sync(_companion_snapshot(message="先补甲，再找高收益出牌。", screen="foo"))

    # 未知 screen 无规则 → 只 catgirl
    assert len(bridge.pushed) == 1
    assert bridge.pushed[0][1] == "catgirl"
    assert bridge.pushed[0][0] == "先补甲，再找高收益出牌。"


@pytest.mark.unit
def test_deliver_catgirl_sync_pushes_narration_on_rule_hit() -> None:
    """narration 条件弹幕：detect_trigger 命中规则才推（裸奔挨打）。"""
    bridge = FakeDanmuBridge()
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **kwargs: None, danmu_bridge=bridge)
    service._cfg["companion_mode_enabled"] = True
    service._cfg["neko_commentary_enabled"] = True

    snap = _companion_snapshot(message="先防御。")
    snap["catgirl_sync"]["payload"]["player"] = {"current_hp": 60, "max_hp": 75, "block": 0}
    snap["catgirl_sync"]["payload"]["enemies"] = [{"name": "小怪", "intent": "TACKLE_MOVE"}]
    service._deliver_catgirl_sync(snap)

    # NakedHit 规则命中 → narration 推规则词条
    assert len(bridge.pushed) >= 2
    narration_text, narration_style = bridge.pushed[1]
    assert narration_style == "narration"
    assert narration_text
    assert "{" not in narration_text and "}" not in narration_text


@pytest.mark.unit
def test_deliver_catgirl_sync_skips_bridge_when_disabled() -> None:
    bridge = FakeDanmuBridge()
    bridge.enabled = False
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **kwargs: None, danmu_bridge=bridge)
    service._cfg["companion_mode_enabled"] = True
    service._cfg["neko_commentary_enabled"] = True

    service._deliver_catgirl_sync(_companion_snapshot(commentary="这条不该上弹幕。"))

    assert bridge.pushed == []
