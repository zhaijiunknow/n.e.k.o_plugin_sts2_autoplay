"""coop 里由插件接管猫娘：活动实例的切换、接管时机、以及读不通时的兜底。

背景（这是本模块存在的原因）：猫娘在 coop 里是**另一个游戏进程**，由 mod 用
`STS2_API_PORT=<coop_client_port>`（默认 18081）拉起；而单人局就是配置里那个
`base_url`（默认 18080）。插件只有一个 `base_url`，所以必须引入"活动实例"：

* `host`    —— 配置里的地址。**开房必须发给它**（只有主机能拉起猫娘进程）。
* `catgirl` —— 猫娘进程。插件驱动游玩时读状态、下动作都对着它。

切过去时要顺手给那个实例推 `coop_control_mode="hold"`，让它自己的 mod 驱动在除"进局流程"
外的界面停手——否则两个驱动会抢着下同一个动作。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def exception(self, *a, **k):
        return None


class FakeClient:
    """够用的传输客户端替身：可分别配置「配置读得到什么」与「健康探测成不成功」。"""

    def __init__(self, *, base_url: str = "", config: dict[str, Any] | None = None, healthy: bool = True) -> None:
        self.base_url = base_url
        self._config = config or {}
        self.healthy = healthy
        self.config_writes: list[dict[str, Any]] = []
        self.health_calls = 0

    async def get_config(self) -> dict[str, Any]:
        return {"ok": True, "data": dict(self._config)}

    async def set_config(self, **fields: Any) -> dict[str, Any]:
        self.config_writes.append(fields)
        return {"ok": True}

    async def health(self) -> dict[str, Any]:
        self.health_calls += 1
        if not self.healthy:
            raise RuntimeError("connection refused")
        return {"ok": True}


def _service() -> STS2AutoplayService:
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None)
    # 跳过 startup 的网络部分：直接手工摆好两个客户端。
    host = FakeClient(base_url="http://127.0.0.1:18080", config={"coop_client_port": 18081})
    service._client = host
    service._host_client = host
    service._host_base_url = "http://127.0.0.1:18080"
    return service


# ---- 实例名归一化 -------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("host", "host"),
        ("catgirl", "catgirl"),
        ("coop", "catgirl"),
        ("client", "catgirl"),
        ("猫娘", "catgirl"),
        # 未知取值一律退化为 host：宁可对着主机（单人流程照旧），
        # 也不能因为写错配置就把插件指向一个可能不存在的进程。
        ("胡说八道", "host"),
        ("", "host"),
        (None, "host"),
    ],
)
def test_instance_name_normalisation(raw: object, expected: str) -> None:
    assert STS2AutoplayService._normalize_instance(raw) == expected


# ---- 猫娘地址从主机问出来 -----------------------------------------------------------


@pytest.mark.unit
def test_catgirl_address_is_derived_from_the_hosts_coop_client_port() -> None:
    service = _service()
    address = asyncio.run(service.resolve_catgirl_base_url())
    assert address == "http://127.0.0.1:18081"


@pytest.mark.unit
def test_missing_coop_client_port_yields_no_address_instead_of_guessing() -> None:
    """旧版 mod 没有这个字段——**不许猜端口**，猜错会连到别的进程上。"""
    service = _service()
    service._host_client = FakeClient(base_url="http://127.0.0.1:18080", config={})
    assert asyncio.run(service.resolve_catgirl_base_url()) == ""


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["", "abc", 0, -1, 70000, None])
def test_nonsense_port_is_rejected(bad: object) -> None:
    service = _service()
    service._host_client = FakeClient(config={"coop_client_port": bad})
    assert asyncio.run(service.resolve_catgirl_base_url()) == ""


# ---- 切换 ---------------------------------------------------------------------------


@pytest.mark.unit
def test_switching_to_the_catgirl_pushes_driver_standdown() -> None:
    service = _service()
    catgirl = FakeClient(base_url="http://127.0.0.1:18081")
    service._build_client = lambda base_url: catgirl  # type: ignore[method-assign]

    result = asyncio.run(service.switch_active_instance("catgirl", reason="test"))

    assert result["switched"] is True
    assert service._state.active_instance == "catgirl"
    assert service._client is catgirl
    # 关键：必须让它自己的 mod 驱动停手，否则两个驱动会抢动作。
    assert catgirl.config_writes[0] == {"coop_control_mode": "hold"}


@pytest.mark.unit
def test_switching_to_the_catgirl_without_an_address_fails_cleanly() -> None:
    service = _service()
    service._host_client = FakeClient(config={})
    result = asyncio.run(service.switch_active_instance("catgirl"))
    assert result["switched"] is False
    assert result["reason"] == "catgirl_address_unknown"
    # 仍停在主机上，不影响单人流程。
    assert service._state.active_instance == "host"


@pytest.mark.unit
def test_switching_back_to_the_host_restores_the_original_client() -> None:
    service = _service()
    host = service._client
    asyncio.run(service.switch_active_instance("catgirl"))
    assert service._client is not host
    asyncio.run(service.switch_active_instance("host"))
    assert service._client is host
    assert service._state.active_instance == "host"


# ---- 接管时机 -----------------------------------------------------------------------


@pytest.mark.unit
def test_adoption_waits_for_the_catgirl_process_to_come_up() -> None:
    """猫娘是另一个游戏进程，要冷启动十几秒——所以要轮询等待，不能只探一次。"""
    service = _service()
    created: list[FakeClient] = []

    def build(base_url: str) -> FakeClient:
        client = FakeClient(base_url=base_url, healthy=True)
        created.append(client)
        return client

    service._build_client = build  # type: ignore[method-assign]
    result = asyncio.run(service.adopt_coop_catgirl(wait_seconds=0.1))

    assert result["adopted"] is True
    assert service._state.active_instance == "catgirl"
    assert created and created[0].health_calls >= 1
    assert created[0].config_writes[0] == {"coop_control_mode": "hold"}


@pytest.mark.unit
def test_adoption_gives_up_when_the_catgirl_never_appears() -> None:
    service = _service()

    def build(base_url: str) -> FakeClient:
        return FakeClient(base_url=base_url, healthy=False)

    service._build_client = build  # type: ignore[method-assign]
    result = asyncio.run(service.adopt_coop_catgirl(wait_seconds=0.05))
    assert result["adopted"] is False
    assert result["reason"] == "catgirl_not_reachable"
    # 停在主机实例上，单人流程不受影响。
    assert service._state.active_instance == "host"


@pytest.mark.unit
def test_adoption_without_an_address_does_not_switch() -> None:
    service = _service()
    service._host_client = FakeClient(config={})
    result = asyncio.run(service.adopt_coop_catgirl(wait_seconds=0.05))
    assert result["adopted"] is False
    assert result["reason"] == "catgirl_address_unknown"


# ---- 读不通时的兜底 -----------------------------------------------------------------


@pytest.mark.unit
def test_repeated_failures_fall_back_to_the_host() -> None:
    """插件不能卡在一个死地址上：连败三次就退回主机。"""
    service = _service()
    asyncio.run(service.switch_active_instance("catgirl"))
    assert service._state.active_instance == "catgirl"

    for _ in range(2):
        asyncio.run(service._note_transport_failure())
    assert service._state.active_instance == "catgirl", "还没到阈值就不该切"

    asyncio.run(service._note_transport_failure())
    assert service._state.active_instance == "host", "到阈值必须退回主机"


@pytest.mark.unit
def test_failures_on_the_host_instance_do_not_trigger_a_switch() -> None:
    service = _service()
    for _ in range(5):
        asyncio.run(service._note_transport_failure())
    assert service._state.active_instance == "host"
    assert service._instance_failures == 0, "主机实例没有可退回的地方，不该累计"
