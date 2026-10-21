"""t16 回归测试：记忆文件损坏时必须**抢救式解析**，绝不静默清空。

修复的生产缺陷（`catgirl_memory.py` 的 `load()`）::

    except (OSError, ValueError):
        self._runs = []      # ← 静默丢弃全部记忆

实测线上 `catgirl_memory.json` 已损坏（完整 JSON 之后残留 22,964 字符），
`json.load` 抛 `ValueError` ⇒ 静默得 0 局 ⇒ 危险链
「load 得 0 局 → save 把 0 局写回」会**坐实丢失全部 50 局**。

本文件覆盖：D1 抢救、D2 不丢数据、D3 残片形态、D4 向前兼容。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# ★t16 注意（变异靶子）：本文件**不**手工插入 sys.path —— 让导入路径
# 完全由 conftest / `.pth` 决定，与既有测试**同一份副本**。
# 早先版本写了 `sys.path.insert(0, <installed>/plugins)`，那会让本文件
# 导入 **installed** 副本，而既有测试导入 **builtin** 副本 ⇒
# 变异打在 builtin 上却测 installed，**4/4 变异体全部逃逸**。
# 现在统一走同一解析路径，靶子与测试对象一致。


class _Logger:
    """收集日志，用来断言「抢救**被显式记录**，不是静默」。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, msg, *a, **k):
        self.lines.append(str(msg))

    def warning(self, msg, *a, **k):
        self.lines.append(str(msg))

    def debug(self, msg, *a, **k):
        self.lines.append(str(msg))

    def text(self) -> str:
        return "\n".join(self.lines)


def _store(path, logger=None):
    # ★ 必须用完整包路径 `plugin.plugins.sts2_autoplay.*`，与其余测试一致
    #   （conftest/`.venv` editable `.pth` 只提供 `plugin.plugins...` 与 `plugin`，
    #    裸 `sts2_autoplay` 不在导入路径上 ⇒ 原先 23 条全部 ModuleNotFoundError）。
    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    return CatgirlMemoryStore(path=str(path), logger=logger or _Logger())


def _write(p: Path, content: str) -> Path:
    p.write_text(content, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# D1 / D2：损坏文件必须被抢救，且不得丢数据
# --------------------------------------------------------------------------

def test_corrupt_trailing_residue_is_salvaged_not_wiped(tmp_path):
    """★D1+D2 核心：完整对象 + 中段残片 ⇒ 必须救回全部 runs，**不得清空**。"""
    runs = [{"run_id": f"R{i:02d}", "floor": i} for i in range(7)]
    good = json.dumps({"runs": runs, "updated_at": 1.0}, ensure_ascii=False)
    # 模拟线上形态：完整 JSON 之后紧跟一段 run 记录的**中段**（无 '{'、无 run_id）
    corrupt = good + '\n          "hp_start": 64,\n          "hp_end": 64,\n          "lowest_hp": 0,'

    p = _write(tmp_path / "m.json", corrupt)
    log = _Logger()
    store = _store(p, log)

    assert [r["run_id"] for r in store.runs] == [r["run_id"] for r in runs], (
        "抢救失败：损坏文件没有救回全部记录（这正是被修复的缺陷）"
    )
    assert len(store.runs) == 7


def test_salvage_is_logged_not_silent(tmp_path):
    """★D1：抢救必须**显式记日志**，不得静默。"""
    good = json.dumps({"runs": [{"run_id": "A"}]})
    p = _write(tmp_path / "m.json", good + '\n "hp_start": 1,')

    log = _Logger()
    _store(p, log)
    text = log.text().lower()
    assert "recover" in text, f"抢救未被记录（静默）：{log.lines!r}"


def test_wiping_loader_would_lose_data(tmp_path):
    """反向对照：证明本测试**真的能捕获**「清空」这一缺陷。

    这里用「旧行为」的等价实现直接对照 —— 若把 load 改回 `self._runs = []`，
    上面的断言必然转红（D7 的变异判定依据）。
    """
    good = json.dumps({"runs": [{"run_id": "A"}, {"run_id": "B"}]})
    p = _write(tmp_path / "m.json", good + '\n "hp_start": 1,')

    # 旧行为：json.load 失败即清空
    old_runs: list = []
    try:
        old_runs = json.loads(p.read_text(encoding="utf-8")).get("runs") or []
    except ValueError:
        old_runs = []  # ← 被修复的缺陷
    assert old_runs == [], "旧行为应当丢光数据"

    # 新行为：抢救
    assert len(_store(p).runs) == 2, "新行为必须救回数据"


def test_salvaged_ids_match_an_independent_parse(tmp_path):
    """★D2：抢救结果的 run_id 集合必须与独立解析**完全一致**。"""
    runs = [{"run_id": f"K{i}"} for i in range(5)]
    good = json.dumps({"runs": runs})
    text = good + '\n "hp_start": 64,'

    expected, _ = json.JSONDecoder().raw_decode(text)
    expected_ids = [r["run_id"] for r in expected["runs"]]

    p = _write(tmp_path / "m.json", text)
    assert [r["run_id"] for r in _store(p).runs] == expected_ids


# --------------------------------------------------------------------------
# D3：多种残片形态都必须能救（机制无关的防御）
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "residue",
    [
        '\n          "hp_start": 64,',          # 线上实际形态（对象中段）
        ',\n  "hp_start": 64,',                  # 逗号起始形态
        '\n{"runs": []}',                        # 第二个完整文档（串联）
        '\n<<<garbage>>>',                       # 纯垃圾
        '\n' + "x" * 5000,                       # 长垃圾
    ],
    ids=["mid-object", "comma", "second-doc", "garbage", "long-garbage"],
)
def test_all_residue_shapes_are_salvaged(tmp_path, residue):
    """残片形态各异，但**只要第一个完整对象可解析，就必须救回它**。"""
    runs = [{"run_id": "A"}, {"run_id": "B"}, {"run_id": "C"}]
    good = json.dumps({"runs": runs}, ensure_ascii=False)
    p = _write(tmp_path / "m.json", good + residue)

    store = _store(p)
    assert [r["run_id"] for r in store.runs] == ["A", "B", "C"], (
        f"残片形态 {residue[:20]!r} 未被抢救"
    )


