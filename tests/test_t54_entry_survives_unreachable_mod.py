# -*- coding: utf-8 -*-
"""t54：本地设置的 UI/LLM 入口，不许因为「游戏没开 / mod 路由不存在」而整条炸掉。

## 为什么需要这份测试（缺陷出处，不是假想）

玩家 2026-10-26 的 NEKO 日志里有一条真实的红线：

```text
2026-10-26 16:00:03 - ... - ERROR - Unexpected STS2 plugin entry failure
Traceback (most recent call last):
  File ".../__init__.py", line 114, in _run_entry
    payload = await action()
  File ".../neko_interface.py", line 118, in set_companion_mode
    await self._service.refresh_state(trigger_sync=True)
  File ".../loop_runner.py", line 110, in tick
    raw_actions = await client.get_available_actions()
  ...
STS2ActionRejected: Route not found. (code=not_found)
```

也就是说：**"我来陪你玩"这个开关在游戏没开（或 mod 版本不匹配、`/actions/available`
返回 404）时会抛异常**，一路冒到 `_run_entry` 的兜底，变成一条 `Unexpected ... failure`。
而同一个文件里 `set_control_mode` / `set_play_style` / `set_instance` 早就走
`_refresh_if_connected()`（吞掉连接错误、用 `refreshed` 如实回报）——两套口径不一致，
真正被玩到的偏偏是不一致的那一侧。

## 本测试钉住的契约

1. `set_companion_mode(True)` / `set_standby(True)` 在 mod 不可达时**返回正常 payload**，
   本地状态**确实生效**（`enabled` / `standby` 为真），且 `refreshed is False`。
2. 失败**不静默**：必须有一条 `[sts2_entry_refresh_skipped]` 日志（此前只有 debug 级，
   玩家日志里根本看不见，只能看到界面不刷新然后自己猜）。
3. mod 可达时 `refreshed is True`（不能为了"别炸"把成功也报成失败）。

运行方式（cwd 必须是 NEKO 仓库根，见 tests/test_play_style_neko_integration.py 的说明）::

    cd D:\\NekoClaw\\N.E.K.O
    $env:PYTHONPATH="D:\\NekoClaw\\N.E.K.O\\plugin\\plugins\\sts2_autoplay"
    .\\.venv\\Scripts\\python.exe -m pytest plugin/plugins/sts2_autoplay/tests/test_t54_entry_survives_unreachable_mod.py -q
"""

from __future__ import annotations

