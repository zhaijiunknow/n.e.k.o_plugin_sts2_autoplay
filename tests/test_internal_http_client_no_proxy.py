"""防回归门：插件的本机 HTTP 客户端**绝不能被系统/环境代理劫持**。

## 为什么需要这个门

实测缺陷（2026-10-19）：本机开着 FlClash 时

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings
      ProxyEnable   = 1
      ProxyServer   = 127.0.0.1:7890
      ProxyOverride = ...;localhost;*.local;127.*;10.*;172.16.*;...

系统本意是让 ``127.*`` **直连**（``ProxyOverride`` 里明明有 ``127.*``），
但 **httpx 不读那个绕过列表**（它只认 ``NO_PROXY`` 环境变量）。后果：

    default(trust_env=True) -> HTTP 502  len=0        ← 被丢给 7890，代理返回 502
    trust_env=False         -> HTTP 200  ok=True

502 + 空 body ⇒ ``_request`` 抛「STS2-Agent 返回了无效 JSON」⇒ 整个生产通路瘫掉。
这不是测试环境问题，是**真实生产缺陷**，所以必须钉住语义而不是掩盖。

## 为什么用**环境变量**注入坏代理，而不是注册表

注册表级代理在 CI / 其他机器上**不存在**，用它写的门会永远绿 = 等于没有门。
``HTTP_PROXY`` / ``HTTPS_PROXY`` 是 httpx 在 ``trust_env=True`` 下会读的代理来源
（``utils/http/internal_client.py:102`` 也是这么注释的），跨平台可复现。
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from plugin.plugins.sts2_autoplay.transport_client import STS2TransportClient

# 必然连不通的代理：端口 1 上没有任何服务在听。
# 用它当"坏代理"，只要客户端**真的**走了代理就必然失败 —— 这样断言"仍成功"
# 才有鉴别力（若客户端压根不读代理，也会成功，但那正是我们要的语义）。
DEAD_PROXY = "http://127.0.0.1:1"


async def _serve_once() -> tuple[str, asyncio.AbstractServer, asyncio.Event]:
    """起一个只回一次 200 JSON 的本机 server，返回 (base_url, server, finished)。"""
    finished = asyncio.Event()

    async def handler(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in head.decode("latin-1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip() or 0)
            if length:
                await reader.readexactly(length)
            body = json.dumps(
                {"ok": True, "request_id": "proxy-gate", "data": {"run_id": "PROXY-GATE"}}
            ).encode("utf-8")
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
    return f"http://127.0.0.1:{port}", server, finished


def _inject_dead_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """把代理指向必然连不通的地址，并**清掉** NO_PROXY（否则门会被自己绕过）。"""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, DEAD_PROXY)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)


def test_get_state_survives_a_dead_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 核心防回归门：注入必然失败的代理，**生产** ``STS2TransportClient.get_state()`` 仍须成功。

    走的是生产 ``_request`` 通路（不是另起一个 httpx 客户端），
    因此任何"又回去用裸 ``httpx.AsyncClient(trust_env=True)``"的改动都会让这条转红。
    """
    _inject_dead_proxy(monkeypatch)

    async def go() -> dict:
        base_url, server, finished = await _serve_once()
        try:
            client = STS2TransportClient(base_url, connect_timeout=3.0, request_timeout=10.0)
            data = await client.get_state()
            await asyncio.wait_for(finished.wait(), timeout=5.0)
            return data
        finally:
            server.close()
            await server.wait_closed()

    data = asyncio.run(go())
    assert data.get("run_id") == "PROXY-GATE", (
        "本机 mod 请求被系统/环境代理劫持了："
        f"HTTP_PROXY={os.environ.get('HTTP_PROXY')} 下未能拿到本机 server 的响应，得到 {data!r}"
    )


