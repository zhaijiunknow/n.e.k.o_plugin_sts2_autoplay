# -*- coding: utf-8 -*-
"""pytest 全局引导：让测试能 `import plugin.plugins.sts2_autoplay.*`。

为什么放在这里而不是各测试模块顶部：
  各模块原先在文件顶部插入 sys.path、然后再导入插件包，这会触发 E402
  （Module level import not at top of file）。pytest 在收集测试模块**之前**
  先导入同目录的 conftest.py，所以把同一段路径设置上移到这里，语义完全等价，
  而测试模块的导入就能回到文件顶部正常书写，不再需要 `# noqa: E402`。

  ⇒ 这样做也顺带修掉了一个隐性依赖：原先每个文件各自算一次 `__file__`，
  换目录结构时会各自静默失效；现在只有一处定义。
"""
from __future__ import annotations

import sys
from pathlib import Path

# tests/ -> 插件目录 -> ... -> 仓库根（与原各测试模块内的口径一致）
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PLUGIN_ROOT.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: 宿主 N.E.K.O 目录：部分用例需要 `import plugin.core.*` / `import utils.*`。
#: 只在它**真的存在**时插入 —— CI 上不存在，插进去也没用，反而掩盖问题。
_NEKO_ROOT = Path(r"D:\NekoClaw\N.E.K.O")
if _NEKO_ROOT.is_dir() and str(_NEKO_ROOT) not in sys.path:
    sys.path.insert(0, str(_NEKO_ROOT))