from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    """记录所有 info 行，供「失败必须可见」的断言使用。"""

    def __init__(self) -> None:
        self.infos: list[str] = []

    def info(self, *args: Any, **kwargs: Any) -> None:
        self.infos.append(" ".join(str(item) for item in args))

    def debug(self, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return None

    def error(self, *args: Any, **kwargs: Any) -> None:
        return None

    def exception(self, *args: Any, **kwargs: Any) -> None:
        return None


class _UnreachableMod(Exception):
    """复刻 `transport_client.STS2ActionRejected` 的形状：路由不存在 = mod 没在跑。"""

    code = "not_found"


def _make_service(monkeypatch: pytest.MonkeyPatch, *, refresh_error: Exception | None) -> tuple[STS2AutoplayService, DummyLogger, list[bool]]:
    """构造一个裸 service，并把 `refresh_state` 换成"连不上 mod"或"连得上"的假实现。"""
    logger = DummyLogger()
    service = STS2AutoplayService(logger, lambda payload: None)  # type: ignore[arg-type]
    refresh_calls: list[bool] = []

    if refresh_error is None:

        async def fake_refresh_state(*, trigger_sync: bool = False) -> dict[str, Any]:
            refresh_calls.append(trigger_sync)
            return {"status": "ok", "snapshot": {}}

    else:

        async def fake_refresh_state(*, trigger_sync: bool = False) -> dict[str, Any]:
            refresh_calls.append(trigger_sync)
            raise refresh_error

    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)
    return service, logger, refresh_calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_companion_mode_survives_unreachable_mod(monkeypatch: pytest.MonkeyPatch) -> None:
    """游戏没开时开陪伴模式：必须是"设置成功 + 刷新失败"，不是异常。"""
    service, _logger, refresh_calls = _make_service(
        monkeypatch, refresh_error=_UnreachableMod("Route not found. (code=not_found)")
    )

    result = await service.neko.set_companion_mode(True)

    assert result["status"] == "ok"
    assert result["enabled"] is True
    assert result["refreshed"] is False
    assert refresh_calls == [True], "仍应尝试刷新一次（尽力而为），只是失败被如实回报"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_standby_survives_unreachable_mod(monkeypatch: pytest.MonkeyPatch) -> None:
    """游戏没开时按"停手"：档位必须生效。这条此前同样走裸 `refresh_state()`。"""
    service, _logger, _calls = _make_service(monkeypatch, refresh_error=_UnreachableMod("Route not found"))

    result = await service.neko.set_standby(True)

    assert result["status"] == "ok"
    assert result["standby"] is True
    assert result["refreshed"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_failure_is_logged_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败不许静默：必须有一条可见的 `[sts2_entry_refresh_skipped]`，且带上异常类型。"""
    service, logger, _calls = _make_service(monkeypatch, refresh_error=_UnreachableMod("Route not found"))

    await service.neko.set_companion_mode(True)

    skipped = [line for line in logger.infos if "[sts2_entry_refresh_skipped]" in line]
    assert skipped, f"连接失败必须在日志里可见，实际 info 行={logger.infos}"
    assert "_UnreachableMod" in skipped[0]
    assert "Route not found" in skipped[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_companion_mode_reports_refreshed_true_when_mod_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """反向断言：连得上就是 True。别为了"别炸"把成功也报成失败。"""
    service, logger, refresh_calls = _make_service(monkeypatch, refresh_error=None)

    result = await service.neko.set_companion_mode(True)

    assert result["enabled"] is True
    assert result["refreshed"] is True
    assert refresh_calls == [True]
    assert not [line for line in logger.infos if "[sts2_entry_refresh_skipped]" in line]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_standby_reports_refreshed_true_when_mod_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _logger, _calls = _make_service(monkeypatch, refresh_error=None)

    result = await service.neko.set_standby(True)

    assert result["refreshed"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disabling_companion_mode_still_skips_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """关陪伴模式本来就不刷新——t54 不许把这个既有语义改掉（回归）。"""
    service, _logger, refresh_calls = _make_service(monkeypatch, refresh_error=None)

    result = await service.neko.set_companion_mode(False)

    assert result["enabled"] is False
    assert refresh_calls == []
    assert "refreshed" not in result


# ── 附：启动行必须报**真实**版本（同一轮发布的可诊断性修复）──────────────────────────
#
# 出处：`[sts2_code_version]` 此前写死成 `20260521_companion_eval_debug`，于是 2026-10-26
# 给玩家排障时无法从日志判断他到底跑的是 0.7.7 还是 0.7.10 —— 只能靠"有没有某个日志标签"反推。
# 这一组测试钉住："报的是 plugin.toml 里的 version"、"读不到就老实说 unknown"。


def test_read_plugin_version_reads_plugin_toml() -> None:
    from plugin.plugins.sts2_autoplay.service import _read_plugin_version

    label = _read_plugin_version()

    assert label != "unknown", "仓库里就有 plugin.toml，必须读得到版本"
    assert label[0].isdigit(), f"版本号应当形如 x.y.z，实际={label!r}"


def test_read_plugin_version_parses_the_plugin_section(tmp_path: Any) -> None:
    from plugin.plugins.sts2_autoplay.service import _read_plugin_version

    manifest = tmp_path / "plugin.toml"
    manifest.write_text(
        '[sts2]\nroot = "ignored"\n\n[plugin]\nid = "sts2_autoplay"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )

    assert _read_plugin_version(manifest) == "9.9.9"


def test_read_plugin_version_returns_unknown_when_manifest_missing(tmp_path: Any) -> None:
    """读不到就说不知道 —— 绝不编一个版本号出来（那正是旧实现的问题）。"""
    from plugin.plugins.sts2_autoplay.service import PLUGIN_VERSION_UNKNOWN, _read_plugin_version

    assert _read_plugin_version(tmp_path / "nope.toml") == PLUGIN_VERSION_UNKNOWN
