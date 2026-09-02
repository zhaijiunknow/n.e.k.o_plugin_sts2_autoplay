"""Verifies the plugin's SSE event consumption (transport_client.subscribe_events) against a mock mod
/events/stream server, without needing the game running. Emits the same frame shape the mod produces
(id/event/data + comments + heartbeats) and asserts we yield exactly the scene-change envelopes."""
from __future__ import annotations

import asyncio
import json

from transport_client import STS2TransportClient, STS2TransportError


def _frame(event: str, payload: dict, event_id: int) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(payload)}\n\n"


def _heartbeat() -> str:
    return ": heartbeat\n\n"


async def _serve_and_collect() -> list[str]:
    frames = [
        _heartbeat(),  # comment — must be skipped
        _frame("stream_ready", {"type": "stream_ready", "event_id": 1, "timestamp_utc": "t", "data": {"screen": "MAP", "in_combat": False}}, 1),
        _frame("screen_changed", {"type": "screen_changed", "event_id": 2, "timestamp_utc": "t", "data": {"from": "MAP", "to": "COMBAT"}}, 2),
        _heartbeat(),  # mid-stream heartbeat — skipped
        _frame("combat_started", {"type": "combat_started", "event_id": 3, "timestamp_utc": "t", "data": {"turn": 1}}, 3),
        _frame("available_actions_changed", {"type": "available_actions_changed", "event_id": 4, "timestamp_utc": "t", "data": {"actions": ["play_card"]}}, 4),
    ]

    handler_started = asyncio.Event()

    async def handler(reader, writer):
        handler_started.set()
        try:
            # A real SSE response: status line + headers, then the event frames. httpx won't hand the
            # stream to aiter_lines() until it sees the HTTP response headers.
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Connection: keep-alive\r\n"
                b"\r\n"
            )
            await writer.drain()
            for f in frames:
                writer.write(f.encode("utf-8"))
                await writer.drain()
                await asyncio.sleep(0.01)
            # Hold the connection open briefly so subscribe_events observes the EOF cleanly.
            await asyncio.sleep(0.3)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = STS2TransportClient(f"http://127.0.0.1:{port}", connect_timeout=3.0, request_timeout=10.0)

    got: list[str] = []
    try:
        async for envelope in client.subscribe_events():
            got.append(envelope["type"])
    except STS2TransportError:
        pass
    finally:
        server.close()
        await server.wait_closed()

    return got


def test_subscribe_yields_scene_events_without_duplicates() -> None:
    got = asyncio.run(_serve_and_collect())
    # Exactly the real scene-change envelopes; comments/heartbeats dropped; no duplicates.
    assert got == ["stream_ready", "screen_changed", "combat_started", "available_actions_changed"], got


if __name__ == "__main__":
    print("events yielded:", asyncio.run(_serve_and_collect()))
