"""局末总结：损血归因 → 结构化记录 → 对照复盘（玩家的选择 vs 当时的推荐）→ 历史页。

用户口径："每次打完做一次总结（**每局结束一次**）…… 看看哪些地方损血导致失败，
下一次根据情况做出更好的选择"，反思"插件生成 + 也告诉本体"。

顺带修掉一个从来没生效过的东西：`catgirl_memory.summarize_run` 里那条"LLM 自然语言归纳"
是**同步**调用 `llm.generate(...)`（`async` 方法），拿到协程后 `.splitlines()` 抛错被吞；
外面还有 `self._engine == "llm"` 这道默认关闭的闸门 —— 那条路一直是死的。
后来的「LLM 反思当教训」与「复盘期间暂停自动玩」也已删除：局末只把玩家亲手打的步里
和推荐差得最多的几步交给模型讲（`_explain_review`），讲解经 `attach_review` 贴回记录，不进教训。
"""

from __future__ import annotations

import asyncio

import pytest
from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def exception(self, *a, **k):
        return None


def _service() -> STS2AutoplayService:
    return STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: None)


def _seed_trail(service: STS2AutoplayService, samples: list[tuple[int, str, int]]) -> None:
    service._state.hp_trail = [
        {"hp": hp, "max_hp": 80, "screen": screen, "floor": floor, "step_count": index, "at": float(index)}
        for index, (hp, screen, floor) in enumerate(samples)
    ]


# ---- 损血归因 ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hp_loss_report_finds_the_heaviest_hits() -> None:
    """**"看看哪些地方损血导致失败"**：要把单次掉血最多的几处连界面/层级一起报出来。"""
    service = _service()
    _seed_trail(
        service,
        [(80, "MAP", 3), (62, "COMBAT", 4), (55, "COMBAT", 4), (13, "COMBAT", 6), (2, "COMBAT", 7)],
    )

    report = service._hp_loss_report()

    assert report["total_lost"] == 18 + 7 + 42 + 11
    assert report["loss_events"] == 4
    assert report["min_hp"] == 2
    assert report["final_hp"] == 2
    assert report["heaviest"][0]["lost"] == 42
    assert report["heaviest"][0]["screen"] == "COMBAT"
    # 界面/层级按"观测到掉血的那一刻"记（掉血发生在两次采样之间那场战斗里）。
    assert report["heaviest"][0]["floor"] == 6


@pytest.mark.unit
def test_hp_loss_report_is_empty_without_samples() -> None:
    assert _service()._hp_loss_report() == {}


@pytest.mark.unit
def test_hp_trail_resets_when_the_run_changes() -> None:
    """换局必须清零，否则上一局的损血会算到这一局头上。"""
    service = _service()
    service._remember_hp_sample({"run_id": "run-a", "screen": "COMBAT", "floor": 2, "raw_state": {"combat": {"player": {"current_hp": 50, "max_hp": 80}}}})
    assert len(service._state.hp_trail) == 1

    service._remember_hp_sample({"run_id": "run-b", "screen": "COMBAT", "floor": 1, "raw_state": {"combat": {"player": {"current_hp": 80, "max_hp": 80}}}})

    assert len(service._state.hp_trail) == 1
    assert service._state.hp_trail[0]["hp"] == 80


@pytest.mark.unit
def test_hp_sample_is_not_duplicated_when_nothing_changes() -> None:
    service = _service()
    snapshot = {"run_id": "run-a", "screen": "COMBAT", "floor": 2, "raw_state": {"combat": {"player": {"current_hp": 50, "max_hp": 80}}}}

    service._remember_hp_sample(snapshot)
    service._remember_hp_sample(snapshot)

    assert len(service._state.hp_trail) == 1


# ---- 对照复盘的讲解 ---------------------------------------------------------------------

_DIVERGENT_STEP = {
        "source": "network", "decision": "COMBAT", "floor": 6, "act": 1, "round": 2, "room": "NIBBIT",
        "situation": "血 30/80", "hp": 30, "max_hp": 80, "chosen": "play 打击 -> 小啃兽", "recommended": "play 防御",
        "same": False, "p_chosen": 0.05, "p_recommended": 0.8, "gap": 0.75,
    }


def _review() -> dict:
    return {"compared": 4, "agreed": 3, "network_steps": 4, "planner_steps": 0, "steps": [dict(_DIVERGENT_STEP)], "explanation": ""}


