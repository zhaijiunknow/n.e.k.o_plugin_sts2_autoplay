"""t52：内存自检 —— 回答"玩尖塔时 NEKO 占用越来越大"。

## 为什么需要（玩家反馈 → 必须先有数据）

玩家（第三方）反馈：玩尖塔时 NEKO 的占用越来越大。而 NEKO **不是单个进程**，本机实测是
13 个（工作集合计 ~3.2 GB）：

    N.E.K.O.exe (Electron 壳)
    ├─ projectneko_server  ← 主 Python server（含 onnxruntime/numpy，私有量级 ~1.3 GB）
    │   ├─ projectneko_server --multiprocessing-fork   ← **每个插件一个宿主进程**（200–370 MB/个）
    │   └─ ...
    ├─ renderer ×2 / gpu-process / utility ×3          ← Chromium 常态

⇒ "越玩越大"可能发生在：① 某个插件宿主进程（我们在这里）② 主 server（会话/记忆/缓存）
③ renderer/gpu（聊天气泡与纹理）。**没有一条时间序列就只能靠猜**，而本项目历史上靠猜归因
的代价很高（t37 的教训）。

`[sts2_mem]` 就是那条时间序列：同一行里既报**本进程 RSS**，也报**我方容器的长度**。
若 RSS 涨而这些长度不动 ⇒ 增长不在我们的数据结构上（去看主 server/renderer 或宿主自身）。

## 本文件钉死

1. 报告行包含四项必需信息：`rss` / `tasks` / `gc_objs` / `recent{...}`；
2. 我方容器的长度**真的**反映运行时状态（不是写死的 0）；
3. 周期配置 `memory_report_interval_seconds`：默认 300s，`<=0` 表示关闭；
4. 取 RSS 失败时**绝不能抛**（返回 0.0 / 显示 `rss=?`）—— 可观测性代码不许影响游玩链路。
"""

from __future__ import annotations

from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.loop_runner import STS2LoopRunner, _process_rss_bytes
from plugin.plugins.sts2_autoplay.runtime_state import STS2RuntimeState

pytestmark = pytest.mark.unit


class _Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, *args: Any, **kwargs: Any) -> None:
        self.lines.append(str(args[0]) % tuple(args[1:]) if len(args) > 1 else str(args[0]))

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.info(*args, **kwargs)

    def error(self, *args: Any, **kwargs: Any) -> None:
        self.info(*args, **kwargs)

    def debug(self, *args: Any, **kwargs: Any) -> None: ...


class _Svc:
    def __init__(self, *, interval: float = 300.0) -> None:
        self.logger = _Logger()
        self._state = STS2RuntimeState()
        self._interval = interval

    def _cfg_memory_report_interval(self) -> float:
        return self._interval


def _runner(interval: float = 300.0) -> tuple[STS2LoopRunner, _Svc]:
    svc = _Svc(interval=interval)
    return STS2LoopRunner(svc), svc


def test_memory_report_line_has_the_four_required_fields() -> None:
    """一行里必须有 rss / tasks / gc_objs / recent{...} —— 少一项就判不出"谁在涨"。"""
    runner, _ = _runner()
    line = runner._memory_report_line()

    assert "rss=" in line, line
    assert "tasks=" in line, line
    assert "gc_objs=" in line, line
    assert "recent{" in line, line
    assert "snapshot_keys=" in line, line
    assert "plan_steps=" in line, line


def test_memory_report_reflects_our_own_container_sizes() -> None:
    """我方环形队列的长度必须真实反映（否则"我方没涨"这个结论毫无意义）。"""
    runner, svc = _runner()
    for i in range(3):
        svc._state.remember_action_frame({"i": i})
        svc._state.remember_decision_memory({"i": i})

    line = runner._memory_report_line()

    assert "act=3" in line, line
    assert "dec=3" in line, line


def test_our_containers_are_bounded_so_they_cannot_be_the_leak() -> None:
    """环形队列封顶 20：塞 100 条也只有 20 —— 这条断言本身就是"不是我方泄漏"的证据。"""
    runner, svc = _runner()
    for i in range(100):
        svc._state.remember_action_frame({"i": i})
        svc._state.remember_continuous_delta({"i": i})
        svc._state.remember_decision_memory({"i": i})
        svc._state.remember_delivery({"i": i})

    line = runner._memory_report_line()

    assert "act=20" in line, line
    assert "delta=20" in line, line
    assert "dec=20" in line, line
    assert "deliv=20" in line, line


def test_report_interval_defaults_to_300_and_can_be_disabled() -> None:
    """默认 300s；`<=0` 表示关掉自检（玩家不想要这行日志时的开关）。"""
    from plugin.plugins.sts2_autoplay.service import STS2AutoplayService

    service = STS2AutoplayService(_Logger(), lambda payload: None, lambda **kwargs: None)
    assert service._cfg_memory_report_interval() == pytest.approx(300.0)

    service._cfg["memory_report_interval_seconds"] = 0
    assert service._cfg_memory_report_interval() == pytest.approx(0.0)

    service._cfg["memory_report_interval_seconds"] = "oops"
    assert service._cfg_memory_report_interval() == pytest.approx(300.0)


def test_rss_helper_never_raises_and_returns_a_number() -> None:
    """取 RSS 失败也不许抛（可观测性代码不能影响游玩链路），失败时退化成 0.0。"""
    value = _process_rss_bytes()
    assert isinstance(value, float)
    assert value >= 0.0
