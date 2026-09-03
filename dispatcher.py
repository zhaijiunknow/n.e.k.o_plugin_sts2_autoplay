# -*- coding: utf-8 -*-
"""STS2 陪玩输出统一边界（镜像 neko_live 的 NekoDispatcher 结构）。

插件所有 ``push_message`` 都从这里走，形成单一输出出口：

- 统一 source / visibility / ai_behavior / priority / coalesce_key / metadata
- catgirl 点评不再经 ``push_message`` 发 NEKO 宿主，改由插件直接走 ``POST /danmaku``
  （文本+头像）进游戏内渲染，因此这里不再做 catgirl 的 prompt 加固。
- fire-and-forget：``plugin.push_message`` 只返回本地提交回执，不返回宿主投递
  确认或猫娘生成的回复文本；调用方不得依赖返回值。
"""

from __future__ import annotations

from typing import Any

SOURCE = "sts2_autoplay"

# 优先级方案（数值越大越优先，单处定义）
PRIORITY_STATUS_FEEDBACK = 5          # 用户主动查询的状态反馈
PRIORITY_COMPANION_MODE_ENABLED = 5   # 陪玩模式开启通知


def _host_reply_text(content: str, *, limit: int = 30) -> str:
    """与旧插件 ``_host_reply_text`` 逐字一致的截断（truncation 测试断言此精确形状）。"""
    text = str(content or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


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

        v2 分支（visibility 或 ai_behavior 非 None）走 visibility/ai_behavior/parts；
        v1 分支（两者皆 None）原样走 message_type/description/content。
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


__all__ = [
    "SOURCE",
    "PRIORITY_STATUS_FEEDBACK",
    "PRIORITY_COMPANION_MODE_ENABLED",
    "STS2Dispatcher",
]
