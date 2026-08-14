# -*- coding: utf-8 -*-
"""一键安装 PyQt6（弹幕浮层依赖）。

给弹幕浮层实际使用的 python 安装 PyQt6 并验证。
可直接运行：python install_pyqt6.py

优先级（与 qt_overlay_manager._resolve_python 一致）：
1. 仓库虚拟环境 .venv（N.E.K.O 运行时，最可靠）
2. 当前运行时 sys.executable（通常是同一 venv）
3. 系统 python（兜底，不同用户可能没有）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _repo_venv_python() -> str:
    return str(Path(__file__).resolve().parents[3] / ".venv" / "Scripts" / "python.exe")


_C_DRIVE_PYTHON = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"

# 安装目标（去重保序）
_TARGETS: list[str] = []
for _exe in (_repo_venv_python(), sys.executable, _C_DRIVE_PYTHON):
    if _exe not in _TARGETS:
        _TARGETS.append(_exe)


def _has_pyqt6(exe: str) -> bool:
    try:
        probe = subprocess.run(
            [exe, "-c", "import PyQt6"],
            capture_output=True,
            timeout=15,
        )
        return probe.returncode == 0
    except Exception:
        return False


def _has_pip(exe: str) -> bool:
    try:
        probe = subprocess.run(
            [exe, "-m", "pip", "--version"],
            capture_output=True,
            timeout=15,
        )
        return probe.returncode == 0
    except Exception:
        return False


def _ensure_pip(exe: str) -> bool:
    """若没有 pip，先用 ensurepip 引导。"""
    try:
        result = subprocess.run(
            [exe, "-m", "ensurepip", "--upgrade"],
            capture_output=True,
            timeout=120,
        )
        return result.returncode == 0
    except Exception:
        return False


def main() -> int:
    already: list[str] = []
    installed: list[str] = []
    failed: list[tuple[str, str]] = []

    for exe in _TARGETS:
        if _has_pyqt6(exe):
            already.append(exe)
            continue
        print(f"为 {exe} 安装 PyQt6 ...", flush=True)
        if not _has_pip(exe):
            print(f"  {exe} 无 pip，先用 ensurepip 引导 ...", flush=True)
            if not _ensure_pip(exe):
                failed.append((exe, "ensurepip 失败"))
                continue
        try:
            result = subprocess.run(
                [exe, "-m", "pip", "install", "PyQt6"],
                capture_output=True,
                timeout=600,
            )
            if result.returncode == 0 and _has_pyqt6(exe):
                installed.append(exe)
            else:
                tail = (result.stderr or result.stdout or b"").decode("utf-8", "replace")[-400:]
                failed.append((exe, tail))
        except Exception as exc:
            failed.append((exe, str(exc)))

    print("=== 结果 ===", flush=True)
    for exe in already:
        print(f"  已安装：{exe}", flush=True)
    for exe in installed:
        print(f"  安装成功：{exe}", flush=True)
    for exe, err in failed:
        print(f"  安装失败：{exe}\n    {err}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
