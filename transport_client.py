from __future__ import annotations

import json
from collections.abc import Mapping
from time import time
from typing import Any, AsyncIterator, Dict

import httpx


def _internal_client() -> httpx.AsyncClient:
    """本机 mod（127.0.0.1）专用 HTTP 客户端——**绝不经系统/环境代理**。

    为什么必须共用宿主那个 client（而不是每次请求新建 `httpx.AsyncClient`）：

    1. ★ **代理正确性（本函数的直接动因）**：httpx 默认 ``trust_env=True``，会读
       ``HTTP_PROXY`` / ``HTTPS_PROXY`` 等环境变量；在 Windows 上它还会通过
       ``urllib.request.getproxies()`` 读注册表的 WinINET 设置。
       我方**只**访问 ``base_url``（本机 mod，``http://127.0.0.1:<port>``），
       **没有任何合法场景需要经系统代理**。实测本机开着 FlClash
       （``HKCU\\...\\Internet Settings``：``ProxyEnable=1``、``ProxyServer=127.0.0.1:7890``）；
       系统本意是让 ``127.*`` 直连（``ProxyOverride`` 里有 ``127.*``），
       但 **httpx 不读那个绕过列表** ⇒ 127.0.0.1 的请求被丢给 7890 ⇒ **HTTP 502**。
       宿主的 ``utils/http/internal_client.py:107`` 用 ``trust_env=False`` + ``proxy=None``
       从根上关掉这条路。
    2. **性能**：`utils/http/internal_client.py:20-25` 记述每次新建 client 会**急切**初始化
       SSLContext（读 certifi / Windows 证书库），即便访问的是纯 ``http://127.0.0.1``；
       冷启动实测**最多 1.1 秒/次**，曾把 2 秒超时的接口打成"mosel server 响应超时"。

    生命周期归宿主：此 client 按 event loop 共享（``_clients_by_loop``），
    由 main_server 的 shutdown hook 关闭 ⇒ **调用方不得 `async with` 包裹它、不得 `aclose()`**。
    超时**逐请求传**（``client.get(url, timeout=...)``），因为共享 client 带的是无关的默认值（5s）。
    """
    from utils.internal_http_client import get_internal_http_client

    return get_internal_http_client()


#: `_request(..., read_timeout=...)` 的哨兵：传它（默认）⇒ 用本类配置的 `request_timeout`。
#: 传 `None` ⇒ **该请求不设读超时**（httpx 语义：这一阶段永不超时）。
#: 只有 `/solver/plan` 会用 `None` —— 理由见 `get_combat_plan` 的注释。
_DEFAULT_READ_TIMEOUT: Any = object()


class STS2TransportError(RuntimeError):
    pass