def test_post_path_survives_a_dead_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_request`` 是 get/post 的唯一通路，这条单独钉住 POST 分支。"""
    _inject_dead_proxy(monkeypatch)

    async def go() -> dict:
        base_url, server, finished = await _serve_once()
        try:
            client = STS2TransportClient(base_url, connect_timeout=3.0, request_timeout=10.0)
            data = await client.execute_action("end_turn")
            await asyncio.wait_for(finished.wait(), timeout=5.0)
            return data
        finally:
            server.close()
            await server.wait_closed()

    assert asyncio.run(go()).get("run_id") == "PROXY-GATE"


def test_shared_internal_client_disables_env_and_proxy() -> None:
    """直接钉住 client 的构造属性 —— 比"跑一次请求"更早失败、定位更准。

    ``trust_env=False`` ⇒ httpx 不读 ``HTTP_PROXY``/``NO_PROXY``；
    ``proxy=None`` ⇒ 也不显式指定代理。两者缺一都可能让 127.0.0.1 走代理。
    """
    from utils.internal_http_client import get_internal_http_client

    async def go():
        # ★ 不得 aclose()：这是按 event loop 共享、由宿主管理生命周期的 client。
        return get_internal_http_client()

    client = asyncio.run(go())
    assert getattr(client, "_trust_env", None) is False, (
        "共享 internal client 的 trust_env 必须为 False，否则本机请求会被环境代理劫持"
    )


def test_no_proxy_mount_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 最强判据：``client._mounts`` 里**没有**代理 transport。

    httpx 在 ``trust_env=True`` 且环境/注册表有代理时，会往 ``_mounts`` 里挂一个
    针对具体 scheme 的代理 transport；**没有任何 proxy mount** 才是"绝不可能走代理"的
    结构性证明 —— 它比"某次请求恰好返回 200"强得多（后者可能因目标恰好可达而蒙对）。

    实测对照（本机开着 FlClash，``HTTP_PROXY`` 注入 ``http://127.0.0.1:1``）：
        trust_env=True   -> len(client._mounts) == 2     ← 代理 transport 已挂上
        trust_env=False  -> len(client._mounts) == 0     ← 无代理 transport
    """
    from utils.internal_http_client import get_internal_http_client

    # 注入坏代理，"有代理时必然挂 mount"这个前提才成立。
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, DEAD_PROXY)

    async def go():
        return get_internal_http_client()

    client = asyncio.run(go())
    mounts = getattr(client, "_mounts", None)
    assert mounts is not None, "httpx 内部结构变了：client._mounts 不存在，请更新本门"
    assert len(mounts) == 0, (
        f"共享 internal client 挂了 {len(mounts)} 个 transport mount "
        "（注入 HTTP_PROXY 后应为 0 —— 非 0 说明代理 transport 被装上了，"
        "本机 127.0.0.1 请求会被劫持）"
    )


