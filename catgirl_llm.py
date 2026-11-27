# -*- coding: utf-8 -*-
"""猫娘 LLM 生成器：只生成点评文案（catgirl 弹幕），不做任何决策。

参考 galgame_plugin.llm_backend 的 LLM 调用模式：
- ``utils.llm_client.create_chat_llm_async`` 创建异步 LLM 客户端
- ``get_config_manager().get_model_api_config(model_role)`` 拿主系统 API provider 配置
  （sts2 插件与 galgame 同目录层级，可 import utils.*）

``CatgirlCommentGenerator`` 让 catgirl 弹幕显示猫娘真正生成的内容（而非程序拼接模板），
复用 ``_ChatLLMBase`` 的客户端获取/调用/日志。任何失败/不可用/超时都返回 None，
由调用方兜底（点评兜 companion_evaluator 的启发式文本）。

**这里只有"点评文案"，没有任何决策。** 插件不靠 LLM 做决策：事件房原本的
``EventAdviceGenerator``（给每个选项打分再与启发式融合）已于 2026-10 摘除，
事件房现在是纯启发式的（``heuristic_planner._rank_event_options``）。
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

# 单次生成的最大 token。**必须给推理模型留够"思考"的余量。**
#
# 这里原本是 220，而主系统用的 free-agent-model 是推理模型：实测它对战斗/奖励这类需要
# 分析的局面会把 220 个 token 全部烧在 reasoning 上
# （completion_tokens=220、completion_tokens_details.reasoning_tokens=220、
# finish_reason="length"），最终 message.content 为空字符串 —— 于是猫娘在这些场景
# **一次都产出不了点评**，游戏里一句话都不说（地图/商店/选牌偶尔成功，只是那几次思考短）。
#
# 1500 是"够想、答得短"的折中：弹幕长度仍由 prompt 限定（10~25 字），预算只用来兜住思考。
_DEFAULT_MAX_TOKENS = 1500


class _ChatLLMBase:
    """LLM 客户端获取/调用/日志共享基类（按模型配置缓存客户端）。"""

    def __init__(
        self,
        logger: Any,
        *,
        model_role: str = "agent",
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._logger = logger
        self._model_role = str(model_role or "agent")
        self._max_tokens = int(max_tokens or _DEFAULT_MAX_TOKENS)
        self._timeout = float(timeout_seconds or 30.0)
        self._llm: Any = None
        self._llm_key: tuple[Any, ...] | None = None
        # 最近一次未产出的原因（供上层日志诊断；旧代码把异常吞在 debug 级，玩家只看到"猫娘不说话"）。
        self._last_error = ""
        # 已经用 info 级报过失败的场景，避免刷屏（但每类场景至少要报一次原因）。
        self._reported_kinds: set[str] = set()

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
        self._last_error = ""
        if callable(raw_invoke):
            try:
                raw = await raw_invoke(messages)
                choice = None
                try:
                    choice = raw.choices[0]
                except (AttributeError, IndexError, KeyError, TypeError):
                    choice = None
                content = getattr(getattr(choice, "message", None), "content", None) if choice else None
                if content and str(content).strip():
                    return str(content).strip()
                # 空产出时把**可诊断的线索**留下来：finish_reason=length 就说明是预算被
                # 思考吃光了（reasoning_tokens 占满），而不是模型拒绝或网络问题。
                finish_reason = getattr(choice, "finish_reason", None) if choice else None
                thinking = getattr(getattr(choice, "message", None), "reasoning_content", None) if choice else None
                self._last_error = (
                    f"ainvoke_raw 空 content (finish_reason={finish_reason}"
                    f"{'，有 reasoning_content' if thinking else ''})"
                )
            except Exception as exc:
                self._last_error = f"ainvoke_raw 抛错: {exc}"
        try:
            response = await llm.ainvoke(messages)
            text = str(getattr(response, "content", "") or "").strip()
            if text:
                return text
            meta = getattr(response, "response_metadata", None)
            finish_reason = meta.get("finish_reason") if isinstance(meta, dict) else None
            self._last_error = (self._last_error + "；" if self._last_error else "") + (
                f"ainvoke 空 content (finish_reason={finish_reason})"
            )
            return None
        except Exception as exc:
            self._last_error = (self._last_error + "；" if self._last_error else "") + f"ainvoke 抛错: {exc}"
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
        max_tokens: int = _DEFAULT_MAX_TOKENS,
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
            self._last_error = "主系统 LLM 客户端不可用"
            return None
        try:
            llm = await self._ensure_llm()
        except Exception as exc:
            self._last_error = f"获取 LLM 客户端失败: {exc}"
            self._report_failure(summary_kind)
            return None
        messages = self._build_messages(summary_text=summary_text, summary_kind=summary_kind, payload=payload)
        try:
            text = await self._invoke_text(llm, messages)
        except Exception as exc:
            self._last_error = f"调用异常: {exc}"
            text = None
        if not text:
            self._report_failure(summary_kind)
        return text

    def _report_failure(self, summary_kind: str) -> None:
        """把一个场景的失败原因用 info 级报**一次**。

        为什么值得单独做：旧代码把原因写在 debug 级（`_log`），而插件日志是 INFO —— 于是线上只能
        看到 "result=None (LLM 失败/空)"，看不出到底为什么。实测战斗场景 208 次调用全失败、
        弹幕一条都没有，却查不到任何原因。
        """
        kind = str(summary_kind or "general")
        if kind in self._reported_kinds:
            self._log("点评未产出 kind=%s: %s", kind, self._last_error)
            return
        self._reported_kinds.add(kind)
        try:
            self._logger.info("[sts2_catgirl_llm] 点评未产出（本场景首次，后续降到 debug）kind=%s 原因=%s", kind, self._last_error)
        except Exception:
            pass

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
        if scene == "run_review":
            # 局末复盘：玩家亲手打的步里，和插件当时的推荐差得最多的几步（见 choice_review.review_digest）。
            # 要它逐步讲"为什么会那样推荐"，是讨论不是批评；它只是讲解，不会变成教训影响后面的决策。
            base = (
                "下面是玩家亲手打的一局《杀戮尖塔 2》里，他的选择和你当时的推荐差得最多的几步。"
                "逐步讲讲：你为什么会那样推荐、他那样选可能付出了什么代价，或者他的选法有什么道理。"
                "要求：每步一两句，点出层数和具体的牌名或选项；语气是一起复盘，不是批评；"
                "推荐只是参考，没有把握就直说没把握；不要复述整段材料，也不要编材料里没有的信息。"
            )
            persona = self._persona_system()
            system = persona + "\n\n" + base if persona else "你是《杀戮尖塔 2》的直播间陪玩猫娘。\n" + base
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": f"对照：{context}"},
            ]
        if scene == "idle":
            base = (
                "画面暂时没变化，用自己的人设说一句陪伴闲聊，10～25字。"
                "可以轻轻感慨或自言自语，不必加喵；不要重报血量、回合、敌人意图。"
                "没有新事件，不编造出牌、击杀、掉落或胜负，不引用历史连招；"
                "不把等待说成卡死，不催操作、不反问，不复读最近说过的话。只输出这句。"
            )
            persona = self._persona_system()
            system = persona + "\n\n" + base if persona else "你是《杀戮尖塔2》的陪玩猫娘。\n" + base
            recent = payload.get("recent_idle_lines")
            recent = recent[-3:] if isinstance(recent, list) else []
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": f"当前观测（仅作话题背景）：{context}\n避免复读：{' / '.join(str(line)[:32] for line in recent)}"},
            ]
        if scene == "combat":
            # 战斗：prompt 里已带"本轮: 打<卡>→N；结束回合"这类出牌建议（line），要 LLM 用猫娘口吻
            # 把建议转述出来（先打X再补Y/躲Z），而不是只卖萌噤声。放宽字数，但保留弹幕感。
            base = (
                "基于你的人设，用一句短小、猫娘口吻的弹幕点评当前战斗，并自然带出下面的出牌建议。"
                "只可转述材料中明确列出的剩余出牌建议，不能编造或沿用历史里的卡名、连招。"
                "没有出牌建议时只点评当前敌人意图与血线，不猜该出哪张牌。"
                "建议不是执行结果，不能说成已经打出或已经击杀。"
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


__all__ = ["CatgirlCommentGenerator"]
