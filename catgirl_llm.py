# -*- coding: utf-8 -*-
"""猫娘 LLM 生成器：点评（catgirl 弹幕）与事件房策略建议。

参考 galgame_plugin.llm_backend 的 LLM 调用模式：
- ``utils.llm_client.create_chat_llm_async`` 创建异步 LLM 客户端
- ``get_config_manager().get_model_api_config(model_role)`` 拿主系统 API provider 配置
  （sts2 插件与 galgame 同目录层级，可 import utils.*）

``CatgirlCommentGenerator`` 让 catgirl 弹幕显示猫娘真正生成的内容（而非程序拼接模板）；
``EventAdviceGenerator`` 让事件房决策能吃到 LLM 对各选项的评分（与 heuristic 融合，见
heuristic_planner._preferred_event_option）。两者共用 ``_ChatLLMBase`` 的客户端获取/调用/日志。
任何失败/不可用/超时都返回 None，由调用方兜底（点评兜 companion_evaluator；事件兜 heuristic）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

try:
    from utils.config_manager import get_config_manager
    from utils.llm_client import create_chat_llm_async
except Exception:  # 单测/无主系统环境
    get_config_manager = None  # type: ignore[assignment]
    create_chat_llm_async = None  # type: ignore[assignment]

_MAX_PROMPT_CHARS = 1200


def _extract_json_object(text: str) -> str | None:
    """粗略截取文本里第一个 ``{`` 到最后一个 ``}`` 的 JSON 块（容忍代码块/夹杂文字）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _parse_llm_scores(text: str | None, valid_indices: list[int]) -> dict[int, float] | None:
    """宽容解析 LLM 返回的事件选项评分。

    期望 ``{"scores": {"<index>": 0-100}}`` 或直接 ``{"<index>": 0-100}``；
    容忍 JSON 代码块/前后夹杂文字；夹到 0-100；只保留合法 index；
    无任何合法分/解析失败 → None（调用方兜底）。
    """
    if not text:
        return None
    stripped = str(text).strip()
    if not stripped:
        return None

    payload: Any = None
    candidates = [stripped]
    extracted = _extract_json_object(stripped)
    if extracted is not None:
        candidates.append(extracted)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            payload = parsed
            break

    if not isinstance(payload, dict):
        return None

    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else None
    source = scores if isinstance(scores, dict) else (
        payload if all(isinstance(k, str) and k.lstrip("-").isdigit() for k in payload) else None
    )
    if not isinstance(source, dict):
        return None

    result: dict[int, float] = {}
    for index in valid_indices:
        raw = source.get(str(index))
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        result[index] = max(0.0, min(100.0, value))
    return result or None


class _ChatLLMBase:
    """LLM 客户端获取/调用/日志共享基类（按模型配置缓存客户端）。"""

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


class CatgirlCommentGenerator(_ChatLLMBase):
    """猫娘 LLM 点评生成器（按模型配置缓存客户端）。"""

    def __init__(
        self,
        logger: Any,
        *,
        model_role: str = "agent",
        max_tokens: int = 220,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            logger,
            model_role=model_role,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

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
        scene = str(summary_kind or "general")
        context = str(summary_text or "").strip()[:_MAX_PROMPT_CHARS]
        if scene == "combat":
            # 战斗：prompt 里已带"本轮: 打<卡>→N；结束回合"这类出牌建议（line），要 LLM 用猫娘口吻
            # 把建议转述出来（先打X再补Y/躲Z），而不是只卖萌噤声。放宽字数，但保留弹幕感。
            base = (
                "基于你的人设，用一句短小、猫娘口吻的弹幕点评当前战斗，并自然带出下面的出牌建议。"
                "要求：把给出的『本轮』建议用猫娘话讲清楚（比如：先打痛击再补冷光，躲开诅咒喵！），"
                "让人一听就知道该打哪张牌、注意敌人什么；一句话 10~25 字；像直播间弹幕一样活泼、有情绪；"
                "不要逐字复述卡名编号堆砌，不要用『按X策略』模板；"
                "句末或句首带你的口癖收尾，简短有力。"
            )
            persona = self._persona_system()
            system = persona + "\n\n" + base if persona else "你是《杀戮尖塔 2》的直播间陪玩猫娘。\n" + base
            user = f"当前场景：{scene}\n当前战斗局面与出牌建议：{context}"
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        base = (
            "基于你的人设，用一句短小、猫娘口吻的弹幕表达对当前局面的即时反应"
            "（吐槽/加油/紧张/得意都可以）。"
            "要求：一句话不超过 10 字；像直播间弹幕一样自然活泼、有情绪起伏；"
            "不要写建议式长篇，不要用『按X策略』模板，不要复述术语堆砌。"
            "按场景带不同情绪：选牌/奖励→期待或挑剔；"
            "商店→心动或精打细算；火堆→放松或鼓励；事件→好奇或惊讶。"
            "句末或句首带你的口癖收尾，简短有力。"
        )
        persona = self._persona_system()
        system = persona + "\n\n" + base if persona else "你是《杀戮尖塔 2》的直播间陪玩猫娘。\n" + base
        user = f"当前场景：{scene}\n当前局面：{context}"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]


