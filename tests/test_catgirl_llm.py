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
    assert captured["max_tokens"] == 1500, "推理模型需要足够的思考余量，否则 content 为空（猫娘噤声）"


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
def test_generate_records_why_it_produced_nothing(monkeypatch) -> None:
    """失败原因必须留得下来。

    线上实测：战斗场景 208 次调用全部 `result=None`、弹幕一条都没有，而原因被吞在 debug 级
    （`_log`），INFO 日志里只看到"LLM 失败/空"，根本没法诊断。
    """
    import plugin.plugins.sts2_autoplay.catgirl_llm as m

    async def fake_create_llm(**kw):
        return FakeLLM("")

    monkeypatch.setattr(m, "create_chat_llm_async", fake_create_llm)
    monkeypatch.setattr(m, "get_config_manager", lambda: FakeConfigManager())
    gen = CatgirlCommentGenerator(DummyLogger())

    text = asyncio.run(gen.generate(summary_text="x", summary_kind="combat", payload={}))

    assert text is None
    assert "ainvoke 空 content" in gen._last_error, "必须记录未产出原因"


@pytest.mark.unit
def test_generate_reports_length_finish_reason_for_reasoning_models(monkeypatch) -> None:
    """**"猫娘在战斗里噤声"的根因回归测试。**

    主系统用的是推理模型（free-agent-model）。当生成预算被 reasoning 吃光时，上游返回
    ``content=""`` 且 ``finish_reason="length"`` —— 这时必须把 finish_reason 记下来，
    否则线上只看到"空 content"，根本分不清是预算不够、被拒绝还是网络问题。
    """
    import plugin.plugins.sts2_autoplay.catgirl_llm as m

    class Choice:
        finish_reason = "length"
        message = SimpleNamespace(content="", reasoning_content="已经想好了喵…")

    class RawResp:
        choices = [Choice()]

    class ReasoningLLM:
        async def ainvoke_raw(self, messages):
            return RawResp()

        async def ainvoke(self, messages):
            return SimpleNamespace(content="")

    async def fake_create_llm(**kw):
        return ReasoningLLM()

    monkeypatch.setattr(m, "create_chat_llm_async", fake_create_llm)
    monkeypatch.setattr(m, "get_config_manager", lambda: FakeConfigManager())
    gen = CatgirlCommentGenerator(DummyLogger())

    assert asyncio.run(gen.generate(summary_text="x", summary_kind="combat", payload={})) is None
    assert "finish_reason=length" in gen._last_error
    assert "reasoning_content" in gen._last_error


@pytest.mark.unit
def test_generate_records_llm_exception_reason(monkeypatch) -> None:
    import plugin.plugins.sts2_autoplay.catgirl_llm as m

    async def fake_create_llm(**kw):
        llm = FakeLLM("")

        async def boom(*a, **k):
            raise RuntimeError("model down")

        llm.ainvoke = boom
        return llm

    monkeypatch.setattr(m, "create_chat_llm_async", fake_create_llm)
    monkeypatch.setattr(m, "get_config_manager", lambda: FakeConfigManager())
    gen = CatgirlCommentGenerator(DummyLogger())

    assert asyncio.run(gen.generate(summary_text="x", summary_kind="combat", payload={})) is None
    assert "model down" in gen._last_error


@pytest.mark.unit
def test_catgirl_llm_async_falls_back_when_llm_produces_nothing() -> None:
    """**"猫娘在战斗里一句话都不说"的回归测试。**

    LLM 客户端"可用"但调用没产出时，旧代码什么都不推；而 `_maybe_emit_catgirl_llm` 又因为
    `llm_ok=True` 不会走它自己的启发式兜底分支 —— 两条路都不出声，弹幕就彻底消失了。
    """
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None)

    pushed: list[str] = []

    class SilentGenerator:
        available = True

        async def generate(self, **kw):
            return None

    async def fake_combat_prompt(snapshot, payload):
        return "本轮: 打打击"

    async def fake_push(text, **kwargs):
        pushed.append(text)

    service._catgirl_llm = SilentGenerator()
    service._client = object()
    service._danmaku_enabled = True
    service._build_combat_prompt = fake_combat_prompt
    service._push_catgirl = fake_push

    async def drive() -> None:
        await service._catgirl_llm_async(
            {"situation_summary": {"text": "局面"}},
            {"summary_kind": "combat", "message": "局面"},
            {"primary_message": "小心点喵"},
        )
        # 兜底是 fire-and-forget 的任务，让出一轮让它跑完。
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(drive())

    assert pushed == ["小心点喵"], "LLM 没产出时也必须出声（退回启发式文本）"


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
def test_maybe_emit_catgirl_llm_disabled_falls_back(monkeypatch) -> None:
    """LLM 关掉时仍要推启发式文本。

    这里**把头像 HTTP 打桩成瞬时返回**，而不是靠 ``sleep`` 等它 —— 真实取头像要 331~685ms，
    原先的 200ms 预算压不住，于是这条用例长期假红（实测：真实路径 685/336/331ms，
    打桩后 0.07ms 即过）。用 sleep 兜住真实网络是脆弱的，涨超时也只是把偶发变偶发；
    头像本身已由 ``test_danmaku_disabled_blocks_llm_avatar_and_post_entirely`` 等用例单独覆盖。
    """
    client = FakeClient()
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None)
    service._client = client
    service._catgirl_llm_enabled = False

    async def fake_avatar_fast() -> str | None:
        return None

    monkeypatch.setattr(service, "_catgirl_avatar_base64", fake_avatar_fast)
    monkeypatch.setattr(service, "_cached_avatar_base64", lambda: None)

    async def go() -> None:
        service._maybe_emit_catgirl_llm(
            {},
            {"should_comment": True, "primary_message": "建议先防御。"},
            {"message": "建议先防御。"},
        )
        # 只等"这个 fire-and-forget 任务被调度完"，不再依赖真实网络耗时。
        for _ in range(50):
            if client.pushed:
                break
            await asyncio.sleep(0.01)

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