class STS2ActionRejected(STS2TransportError):
    """mod 明确拒收了这个动作（HTTP 4xx/5xx 且带 error 包）。

    与"连不上 mod"是两回事：连不上要退避重连，而被拒多半意味着**游戏状态已经变了**——
    例如缓存里的战斗计划指向的敌人已经死了，于是 target_index 越界。这类拒绝的正确反应是
    "丢掉旧计划、按现场重来"，而不是把自动玩打成终止态。

    ``retryable`` 由 mod 的 ``error.retryable`` 或本地按 ``code`` 推断得出。
    """

    # 这些 code 表示"你问的时候已经不是那个状态了"，重算即可。
    RETRYABLE_CODES = frozenset({
        "invalid_target",
        "invalid_action",
        "state_unavailable",
        "invalid_state",
        "action_unavailable",
    })

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        action_name: str = "",
        screen: str = "",
        details: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "")
        self.action_name = str(action_name or "")
        self.screen = str(screen or "")
        self.details = dict(details or {})
        if retryable is None:
            retryable = self.code in self.RETRYABLE_CODES
        self.retryable = bool(retryable)

    # ★★ D11（t28）：游戏逐字告知"这个选项被锁"的判据。
    #
    #   实机真形状（2026-10-20 01:15:34 抓取，HTTP 409，`_t28_probe_reject.log`）：
    #     {"ok": false, "request_id": "req_...",
    #      "error": {"code": "invalid_target",
    #                "message": "The selected event option is locked.",
    #                "details": {"action": "choose_event_option", "option_index": 0},
    #                "retryable": false}}
    #
    #   ★ 修复前**从未解析**这条信息：源码对 "The selected event option" 命中 **0**。
    #   ★ 而它携带两个高价值信号：
    #       ① `details.option_index` = 游戏**精确点名**了哪个选项被锁（比读 `is_locked` 更直接）；
    #       ② `retryable: false` ⇒ 与本类 `RETRYABLE_CODES` 的推断**冲突**，
    #          而这个显式值优先（见 `__init__` 的 `if retryable is None`）
    #          ⇒ `invalid_target` 在**此处**并非"状态漂移、重算即可"，而是**确定不可选**。
    #   ★ 为什么按"消息文本 + option_index 存在"判定而不是按屏判定：
    #     `details` **不含 `screen`**（实测 `exc.screen == ''`）⇒ 无法从错误本身知道当前屏，
    #     故不能写"EVENT 屏才这样"。改用**最强可用信号**：确切的 message 文本，
    #     且要求 `option_index` 存在（战斗屏的越界错误不带该字段 ⇒ 天然不误伤）。
    LOCKED_OPTION_MESSAGE_MARKERS = ("option is locked", "option locked")

    @property
    def is_locked_option_rejection(self) -> bool:
        """这次拒收是否是「事件选项被锁」——**错误驱动**的精确判据。

        ★ 安全边界（为什么不会误伤战斗屏的 `target_index` 越界）：
          · 契约与实测均确认：`invalid_target` 在 EVENT 场景下**语义单一**
            （全日志 `invalid_target` 文本去重只有 locked 这一种；
              `play_card requires card_index` 是 `invalid_request`，**不同码**）；
          · 进一步要求 **message 命中** locked 字样 —— 战斗屏越界的 message 是
            `target_index is out of range for combat.enemies[]`，**不含** locked；
          · 且要求 `details` 里**确有** `option_index`。
          ⇒ 三条同时成立才判定，战斗屏**结构上不可能**命中。
        """
        if self.code != "invalid_target":
            return False
        text = str(self).lower()
        if not any(marker in text for marker in self.LOCKED_OPTION_MESSAGE_MARKERS):
            return False
        return self.details.get("option_index") is not None

    @property
    def locked_option_index(self) -> int | None:
        """被游戏点名锁定的选项下标；不是「被锁」类拒收时返回 `None`。"""
        if not self.is_locked_option_rejection:
            return None
        try:
            return int(self.details.get("option_index"))
        except (TypeError, ValueError):
            return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "action": self.action_name,
            "screen": self.screen,
            "retryable": self.retryable,
            "message": str(self),
        }