def test_client_construction_does_not_install_a_proxy_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对照实验：**证明上面的 0-mount 断言有鉴别力**。

    直接构造一个 ``trust_env=True`` 的 client，在同样的坏代理注入下它**必须**
    挂上代理 transport。若这条也变成 0，说明环境里读不到代理、上面的门就失去意义了 ——
    所以这条让"门是否还有鉴别力"本身可被检测。

    ★ 必须用 monkeypatch（而非直接改 os.environ）：直接改会**泄漏**到同一 session 的
      其他用例。实测踩过：泄漏后 `test_get_state_survives_a_dead_proxy` 等 2 条
      因为环境里残留代理而转红 —— 那是我自己的测试互相污染，不是产品缺陷。
    """
    import httpx

    monkeypatch.setenv("HTTP_PROXY", DEAD_PROXY)
    monkeypatch.setenv("HTTPS_PROXY", DEAD_PROXY)

    async def go() -> int:
        async with httpx.AsyncClient(timeout=3.0) as c:
            return len(c._mounts)

    n = asyncio.run(go())
    assert n > 0, (
        "对照实验失败：trust_env=True 的 client 竟然也没挂代理 transport ⇒ "
        "环境里读不到代理 ⇒ 上面那条 0-mount 断言失去鉴别力（不是被修好了，而是测不出来）"
    )


def test_no_unguarded_httpx_client_construction_remains() -> None:
    """静态门：``transport_client.py`` / ``service.py`` 不得再有裸 ``httpx.AsyncClient(...)`` 构造。

    上面几条运行时门只覆盖**当前**走到的分支；这条用静态扫描兜住"新加一处构造点"的回归
    （例如以后有人在别的函数里为了省事又写了一个）。
    允许保留的只有类型注解与注释（都不含 ``(``）。
    """
    import re
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent
    offenders: list[str] = []
    for name in ("transport_client.py", "service.py"):
        text = (here / name).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # 只抓真正的**构造**：httpx.AsyncClient( 或 httpx.Client(
            if re.search(r"httpx\.(Async)?Client\s*\(", line):
                offenders.append(f"{name}:{i}: {stripped}")
    assert not offenders, (
        "发现无防护的 httpx 客户端构造点（应改用 utils.internal_http_client."
        "get_internal_http_client()，它带 proxy=None + trust_env=False）：\n  "
        + "\n  ".join(offenders)
    )


def test_shared_client_is_not_closed_by_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 共享 client **不得被 `async with` 包裹 / 不得 aclose()**（契约明令）。

    踩过的坑：`async with get_internal_http_client() as c:` 语法完全合法、
    **第一条请求照样成功**，但退出时会 `aclose()` 掉这个按 event loop 共享的 client。
    实测（把 `_request` 改成 async with 后）：

        before is_closed = False
        get_state ok, run_id = Q9KDZQN6N7CX      ← 第一条请求成功 ⇒ "只跑一次"的门抓不到
        after  is_closed = True                  ← 已被关掉
        reuse: RuntimeError: Cannot send a request, as the client has been closed.

    ★ 门必须这样写才有效（实测踩过两版无效的）：
      1. **同一次 `asyncio.run` 内**取 reference。宿主按 event loop 存 client
         （`_clients_by_loop`），跨 `asyncio.run` 会换 loop ⇒ 拿到的是别的对象。
      2. 断言**手里那个 reference** 的 `is_closed`。**不能**重新调
         `get_internal_http_client()` 之后再断言 —— 那个函数会自愈
         （`internal_client.py:91-94` 检测到 `is_closed` 就新建一个），
         于是重新取到的永远是"健康的"，把真实损坏掩盖掉。
         我第一版就是这么写的，所以变异体 P7 仍绿 ⇒ **改掉**。
    """
    _inject_dead_proxy(monkeypatch)

    async def go() -> tuple[bool, Exception | None]:
        from utils.internal_http_client import get_internal_http_client

        # ① 拿一次 reference（**同一个 loop 内**），并确认它是健康的。
        ref = get_internal_http_client()
        assert not ref.is_closed, "共享 client 在测试开始时就已经是关的"

        # ② 走一次生产请求通路 —— 若生产代码用 async with，ref 会被关掉。
        base_url, server, finished = await _serve_once()
        try:
            client = STS2TransportClient(base_url, connect_timeout=3.0, request_timeout=10.0)
            await client.get_state()
            await asyncio.wait_for(finished.wait(), timeout=5.0)
        finally:
            server.close()
            await server.wait_closed()

        # ③ 断言**手里那个 reference** 没被关（不要重新取！见 docstring）。
        closed = ref.is_closed

        # ④ 并且它必须真的还能用。
        #
        # ★★ 2026-10-20 修（t43 副产物，队长实测定位）：这里原本硬编码请求
        #    `http://127.0.0.1:18080/health` —— 那是**游戏 mod 服务的真实端口**。
        #    后果：这条"单元"测试在**游戏没开**时必然失败（连接被拒 ⇒ err 非 None
        #    ⇒ 下面 :296 的断言挂），而在游戏开着时通过。
        #    实测时间线完全吻合：
        #      15:45 全量 ⇒ 1352 passed / 0 failed（当时游戏在跑）
        #      16:03 之后游戏关闭 ⇒ 此后每次全量/单独跑都在这条上失败
        #    我一开始误判成"顺序依赖"，是错的 —— 真相是 **non-hermetic：依赖外部进程**。
        #    （同类缺陷：t21「反思测试读实时记忆库」；判据：单元测试不得依赖本机以外的状态。）
        #    ⇒ 修法：改用本进程内的一次性 stub server。**断言强度不变**（仍要求
        #      `ref` 在被生产请求用过之后依然可用），但不再依赖游戏是否运行。
        err: Exception | None = None
        try:
            base_url2, server2, finished2 = await _serve_once()
            try:
                await ref.get(f"{base_url2}/health", timeout=5.0)
                await asyncio.wait_for(finished2.wait(), timeout=5.0)
            finally:
                server2.close()
                await server2.wait_closed()
        except Exception as exc:  # noqa: BLE001 - 故意捕获以断言"不该抛"
            err = exc
        return closed, err

    closed, err = asyncio.run(go())
    assert not closed, (
        "共享 internal client 被一次请求关掉了 ⇒ 生产代码里有人用 `async with` 包了它"
        "（或调了 aclose()）。它的生命周期归宿主（main_server shutdown hook），"
        "关掉会让**同一 loop 上所有并发复用者**报 "
        "'Cannot send a request, as the client has been closed'。"
    )
    assert err is None, (
        f"共享 client 在请求后无法复用：{type(err).__name__}: {err}"
    )
