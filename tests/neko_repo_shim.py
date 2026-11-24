# -*- coding: utf-8 -*-
"""pytest 插件：让 `plugin.plugins.sts2_autoplay` 解析到**本仓库检出**，而不是部署在 N.E.K.O 里的那份拷贝。

为什么需要它：
  测试一律 `import plugin.plugins.sts2_autoplay.*`。这个包名只有在 N.E.K.O 的
  `plugin/plugins/sts2_autoplay/` 目录下才天然成立；而本仓库的目录名
  （`n.e.k.o_plugin_sts2_autoplay`）不是合法的包名。于是：
    · 在仓库里直接 `pytest tests` ⇒ 全部在 setup 阶段 ImportError；
    · 到部署目录里跑 ⇒ 测的是**上次部署的拷贝**，不是你刚改的代码。

用法（在仓库的 tests/ 目录下，用 N.E.K.O 的 venv）：

    cd tests
    D:\\NekoClaw\\N.E.K.O\\.venv\\Scripts\\python.exe -m pytest -p neko_repo_shim ^
        -p no:cacheprovider --rootdir=<本目录的绝对路径> -q

  `--rootdir` 必须是 tests/ 的绝对路径：否则 pytest 以仓库根为 rootdir，而仓库根有 `__init__.py`。
  宿主目录不在默认位置时设环境变量 `NEKO_ROOT`。

不在本文件名里用 `test_` 前缀，pytest 不会把它当测试收集；部署目录里它只是一个没人加载的文件。
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NEKO = Path(os.environ.get("NEKO_ROOT") or r"D:\NekoClaw\N.E.K.O")
NAME = "plugin.plugins.sts2_autoplay"

if not (NEKO / "plugin" / "plugins").is_dir():
    raise RuntimeError(f"N.E.K.O host not found at {NEKO} (set NEKO_ROOT)")
if str(NEKO) not in sys.path:
    sys.path.insert(0, str(NEKO))

# 宿主的包：提供 plugin.core / plugin.sdk。必须在上面把 NEKO 放进 sys.path 之后才能导入，
# 所以用 import_module 而不是 import 语句（Market 的 CI 以 --ignore-noqa 跑 ruff，E402 压不掉）。
plugin = importlib.import_module("plugin")
importlib.import_module("plugin.plugins")

_spec = importlib.util.spec_from_file_location(
    NAME, REPO / "__init__.py", submodule_search_locations=[str(REPO)]
)
_module = importlib.util.module_from_spec(_spec)
sys.modules[NAME] = _module
_spec.loader.exec_module(_module)
plugin.plugins.sts2_autoplay = _module
# 仓库根有 __init__.py 但目录名不可导入，pytest 的 Package 收集器会把它当成名为 `__init__` 的
# 顶层模块去导入（相对导入随即失败）。把已经装好的包递给它。
sys.modules["__init__"] = _module


def pytest_report_header(config):  # noqa: ARG001
    return f"sts2_autoplay under test: {REPO}"
