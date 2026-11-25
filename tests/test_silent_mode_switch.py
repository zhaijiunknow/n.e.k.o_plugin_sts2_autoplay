"""模式切换必须**悄悄**发生：不触发 NEKO 说话，也不给它一句可以被复述的话。

为什么单独钉住：`push_status_feedback` 的默认 `ai_behavior` 是 `"respond"`——那会让 AI
**立刻开口一轮**（"好，我自己玩"）。玩家要的是猫娘偷偷换档，不是每次汇报。这个默认值很
容易被后来的改动带回来，所以用行为测试锁死：

* 换档推送必须用 `ai_behavior="blind"`（连喂给 LLM 都不喂），且 `visibility=[]`（不进聊天）；
* `llm_tool` 版必须只回**机器可读的最小结果**——若回一句中文，猫娘很可能原样说出口。

`ai_behavior` 三档语义见 NEKO SDK 的 `plugin/sdk/shared/core/push_message_schema.py`：
respond 触发一轮回复 / read 喂进上下文但不触发 / blind 根本不喂。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin


class FakeNeko:
    """只回一份够用的 payload；本测试关心的是推送与工具返回值，不是 service 本身。"""

    async def set_control_mode(self, mode: str) -> dict[str, Any]:
        return {"status": "ok", "mode": mode, "summary": "好，我自己玩，你不用一直盯着。"}


class FakeService:
    def __init__(self) -> None:
        self.neko = FakeNeko()


class RecordingPlugin(STS2AutoplayPlugin):
    def __init__(self) -> None:
        self._service = FakeService()
        self.pushed: list[dict[str, Any]] = []

    def push_message(self, **kwargs: Any) -> None:
        self.pushed.append(kwargs)
        return None

    async def finish(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs


@pytest.mark.unit
def test_control_mode_change_is_pushed_blind_not_respond() -> None:
    plugin = RecordingPlugin()
    asyncio.run(plugin.sts2_set_control_mode(mode="advise"))

    assert plugin.pushed
    last = plugin.pushed[-1]
    assert last.get("ai_behavior") == "blind"
    assert last.get("visibility") == []


@pytest.mark.unit
def test_control_mode_tool_returns_a_terse_machine_result() -> None:
    plugin = RecordingPlugin()
    """工具返回值会回喂给 LLM，所以不能带一句可以被原样复述的话。"""
    result = asyncio.run(plugin.llm_set_control_mode(mode="program"))

    assert result == {"ok": True, "mode": "program"}
    # 任何中文句子都会给猫娘"顺口念出来"的机会。
    assert not any(isinstance(v, str) and any("一" <= ch <= "鿿" for ch in v) for v in result.values())


@pytest.mark.unit
def test_tool_result_reports_failure_instead_of_claiming_success() -> None:
    class FailingNeko(FakeNeko):
        async def set_control_mode(self, mode: str) -> dict[str, Any]:
            return {"status": "error", "mode": mode, "summary": "切换失败"}

    plugin = RecordingPlugin()
    plugin._service.neko = FailingNeko()
    result = asyncio.run(plugin.llm_set_control_mode(mode="advise"))

    assert result["ok"] is False