class STS2TransportClient:
    # 每个请求都带上的"外部控制者"标识头。mod 用它 + 租约判断自己是被插件控制还是独自运行：
    # 插件在（且带这个头）时 mod 走 coop_map_policy（默认彻底等玩家发话）；插件不在时 mod 自己跑，
    # 地图不会永久等待。插件空闲 3 秒 / 活动 1 秒轮询一次 /state，所以这个头天然就是心跳。
    EXTERNAL_CONTROL_HEADERS: Dict[str, str] = {
        "X-Neko-External-Control": "1",
        "X-Neko-Client": "sts2_autoplay",
    }

    def __init__(self, base_url: str, *, connect_timeout: float = 5.0, request_timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        # ★★ t14/D4（批 2）：**最后一次收到业务事件（非心跳）的时刻**。
        #
        # 为什么需要它（实机证据，2026-10-19）：mod 每 ~1s 发一行 `: heartbeat` 注释，
        # 它刷新了 `read=60.0` 的读超时 ⇒ 超时永不触发；而注释行不产出事件
        # ⇒ 当**业务事件流**真的停了，连接看起来**完全健康**，
        # `_event_loop` 永久阻塞在 `async for`、不抛异常、不重连、不刷新。
        #   实机：`ENTER=4 / EXIT=0 / WAKE=0 / DROPPED=0`，而直连 mod `/events/stream`
        #   仍是 HTTP 200、15 秒 16 行 ⇒ ★ **保活信号掩盖了业务事件流的死亡**。
        #
        # 判据：`time() - last_business_event_at` 才是"事件流是否还有用"的正解，
        #       而不是"连接还在不在"。★ 0.0 表示本次连接还没收到过业务事件。
        self.last_business_event_at: float = 0.0

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

        ★★★ t46：**本请求不设读超时**（`read_timeout=None`）—— 算多久就等多久。

        ## 为什么必须去掉读超时（玩家口径 + 两起实机故障）

        1. **等不及就退级，比卡住更坏。** 求解器预算实测会被配到 120 s（`SEARCH_SESSION
           total_budget_ms=120000`），后面还挂着一轮 120 s 的补充审计
           （`SUPPLEMENTAL_AUDIT_BUDGET budget_ms=120000`）。旧实现用 15 s 读超时，
           于是**每一次**都在搜索没算完时抛 `httpx.ReadTimeout`；上层把它当成"没有计划"，
           从此走上另一条策略路径 —— 同一局面本来能赢，退级后可能输。这不是"卡住"，
           是**结果被悄悄换掉了**。
        2. 实机证据（`Desktop\新建文件夹`，nekospire 正常加载的那套环境）：
           `/solver/plan` 有 202 次 200（min 5 ms）与 23 次失败**耗时 78–155 s**，
           失败原因是 `HttpListenerException (64) 指定的网络名不再可用` —— 服务端写响应时
           客户端早已放弃。更糟的是旧实现超时后立刻重发，实测**最多 21 个搜索同时在飞**。
        3. 连接的建立仍然受 `connect_timeout` 约束：端口不通/对端不是这个 mod 会**立刻**失败，
           那是错误（要喊出来），不是"在算"（要等）。两者由 `loop_runner` 的
           `solver_status` 分开记账（见 `runtime_state.solver_status`）。

        读到结果之后是否"可用"由 `solver_play_gate` 判定，本层不解释业务语义。
        """
        return await self._request("GET", "/solver/plan", read_timeout=None)

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
            # ★ 共用宿主 client（不经代理），但 **client 本身绝不可 `async with`** ——
            #   它按 event loop 共享、由宿主管理生命周期，`async with` 退出时会调 `aclose()`
            #   把别人正在用的连接池关掉。这里用 `client.stream(...)` 的 `async with`：
            #   它只关**本次响应**，不碰 client。
            #   timeout 逐请求传（共享 client 的默认超时 5s 与本流的语义无关）。
            client = _internal_client()
            async with client.stream(
                "GET", url, headers=dict(self.EXTERNAL_CONTROL_HEADERS), timeout=timeout
            ) as response:
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
                            # ★★ t14/D4（批 2）：**只有业务事件**才刷新这个时刻。
                            #   心跳（下面的 `:` 行）刻意【不】刷新它 —— 那正是本缺陷的病灶：
                            #   心跳让连接"看起来活着"，从而掩盖业务事件流已经死亡。
                            self.last_business_event_at = time()
                            yield flushed
                        continue
                    if line.startswith(":"):
                        # ★ 保活心跳（`: heartbeat`）：**只代表连接没断**，不代表有业务进度。
                        #   ★ 绝不可在这里刷新 `last_business_event_at` —— 否则又回到
                        #   "保活信号掩盖业务事件流死亡"的老路（见 __init__ 的说明）。
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

    async def clear_danmaku(self) -> Dict[str, Any]:
        """Clear every danmaku line already on screen (POST /danmaku/clear).

        Used when the user switches danmaku off: gating new pushes alone leaves the lines already
        scrolling for up to ~20s, which reads as "关不掉".
        """
        return await self._request("POST", "/danmaku/clear", json={})

    async def get_config(self) -> Dict[str, Any]:
        """Read the mod's config (GET /config). The api_key is never exposed by the mod.

        用途之一：从**主机**问出 `coop_client_port`，从而知道 coop 里的猫娘进程在哪个端口。
        """
        return await self._request("GET", "/config")

    async def set_config(self, **fields: Any) -> Dict[str, Any]:
        """Write the mod's config (POST /config). Only the fields provided are applied.

        When the plugin drives the catgirl, call ``set_config(llm_enabled=False)`` so the mod defers its
        MAP/reward/deck/event decision screens to this plugin instead of its own LLM.
        """
        return await self._request("POST", "/config", json=fields)

    def _build_action_payload(self, action_name: str, **kwargs: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"action": action_name}
        for key, value in kwargs.items():
            if value is None or key in {"type", "action"}:
                continue
            payload[key] = value
        return payload

    @staticmethod
    def _describe_error(payload: Dict[str, Any], status_code: int) -> str:
        """把 mod 的 error 包渲染成一句可诊断的文本。

        mod 端多个动作共用同一句 message（例如 "Action is not available in the current state."
        同时来自 open_multiplayer_menu / host_multiplayer_lobby / ...），只取 message 分不出是哪个
        动作挂的；error.details 里的 action / screen / code 才是有用信息，一并带上。
        """
        error = payload.get("error")
        if not isinstance(error, dict):
            return f"STS2-Agent 请求失败: HTTP {status_code}"

        message = str(error.get("message") or error.get("code") or f"HTTP {status_code}")
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        bits = [f"{key}={details[key]}" for key in ("action", "screen") if details.get(key) not in (None, "")]
        code = str(error.get("code") or "")
        if code and code not in message:
            bits.insert(0, f"code={code}")
        return f"{message} ({', '.join(bits)})" if bits else message

    async def _request(
        self,
        method: str,
        path: str,
        *,
        read_timeout: Any = _DEFAULT_READ_TIMEOUT,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """发一个 mod 请求。

        `read_timeout`（t46 新增，仅 `/solver/plan` 用）：
          · 不传            ⇒ 用本类配置的 `request_timeout`（其余端点照旧）
          · `None`          ⇒ **不设读超时**：等到对端把响应写完为止
          · 数字            ⇒ 覆盖本次的读超时
        ★ 只有 `read` 这一个阶段可被放开；`connect`/`write`/`pool` 仍走本类的超时，
          所以"端口不通"依旧是秒级失败，不会被无超时拖成永久挂起。
        """
        url = f"{self.base_url}{path}"
        # 调用方若自带 headers（例如将来的鉴权），以调用方为准，这里只补默认的标识头。
        headers = dict(self.EXTERNAL_CONTROL_HEADERS)
        extra_headers = kwargs.pop("headers", None)
        if isinstance(extra_headers, Mapping):
            headers.update(extra_headers)
        if read_timeout is _DEFAULT_READ_TIMEOUT:
            read_stage_timeout: float | None = self.request_timeout
        else:
            read_stage_timeout = None if read_timeout is None else float(read_timeout)
        timeout = httpx.Timeout(
            connect=self.request_timeout if self.connect_timeout <= 0 else self.connect_timeout,
            read=read_stage_timeout,
            write=self.request_timeout,
            pool=self.request_timeout,
        )
        try:
            # ★ 共用宿主 client：``proxy=None`` + ``trust_env=False``（见 `_internal_client`），
            #   127.0.0.1 的请求因此**绝不被系统代理劫持** —— 修复前实测被丢给
            #   FlClash 的 127.0.0.1:7890，HTTP 502、body 空 ⇒ 抛「返回了无效 JSON」。
            #   ★ **不得 `async with` 包裹它、不得 `aclose()`**：它按 event loop 共享、
            #   生命周期归宿主（main_server 的 shutdown hook），关掉会连带废掉别人正在用的连接池。
            #   ★ timeout **逐请求传**：共享 client 自带 5s 默认值，与本类的
            #   `connect_timeout` / `request_timeout` 无关，不传就静默丢掉本类的超时语义。
            client = _internal_client()
            response = await client.request(method, url, headers=headers, timeout=timeout, **kwargs)
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
            raise rejection_from_envelope(payload, response.status_code)

        data = payload.get("data")
        return data if isinstance(data, dict) else {"value": data}


def rejection_from_envelope(payload: Dict[str, Any], status_code: int) -> STS2TransportError:
    """把 mod 的错误包变成结构化异常：带 error 包 → ``STS2ActionRejected``（可能是"状态漂移"），
    否则退化成普通 ``STS2TransportError``。

    单独抽出来是为了可测：不用起 HTTP 也能验证"哪些 code 会被判成可重试"。
    """
    error = payload.get("error") if isinstance(payload.get("error"), dict) else None
    if error is None:
        return STS2TransportError(STS2TransportClient._describe_error(payload, status_code))
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    raw_retryable = error.get("retryable")
    return STS2ActionRejected(
        STS2TransportClient._describe_error(payload, status_code),
        code=str(error.get("code") or ""),
        action_name=str(details.get("action") or ""),
        screen=str(details.get("screen") or ""),
        details=details,
        retryable=bool(raw_retryable) if isinstance(raw_retryable, bool) else None,
    )


__all__ = ["STS2TransportClient", "STS2TransportError", "STS2ActionRejected", "rejection_from_envelope"]
