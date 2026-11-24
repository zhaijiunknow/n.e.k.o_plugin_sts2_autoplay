"""游戏版本与按版本分的路径（版本只写在 engine/game_version.txt，引擎模块与游戏更新适配.md 第五节）。

游戏更新时改那一个文件；内容目录 engine/catalog/<版本>/、游戏文件 ~/.local/share/sls2/game/<版本>/ 都按它找。
文件不在（只部署了 python/ 的机器）时退回 v0.111.0，不让训练因为缺一个文本文件起不来。
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__import__("os").environ["STS2_POLICY_ROOT"])
_FALLBACK = "v0.111.0"


def game_version() -> str:
    if __import__("os").environ.get("STS2_POLICY_GAME_VERSION"):
        return __import__("os").environ["STS2_POLICY_GAME_VERSION"]
    try:
        v = (REPO / "engine" / "game_version.txt").read_text(encoding="utf-8").strip()
        return v or _FALLBACK
    except OSError:
        return _FALLBACK


GAME_VERSION = game_version()


def catalog_path(version: str | None = None) -> Path:
    """内容目录 engine/catalog/<版本>/catalog.json。"""
    return REPO / "engine" / "catalog" / (version or GAME_VERSION) / "catalog.json"


def game_dir(version: str | None = None) -> Path:
    """游戏程序集与资源（从安装目录复制，不入库）。"""
    return Path.home() / ".local/share/sls2/game" / (version or GAME_VERSION)


def localization_dir(lang: str = "zhs", version: str | None = None) -> Path:
    """游戏自带的本地化文本（res/localization/<语言>）。"""
    return game_dir(version) / "res" / "localization" / lang
