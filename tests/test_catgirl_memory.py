from __future__ import annotations

import os
from pathlib import Path

import pytest
from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore
from plugin.plugins.sts2_autoplay.strategy_repository import STS2StrategyRepository


class _DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


def _run_meta(run_id: str, outcome: str = "defeat", floor: int = 12, act: int = 1) -> dict:
    return {"character": "IRONCLAD", "outcome": outcome, "floor": floor, "act": act}


@pytest.mark.unit
def test_store_add_save_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "catgirl_memory.json"
    store = CatgirlMemoryStore(str(path), logger=_DummyLogger())

    record = store.add_run(
        "r-1",
        character="IRONCLAD",
        outcome="defeat",
        floor=12,
        act=1,
        decision_memory=[{"action_type": "attack", "reason": "翻车", "current_hp": 10, "max_hp": 80}],
    )
    assert record["lessons"]  # 启发式至少产出一条教训
    assert os.path.exists(str(path))

    # 重新实例化（模拟重启）应读到同一批
    store2 = CatgirlMemoryStore(str(path), logger=_DummyLogger())
    assert len(store2.runs) == 1
    assert store2.runs[0]["run_id"] == "r-1"
    assert store2.runs[0]["lessons"] == record["lessons"]


@pytest.mark.unit
def test_add_run_same_run_id_replaces() -> None:
    store = CatgirlMemoryStore(None, logger=_DummyLogger())
    store.add_run("r-x", character="C", outcome="defeat", floor=1, act=1, decision_memory=[])
    store.add_run("r-x", character="C", outcome="victory", floor=5, act=2, decision_memory=[])
    assert len(store.runs) == 1
    assert store.runs[0]["outcome"] == "victory"


@pytest.mark.unit
def test_summarize_recent_dedupes_across_runs() -> None:
    store = CatgirlMemoryStore(None, logger=_DummyLogger())
    # 两局都有同一条"低血翻车"教训 -> 聚合后只出现一次
    store.add_run("r-1", **_run_meta("r-1"), decision_memory=[
        {"action_type": "fight", "reason": "低血还贪输出", "current_hp": 20, "max_hp": 80},
    ])
    store.add_run("r-2", **_run_meta("r-2"), decision_memory=[
        {"action_type": "fight", "reason": "低血还贪输出", "current_hp": 15, "max_hp": 80},
    ])
    summary = store.summarize_recent()
    lessons = summary["lessons"]
    assert len(lessons) == len(set(lessons))
    assert any("低血" in item or "保命" in item for item in lessons)


@pytest.mark.unit
def test_heuristic_lessons_from_low_hp_and_defeat() -> None:
    store = CatgirlMemoryStore(None, logger=_DummyLogger())
    memory = [
        {"action_type": "fight", "current_hp": 10, "max_hp": 80},
        {"action_type": "fight", "current_hp": 8, "max_hp": 80},
    ]
    summary = store.summarize_run(decision_memory=memory, run_meta=_run_meta("r-1"))
    assert any("保命" in item or "低血" in item for item in summary["lessons"])


@pytest.mark.unit
def test_strategy_repository_injects_memory_into_directives() -> None:
    store = CatgirlMemoryStore(None, logger=_DummyLogger())
    store.add_run("r-1", **_run_meta("r-1"), decision_memory=[
        {"action_type": "attack", "reason": "翻车", "current_hp": 8, "max_hp": 80},
    ])
    repo = STS2StrategyRepository(_DummyLogger(), _PrefStoreStub(), catgirl_memory=store)
    snapshot = {"screen": "combat", "classification": {"screen_class": "combat"}}
    ctx = repo.build_context(snapshot)
    directives = ctx["strategy_directives"]
    assert isinstance(directives.get("avoid"), list)
    assert isinstance(directives.get("prefer"), list)
    # 注入不会产生重复条目
    assert len(directives["avoid"]) == len(set(directives["avoid"]))
    assert len(directives["prefer"]) == len(set(directives["prefer"]))


@pytest.mark.unit
def test_repository_without_memory_is_unchanged() -> None:
    repo = STS2StrategyRepository(_DummyLogger(), _PrefStoreStub())
    snapshot = {"screen": "combat", "classification": {"screen_class": "combat"}}
    ctx = repo.build_context(snapshot)
    # 默认无 memory：avoid/prefer 仍为列表且不应含记忆教训
    assert isinstance(ctx["strategy_directives"].get("avoid"), list)
    assert ctx["strategy_directives"]["avoid"] == []


@pytest.mark.unit
def test_memory_path_default_resolves_to_localappdata() -> None:
    # path=None 时为纯内存（不落盘）；显式传 undefined 则不解析默认路径。
    store = CatgirlMemoryStore(None, logger=_DummyLogger())
    assert store.path is None
    assert not store.runs  # 纯内存起始为空，不应误读磁盘


class _PrefStoreStub:
    def get(self, domain: str, key: object) -> None:
        return None

    def list_domain(self, domain: str) -> list:
        return []
