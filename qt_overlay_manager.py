# -*- coding: utf-8 -*-
"""Qt 弹幕浮层进程管理（由插件拥有，不依赖 plugin_ui.py）。

插件通过 LLM entry 控制（sts2_overlay_start / stop / status）：
- ``start``：用带 PyQt6 的 python 拉起 qt_overlay.py，订阅本插件 SSE，可贴合游戏窗口
- ``stop``：terminate
- ``status``：是否在运行

作为弹幕的「额外附加功能」：不随插件自动启动，由用户/Agent 按需触发；
插件 shutdown 时也会清理。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .danmu_bridge import _plugin_server_base


# 仓库虚拟环境（N.E.K.O 运行时，最可靠）——相对本模块向上 4 级定位
def _repo_venv_python() -> str:
    return str(Path(__file__).resolve().parents[3] / ".venv" / "Scripts" / "python.exe")


# 系统 python 兜底（不同用户账号可能不同或不存在，探测失败自动跳过）
_C_DRIVE_PYTHON = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
# PyQt6 探测缓存时长（秒）
_PYQT6_CACHE_TTL_SEC = 30.0


class QtOverlayManager:
    """管理 Qt 弹幕浮层子进程。"""

    def __init__(
        self,
        logger: Any,
        *,
        plugin_id: str,
        plugin_dir: Path,
        window: str = "",
        rect: str = "",
        speed: float = 0.0,
        font_size: int = 0,
    ) -> None:
        self._logger = logger
        self._plugin_id = plugin_id
        self._plugin_dir = Path(plugin_dir)
        self._window = str(window or "").strip()
        self._rect = str(rect or "").strip()
        self._speed = float(speed or 0)
        self._font_size = int(font_size or 0)
        self._height_percent = 0
        self._proc: subprocess.Popen | None = None
        self._pyqt6_installed: bool | None = None
        self._pyqt6_check_at: float = 0.0

    def configure(
        self,
        *,
        window: str = "",
        rect: str = "",
        speed: float = 0.0,
        font_size: int = 0,
        height_percent: int = 0,
    ) -> None:
        """按配置更新浮层参数（下次 start 生效）。"""
        self._window = str(window or "").strip()
        self._rect = str(rect or "").strip()
        self._speed = float(speed or 0)
        self._font_size = int(font_size or 0)
        self._height_percent = int(height_percent or 0)

    def _sse_url(self) -> str:
        return f"{_plugin_server_base()}/plugin/{self._plugin_id}/ui-api/events"

    def _resolve_python(self) -> str | None:
        """找一个能 import PyQt6 的 python（子进程探测）。

        优先级：运行时（sys.executable，通常是仓库 venv）→ 仓库 venv → 系统 python（兜底）。
        """
        candidates = [sys.executable, _repo_venv_python(), _C_DRIVE_PYTHON]
        seen: set[str] = set()
        for exe in candidates:
            if exe in seen:
                continue
            seen.add(exe)
            try:
                probe = subprocess.run(
                    [exe, "-c", "import PyQt6"],
                    capture_output=True,
                    timeout=5,
                )
                if probe.returncode == 0:
                    return exe
            except Exception:
                continue
        return None

    def _check_pyqt6(self) -> bool:
        """PyQt6 是否可用（子进程探测，带缓存；插件进程自身 sys.path 受限不可靠）。"""
        now = time.time()
        if self._pyqt6_installed is not None and now - self._pyqt6_check_at < _PYQT6_CACHE_TTL_SEC:
            return self._pyqt6_installed
        self._pyqt6_installed = self._resolve_python() is not None
        self._pyqt6_check_at = now
        return self._pyqt6_installed

    def status(self) -> dict[str, Any]:
        running = self._proc is not None and self._proc.poll() is None
        pyqt6 = self._check_pyqt6()
        return {
            "ok": True,
            "running": running,
            "pid": self._proc.pid if running else None,
            "pyqt6_installed": pyqt6,
            "install_hint": None if pyqt6 else "pip install PyQt6",
        }

    def start(self) -> dict[str, Any]:
        if self._proc is not None and self._proc.poll() is None:
            return {"ok": True, "already_running": True, "pid": self._proc.pid}
        overlay_script = self._plugin_dir / "qt_overlay.py"
        if not overlay_script.is_file():
            return {"ok": False, "error": "插件目录缺少 qt_overlay.py"}
        python_exe = self._resolve_python()
        if not python_exe:
            return {"ok": False, "error": "未找到带 PyQt6 的 python"}
        cmd = [python_exe, str(overlay_script), "--url", self._sse_url()]
        if self._window:
            cmd += ["--window", self._window]
        elif self._rect:
            cmd += ["--rect", self._rect]
        if self._speed > 0:
            cmd += ["--speed", str(self._speed)]
        if self._font_size > 0:
            cmd += ["--font-size", str(self._font_size)]
        if self._height_percent > 0:
            cmd += ["--height-percent", str(self._height_percent)]
        try:
            flags = 0
            if sys.platform == "win32":
                # 只用 CREATE_NO_WINDOW：与 DETACHED_PROCESS 组合可能导致控制台闪现
                flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
            )
        except Exception as exc:
            return {"ok": False, "error": f"启动 Qt 弹幕浮层失败: {exc}"}
        try:
            self._logger.info(
                "[sts2_autoplay] Qt 弹幕浮层已启动 pid=%s url=%s",
                self._proc.pid,
                self._sse_url(),
            )
        except Exception:
            pass
        return {
            "ok": True,
            "running": True,
            "pid": self._proc.pid,
            "sse_url": self._sse_url(),
        }

    def stop(self) -> dict[str, Any]:
        was_running = False
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            was_running = True
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            except Exception:
                pass
        # 兜底：杀掉所有指向本插件 SSE 的 qt_overlay 进程（含手动启动/未跟踪的）
        if self._kill_untracked_overlays():
            was_running = True
        return {"ok": True, "was_running": was_running}

    def _kill_untracked_overlays(self) -> bool:
        """杀掉所有指向本插件 SSE 的 qt_overlay.py 进程（含手动启动的）。"""
        killed = False
        try:
            import shlex

            import psutil

            marker = f"{self._plugin_id}/ui-api/events"
            for p in psutil.process_iter(["cmdline"]):
                try:
                    cmd = " ".join(p.info.get("cmdline") or [])
                except Exception:
                    continue
                if marker not in cmd:
                    continue
                try:
                    tokens = shlex.split(cmd)
                except Exception:
                    tokens = []
                if not any(token.endswith("qt_overlay.py") for token in tokens):
                    continue
                try:
                    p.terminate()
                    try:
                        p.wait(timeout=2)
                    except Exception:
                        p.kill()
                    killed = True
                except Exception:
                    pass
        except Exception:
            pass
        return killed



__all__ = ["QtOverlayManager"]
