# -*- coding: utf-8 -*-
"""STS2 陪玩点评 → 弹幕浮层 桥接。

把点评文本推送进插件 SSE 弹幕流（POST /plugin/sts2_autoplay/ui-api/push），
web 弹幕页（static/display.html）与 Qt 透明窗（qt_overlay.py）订阅同一条
SSE（/ui-api/events）即可持续滚动显示。

样式：``style="catgirl"`` 表示猫娘角色弹幕（带头像，头像取自主 server
card-drop 快照的当前猫娘头像）；``style="narration"`` 表示社区旁白弹幕（纯文本）。

- ``push_text()`` 是同步入口（service 里 sync def 直接调），内部去重后调度异步 POST
- 去重窗口与 qt_overlay.py 同思路：精确内容 + TTL（按 style+text 去重）
- 无运行事件循环（如单测同步上下文）时静默跳过调度，不抛错
- ``post_async`` 可注入（测试用），签名 ``post_async(payload: dict)``；
  不注入时用 httpx.AsyncClient 打本插件 server
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from typing import Any, Awaitable, Callable

# 秒级去重（与 qt_overlay.py 一致）
DEDUP_WINDOW = 30
DEDUP_TTL_MS = 30000

# 插件 HTTP server 默认端口（config/network.py USER_PLUGIN_SERVER_PORT 默认值）
DEFAULT_PLUGIN_PORT = 48916
# 主 server 默认端口（config/network.py MAIN_SERVER_PORT 默认值，取猫娘头像用）
DEFAULT_MAIN_PORT = 48911
PUSH_TIMEOUT = 3.0
# 猫娘头像缓存时长（秒）
AVATAR_CACHE_TTL_SEC = 120.0
# 顶部弹幕：标准模式下旁白弹幕置顶概率（对齐 DanmakuSpire BaseTopProbability 默认 0.15）
TOP_MODES = ("none", "standard", "all")
DEFAULT_TOP_PROBABILITY = 0.15

PostFn = Callable[[dict[str, Any]], Awaitable[Any]]


def _plugin_server_base() -> str:
    """解析插件 HTTP server 地址（镜像 config/network.py:_read_port_env）。"""
    for key in ("NEKO_USER_PLUGIN_SERVER_PORT", "USER_PLUGIN_SERVER_PORT"):
        raw = os.environ.get(key)
        if raw:
            try:
                port = int(raw)
            except ValueError:
                port = 0
            if 0 < port <= 65535:
                return f"http://127.0.0.1:{port}"
    return f"http://127.0.0.1:{DEFAULT_PLUGIN_PORT}"


def _main_server_port() -> int:
    """解析主 server 端口（取猫娘头像用）。"""
    for key in ("NEKO_MAIN_SERVER_PORT", "MAIN_SERVER_PORT"):
        raw = os.environ.get(key)
        if raw:
            try:
                port = int(raw)
            except ValueError:
                port = 0
            if 0 < port <= 65535:
                return port
    return DEFAULT_MAIN_PORT


class STS2DanmuBridge:
    """把点评文本桥接到插件 SSE 弹幕流。"""

    def __init__(
        self,
        logger: Any,
        *,
        base_url: str | None = None,
        plugin_id: str = "sts2_autoplay",
        enabled: bool = True,
        post_async: PostFn | None = None,
        top_mode: str = "standard",
        top_probability: float = DEFAULT_TOP_PROBABILITY,
        dedup_enabled: bool = False,
    ) -> None:
        self._logger = logger
        self._push_url = f"{base_url or _plugin_server_base()}/plugin/{plugin_id}/ui-api/push"
        self.enabled = bool(enabled)
        self.dedup_enabled = bool(dedup_enabled)
        self._post_async = post_async
        self._dedup: dict[str, float] = {}
        self._dedup_order: list[str] = []
        self._avatar_cache: str = ""
        self._avatar_cache_at: float = 0.0
        # 顶部弹幕模式：none=全部滚动 / standard=旁白按概率置顶 / all=全部置顶
        self.top_mode = top_mode if top_mode in TOP_MODES else "standard"
        self.top_probability = float(top_probability)

    def _decide_placement(self, style: str) -> str:
        """按顶部弹幕模式决定 placement：top / scrolling。"""
        if self.top_mode == "all":
            return "top"
        if self.top_mode == "standard" and style == "narration":
            return "top" if random.random() < self.top_probability else "scrolling"
        return "scrolling"

    def push_text(
        self,
        text: str,
        *,
        style: str = "narration",
        placement: str | None = None,
        delay_seconds: float = 0.0,
    ) -> bool:
        """同步入口：去重 → 调度异步广播。返回是否入队。

        style：``narration``（社区旁白，无头像）或 ``catgirl``（猫娘角色弹幕，带头像）。
        placement：``scrolling``（横向滚动）/ ``top``（顶部置顶）；None 时按 top_mode 决策。
        delay_seconds：延迟推送（秒），对齐 mod 的分批发射节奏。
        """
        content = re.sub(r"\s+", " ", str(text or "")).strip()
        if not content or not self.enabled:
            return False
        if placement is None:
            placement = self._decide_placement(style)
        if self.dedup_enabled:
            # 去重（默认关闭）：同一 style+placement+text 在 TTL 内不重复推送
            dedup_key = f"{style}|{placement}|{content}"
            now_ms = time.time() * 1000.0
            if dedup_key in self._dedup and now_ms - self._dedup[dedup_key] < DEDUP_TTL_MS:
                return False
            self._dedup[dedup_key] = now_ms
            self._dedup_order.append(dedup_key)
            if len(self._dedup_order) > DEDUP_WINDOW:
                self._dedup.pop(self._dedup_order.pop(0), None)
        self._schedule(content, style, placement, float(delay_seconds or 0.0))
        return True

    def _schedule(self, content: str, style: str, placement: str, delay_seconds: float = 0.0) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 同步/无 loop 上下文（如单测）静默跳过
        if delay_seconds > 0:
            loop.create_task(self._broadcast_delayed(content, style, placement, delay_seconds))
        else:
            loop.create_task(self._broadcast(content, style, placement))

    async def _broadcast_delayed(self, content: str, style: str, placement: str, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(delay_seconds)
        except Exception:
            return
        await self._broadcast(content, style, placement)

    async def get_avatar(self) -> str:
        """获取当前猫娘头像 dataUrl（主 server card-drop 快照，带缓存）。"""
        now = time.time()
        if self._avatar_cache and now - self._avatar_cache_at < AVATAR_CACHE_TTL_SEC:
            return self._avatar_cache
        avatar = ""
        try:
            import httpx

            url = f"http://127.0.0.1:{_main_server_port()}/api/card-drop/active-character"
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url,
                    params={"include_avatar": "true"},
                    timeout=PUSH_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
            avatar = self._downscale_avatar(str(data.get("dataUrl") or ""))
        except Exception as exc:  # 拿不到头像不阻断弹幕
            try:
                self._logger.debug("[sts2_danmu] 获取猫娘头像失败: %s", exc)
            except Exception:
                pass
        if avatar:
            self._avatar_cache = avatar
            self._avatar_cache_at = now
        return avatar

    @staticmethod
    def _downscale_avatar(data_url: str, *, max_edge: int = 96) -> str:
        """把 base64 头像缩到小尺寸，避免 SSE payload 超限；失败返回原样。"""
        if not data_url or "," not in data_url:
            return data_url
        try:
            import base64
            import io

            from PIL import Image

            header, b64 = data_url.split(",", 1)
            image = Image.open(io.BytesIO(base64.b64decode(b64)))
            if image.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                if image.mode == "P":
                    image = image.convert("RGBA")
                alpha = image.getchannel("A") if image.mode in ("RGBA", "LA") else None
                background.paste(image.convert("RGBA"), mask=alpha)
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            if max(image.size) > max_edge:
                image.thumbnail((max_edge, max_edge))
            buf = io.BytesIO()
            image.save(buf, format="PNG", optimize=True)
            return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
        except Exception:
            return data_url

    async def _broadcast(self, content: str, style: str, placement: str) -> None:
        """异步推送一条弹幕到插件 SSE 流。失败不阻断主流程。"""
        try:
            payload: dict[str, Any] = {"type": "danmu", "text": content, "style": style, "placement": placement}
            if style == "catgirl":
                avatar = await self.get_avatar()
                if avatar:
                    payload["avatar"] = avatar
            if self._post_async is not None:
                await self._post_async(payload)
                return
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._push_url,
                    json=payload,
                    timeout=PUSH_TIMEOUT,
                )
                resp.raise_for_status()
        except Exception as exc:  # 推送失败只记 debug，不影响点评主链路
            try:
                self._logger.debug("[sts2_danmu] 弹幕推送失败: %s", exc)
            except Exception:
                pass

    def push_status(self, *, data: dict[str, Any]) -> bool:
        """同步入口：推送一条游戏状态事件（type=game_status）到插件 SSE 流。

        供「当前游戏信息状态」监控面板使用；复用 push 通道与鉴权，无弹幕去重/placement/头像。
        游戏信息 JSON 直接放 text 字段（push 路由白名单保留 type/text），前端按 type 过滤后解析。
        """
        if not self.enabled:
            return False
        if not isinstance(data, dict) or not data:
            return False
        content = json.dumps(data, ensure_ascii=False)
        payload: dict[str, Any] = {"type": "game_status", "text": content}
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False  # 同步/无 loop 上下文（如单测）静默跳过
        loop.create_task(self._broadcast_status(payload))
        return True

    async def _broadcast_status(self, payload: dict[str, Any]) -> None:
        """异步推送一条状态事件到插件 SSE 流。失败只记 debug。"""
        try:
            if self._post_async is not None:
                await self._post_async(payload)
                return
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._push_url,
                    json=payload,
                    timeout=PUSH_TIMEOUT,
                )
                resp.raise_for_status()
        except Exception as exc:  # 推送失败只记 debug，不影响点评主链路
            try:
                self._logger.debug("[sts2_danmu] 状态推送失败: %s", exc)
            except Exception:
                pass


__all__ = ["STS2DanmuBridge", "_plugin_server_base", "_main_server_port"]
