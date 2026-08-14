# -*- coding: utf-8 -*-
"""Qt 透明弹幕窗 — 库检测（可选安装）。

sts2_autoplay 的 Qt 保底弹幕浮层依赖 PyQt6（可选安装，不进主 requirements）。
本模块探测 PyQt6 可用性，供前端"Qt 弹幕窗"卡片展示状态与安装提示。

参考 galgame 插件的依赖检测模式（dependency_status.py）：
状态字段沿用 installed / detail / can_install 语义。
"""

from __future__ import annotations

import importlib.util

# Qt 保底方案必需的顶层包
_QT_REQUIRED_PACKAGE = "PyQt6"
# 安装提示（pip 命令）
INSTALL_HINT = "pip install PyQt6"


def detect_pyqt6() -> dict[str, object]:
    """探测 PyQt6 是否可导入。

    返回：
        {"installed": bool, "detail": "installed"|"missing", "can_install": bool}
    """
    try:
        spec = importlib.util.find_spec(_QT_REQUIRED_PACKAGE)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return {
            "installed": False,
            "detail": "missing",
            "can_install": True,
            "install_hint": INSTALL_HINT,
        }
    return {
        "installed": True,
        "detail": "installed",
        "can_install": False,
    }


def qt_overlay_available() -> bool:
    """Qt 弹幕浮层是否可用（PyQt6 已安装）。"""
    status = detect_pyqt6()
    return bool(status.get("installed"))


__all__ = ["detect_pyqt6", "qt_overlay_available", "INSTALL_HINT"]