class EventAdviceGenerator(_ChatLLMBase):
    """事件房策略建议生成器：给每个事件选项打分，供 heuristic 融合（非单点决策）。"""

    def __init__(
        self,
        logger: Any,
        *,
        model_role: str = "agent",
        max_tokens: int = 400,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            logger,
            model_role=model_role,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

    async def score_event_options(
        self,
        *,
        options: list[dict[str, Any]],
        run_context: dict[str, Any],
        strategy_context: dict[str, Any],
    ) -> dict[int, float] | None:
        """对每个事件选项打分（0-100），返回 ``{option_index: score}``；失败/不可用 → None。

        仅给建议，不决策：heuristic_planner 会把自己的评分与这些分融合后再选。
        """
        if not self.available:
            return None
        valid = [
            idx
            for option in options
            if isinstance(option, dict)
            and isinstance((idx := option.get("index")), int)
        ]
        if not valid:
            return None
        try:
            llm = await self._ensure_llm()
        except Exception as exc:
            self._log("获取 LLM 客户端失败: %s", exc)
            return None
        messages = self._build_messages(options=options, run_context=run_context, strategy_context=strategy_context)
        try:
            text = await self._invoke_text(llm, messages)
        except Exception as exc:
            self._log("事件建议生成失败: %s", exc)
            return None
        return _parse_llm_scores(text, valid)

    def _build_messages(
        self,
        *,
        options: list[dict[str, Any]],
        run_context: dict[str, Any],
        strategy_context: dict[str, Any],
    ) -> list[dict[str, str]]:
        system = (
            "你是《杀戮尖塔 2》的策略参谋猫娘。下面是当前事件房的各个选项与局势。"
            "评估每个选项的战术价值，给 0-100 分（100=非常值得选，0=绝对别选），"
            "同时尊重下方的人类指示。只返回 JSON：{\"scores\": {\"<选项index>\": 0-100}}，"
            "不要解释、不要其它文本。"
        )
        lines = []
        run = run_context or {}
        lines.append(
            "局势："
            f"角色={run.get('character_name') or '?'} "
            f"生命={run.get('current_hp')}/{run.get('max_hp') or '?'} "
            f"金币={run.get('gold') or 0}"
        )
        deck = run.get("deck") if isinstance(run.get("deck"), list) else []
        if deck:
            names = [str(c.get("name") or c.get("card_id") or "?") for c in deck[:20] if isinstance(c, dict)]
            lines.append(f"牌组（前{len(names)}卡）：{'、'.join(names)}")
        relics = run.get("relics") if isinstance(run.get("relics"), list) else []
        if relics:
            lines.append(f"遗物：{'、'.join(str(r) for r in relics)}")
        lines.append("事件选项：")
        for opt in options:
            if not isinstance(opt, dict):
                continue
            index = opt.get("index")
            text = str(opt.get("text") or opt.get("name") or opt.get("label") or opt.get("description") or "")
            lines.append(f"  [{index}] {text}")
        strategy = strategy_context or {}
        directives = strategy.get("strategy_directives") if isinstance(strategy.get("strategy_directives"), dict) else {}
        if directives:
            lines.append(f"人类指示：{str(directives)}")
        guidance = strategy.get("guidance") if isinstance(strategy.get("guidance"), dict) else {}
        if guidance:
            lines.append(f"人类指导：{str(guidance)}")
        user = "\n".join(lines)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]


__all__ = ["CatgirlCommentGenerator", "EventAdviceGenerator"]
