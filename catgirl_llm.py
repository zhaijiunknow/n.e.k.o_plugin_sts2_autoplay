# -*- coding: utf-8 -*-
"""猫娘 LLM 点评生成器：让 catgirl 弹幕显示猫娘真正生成的内容（而非程序拼接模板）。

参考 galgame_plugin.llm_backend 的 LLM 调用模式：
- ``utils.llm_client.create_chat_llm_async`` 创建异步 LLM 客户端
- ``get_config_manager().get_model_api_config(model_role)`` 拿主系统 API provider 配置
  （sts2 插件与 galgame 同目录层级，可 import utils.*）

调用方在 should_comment 时异步生成一句猫娘口吻的短点评，生成后推 catgirl 弹幕；
失败返回 None，由调用方兜底启发式文本（companion_evaluator.primary_message）。
"""

from __future__ import annotations

import hashlib
from typing import Any

try:
    from utils.config_manager import get_config_manager
    from utils.llm_client import create_chat_llm_async
except Exception:  # 单测/无主系统环境
    get_config_manager = None  # type: ignore[assignment]
    create_chat_llm_async = None  # type: ignore[assignment]

_MAX_PROMPT_CHARS = 1200


class CatgirlCommentGenerator:
    """猫娘 LLM 点评生成器（按模型配置缓存客户端）。"""

    def __init__(
        self,
        logger: Any,
        *,
        model_role: str = "agent",
        max_tokens: int = 220,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._logger = logger
        self._model_role = str(model_role or "agent")
        self._max_tokens = int(max_tokens or 220)
        self._timeout = float(timeout_seconds or 30.0)
        self._llm: Any = None
        self._llm_key: tuple[Any, ...] | None = None

    @property
    def available(self) -> bool:
        """主系统 LLM 客户端是否可用（utils 未加载时为 False）。"""
        return create_chat_llm_async is not None and get_config_manager is not None

    async def generate(self, *, summary_text: str, summary_kind: str, payload: dict[str, Any]) -> str | None:
        """异步生成一句猫娘口吻点评；失败/不可用返回 None（调用方兜底）。"""
        if not self.available:
            return None
        try:
            llm = await self._ensure_llm()
        except Exception as exc:
            self._log("获取 LLM 客户端失败: %s", exc)
            return None
        messages = self._build_messages(summary_text=summary_text, summary_kind=summary_kind, payload=payload)
        try:
            return await self._invoke_text(llm, messages)
        except Exception as exc:
            self._log("猫娘点评生成失败: %s", exc)
            return None

    async def _invoke_text(self, llm: Any, messages: list[dict[str, str]]) -> str | None:
        """调 LLM 取文本。

        优先 ``ainvoke_raw`` 从 ``choices[0].message.content`` 提取（free-agent-model
        等端点返回标准 OpenAI choices 结构，``LLMResponse.content`` 可能为空）；
        不可用/为空时兜底 ``ainvoke``。
        """
        raw_invoke = getattr(llm, "ainvoke_raw", None)
        if callable(raw_invoke):
            try:
                raw = await raw_invoke(messages)
                try:
                    content = raw.choices[0].message.content
                except (AttributeError, IndexError, KeyError, TypeError):
                    content = None
                if content and str(content).strip():
                    return str(content).strip()
            except Exception:
                pass
        try:
            response = await llm.ainvoke(messages)
            text = str(getattr(response, "content", "") or "").strip()
            return text or None
        except Exception:
            return None

    async def _ensure_llm(self) -> Any:
        config = get_config_manager().get_model_api_config(self._model_role)  # type: ignore[union-attr]
        base_url = str(config.get("base_url") or "").strip()
        model = str(config.get("model") or "").strip()
        api_key = str(config.get("api_key") or "").strip()
        provider_type = config.get("provider_type")
        if not base_url or not model:
            raise RuntimeError(f"未配置 {self._model_role} 模型")
        key = (base_url, model, hashlib.sha1(api_key.encode("utf-8")).hexdigest()[:8], self._max_tokens)
        if self._llm is not None and self._llm_key == key:
            return self._llm
        llm = await create_chat_llm_async(  # type: ignore[union-attr]
            model=model,
            base_url=base_url,
            api_key=api_key,
            max_completion_tokens=self._max_tokens,
            timeout=self._timeout,
            provider_type=provider_type,
        )
        self._llm = llm
        self._llm_key = key
        return llm

    def _persona_system(self) -> str:
        """从主系统取本体猫娘人设（persona guidance），替换占位符；拿不到返回空。"""
        if not self.available:
            return ""
        try:
            cm = get_config_manager()
            data = cm.get_character_data()
            master_name = data[0] if len(data) > 0 else ""
            her_name = data[1] if len(data) > 1 else ""
            lanlan_prompt_map = data[5] if len(data) > 5 else {}
            persona = str(lanlan_prompt_map.get(her_name, "") or "").strip()
            if persona:
                persona = (
                    persona
                    .replace("{LANLAN_NAME}", her_name)
                    .replace("{MASTER_NAME}", master_name)
                    .replace("{lanlan_name}", her_name)
                    .replace("{master_name}", master_name)
                )
            return persona
        except Exception:
            return ""

    def _build_messages(self, *, summary_text: str, summary_kind: str, payload: dict[str, Any]) -> list[dict[str, str]]:
        base = (
            "基于你的人设，用一句短小、猫娘口吻的弹幕表达对当前局面的即时反应"
            "（吐槽/加油/紧张/得意都可以）。"
            "要求：一句话不超过 10 字；像直播间弹幕一样自然活泼、有情绪起伏；"
            "不要写建议式长篇，不要用『按X策略』模板，不要复述术语堆砌。"
            "按场景带不同情绪：战斗→紧张或兴奋；选牌/奖励→期待或挑剔；"
            "商店→心动或精打细算；火堆→放松或鼓励；事件→好奇或惊讶。"
            "句末或句首带你的口癖收尾，简短有力。"
        )
        persona = self._persona_system()
        system = persona + "\n\n" + base if persona else "你是《杀戮尖塔 2》的直播间陪玩猫娘。\n" + base
        context = str(summary_text or "").strip()[: _MAX_PROMPT_CHARS]
        scene = str(summary_kind or "general")
        user = f"当前场景：{scene}\n当前局面：{context}"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _log(self, fmt: str, *args: Any) -> None:
        try:
            self._logger.debug("[sts2_catgirl_llm] " + fmt, *args)
        except Exception:
            pass

    async def shutdown(self) -> None:
        llm = self._llm
        self._llm = None
        if llm is not None:
            try:
                close = getattr(llm, "aclose", None)
                if callable(close):
                    await close()
            except Exception:
                pass


__all__ = ["CatgirlCommentGenerator"]