@pytest.mark.unit
def test_explain_review_stores_the_explanation_and_tells_host(tmp_path) -> None:
    """讲解要贴回这一局的记录、推给本体；它不是教训，不进 lessons，也不发弹幕。"""
    host: list[dict] = []
    pushed: list[str] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: host.append(k))
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    record = service._catgirl_memory.add_run(
        "run-a", character="绯刃战姬", outcome="defeat", floor=7, act=1, decision_memory=[],
        hp_report={"total_lost": 42, "loss_events": 1, "min_hp": 0, "max_hp": 80, "heaviest": []},
    )
    lessons_before = list(record["lessons"])
    seen: dict = {}

    class Gen:
        available = True

        async def generate(self, **kw):
            seen.update(kw)
            return "第6层那回合只剩30血，我想先垫防御喵；你抢着打击也不是不行，就是赌它不出重击。"

    service._catgirl_llm = Gen()
    service._schedule_catgirl_push = lambda text, **kw: pushed.append(text)

    asyncio.run(service._explain_review(dict(record), _review()))

    assert seen["summary_kind"] == "run_review"
    assert "play 打击 -> 小啃兽" in seen["summary_text"] and "5%" in seen["summary_text"] and "80%" in seen["summary_text"]
    row = service._catgirl_memory.runs[-1]
    assert row["review"]["explanation"].startswith("第6层那回合")
    assert row["review"]["steps"][0]["chosen"] == "play 打击 -> 小啃兽"
    assert row["lessons"] == lessons_before, "讲解不是教训，不能进 lessons 影响后面的决策"
    assert pushed == [], "讲解是几句话，不走弹幕"
    # 推给本体：不可见 + 不进记忆 + 让它主动开口。
    assert host and host[-1]["visibility"] == []
    assert host[-1]["ai_behavior"] == "respond"
    assert host[-1]["metadata"]["prompt_ephemeral"] is True
    assert host[-1]["metadata"]["kind"] == "run_review"


@pytest.mark.unit
def test_explain_review_survives_a_silent_llm(tmp_path) -> None:
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    service._catgirl_memory.add_run("run-a", character="C", outcome="defeat", floor=3, act=1, decision_memory=[])

    class Gen:
        available = True

        async def generate(self, **kw):
            return None

    service._catgirl_llm = Gen()
    record = {"run_id": "run-a", "outcome": "defeat", "character": "C", "floor": 3}

    asyncio.run(service._explain_review(dict(record), _review()))  # 不应抛异常

    assert "review" not in service._catgirl_memory.runs[-1], "没产出讲解就不该改动记录"


# ---- 房间标识：复盘必须按"哪个房间"，不是"第几层" --------------------------------------


@pytest.mark.unit
def test_room_identity_uses_enemy_composition_for_combat() -> None:
    """**"复盘说第 N 层没有用"的回归测试**：尖塔地图随机，同一层在不同局是不同的怪。

    mod 没有 encounter/room id，能给的是 `combat.enemies[].enemy_id`，所以战斗用**敌人组合**当房间标识。
    """
    raw = {
        "combat": {
            "enemies": [
                {"enemy_id": "LEAF_SLIME_M", "is_alive": True},
                {"enemy_id": "LEAF_SLIME_S", "is_alive": True},
            ]
        }
    }

    assert STS2AutoplayService._room_identity("COMBAT", raw) == "LEAF_SLIME_M+LEAF_SLIME_S"


@pytest.mark.unit
def test_room_identity_prefers_event_id_then_room_type() -> None:
    assert STS2AutoplayService._room_identity("EVENT", {"event": {"event_id": "THE_CLAW"}}) == "THE_CLAW"
    assert STS2AutoplayService._room_identity("SHOP", {}) == "room:SHOP"
    assert STS2AutoplayService._room_identity("REST", {}) == "room:REST"


@pytest.mark.unit
def test_room_identity_is_pinned_at_the_start_of_a_fight() -> None:
    """战斗中途敌人会死；若每帧重取，同一场战斗会被拆成几个"房间"，归因就散了。"""
    service = _service()
    start = {
        "run_id": "run-a",
        "screen": "COMBAT",
        "in_combat": True,
        "floor": 3,
        "raw_state": {
            "combat": {
                "player": {"current_hp": 70, "max_hp": 80},
                "enemies": [{"enemy_id": "A", "is_alive": True}, {"enemy_id": "B", "is_alive": True}],
            }
        },
    }

    service._remember_hp_sample(start)
    assert service._state.combat_room_id == "A+B"

    # 打死一只之后：HP 变了要记新采样，但房间标识仍是这场遭遇开始时的组合。
    later = {
        "run_id": "run-a",
        "screen": "COMBAT",
        "in_combat": True,
        "floor": 3,
        "raw_state": {
            "combat": {
                "player": {"current_hp": 55, "max_hp": 80},
                "enemies": [{"enemy_id": "A", "is_alive": True}, {"enemy_id": "B", "is_alive": False}],
            }
        },
    }
    service._remember_hp_sample(later)

    rooms = [sample["room"] for sample in service._state.hp_trail]
    assert rooms == ["A+B", "A+B"], "同一场战斗必须共用一个房间标识"
    report = service._hp_loss_report()
    assert report["heaviest"][0]["room"] == "A+B"


