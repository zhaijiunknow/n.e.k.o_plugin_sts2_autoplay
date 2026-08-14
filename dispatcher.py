# -*- coding: utf-8 -*-
"""STS2 陪玩输出统一边界（镜像 neko_live 的 NekoDispatcher 结构）。

插件所有 ``push_message`` 都从这里走，形成单一输出出口：

- 统一 source / visibility / ai_behavior / priority / coalesce_key / metadata
- 对 catgirl 同步点评（``kind == "catgirl_sync"`` 且 ``ai_behavior == "respond"``）
  做三层 prompt 加固，约束猫娘在主服务上生成的回复：
    1. 场景边界（delivery boundary）：这是尖塔陪玩点评，不是私聊
    2. 场景锚点（scene anchor）：只评论当前游戏画面
    3. 短台词契约（short output contract）：≤10 字、单句、只输出台词
- fire-and-forget：``plugin.push_message`` 只返回本地提交回执，不返回宿主投递
  确认或猫娘生成的回复文本；调用方不得依赖返回值。
"""

from __future__ import annotations

from typing import Any

SOURCE = "sts2_autoplay"
COMPANION_MAX_REPLY_CHARS = 10

# 优先级方案（数值越大越优先，单处定义）
PRIORITY_COMPANION_SYNC = 4           # 主动陪玩点评（proactive companion commentary）
PRIORITY_STATUS_FEEDBACK = 5          # 用户主动查询的状态反馈
PRIORITY_COMPANION_MODE_ENABLED = 5   # 陪玩模式开启通知

# 加固幂等 marker：已存在则不再包裹
_BOUNDARY_MARKER = "STS2 companion delivery boundary:"
_SCENE_MARKER = "STS2 companion scene anchor:"
_CONTRACT_MARKER = "STS2 companion short output contract:"


