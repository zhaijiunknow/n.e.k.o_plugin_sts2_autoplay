"""mod 靠"带标识的请求 + 租约"判断当前有没有外部控制者，从而区分两种模式：

  * 插件控制（NEKO 连上）-> 地图按 coop_map_policy（默认 wait，彻底等玩家发话）
  * mod 独自运行（插件不在）-> 地图自己跑，不会永久等待

这个请求头是跨语言契约：mod 侧的常量是 `NekoComm.Game.ExternalControl.HeaderName`
（`game_mod/nekospire/Game/ExternalControl.cs`），插件侧统一在 `transport_client` 里附加。
任何一侧改名都会让 mod 悄悄退回"独自运行"，所以这里把名字钉死。
"""

from __future__ import annotations

import asyncio
import json

from plugin.plugins.sts2_autoplay.transport_client import STS2TransportClient

# 与 C# 侧 ExternalControl.HeaderName 对应，比较时统一小写。
EXPECTED_CONTROL_HEADER = "x-neko-external-control"
EXPECTED_CLIENT_HEADER = "x-neko-client"


def _header_map(request_text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in request_text.split("\r\n")[1:]:
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return headers


async def _capture_request(call) -> str:
    """跑一次真实 HTTP 往返，返回服务端收到的原始请求文本（含请求行与头）。"""
    state: dict[str, str] = {}
    finished = asyncio.Event()

    async def handler(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            # 把 body 也读完，否则 httpx 可能因对端提前关闭而报错。
            length = 0
            for line in head.decode("latin-1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip() or 0)
            if length:
                await reader.readexactly(length)
            state["raw"] = head.decode("latin-1")
            body = json.dumps({"ok": True, "request_id": "t", "data": {}}).encode("utf-8")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            finished.set()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = STS2TransportClient(f"http://127.0.0.1:{port}", connect_timeout=3.0, request_timeout=10.0)
    try:
        await call(client)
        await asyncio.wait_for(finished.wait(), timeout=5.0)
    finally:
        server.close()
        await server.wait_closed()
    return state.get("raw", "")


async def _get_state(client: STS2TransportClient) -> None:
    await client.get_state()


async def _post_action(client: STS2TransportClient) -> None:
    await client.execute_action("choose_map_node", option_index=1)


async def _post_config(client: STS2TransportClient) -> None:
    await client.set_config(coop_map_policy="wait")


def test_get_state_carries_the_external_control_header() -> None:
    headers = _header_map(asyncio.run(_capture_request(_get_state)))
    assert headers.get(EXPECTED_CONTROL_HEADER) == "1"
    assert headers.get(EXPECTED_CLIENT_HEADER) == "sts2_autoplay"


def test_post_action_carries_the_external_control_header() -> None:
    headers = _header_map(asyncio.run(_capture_request(_post_action)))
    assert headers.get(EXPECTED_CONTROL_HEADER) == "1"


def test_post_config_carries_the_external_control_header() -> None:
    headers = _header_map(asyncio.run(_capture_request(_post_config)))
    assert headers.get(EXPECTED_CONTROL_HEADER) == "1"


async def _get_health_with_custom_client(client: STS2TransportClient) -> None:
    await client._request("GET", "/health", headers={"X-Neko-Client": "someone-else"})


def test_caller_supplied_headers_win_over_the_defaults() -> None:
    """调用方自带 headers 时不能被默认标识头覆盖，但外部控制标识必须仍在。"""
    headers = _header_map(asyncio.run(_capture_request(_get_health_with_custom_client)))
    assert headers.get(EXPECTED_CLIENT_HEADER) == "someone-else"
    assert headers.get(EXPECTED_CONTROL_HEADER) == "1"