@pytest.mark.unit
def test_room_identity_resets_when_the_fight_ends() -> None:
    service = _service()
    service._remember_hp_sample(
        {
            "run_id": "run-a", "screen": "COMBAT", "in_combat": True, "floor": 3,
            "raw_state": {"combat": {"player": {"current_hp": 70, "max_hp": 80}, "enemies": [{"enemy_id": "A", "is_alive": True}]}},
        }
    )
    service._remember_hp_sample(
        {
            "run_id": "run-a", "screen": "REWARD", "in_combat": False, "floor": 3,
            "raw_state": {"run": {"current_hp": 70, "max_hp": 80}},
        }
    )

    assert service._state.combat_room_id == ""
    assert service._state.hp_trail[-1]["room"] == "room:REWARD"


@pytest.mark.unit
def test_loss_attribution_carries_the_room_not_just_the_floor() -> None:
    service = _service()
    service._state.hp_trail = [
        {"hp": 80, "max_hp": 80, "screen": "COMBAT", "room": "毛绒伏地虫", "floor": 6, "step_count": 1, "at": 0.0},
        {"hp": 38, "max_hp": 80, "screen": "COMBAT", "room": "毛绒伏地虫", "floor": 6, "step_count": 2, "at": 1.0},
    ]

    heaviest = service._hp_loss_report()["heaviest"][0]

    assert heaviest["room"] == "毛绒伏地虫"
    assert heaviest["lost"] == 42


# ---- 记忆存储本身 -----------------------------------------------------------------------


@pytest.mark.unit
def test_add_run_records_hp_report_and_decisions(tmp_path) -> None:
    store = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())

    record = store.add_run(
        "run-a", character="C", outcome="defeat", floor=7, act=1,
        decision_memory=[{"screen": "COMBAT", "action_type": "play_card", "decision_reason": "贪"}],
        hp_report={"total_lost": 42},
    )

    assert record["hp"]["total_lost"] == 42
    assert record["decisions"][0]["action_type"] == "play_card"
    assert "reflection" not in record, "LLM 反思已删除，新记录不再带这个字段"


@pytest.mark.unit
def test_attach_review_is_stored_and_never_becomes_a_lesson(tmp_path) -> None:
    store = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    store.add_run("run-a", character="C", outcome="defeat", floor=7, act=1, decision_memory=[])
    lessons = list(store.runs[-1]["lessons"])

    store.attach_review("run-a", {"compared": 2, "agreed": 1, "steps": [], "explanation": "讲解"})

    assert store.runs[-1]["review"]["explanation"] == "讲解"
    assert store.runs[-1]["lessons"] == lessons
    assert "讲解" not in store.summarize_recent()["lessons"]
    assert CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger()).runs[-1]["review"]["compared"] == 2


@pytest.mark.unit
def test_attach_review_ignores_unknown_run() -> None:
    store = CatgirlMemoryStore(path=None, logger=DummyLogger())

    assert store.attach_review("nope", {"compared": 1}) is None


# ---- 真实触发路径：终端屏 -> 汇总 -> 落盘 -> 写历史快照 ----------------------------------