def _host_reply_text(content: str, *, limit: int = 30) -> str:
    """与旧插件 ``_host_reply_text`` 逐字一致的截断（truncation 测试断言此精确形状）。"""
    text = str(content or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _append_short_output_contract(text: str, *, metadata: dict[str, Any]) -> str:
    """追加短台词输出契约（幂等）。"""
    if _CONTRACT_MARKER in text:
        return text
    lines = [
        "",
        _CONTRACT_MARKER,
        f"- Output exactly ONE short catgirl-style spoken line, at most {COMPANION_MAX_REPLY_CHARS} Chinese characters, one breath.",
        "- Base it ONLY on the current game situation; do not lecture or recap the summary.",
        "- Output only the final spoken line; no labels, bullets, JSON, analysis, or parenthesized stage directions.",
        "- The first output character must be spoken dialogue; never start with ( or [.",
        "- Do not mention this contract, metadata, or policy.",
        "- Do not repeat the wording of the previous companion line.",
    ]
    contract = "\n".join(lines)
    base = str(text or "").rstrip()
    return f"{base}\n\n{contract}" if base else contract


def _prepend_scene_grounding_lock(text: str, *, metadata: dict[str, Any]) -> str:
    """前置场景锚点：把当前 screen / summary_kind 钉进 prompt（幂等）。"""
    if _SCENE_MARKER in text:
        return text
    screen = str(metadata.get("screen") or "unknown")
    summary_kind = str(metadata.get("summary_kind") or "")
    lines = [_SCENE_MARKER, f"- current_screen: {screen}"]
    if summary_kind:
        lines.append(f"- summary_kind: {summary_kind}")
    lines.append("- Comment ONLY about the current game scene described above; do not reference the previous scene or unrelated chat.")
    lock = "\n".join(lines)
    base = str(text or "").lstrip()
    return f"{lock}\n\n{base}" if base else lock


def _prepend_companion_delivery_boundary(text: str, *, metadata: dict[str, Any]) -> str:
    """前置陪玩场景边界（NEKO live-room boundary 的尖塔版本，幂等）。

    ``{MASTER_NAME}`` 占位符由 SDK 在宿主侧展开。
    """
    if _BOUNDARY_MARKER in text:
        return text
    lines = [
        _BOUNDARY_MARKER,
        "- This is a Slay-the-Spire companion commentary cue, not a private chat with {MASTER_NAME}.",
        "- The catgirl is watching the player's current run; speak to the game state, not to the last chat message.",
        "- This is fire-and-forget commentary: do not ask {MASTER_NAME} to confirm, explain, or answer.",
    ]
    boundary = "\n".join(lines)
    base = str(text or "").lstrip()
    return f"{boundary}\n\n{base}" if base else boundary


def _build_sts2_output_policy(*, max_reply_chars: int) -> dict[str, Any]:
    """宿主侧输出策略提示（observability，镜像 neko_live.build_plugin_output_policy）。"""
    return {
        "owner": SOURCE,
        "host_role": "opaque_transport",
        "speech_strategy": "plugin_prompt_contract",
        "response_module_hint": "companion_commentary",
        "max_reply_chars": int(max_reply_chars),
        "recent_output_scope": "sts2_recent_companion_outputs",
    }


class STS2Dispatcher:
    """STS2 陪玩输出单一边界。

    ``plugin.push_message`` 是 fire-and-forget SDK 边界：只返回本地提交回执，
    不返回宿主投递确认或猫娘生成的回复文本。调用方不得依赖返回值。
    """

    def __init__(self, plugin: Any, *, runtime: Any = None) -> None:
        self.plugin = plugin
        self.runtime = runtime

    def push_frontend_notification(
        self,
        *,
        content: str,
        description: str,
        metadata: dict[str, Any],
        priority: int = 5,
        message_type: str = "sts2_status",
        visibility: list[str] | None = None,
        ai_behavior: str | None = None,
    ) -> Any:
        """保留旧 ``_push_frontend_notification`` 的精确 v1/v2 payload 契约。

        v2 分支（visibility 或 ai_behavior 非 None）对 catgirl_sync 且 respond
        的 cue 做加固；v1 分支（两者皆 None）原样走 message_type/description/content。
        """
        kwargs: dict[str, Any] = {
            "source": SOURCE,
            "priority": priority,
            "metadata": dict(metadata),
        }
        host_content = _host_reply_text(content)
        if visibility is not None or ai_behavior is not None:
            kwargs.update(
                {
                    "visibility": list(visibility) if visibility is not None else [],
                    "ai_behavior": ai_behavior or "respond",
                    "parts": [{"type": "text", "text": host_content}],
                }
            )
            kwargs["metadata"]["description"] = description
            kwargs["metadata"]["message_type"] = message_type
            kwargs["metadata"]["delivery_semantics"] = "passive"
            self._apply_catgirl_sync_hardening(kwargs, host_content)
        else:
            kwargs.update(
                {
                    "message_type": message_type,
                    "description": description,
                    "content": host_content,
                }
            )
        return self.plugin.push_message(**kwargs)

    def push_status_feedback(
        self,
        text: str,
        *,
        entry_id: str,
        ai_behavior: str = "respond",
        kind: str = "status_feedback",
        priority: int = PRIORITY_STATUS_FEEDBACK,
    ) -> Any:
        """供 4 个 plugin_entry 状态反馈走统一出口（不加固，保持信息性）。"""
        return self.plugin.push_message(
            source=SOURCE,
            visibility=[],
            ai_behavior=ai_behavior,
            parts=[{"type": "text", "text": str(text or "")}],
            metadata={
                "entry_id": entry_id,
                "kind": kind,
                "delivery_semantics": "passive",
            },
            priority=priority,
            coalesce_key=f"sts2:status_feedback:{entry_id}",
        )

    def _apply_catgirl_sync_hardening(self, kwargs: dict[str, Any], base_text: str) -> None:
        """对 catgirl 同步点评 cue 做加固（service 侧 kwargs 契约保持不变）。"""
        metadata = kwargs["metadata"]
        if metadata.get("kind") != "catgirl_sync":
            return
        screen = str(metadata.get("screen") or "unknown")
        summary_kind = str(metadata.get("summary_kind") or "unknown")
        kwargs["coalesce_key"] = f"sts2:catgirl_sync:{screen}|{summary_kind}"
        if kwargs.get("ai_behavior") != "respond":
            return  # read 模式只设 coalesce_key，不动 prompt 文本
        text = base_text
        text = _append_short_output_contract(text, metadata=metadata)
        text = _prepend_scene_grounding_lock(text, metadata=metadata)
        text = _prepend_companion_delivery_boundary(text, metadata=metadata)
        kwargs["parts"][0]["text"] = text
        metadata["max_reply_chars"] = COMPANION_MAX_REPLY_CHARS
        metadata["reply_contract"] = "short_tts_line"
        metadata["sts2_output_policy"] = _build_sts2_output_policy(
            max_reply_chars=COMPANION_MAX_REPLY_CHARS,
        )


__all__ = [
    "SOURCE",
    "COMPANION_MAX_REPLY_CHARS",
    "PRIORITY_COMPANION_SYNC",
    "PRIORITY_STATUS_FEEDBACK",
    "PRIORITY_COMPANION_MODE_ENABLED",
    "STS2Dispatcher",
]