# --------------------------------------------------------------------------
# D4：向前兼容 —— 任何输入都不得抛异常
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,content,expect",
    [
        ("normal", '{"runs": [{"run_id": "N1"}, {"run_id": "N2"}]}', 2),
        ("empty", "", 0),
        ("whitespace", "   \n\t ", 0),
        ("non-json", "this is not json at all <<<>>>", 0),
        ("json-array", "[1, 2, 3]", 0),
        ("json-scalar", "42", 0),
        ("no-runs-key", '{"updated_at": 1}', 0),
        ("runs-not-list", '{"runs": {"a": 1}}', 0),
        ("runs-with-non-dicts", '{"runs": [1, "x", {"run_id": "OK"}]}', 1),
    ],
)
def test_forward_compatible_loads_never_raise(tmp_path, name, content, expect):
    """★D4：正常/空/非 JSON/形状异常 ⇒ 一律不报错且行为合理。"""
    p = _write(tmp_path / "m.json", content)
    store = _store(p)  # 不得抛异常
    assert len(store.runs) == expect, name


def test_missing_file_does_not_raise(tmp_path):
    """★D4：文件不存在 ⇒ 空载，不报错。"""
    store = _store(tmp_path / "nope.json")
    assert store.runs == []


def test_none_path_is_memory_only(tmp_path):
    """path=None ⇒ 纯内存，不落盘不读盘。"""
    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    store = CatgirlMemoryStore(path=None)
    assert store.runs == []
    store.save()  # 不得抛异常


# --------------------------------------------------------------------------
# save() 的加固：唯一 tmp 名 + fsync，且不留残file
# --------------------------------------------------------------------------

def test_save_roundtrip_leaves_no_tmp_files(tmp_path):
    p = tmp_path / "m.json"
    store = _store(p)
    store._runs = [{"run_id": "RT1"}, {"run_id": "RT2"}]
    store.save()

    assert p.exists()
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftovers == [], f"留下了临时文件：{leftovers}"
    with open(p, encoding="utf-8") as f:
        assert json.load(f)["runs"][0]["run_id"] == "RT1"


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "m.json"
    s1 = _store(p)
    s1._runs = [{"run_id": "A"}, {"run_id": "B"}]
    s1.save()
    assert [r["run_id"] for r in _store(p).runs] == ["A", "B"]


def test_salvage_then_save_preserves_all_runs(tmp_path):
    """★危险的链是「load 得 0 → save 写回 0」。抢救后 save 必须保住全部记录。"""
    runs = [{"run_id": f"P{i}"} for i in range(9)]
    good = json.dumps({"runs": runs}, ensure_ascii=False)
    p = _write(tmp_path / "m.json", good + '\n "hp_start": 64,')

    store = _store(p)
    assert len(store.runs) == 9
    store.save()

    reloaded = _store(p)
    assert [r["run_id"] for r in reloaded.runs] == [f"P{i}" for i in range(9)], (
        "抢救后 save 丢数据 —— 这正是本任务要防的危险链"
    )