@pytest.mark.unit
def test_terminal_screen_triggers_run_record_and_history_snapshot(tmp_path, monkeypatch) -> None:
    """**把"终端屏"这条真实入口整条串起来测。**

    上面那些测试是分别打各块的（损血归因 / 落盘 / 反思 / 历史快照），但把它们**接线**起来的
    `_maybe_record_run_memory` 才是真正被线上调用的那一处 —— 这里从"分类为 terminal 的快照"
    一路验到"记忆里有这条记录 + 历史快照文件被写出来"。
    """
    import plugin.plugins.sts2_autoplay.service as service_module

    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    service._state.recent_decision_memory = [
        {"screen": "COMBAT", "action_type": "play_card", "decision_reason": "贪输出", "step_count": 12}
    ]
    _seed_trail(service, [(80, "COMBAT", 6), (38, "COMBAT", 6), (0, "COMBAT", 7)])

    written: list[str] = []
    # service.py 是 `from .history_view import write_history_view` 的模块级绑定，
    # 所以要补 service 里的那个名字，而不是 history_view 里的。
    monkeypatch.setattr(
        service_module, "write_history_view",
        lambda runs, **kw: written.append("called") or str(tmp_path / "snap.json"),
    )

    class Gen:
        available = True

        async def generate(self, **kw):
            return "下次血不过半先垫格挡喵。"

    service._catgirl_llm = Gen()
    service._schedule_catgirl_push = lambda text, **kw: None
    service._choice_review.steps = [dict(_DIVERGENT_STEP)]

    snapshot = {
        "classification": {"screen_class": "terminal"},
        "screen": "GAME_OVER",
        "run_id": "run-e2e",
        "character": "IRONCLAAD",
        "floor": 7,
        "act": 1,
        "raw_state": {"run": {"character_id": "IRONCLAD", "character_name": "绯刃战姬"}},
    }

    async def drive() -> None:
        service._maybe_record_run_memory(snapshot)
        # 让 fire-and-forget 的反思任务跑完
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(drive())

    rows = service._catgirl_memory.runs
    assert len(rows) == 1, "终端屏必须立刻落一条本局记录"
    assert rows[0]["run_id"] == "run-e2e"
    assert rows[0]["outcome"] == "defeat"
    assert rows[0]["hp"]["total_lost"] == 80, "损血必须来自本局生命轨迹"
    assert rows[0]["decisions"][0]["action_type"] == "play_card"
    # 会被写两次：局末落记录时一次、讲解贴回后再一次（让历史页也能看到讲解）。
    assert written, "局末必须刷新历史快照"
    assert rows[0]["review"]["compared"] == 1, "对照要随局末记录一起落盘"
    assert rows[0]["review"]["explanation"].startswith("下次血不过半"), "异步讲解要贴回同一条记录"
    assert service._choice_review.steps == [], "对照按局累加，局末必须清空"


@pytest.mark.unit
def test_terminal_screen_is_recorded_only_once_per_run(tmp_path) -> None:
    """终端屏会反复出现在快照里，同一局只能记一次（否则记忆里堆一堆重复记录）。"""
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    snapshot = {
        "classification": {"screen_class": "terminal"},
        "screen": "GAME_OVER",
        "run_id": "run-once",
        "character": "C",
        "floor": 3,
        "act": 1,
        "raw_state": {},
    }

    async def drive() -> None:
        service._maybe_record_run_memory(snapshot)
        service._maybe_record_run_memory(snapshot)

    asyncio.run(drive())

    assert len(service._catgirl_memory.runs) == 1


@pytest.mark.unit
def test_non_terminal_screen_does_not_record_a_run(tmp_path) -> None:
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    snapshot = {"classification": {"screen_class": "combat"}, "screen": "COMBAT", "run_id": "run-x", "raw_state": {}}

    asyncio.run(service._maybe_record_run_memory(snapshot) or asyncio.sleep(0))

    assert service._catgirl_memory.runs == []


@pytest.mark.parametrize("reenable", [False, True])
def test_review_disabled_during_generation_discards_pending_explanation(tmp_path, reenable):
    host = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: host.append(k))
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    record = service._catgirl_memory.add_run("run-a", character="C", outcome="defeat", floor=3, act=1, decision_memory=[])
    service._catgirl_memory.attach_review("run-a", _review())

    class Gen:
        available = True

        async def generate(self, **kw):
            service.set_run_reflection_enabled(False)
            if reenable:
                service.set_run_reflection_enabled(True)
            await asyncio.sleep(0)
            return "This pending explanation must not be delivered."

    service._catgirl_llm = Gen()
    asyncio.run(service._explain_review(dict(record), _review()))
    assert host == []
    assert service._catgirl_memory.runs[-1]["review"]["explanation"] == ""
    assert service._catgirl_memory.runs[-1]["review"]["steps"]


def test_review_disabled_before_task_starts_never_calls_llm():
    from unittest.mock import AsyncMock

    service = _service()
    service._catgirl_llm = AsyncMock(available=True)
    service.set_run_reflection_enabled(False)
    asyncio.run(service._explain_review({"run_id": "run-a"}, _review()))
    service._catgirl_llm.generate.assert_not_called()


def test_queued_review_is_invalidated_even_if_reenabled_before_start():
    from unittest.mock import AsyncMock

    service = _service()
    service._catgirl_llm = AsyncMock(available=True)
    generation = service._review_generation
    service.set_run_reflection_enabled(False)
    service.set_run_reflection_enabled(True)
    asyncio.run(service._explain_review({"run_id": "run-a"}, _review(), generation=generation))
    service._catgirl_llm.generate.assert_not_called()
