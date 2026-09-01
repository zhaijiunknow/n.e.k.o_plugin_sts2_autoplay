"""猫娘 LLM 点评生成器测试：不可用兜底 / 调用链路 / prompt 构建 / service 集成。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.sts2_autoplay.catgirl_llm import CatgirlCommentGenerator
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None


class FakeConfigManager:
    def get_model_api_config(self, model_role: str) -> dict:
        return {"base_url": "http://localhost:11434", "model": "qwen", "api_key": "k", "provider_type": "openai"}


class FakeLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    async def ainvoke(self, messages: list) -> SimpleNamespace:
        return SimpleNamespace(content=self._text)


@pytest.mark.unit
def test_generate_returns_none_when_unavailable(monkeypatch) -> None:
    import plugin.plugins.sts2_autoplay.catgirl_llm as m

    monkeypatch.setattr(m, "create_chat_llm_async", None)
    gen = CatgirlCommentGenerator(DummyLogger())
    assert gen.available is False
    text = asyncio.run(gen.generate(summary_text="局面", summary_kind="combat", payload={}))
    assert text is None


@pytest.mark.unit
def test_generate_calls_llm_and_returns_text(monkeypatch) -> None:
    import plugin.plugins.sts2_autoplay.catgirl_llm as m

    captured: dict = {}

    async def fake_create_llm(model, base_url, api_key, **kw):
        captured["args"] = (model, base_url, api_key)
        captured["max_tokens"] = kw.get("max_completion_tokens")
        return FakeLLM("喵呜，这波该稳一手了")

    monkeypatch.setattr(m, "create_chat_llm_async", fake_create_llm)
    monkeypatch.setattr(m, "get_config_manager", lambda: FakeConfigManager())
    gen = CatgirlCommentGenerator(DummyLogger())
    text = asyncio.run(gen.generate(summary_text="敌人要重击", summary_kind="combat", payload={}))
    assert text == "喵呜，这波该稳一手了"
    assert captured["args"][0] == "qwen"
    assert captured["args"][1] == "http://localhost:11434"
    assert captured["max_tokens"] == 220


@pytest.mark.unit
def test_generate_returns_none_on_llm_failure(monkeypatch) -> None:
    import plugin.plugins.sts2_autoplay.catgirl_llm as m

    async def fake_create_llm(**kw):
        return FakeLLM("")

    async def boom(*args, **kw):
        raise RuntimeError("model down")

    monkeypatch.setattr(m, "create_chat_llm_async", fake_create_llm)
    monkeypatch.setattr(m, "get_config_manager", lambda: FakeConfigManager())
    gen = CatgirlCommentGenerator(DummyLogger())
    # ainvoke 返回空 → None
    text = asyncio.run(gen.generate(summary_text="x", summary_kind="combat", payload={}))
    assert text is None
    # ainvoke 抛异常 → None
    gen._llm = FakeLLM("")
    gen._llm.ainvoke = boom
    text2 = asyncio.run(gen.generate(summary_text="x", summary_kind="combat", payload={}))
    assert text2 is None


@pytest.mark.unit
def test_build_messages_prompt() -> None:
    gen = CatgirlCommentGenerator(DummyLogger())
    messages = gen._build_messages(summary_text="当前局势偏危险", summary_kind="combat", payload={})
    assert messages[0]["role"] == "system"
    assert "猫娘" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "当前场景：combat" in messages[1]["content"]
    assert "当前局势偏危险" in messages[1]["content"]


# ---- service 集成 ----

class FakeClient:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    async def push_danmaku(self, text, *, style="catgirl", placement="scrolling", avatar=None) -> dict:
        self.pushed.append((text, style))
        return {"status": "ok"}


@pytest.mark.unit
def test_maybe_emit_catgirl_llm_disabled_falls_back() -> None:
    client = FakeClient()
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None)
    service._client = client
    service._catgirl_llm_enabled = False

    async def go() -> None:
        service._maybe_emit_catgirl_llm(
            {},
            {"should_comment": True, "primary_message": "建议先防御。"},
            {"message": "建议先防御。"},
        )
        await asyncio.sleep(0.2)  # let the fire-and-forget push task run (it awaits a localhost avatar fetch)

    asyncio.run(go())
    assert client.pushed == [("建议先防御。", "catgirl")]


@pytest.mark.unit
def test_catgirl_llm_async_pushes_generated_text(monkeypatch) -> None:

    client = FakeClient()
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None)
    service._client = client

    async def fake_generate(*, summary_text, summary_kind, payload):
        return "喵呜，我盯上这个精英了"

    service._catgirl_llm.generate = fake_generate  # type: ignore[method-assign]
    asyncio.run(service._catgirl_llm_async({}, {"message": "局面描述", "summary_kind": "combat"}))
    assert client.pushed == [("喵呜，我盯上这个精英了", "catgirl")]
