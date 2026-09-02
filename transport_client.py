from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict

import httpx


class STS2TransportError(RuntimeError):
    pass


class STS2TransportClient:
    def __init__(self, base_url: str, *, connect_timeout: float = 5.0, request_timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout

    async def close(self) -> None:
        return None

    async def health(self) -> Dict[str, Any]:
        return await self._request("GET", "/health")

    async def get_state(self) -> Dict[str, Any]:
        return await self._request("GET", "/state")

    async def get_available_actions(self) -> Dict[str, Any]:
        return await self._request("GET", "/actions/available")

    async def get_combat_plan(self) -> Dict[str, Any]:
        """Read the mod's authoritative combat solver plan (GET /solver/plan).

        Returns the SolverPlanPayload (in_combat/action/card_index/card_id/target_index/line),
        or {"in_combat": false, "reason": "..."} when not in combat.
        """
        return await self._request("GET", "/solver/plan")

    async def execute_action(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        return await self._request("POST", "/action", json=self._build_action_payload(action, **kwargs))

    async def subscribe_events(self) -> AsyncIterator[Dict[str, Any]]:
        """Stream the mod's scene-change events (GET /events/stream, server-sent events).

        Yields one dict per SSE event — the parsed GameEventEnvelope (type / event_id /
        timestamp_utc / data) — as it arrives, so callers react to the scene changing instead of
        polling /state. Raise STS2TransportError on a transport/HTTP failure so the caller can
        reconnect; the stream is long-lived, so browse it with ``async for``.
        """
        url = f"{self.base_url}/events/stream"
        timeout = httpx.Timeout(connect=self.connect_timeout, read=60.0, write=60.0, pool=self.connect_timeout)
        event_name: str | None = None
        data_lines: list[str] = []

        def _flush() -> Dict[str, Any] | None:
            nonlocal event_name, data_lines
            if not data_lines:
                return None
            text = "\n".join(data_lines)
            data_lines = []
            try:
                payload = json.loads(text)
            except ValueError:
                return None
            if not isinstance(payload, dict):
                return None
            if event_name and not payload.get("type"):
                payload["type"] = event_name
            event_name = None
            return payload

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise STS2TransportError(
                            f"events stream HTTP {response.status_code}: {body[:120]!r}"
                        )
                    async for raw_line in response.aiter_lines():
                        line = raw_line.rstrip("\r")
                        if not line:
                            # Blank line terminates the current SSE event (if any).
                            flushed = _flush()
                            if flushed is not None:
                                yield flushed
                            continue
                        if line.startswith(":"):
                            # Comment / heartbeat; carries no event.
                            continue
                        if line.startswith("id:"):
                            # id is also mirrored inside the JSON envelope; ignore the raw field.
                            continue
                        if line.startswith("event:"):
                            event_name = line[len("event:"):].strip()
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[len("data:"):].lstrip(" "))
        except STS2TransportError:
            raise
        except httpx.HTTPError as exc:
            raise STS2TransportError(f"events stream failed: {exc}") from exc

    async def push_danmaku(self, text: str, *, style: str = "catgirl", placement: str = "scrolling", avatar: str | None = None) -> Dict[str, Any]:
        """Push a catgirl danmaku line to the in-game overlay (POST /danmaku).

        `avatar` is an optional base64 image (with or without a data:...;base64, prefix) rendered next to
        the catgirl text in-game.
        """
        payload: Dict[str, Any] = {"text": text, "style": style, "placement": placement}
        if avatar:
            payload["avatar"] = avatar
        return await self._request("POST", "/danmaku", json=payload)

    def _build_action_payload(self, action_name: str, **kwargs: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"action": action_name}
        for key, value in kwargs.items():
            if value is None or key in {"type", "action"}:
                continue
            payload[key] = value
        return payload

    async def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        timeout = httpx.Timeout(
            connect=self.request_timeout if self.connect_timeout <= 0 else self.connect_timeout,
            read=self.request_timeout,
            write=self.request_timeout,
            pool=self.request_timeout,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.request(method, url, **kwargs)
        except RuntimeError as exc:
            if "Event loop is closed" in str(exc):
                raise STS2TransportError("事件循环已关闭，无法完成 STS2 请求") from exc
            raise STS2TransportError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise STS2TransportError(f"无法连接 STS2-Agent: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise STS2TransportError(f"STS2-Agent 返回了无效 JSON: {url}") from exc

        if not isinstance(payload, dict):
            raise STS2TransportError(f"STS2-Agent 返回了非对象 JSON: {url}")

        if response.status_code >= 400 or payload.get("ok") is False:
            error = payload.get("error")
            if isinstance(error, dict):
                raise STS2TransportError(str(error.get("message") or error.get("code") or f"HTTP {response.status_code}"))
            raise STS2TransportError(f"STS2-Agent 请求失败: HTTP {response.status_code}")

        data = payload.get("data")
        return data if isinstance(data, dict) else {"value": data}


__all__ = ["STS2TransportClient", "STS2TransportError"]
