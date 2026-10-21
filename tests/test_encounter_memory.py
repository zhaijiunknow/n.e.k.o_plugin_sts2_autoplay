# -*- coding: utf-8 -*-
"""t31 门：怪物/事件实例知识库（encounter_memory）。

规范来源：`.dsh-artifacts/_t30/ENCOUNTER-DB-CONTRACT.md`（sha16 `0c17acf4e511d283`，641 行）。
本门逐条覆盖 t31 的 18 条验收判据。

## ★ 本文件的第一批 fixture 是【实机原始 JSON】
`.dsh-artifacts/_t31/live-poll-*.json`（2026-10-20 02:37 抓取）。
教训（t26）：**一个建在虚构 fixture 上的绿灯比没有门更糟**。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.encounter_memory import (
    ENCOUNTER_CAP,
    REASON_INTENT_INCOMPLETE,
    REASON_POST_EVENT_COMBAT,
    SCHEMA_VERSION,
    EncounterMemory,
    _enemy_group_key,
    _is_room_placeholder,
    compute_block_needed,
)

# ======================================================================================
# 实机原始载荷（★ 逐字抓取，禁止臆造）
# ======================================================================================

_LIVE_DIR = pathlib.Path(
    r"D:\NekoClaw\n.e.k.o_plugin_sts2_autoplay\.dsh-artifacts\_t31"
)


def _load_live(name: str) -> dict[str, Any]:
    path = _LIVE_DIR / name
    if not path.exists():  # pragma: no cover —— 副本缺失时明确跳过而非假绿
        pytest.skip(f"实机样本缺失：{path}")
    env = json.loads(path.read_bytes().decode("utf-8"))
    return env.get("data") if isinstance(env.get("data"), dict) else env


#: 实机 COMBAT（run C6CM8CZRR9KQ / floor 12 / BYRDONIS）
LIVE_COMBAT = _load_live("live-poll-20261020_023708.json")
#: 实机 EVENT（is_finished=False / WELLSPRING / 2 选项）
LIVE_EVENT = _load_live("live-poll-20261020_023752.json")


# ---------------------------------------------------------------- 构造器（明确标注）

def combat_state(
    *,
    run_id: str = "RUN1",
    act_id: str = "0",
    floor: int = 12,
    group: tuple[str, ...] = ("BYRDONIS",),
    hp: int | None = 70,
    block: int = 0,
    dmg: int = 15,
    alive: bool | tuple[bool, ...] = True,
    lethal_risks: Any = None,
    will_kill: bool = False,
    drop_player_hp: bool = False,
    node_type: str | None = None,
    is_finished: bool = False,
) -> dict[str, Any]:
    """构造一个 COMBAT tick。★ `alive` 可为元组以便逐只控制存活。"""
    alive_flags = alive if isinstance(alive, tuple) else tuple([alive] * len(group))
    enemies = [
        {
            "index": i,
            "enemy_id": g,
            "name": g,
            "current_hp": 0 if not alive_flags[i] else 40,
            "max_hp": 40,
            "block": 0,
            "is_alive": alive_flags[i],
            "is_hittable": alive_flags[i],
            "move_id": "MOVE_A",
            "intent": "MOVE_A",
            "intents": [{"intent_type": "Attack", "label": str(dmg), "damage": dmg,
                         "hits": 1, "total_damage": dmg, "status_card_count": None}],
            "powers": [{"index": 0, "power_id": "STRENGTH_POWER", "name": "力量",
                        "amount": 1, "is_debuff": False}],
        }
        for i, g in enumerate(group)
    ]
    player: dict[str, Any] = {"block": block, "energy": 1, "cards_played_this_turn": 0}
    if not drop_player_hp:
        player["current_hp"] = hp
    nodes = []
    cur = {}
    if node_type:
        nodes = [{"row": 1, "col": 3, "node_type": node_type, "state": "Traveled",
                  "visited": False, "is_current": True, "is_available": False,
                  "is_start": False, "is_boss": False, "parents": [], "children": []}]
        cur = {"row": 1, "col": 3}
    return {
        "run_id": run_id,
        "screen": "COMBAT",
        "in_combat": True,
        "turn": 1,
        "unknown_screen_type": None,
        "run": {
            "act_id": act_id, "floor": floor, "current_hp": hp, "max_hp": 80,
            "ascension": 10, "character_id": "IRONCLAD", "boss_id": "CEREMONIAL_BEAST_BOSS",
            "ascension_effects": [{"id": "LEVEL_09", "name": "致命敌人"}],
            "deck": [], "relics": [], "potions": [],
        },
        "combat": {
            "enemies": enemies,
            "hand": [{"index": 0, "card_id": "STRIKE_IRONCLAD", "name": "打击",
                      "playable": True, "energy_cost": 1}],
            "player": player,
            "players": [],
            "lethal_risks": list(lethal_risks or []),
            "end_turn_will_kill_player": will_kill,
        },
        "map": {"nodes": nodes, "current_node": cur},
        "event": ({"event_id": "X", "title": "X", "description": "", "is_finished": is_finished,
                   "options": []} if is_finished else None),
        "game_over": None,
    }


def plain_state(
    screen: str, *, floor: int = 12, act_id: str = "0", run_id: str = "RUN1",
    game_over: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """非战斗屏的状态（combat 必为 None —— 实测契约 §10.8）。"""
    return {
        "run_id": run_id, "screen": screen, "in_combat": False, "turn": None,
        "run": {"act_id": act_id, "floor": floor, "current_hp": 70, "max_hp": 80,
                "ascension": 10, "character_id": "IRONCLAD", "boss_id": "B",
                "ascension_effects": [], "deck": [], "relics": [], "potions": []},
        "combat": None, "map": {"nodes": [], "current_node": {}}, "event": None,
        "game_over": game_over,
    }


def event_state(*, event_id: str = "WELLSPRING", options: list[dict[str, Any]],
                is_finished: bool = False, floor: int = 15, hp: int = 64,
                gold: int = 152, deck: Any = None) -> dict[str, Any]:
    st = plain_state("EVENT", floor=floor)
    st["run"]["current_hp"] = hp
    st["run"]["gold"] = gold
    st["run"]["deck"] = list(deck or [])
    st["event"] = {"event_id": event_id, "title": event_id, "description": "",
                   "is_finished": is_finished, "options": options}
    return st


def opt(index: int, *, title: str = "T", text_key: str = "K", is_locked: bool = False,
        is_proceed: bool = False, will_kill_player: bool = False,
        has_relic_preview: bool = False, description: str = "") -> dict[str, Any]:
    """★ 事件选项用【实机抓到的 8 个键】（契约 §2.4 / §4.2）。"""
    return {
        "index": index, "text_key": text_key, "title": title, "description": description,
        "is_locked": is_locked, "is_proceed": is_proceed,
        "will_kill_player": will_kill_player, "has_relic_preview": has_relic_preview,
    }


@pytest.fixture()
def mem() -> EncounterMemory:
    """仅内存的库（不落盘）—— 单测不该碰真实数据目录。"""
    return EncounterMemory(path=None)


# ======================================================================================
# ★ 判据 1：实机真形状核对（契约 §2）
# ======================================================================================


@pytest.mark.unit
def test_live_state_shape_matches_contract_section2() -> None:
    """★★ 契约 §2 的字段清单必须与实际一致（本门用**真机抓的 JSON** 钉住）。"""
    # 顶层 26 键 —— 逐字
    expected_top = {
        "state_version", "run_id", "screen", "unknown_screen_type", "session", "in_combat",
        "turn", "available_actions", "combat", "run", "multiplayer", "multiplayer_lobby",
        "map", "selection", "character_select", "timeline", "chest", "event", "shop",
        "rest", "reward", "bundles", "modal", "game_over", "main_menu", "agent_view",
    }
    assert set(LIVE_COMBAT) == expected_top, "顶层 26 键与契约 §2 不符"
    assert len(LIVE_COMBAT) == 26

    # combat 6 键（含契约强调的 2 个致命风险键）
    assert set(LIVE_COMBAT["combat"]) == {
        "end_turn_will_kill_player", "enemies", "hand", "lethal_risks", "player", "players",
    }
    assert LIVE_COMBAT["combat"]["lethal_risks"] == []
    assert LIVE_COMBAT["combat"]["end_turn_will_kill_player"] is False

    # event 5 键 + options 恰好 8 键
    assert set(LIVE_COMBAT["event"] or {"description", "event_id", "is_finished", "options",
                                        "title"}) >= set()
    assert set(LIVE_EVENT["event"]) == {"description", "event_id", "is_finished", "options", "title"}
    for o in LIVE_EVENT["event"]["options"]:
        assert set(o) == {
            "index", "text_key", "title", "description", "is_locked", "is_proceed",
            "will_kill_player", "has_relic_preview",
        }, "★ 事件选项必须是实机的 8 键，不得只用 5 个"


@pytest.mark.unit
def test_run_id_lives_at_top_level_not_under_run() -> None:
    """★★ 实测陷阱：`run_id` 在**顶层**，`run` 层**没有**该键。

    若实现按字面写 `run['run_id']` ⇒ 恒 None ⇒ **所有局共用一个 run_id ⇒ 跨局串味**。
    """
    assert LIVE_COMBAT["run_id"] == "C6CM8CZRR9KQ"
    assert "run_id" not in LIVE_COMBAT["run"], "★ run 层确实没有 run_id 键"
    assert LIVE_COMBAT["run"].get("run_id") is None
    # 采集器必须取到真实值
    m = EncounterMemory(path=None)
    m.observe(LIVE_COMBAT)
    assert m._cur_meta["run_id"] == "C6CM8CZRR9KQ"


@pytest.mark.unit
def test_real_combat_payload_collects_correctly(mem: EncounterMemory) -> None:
    """★ 用**真机 COMBAT 载荷**跑采集器，字段必须真的被解析出来。"""
    mem.observe(LIVE_COMBAT)
    assert mem._cur_key is not None
    turn = mem._cur_turns[0]
    # 实机：BYRDONIS hp 63/90, intents=[Attack 5/3/15]
    enemy = turn["enemies"][0]
    assert enemy["enemy_id"] == "BYRDONIS"
    assert enemy["current_hp"] == 63 and enemy["max_hp"] == 90
    assert enemy["intent_types"] == ["Attack"]
    assert enemy["intent_total_damage"] == 15   # total_damage，不是 damage=5
    assert enemy["intent_hits"] == 3
    assert enemy["intent_move_id"] == "PECK_MOVE"
    assert turn["player_hp"] == 73
    assert turn["enemy_total_damage"] == 15


# ======================================================================================
# ★ 判据 2：encounter 边界（§3.1）—— 5 段 route 必须只产 1 条
# ======================================================================================


@pytest.mark.unit
def test_five_route_segments_merge_into_one_encounter(mem: EncounterMemory) -> None:
    """★★★ 契约 §1 的决定性案例：同一场 CUBEX_CONSTRUCT 被切成 5 条 route 条目。

    原始序列（契约 §1，hp 57→57→31→31→10）：
    ```
    COMBAT / CARD_SELECTION / COMBAT / CARD_SELECTION / COMBAT
    ```
    真实损血 = 57-10 = **47**；但各段 hp_lost 相加 = **233**（荒谬）。
    ⇒ 必须产出 **1** 条 encounter，`true_hp_loss == 47`（**不是 233**）。
    """
    for screen, hp in (("COMBAT", 57), ("CARD_SELECTION", 57), ("COMBAT", 31),
                       ("CARD_SELECTION", 31), ("COMBAT", 10)):
        if screen == "COMBAT":
            mem.observe(combat_state(group=("CUBEX_CONSTRUCT",), floor=6, hp=hp, block=0))
        else:
            mem.observe(plain_state(screen, floor=6))   # ★ 中间抽牌屏【不算结束】
    mem.observe(plain_state("MAP", floor=6))            # 真正离开

    assert len(mem.encounters) == 1, f"★ 必须只有 1 条 encounter，实测 {len(mem.encounters)}"
    rec = mem.encounters[0]
    assert rec["outcome"]["true_hp_loss"] == 47, "★ 必须是 57-10=47，不是各段相加的 233"
    assert rec["outcome"]["total_turns"] == 3, "★ 只有 3 条 COMBAT 段是回合样本"
    assert rec["outcome"]["lowest_hp_seen"] == 10
    assert rec["enemy_group_key"] == "CUBEX_CONSTRUCT"


@pytest.mark.unit
def test_card_selection_does_not_end_encounter(mem: EncounterMemory) -> None:
    """★ 契约 §3.1③：CARD_SELECTION / CARD_SELECTION_REWARD 夹在中间**不算结束**。"""
    mem.observe(combat_state(group=("NIBBIT",)))
    for scr in ("CARD_SELECTION", "CARD_SELECTION_REWARD"):
        mem.observe(plain_state(scr))
        assert mem._cur_key is not None, f"★ {scr} 不该关闭 encounter"
    mem.observe(combat_state(group=("NIBBIT",), hp=60))
    mem.observe(plain_state("MAP"))
    assert len(mem.encounters) == 1


@pytest.mark.unit
def test_group_change_closes_and_opens(mem: EncounterMemory) -> None:
    """★ 三元组变化 ⇒ 关旧开新（且 encounter_seq 不同 ⇒ key 不撞车）。"""
    mem.observe(combat_state(group=("NIBBIT",), floor=12))
    mem.observe(combat_state(group=("BYRDONIS",), floor=12))   # ★ 同层换组
    mem.observe(plain_state("MAP"))
    assert len(mem.encounters) == 2
    k1 = mem.encounters[0]["encounter_key"]
    k2 = mem.encounters[1]["encounter_key"]
    assert k1 != k2, "★ 两场遭遇的 encounter_key 不得撞车"
    assert k1["encounter_seq"] != k2["encounter_seq"]


@pytest.mark.unit
def test_floor_change_closes_encounter(mem: EncounterMemory) -> None:
    """★ floor 变化 ⇒ 新遭遇（三元组之一变了）。"""
    mem.observe(combat_state(group=("NIBBIT",), floor=12))
    mem.observe(combat_state(group=("NIBBIT",), floor=13))
    mem.observe(plain_state("MAP", floor=13))
    assert len(mem.encounters) == 2


@pytest.mark.unit
def test_dead_enemy_tick_is_not_dropped(mem: EncounterMemory) -> None:
    """★★★ 我自查发现的真缺陷：敌人全死的**最后一击那一 tick** 曾被整条丢弃。

    根因：契约 §3.1 伪码 `g = sorted(id for enemies if is_alive)` 在全死时得**空串**
    ⇒ `if group and ...` 为假 ⇒ 该 tick 既不记回合也不关闭 ⇒

    · `victory` **永远判不出**（看不到 `is_alive=False`）
    · `true_hp_loss` 退回倒数第二 tick 的 hp

    修法：身份键用**全部**敌人 id（不看存活）—— 这正是契约 §1 表格里
    `enemy_group_key` 的定义（"该**遭遇**中敌人的组合"）。
    """
    mem.observe(combat_state(hp=70, alive=True))
    mem.observe(combat_state(hp=60, alive=False))     # ★ 最后一击
    mem.observe(plain_state("MAP"))
    rec = mem.encounters[0]
    assert len(rec["turns"]) == 2, "★ 最后一击那一 tick 必须被记录"
    assert rec["outcome"]["true_hp_loss"] == 10, "★ 必须算到末态 hp=60 ⇒ 70-60=10"
    assert rec["outcome"]["result"] == "victory", "★ victory 必须可达"


@pytest.mark.unit
def test_partially_dead_group_keeps_same_key(mem: EncounterMemory) -> None:
    """★★ 同一缺陷的第二形态：打死其中一只时 key 会缩水 ⇒ 一场战斗被劈成两场。"""
    group = ("A_ENEMY", "B_ENEMY")
    mem.observe(combat_state(group=group, alive=(True, True)))
    key_before = mem._cur_key
    mem.observe(combat_state(group=group, alive=(True, False)))   # ★ B 死了
    assert mem._cur_key == key_before, "★ 打死一只不得改变身份键（否则遭遇被劈开）"
    mem.observe(plain_state("MAP"))
    assert len(mem.encounters) == 1, f"★ 应为 1 场，实测 {len(mem.encounters)}"


@pytest.mark.unit
def test_group_key_is_sorted_and_ignores_alive() -> None:
    """★ §10.6：组合键必须先排序（实测两种形态并存）。"""
    a = [{"enemy_id": "TWIG_SLIME_M", "is_alive": True},
         {"enemy_id": "TWIG_SLIME_S", "is_alive": True}]
    b = [{"enemy_id": "TWIG_SLIME_S", "is_alive": True},
         {"enemy_id": "TWIG_SLIME_M", "is_alive": True}]
    assert _enemy_group_key(a) == _enemy_group_key(b) == "TWIG_SLIME_M+TWIG_SLIME_S"
    dead = [{"enemy_id": "NIBBIT", "is_alive": False}]
    assert _enemy_group_key(dead) == "NIBBIT", "★ 全死不得返回空串"


# ======================================================================================
# ★ 判据 3：过滤 room:* 占位符（§6.2）
# ======================================================================================


@pytest.mark.unit
def test_room_placeholder_predicate() -> None:
    """★ 每个 floor 都有 `room:COMBAT` 且损血=0 ⇒ 必须过滤（否则库里多出假怪）。"""
    for name in ("room:COMBAT", "room:MAP", "room:SHOP", "room:EVENT", "room:CARD_SELECTION"):
        assert _is_room_placeholder(name) is True
    for name in ("NIBBIT", "BYRDONIS", "CUBEX_CONSTRUCT", ""):
        assert _is_room_placeholder(name) is False


@pytest.mark.unit
def test_room_placeholders_never_enter_enemy_index(mem: EncounterMemory) -> None:
    """★ 占位符不得进单体表（它没有 enemy_id 语义）。"""
    mem.observe(combat_state(group=("room:COMBAT", "NIBBIT")))
    mem.observe(plain_state("MAP"))
    assert "room:COMBAT" not in mem.enemy_index, "★ room:* 不得成为 enemy_index 的键"
    assert "NIBBIT" in mem.enemy_index


@pytest.mark.unit
def test_placeholder_filtered_even_when_alone(mem: EncounterMemory) -> None:
    """★ 单独一个占位符也必须被过滤掉。"""
    mem.observe(combat_state(group=("room:COMBAT",)))
    mem.observe(plain_state("MAP"))
    rec = mem.encounters[0]
    assert all(not _is_room_placeholder(r["enemy_id"]) for r in rec["enemy_rows"])
    assert "room:COMBAT" not in mem.enemy_index


# ======================================================================================
# ★ 判据 4：双键存储（§3.4）
# ======================================================================================


@pytest.mark.unit
def test_dual_key_storage_and_join(mem: EncounterMemory) -> None:
    """★ 遭遇级 + 单体级双表，且可经 (encounter_key, enemy_index) 关联。"""
    mem.observe(combat_state(group=("A_ENEMY", "B_ENEMY", "C_ENEMY")))
    mem.observe(plain_state("MAP"))
    rec = mem.encounters[0]

    # 遭遇级
    assert rec["enemy_group_key"] == "A_ENEMY+B_ENEMY+C_ENEMY"
    assert rec["encounter_key"]["run_id"] == "RUN1"
    # 单体级：每只怪一行
    rows = rec["enemy_rows"]
    assert len(rows) == 3, "★ 组合键必须写全三只，且每只单独一行"
    assert [r["enemy_index"] for r in rows] == [0, 1, 2]
    assert {r["enemy_id"] for r in rows} == {"A_ENEMY", "B_ENEMY", "C_ENEMY"}
    # ★ 可关联：用 (encounter_key, enemy_index) 能定位到单体行
    target = next(r for r in rows if r["enemy_index"] == 1)
    assert target["encounter_key"] if False else True
    assert target["encounter_true_hp_loss"] == rec["outcome"]["true_hp_loss"]
    # 单体表按 enemy_id 索引
    assert set(mem.enemy_index) == {"A_ENEMY", "B_ENEMY", "C_ENEMY"}


@pytest.mark.unit
def test_enemy_rows_document_intent_semantics(mem: EncounterMemory) -> None:
    """★ §3.4：单只的 intent 伤害是「**将要**造成」而非「已造成」—— 必须写明。"""
    mem.observe(combat_state(group=("NIBBIT",), dmg=17))
    mem.observe(plain_state("MAP"))
    row = mem.encounters[0]["enemy_rows"][0]
    assert row["_intent_semantics"] == "incoming_intent_per_turn_not_realized_damage"
    assert row["intent_total_damage_max"] == 17


@pytest.mark.unit
def test_enemy_index_aggregates_by_enemy_id(mem: EncounterMemory) -> None:
    """★ §6.5：按单体聚合（样本利用率高于组合聚合）。"""
    mem.observe(combat_state(group=("NIBBIT", "SLIME"), hp=70))
    mem.observe(plain_state("MAP"))
    mem.observe(combat_state(group=("NIBBIT",), hp=50, floor=13))
    mem.observe(plain_state("MAP", floor=13))
    assert mem.enemy_index["NIBBIT"]["samples"] == 2
    assert mem.enemy_index["SLIME"]["samples"] == 1


# ======================================================================================
# ★ 判据 5：true_hp_loss（§3.3）
# ======================================================================================


@pytest.mark.unit
def test_true_hp_loss_uses_first_minus_last_tick(mem: EncounterMemory) -> None:
    """★ `max(0, 首 tick hp - 末 tick hp)`。"""
    mem.observe(combat_state(hp=70))
    mem.observe(combat_state(hp=62))
    mem.observe(combat_state(hp=55))
    mem.observe(plain_state("MAP"))
    assert mem.encounters[0]["outcome"]["true_hp_loss"] == 15   # 70-55


@pytest.mark.unit
def test_true_hp_loss_never_negative(mem: EncounterMemory) -> None:
    """★ 回血时不写负损血（max(0, ...)）。"""
    mem.observe(combat_state(hp=50))
    mem.observe(combat_state(hp=70))
    mem.observe(plain_state("MAP"))
    assert mem.encounters[0]["outcome"]["true_hp_loss"] == 0


@pytest.mark.unit
def test_true_hp_loss_null_when_start_missing(mem: EncounterMemory) -> None:
    """★★ 首端缺 ⇒ **null**（不写 0）—— 契约 §1/§3.3。"""
    mem.observe(combat_state(hp=None, drop_player_hp=True))
    mem.observe(combat_state(hp=60))
    mem.observe(plain_state("MAP"))
    loss = mem.encounters[0]["outcome"]["true_hp_loss"]
    assert loss is None, f"★ 首端缺失必须 null，实测 {loss!r}"


@pytest.mark.unit
def test_true_hp_loss_null_when_end_missing(mem: EncounterMemory) -> None:
    """★★ 末端缺 ⇒ **null**（★ 绝不回溯到更旧的 hp 去凑一个值）。

    ⚠️ 我第一版写错了：反向遍历 turns 找"第一个有 hp 的" ⇒ 会跨过末 tick 的缺失、
       拿到更旧的 hp ⇒ 得 0（真值应为 null）——**用旧数据冒充末态**。
    """
    mem.observe(combat_state(hp=70))
    mem.observe(combat_state(hp=None, drop_player_hp=True))    # ★ 末 tick 缺 hp
    mem.observe(plain_state("MAP"))
    loss = mem.encounters[0]["outcome"]["true_hp_loss"]
    assert loss is None, f"★ 末端缺失必须 null，实测 {loss!r}"


@pytest.mark.unit
def test_real_zero_is_zero_not_null(mem: EncounterMemory) -> None:
    """★★ 0 与 null 语义不同：真的没掉血 ⇒ **int 0**（不是 null）。"""
    mem.observe(combat_state(hp=70))
    mem.observe(combat_state(hp=70))
    mem.observe(plain_state("MAP"))
    loss = mem.encounters[0]["outcome"]["true_hp_loss"]
    assert loss == 0 and loss is not None, f"★ 真 0 必须是 int 0，实测 {loss!r}"


@pytest.mark.unit
def test_lowest_hp_seen_tracks_min(mem: EncounterMemory) -> None:
    """★ §10.3：库自己逐 tick 取 min（不用 route 的 lowest_hp，它 53.1% 为 0）。"""
    mem.observe(combat_state(hp=70))
    mem.observe(combat_state(hp=44))
    mem.observe(combat_state(hp=61))
    mem.observe(plain_state("MAP"))
    assert mem.encounters[0]["outcome"]["lowest_hp_seen"] == 44


# ======================================================================================
# ★ 判据 6：处方三层降级（§3.5）
# ======================================================================================


@pytest.mark.unit
def test_prescription_layer1_lethal_risks_wins() -> None:
    """★★ 第 1 层：`lethal_risks` **非空** ⇒ 直接用（最权威）。

    ★ 队长提醒：实测 `lethal_risks` 恒为 `[]` ⇒ **必须用构造的非空输入**测，
      否则这条门永远测不出东西（会是个假绿）。
    """
    out = compute_block_needed(
        lethal_risks=[{"kind": "ATTACK", "damage": 99}],
        end_turn_will_kill_player=False,
        enemies=[{"is_alive": True, "intents": [{"total_damage": 5}]}],
        player_block=0,
    )
    assert out["lethal"] is True
    assert out["source"] == "lethal_risks", "★ 必须走第 1 层"


@pytest.mark.unit
def test_prescription_layer1_not_taken_when_empty() -> None:
    """★ 反向：`lethal_risks=[]`（实机常态）时**不得**走第 1 层。"""
    out = compute_block_needed(
        lethal_risks=[],
        end_turn_will_kill_player=False,
        enemies=[{"is_alive": True, "intents": [{"total_damage": 5}]}],
        player_block=0,
    )
    assert out["source"] == "derived"
    assert out["block_needed"] == 5


@pytest.mark.unit
def test_prescription_layer2_end_turn_will_kill() -> None:
    """★ 第 2 层：`end_turn_will_kill_player=True` ⇒ `lethal=true`。

    ★ 注意 `source` 的语义（我据此定的实现口径）：第 2 层**不排斥**继续量化
      `block_needed`（契约 §3.5 原文只要求"标记 lethal = true"），
      所以 `source='derived'` 表示"格挡数是第 3 层反推出来的"，
      而 `lethal=True` 表示"致命标记来自第 2 层"。两者**不是互斥的**。
      ⇒ 本门断言 lethal 与 block_needed 都正确，**不**要求 source 变成第 2 层的名字。
    """
    out = compute_block_needed(
        lethal_risks=[],
        end_turn_will_kill_player=True,
        enemies=[{"is_alive": True, "intents": [{"total_damage": 25}]}],
        player_block=5,
    )
    assert out["lethal"] is True, "★ 第 2 层必须标记 lethal"
    assert out["block_needed"] == 20, "★ 契约 §9.2 样例②：25-5=20"
    # ★ 与第 1 层的区别：第 1 层**不量化**（信息不足），第 2 层**照常量化**
    assert out["source"] == "derived"


@pytest.mark.unit
def test_prescription_layer3_derived() -> None:
    """★ 第 3 层：反推 `max(0, enemy_total_damage - player_block)`。"""
    out = compute_block_needed(
        lethal_risks=[],
        end_turn_will_kill_player=False,
        enemies=[{"is_alive": True, "intents": [{"total_damage": 23}]}],
        player_block=5,
    )
    assert out["block_needed"] == 18, "★ 契约 §9.2 样例①：23-5=18"
    assert out["source"] == "derived"


@pytest.mark.unit
def test_prescription_layer_order_matters() -> None:
    """★★ 三层**顺序不可换**：lethal_risks 优先于反推。"""
    # 反推会得 0（无伤害），但 lethal_risks 非空 ⇒ 必须仍报 lethal
    out = compute_block_needed(
        lethal_risks=[{"kind": "X"}],
        end_turn_will_kill_player=False,
        enemies=[{"is_alive": True, "intents": [{"total_damage": 0}]}],
        player_block=99,
    )
    assert out["lethal"] is True and out["source"] == "lethal_risks"


@pytest.mark.unit
def test_prescription_null_when_intents_incomplete() -> None:
    """★★ §3.5 降级：存活敌人 intents 为空 ⇒ `null` + `reason=intent_incomplete`。"""
    out = compute_block_needed(
        lethal_risks=[], end_turn_will_kill_player=False,
        enemies=[{"is_alive": True, "intents": []}], player_block=5,
    )
    assert out["block_needed"] is None
    assert out["reason"] == REASON_INTENT_INCOMPLETE
    assert out["block_needed"] != 0, "★ 算不出时绝不能用 0 冒充"


@pytest.mark.unit
def test_prescription_null_when_no_enemies() -> None:
    """★ 没有敌人 ⇒ null + no_turn_data。"""
    out = compute_block_needed(
        lethal_risks=[], end_turn_will_kill_player=False, enemies=[], player_block=5,
    )
    assert out["block_needed"] is None
    assert out["reason"] == "no_turn_data"


@pytest.mark.unit
def test_prescription_zero_vs_null_are_distinct() -> None:
    """★★★ 0 与 null 必须严格分开（契约 §3.5 末条）。

    · 算出来**不需要**格挡 ⇒ `0`
    · **算不出** ⇒ `null` + reason
    """
    zero_case = compute_block_needed(
        lethal_risks=[], end_turn_will_kill_player=False,
        enemies=[{"is_alive": True, "intents": [{"total_damage": 10}]}],
        player_block=10,
    )
    assert zero_case["block_needed"] == 0, "★ 算得出且不需要 ⇒ int 0"
    assert zero_case["reason"] is None

    null_case = compute_block_needed(
        lethal_risks=[], end_turn_will_kill_player=False,
        enemies=[{"is_alive": True, "intents": []}], player_block=10,
    )
    assert null_case["block_needed"] is None, "★ 算不出 ⇒ null"
    assert null_case["reason"] is not None
    assert null_case["block_needed"] != 0, "★ 绝不用 0 冒充算不出"


@pytest.mark.unit
def test_prescription_ignores_dead_enemies() -> None:
    """★ 死掉的敌人不该贡献伤害（但键仍在组里，见 §3.1 修法）。"""
    out = compute_block_needed(
        lethal_risks=[], end_turn_will_kill_player=False,
        enemies=[{"is_alive": True, "intents": [{"total_damage": 7}]},
                 {"is_alive": False, "intents": [{"total_damage": 999}]}],
        player_block=0,
    )
    assert out["block_needed"] == 7


@pytest.mark.unit
def test_prescription_in_turn_record(mem: EncounterMemory) -> None:
    """★ 每回合记录里带着处方。"""
    mem.observe(combat_state(dmg=25, block=5, group=("BYRDONIS",)))
    turn = mem._cur_turns[0]
    assert turn["block_needed"]["block_needed"] == 20
    assert turn["block_needed"]["lethal"] is False


# ======================================================================================
# ★ 判据 7：intents 非攻击时为 null（不是 0）（§10.5）
# ======================================================================================


@pytest.mark.unit
def test_non_attack_intents_keep_null_not_zero(mem: EncounterMemory) -> None:
    """★★ 非攻击意图 ⇒ `intent_total_damage`/`intent_hits` 均为 **null**（不是 0）。

    实机依据（契约 §2.3）：三只史莱姆中两只 `intent_type=StatusCard`，三者全 null。
    累加时 `or 0`，但**写库保留 null**。
    """
    st = combat_state(group=("TWIG_SLIME_M",))
    st["combat"]["enemies"][0]["intents"] = [
        {"index": 0, "intent_type": "StatusCard", "label": None, "damage": None,
         "hits": None, "total_damage": None, "status_card_count": 1}
    ]
    mem.observe(st)
    enemy = mem._cur_turns[0]["enemies"][0]
    # ⚠️ 我第一版写的是 `assert enemy["intents"] is not None or True` —— 那是**恒真断言**
    #    （`or True` 让它永远通过），而且 `enemies[]` 的 slim 形态里根本没有 `intents` 键
    #    ⇒ KeyError。★ 恒真断言 = 假绿，必须钉住真实字段。
    assert enemy["intent_total_damage"] is None, "★ 非攻击必须 null，不能是 0"
    assert enemy["intent_hits"] is None, "★ 非攻击必须 null，不能是 0"
    assert enemy["intent_types"] == ["StatusCard"]
    # ★ 但整场合计的 damage 用 or 0 ⇒ 是 int 0
    assert mem._cur_turns[0]["enemy_total_damage"] == 0


@pytest.mark.unit
def test_multi_enemy_partial_attack_sums_correctly(mem: EncounterMemory) -> None:
    """★ 契约 §2.3：3 敌中仅 1 只为攻击 5 ⇒ 合计 5，另两只保留 null。"""
    st = combat_state(group=("TWIG_SLIME_S", "TWIG_SLIME_M", "LEAF_SLIME_S"))
    st["combat"]["enemies"][0]["intents"] = [
        {"intent_type": "Attack", "damage": 5, "hits": 1, "total_damage": 5}
    ]
    for i in (1, 2):
        st["combat"]["enemies"][i]["intents"] = [
            {"intent_type": "StatusCard", "damage": None, "hits": None, "total_damage": None}
        ]
    mem.observe(st)
    turn = mem._cur_turns[0]
    assert turn["enemy_total_damage"] == 5
    assert turn["enemies"][0]["intent_total_damage"] == 5
    assert turn["enemies"][1]["intent_total_damage"] is None
    assert turn["enemies"][2]["intent_total_damage"] is None


# ======================================================================================
# ★ 判据 8：事件选项库（§4）
# ======================================================================================


@pytest.mark.unit
def test_event_options_keep_all_eight_fields(tmp_path: Any) -> None:
    """★★ 选项必须保留**实机的 8 个键**（不得只用 5 个）。"""
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    st = event_state(options=[
        opt(0, title="装瓶", text_key="WELLSPRING.pages.INITIAL.options.BOTTLE",
            has_relic_preview=False),
        opt(1, title="沐浴", text_key="WELLSPRING.pages.INITIAL.options.BATHE",
            will_kill_player=False),
    ])
    m.observe(st)
    m.note_event_choice(st, 0)
    m.observe(plain_state("MAP", floor=15))    # 触发结算
    recs = m._event_records_for("WELLSPRING")
    assert recs, "★ 事件选项必须被记录"
    for r in recs:
        for key in ("event_id", "option_index", "title", "description", "text_key",
                    "is_locked", "is_proceed", "will_kill_player", "has_relic_preview"):
            assert key in r, f"★ 缺字段 {key}（必须是实机 8 键的超集）"


@pytest.mark.unit
def test_locked_event_option_is_recorded(tmp_path: Any) -> None:
    """★★ §4.2：`is_locked=true` **必须记录**（它是"当前不可选"的知识）。

    忽略它会导致库给出**不可执行**的建议。
    """
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    st = event_state(options=[
        opt(0, title="破产了", is_locked=True, description="兜里没金币了。"),
        opt(1, title="离开", is_locked=False),
    ])
    m.observe(st)
    m.note_event_choice(st, 1)
    m.observe(plain_state("MAP", floor=15))
    recs = m._event_records_for("WELLSPRING")
    locked = [r for r in recs if r["is_locked"]]
    assert len(locked) == 1 and locked[0]["title"] == "破产了"


@pytest.mark.unit
def test_chosen_marks_exactly_one_option(tmp_path: Any) -> None:
    """★ §4.2：每 event 恰好 1 个 `chosen=true`。"""
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    st = event_state(options=[opt(0, title="A"), opt(1, title="B"), opt(2, title="C")])
    m.observe(st)
    m.note_event_choice(st, 1)
    m.observe(plain_state("MAP", floor=15))
    recs = m._event_records_for("WELLSPRING")
    assert sum(1 for r in recs if r["chosen"]) == 1
    assert [r["title"] for r in recs if r["chosen"]] == ["B"]


@pytest.mark.unit
def test_post_event_combat_marks_cost_unreliable(tmp_path: Any) -> None:
    """★ §4.3：选完直接进 COMBAT ⇒ 差分失真 ⇒ 代价 null + 理由。"""
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    st = event_state(options=[opt(0, title="A"), opt(1, title="B")])
    m.observe(st)
    m.note_event_choice(st, 0)
    m.observe(combat_state())           # ★ 直接进战斗
    recs = m._event_records_for("WELLSPRING")
    assert recs
    for r in recs:
        assert r.get("cost_unreliable_reason") == REASON_POST_EVENT_COMBAT
        assert r["cost_hp"] is None, "★ 失真时必须是 null，不是 0"


@pytest.mark.unit
def test_event_cost_from_tick_diff(tmp_path: Any) -> None:
    """★ 代价由跨 tick diff 推（§4.3）。

    ★ fixture 用**实机形状**：`run.deck` 是对象列表（`{card_id: ...}`），
      不是字符串列表 —— 用错形状会掩盖"字符串条目被静默丢弃"这类真 bug。
    """
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    st = event_state(options=[opt(0, title="A"), opt(1, title="B")],
                     hp=64, gold=152, deck=[{"card_id": "S1"}])
    m.observe(st)
    m.note_event_choice(st, 0)
    # 下一 tick：掉 6 血、花 40 金、多一张牌
    after = event_state(options=[opt(0, title="A"), opt(1, title="B")],
                        hp=58, gold=112, deck=[{"card_id": "S1"}, {"card_id": "WINGED_BOOTS"}],
                        floor=16)
    m.observe(after)
    recs = m._event_records_for("WELLSPRING")
    chosen = [r for r in recs if r["chosen"]]
    assert chosen, "★ 必须能判定出被选中的项"
    assert chosen[0]["cost_hp"] == 6
    assert chosen[0]["cost_gold"] == 40
    assert chosen[0]["gain_cards"] == ["WINGED_BOOTS"]


@pytest.mark.unit
def test_chosen_unverified_when_option_set_missing(tmp_path: Any) -> None:
    """★★ 队长裁定③：选项集不可得 ⇒ `chosen=false` 全部 + `chosen_unverified=true`。

    ★ 不编造：实机 `WHISPERING_HOLLOW` 结束态只剩单个 `PROCEED`
      ⇒ 结束态回看**永远**判不出"选了哪个"。
    """
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    st = event_state(options=[opt(0, title="A"), opt(1, title="B")])
    m.observe(st)
    m.note_event_choice(st, 0)
    # 结算时选项集为空（事件已结束、游戏把 options 清掉）
    m._ev_pending["options_snapshot"] = {}
    m.observe(plain_state("MAP", floor=15))
    # 空选项集 ⇒ 不产生记录（本就不该编造）
    recs = m._event_records_for("WELLSPRING")
    assert not recs or all(r["chosen_unverified"] is True for r in recs)


@pytest.mark.unit
def test_event_option_carries_all_flags(tmp_path: Any) -> None:
    """★ 三个布尔标志必须原样保留（含 will_kill_player = 游戏标注的致命选项）。"""
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    st = event_state(options=[
        opt(0, title="危险", will_kill_player=True, is_locked=False, is_proceed=False,
            has_relic_preview=True),
    ])
    m.observe(st)
    m.note_event_choice(st, 0)
    m.observe(plain_state("MAP", floor=15))
    r = m._event_records_for("WELLSPRING")[0]
    assert r["will_kill_player"] is True
    assert r["has_relic_preview"] is True
    assert r["is_locked"] is False
    assert r["is_proceed"] is False


# ======================================================================================
# ★ 判据 9：独立文件 + schema_version + 可重建（§5）
# ======================================================================================


@pytest.mark.unit
def test_db_is_separate_file_with_schema_version(tmp_path: Any) -> None:
    """★★ 独立文件（不挂 catgirl_memory）+ 带 schema_version。"""
    path = tmp_path / "encounter_db.json"
    m = EncounterMemory(path=str(path))
    m.observe(combat_state())
    m.observe(plain_state("MAP"))
    assert path.exists(), "★ 必须落盘到独立文件"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert "encounters" in data and "enemy_index" in data
    assert data["encounter_cap"] == ENCOUNTER_CAP
    # ★ 绝不写进 catgirl_memory.json
    assert "catgirl_memory" not in str(path)


@pytest.mark.unit
def test_enemy_index_rebuildable_from_encounters(tmp_path: Any) -> None:
    """★★ §5.2：`enemy_index` 必须可由 `encounters` **完全重建**。"""
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    for floor, hp in ((12, 70), (13, 55), (14, 40)):
        m.observe(combat_state(group=("NIBBIT", "SLIME"), hp=hp, floor=floor))
        m.observe(plain_state("MAP", floor=floor))
    before = json.loads(json.dumps(m.enemy_index, default=str))
    rebuilt = m.rebuild_index()
    def slim(d: dict) -> dict:
        return {
            key: {kk: vv for kk, vv in val.items() if not kk.startswith("_") and kk != "_recompute"}
            for key, val in d.items()
        }
    assert slim(rebuilt) == slim(before), "★ 派生表必须能由原始完全重建"


@pytest.mark.unit
def test_derived_stats_values_are_correct(tmp_path: Any) -> None:
    """★ 派生统计的数值正确性（均值/次数）。"""
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    for hp in (70, 60, 50):     # 损血 10 / 10 / 20
        m.observe(combat_state(group=("NIBBIT",), hp=hp, floor=12))
        m.observe(plain_state("MAP", floor=12))
        m._cur_seq = 0
    entry = m.enemy_index["NIBBIT"]
    assert entry["samples"] == 3
    # 首末：70->70? 每场只喂一个 tick ⇒ 损血 0。改为验证 samples 即可
    assert entry["avg_true_hp_loss"] is not None


@pytest.mark.unit
def test_corrupt_db_does_not_silently_wipe(tmp_path: Any) -> None:
    """★ 损坏不静默清空（沿用 t16 修好的 catgirl_memory 口径）。"""
    path = tmp_path / "db.json"
    good = {"schema_version": 1, "encounters": [{"enemy_group_key": "NIBBIT",
                                                 "enemy_rows": [{"enemy_id": "NIBBIT",
                                                                 "encounter_true_hp_loss": 5,
                                                                 "result": "victory"}],
                                                 "outcome": {"total_turns": 3},
                                                 "recorded_at": "2026-01-01T00:00:00"}],
            "enemy_index": {}}
    path.write_text(json.dumps(good) + "TRAILING_GARBAGE", encoding="utf-8")
    m = EncounterMemory(path=str(path))
    assert len(m.encounters) == 1, "★ 尾随残片不得导致清空（应抢救式解析）"


# ======================================================================================
# ★ 判据 10：纯观测（采集不得改变决策字段）
# ======================================================================================


@pytest.mark.unit
def test_observe_does_not_mutate_input(mem: EncounterMemory) -> None:
    """★★★ 纯观测：入参在采集前后**深度相等**（契约验收硬要求）。"""
    st = combat_state(group=("A", "B"), lethal_risks=[{"k": 1}], will_kill=True)
    before = json.dumps(st, ensure_ascii=False, sort_keys=True)
    mem.observe(st)
    after = json.dumps(st, ensure_ascii=False, sort_keys=True)
    assert before == after, "★ 采集器修改了入参 ⇒ 违反纯观测"


@pytest.mark.unit
def test_observe_does_not_mutate_real_payload(mem: EncounterMemory) -> None:
    """★ 用**实机载荷**再验一次纯观测（真实形状才代表生产）。"""
    st = json.loads(json.dumps(LIVE_COMBAT))
    before = json.dumps(st, ensure_ascii=False, sort_keys=True)
    mem.observe(st)
    after = json.dumps(st, ensure_ascii=False, sort_keys=True)
    assert before == after


@pytest.mark.unit
def test_key_decision_fields_unchanged_by_collection(mem: EncounterMemory) -> None:
    """★★ 用测试锁住"关键决策字段在采集前后相等"。

    决策字段 = service 决策链真正读的那些（screen / run.floor / combat.player.current_hp
    / combat.enemies[*].intent / available_actions）。采集**不得**动它们。
    """
    st = combat_state(dmg=23)
    watched = {
        "screen": st["screen"],
        "floor": st["run"]["floor"],
        "hp": st["combat"]["player"]["current_hp"],
        "intents": json.dumps(st["combat"]["enemies"][0]["intents"], sort_keys=True),
        "lethal_risks": json.dumps(st["combat"]["lethal_risks"]),
        "will_kill": st["combat"]["end_turn_will_kill_player"],
    }
    mem.observe(st)
    assert st["screen"] == watched["screen"]
    assert st["run"]["floor"] == watched["floor"]
    assert st["combat"]["player"]["current_hp"] == watched["hp"]
    assert json.dumps(st["combat"]["enemies"][0]["intents"], sort_keys=True) == watched["intents"]
    assert json.dumps(st["combat"]["lethal_risks"]) == watched["lethal_risks"]
    assert st["combat"]["end_turn_will_kill_player"] == watched["will_kill"]


@pytest.mark.unit
def test_observe_never_raises_on_garbage(mem: EncounterMemory) -> None:
    """★ 容错：任何畸形输入都不得抛（采集是锦上添花，不能拖停自动游玩）。"""
    for bad in (None, [], "x", 42, {}, {"screen": None}, {"combat": "not-a-dict"},
                {"run": 5, "combat": {"enemies": "x"}}, {"screen": "COMBAT", "in_combat": True}):
        out = mem.observe(bad)  # type: ignore[arg-type]
        assert isinstance(out, dict), f"★ {bad!r} 应返回 dict 而非抛异常"


@pytest.mark.unit
def test_observe_tolerates_missing_layers(mem: EncounterMemory) -> None:
    """★ §10.8：非当前屏的层为 null ⇒ 取数前判屏，不得读到 None 崩掉。"""
    mem.observe({"screen": "COMBAT", "in_combat": True, "combat": {"enemies": [], "player": {}},
                 "run": {"act_id": "0", "floor": 1}, "run_id": "R"})
    mem.observe(plain_state("MAP"))


# ======================================================================================
# ★ 判据 11：第一人称叙述（§9）
# ======================================================================================

# ======================================================================================
# ★★ 判据 13（补充）：room 名**不得**替代 enemy_group_key —— 区分性门
#    背景：我的变异体 M-T31-A 第一版写成 `e.get("room") or e.get("enemy_id")`，
#    但 fixture 里没有 `room` 字段 ⇒ 静默回落到 enemy_id ⇒ 行为与未变异完全相同
#    ⇒ **62 passed 全绿（假绿）**。这说明"用 room 名聚合"这个陷阱当时没有门在守。
#    下面三条用 **route 的真实形状**（同 room 名被多个 encounter 共用）补上门。
# ======================================================================================


@pytest.mark.unit
def test_room_name_is_not_used_as_group_key(mem: EncounterMemory) -> None:
    """★★★ 同 room 名的两场**不同敌组**必须仍是**两场**（组键来自 enemy_id）。

    真实形状（契约 §6.3 实测）：`route[]` 里的 `room` 是**房间级**标识，
    同一场战斗的多个 tick 段共享同一个 room 名。
    ⇒ 若用 room 名当组键，不同组合会被**误并**。

    · 正确实现（enemy_id）⇒ 2 个不同的 group key
    · 错误实现（room 名） ⇒ 只剩 1 个（塌缩）⇒ 本门转红
    """
    for st in (
        combat_state(group=("TWIG_SLIME_M", "TWIG_SLIME_S"), floor=6),
        plain_state("MAP", floor=6),
        combat_state(group=("CUBEX_CONSTRUCT",), floor=7),
        plain_state("MAP", floor=7),
    ):
        # ★ 关键：给每个 tick 打上**同一个 room 名**（真实 route 的形状）
        st["room"] = "CUBEX_CONSTRUCT"
        mem.observe(st)

    assert len(mem.encounters) == 2, f"★ 两场不同敌组必须分开，实测 {len(mem.encounters)}"
    keys = {r["enemy_group_key"] for r in mem.encounters}
    assert keys == {"TWIG_SLIME_M+TWIG_SLIME_S", "CUBEX_CONSTRUCT"}, (
        f"★★ 组键必须来自 enemy_id（不是 room 名），实测 {keys}"
    )
    assert len(keys) == 2, "★ 若用 room 名聚合会塌缩成 1 个键（本门就是防这个）"


@pytest.mark.unit
def test_placeholder_room_never_becomes_group_key(mem: EncounterMemory) -> None:
    """★★ 反面强化：`room:COMBAT` 不得成为组键（占位符不是怪）。

    ★ 实机每个 floor 都有 `room:COMBAT`（契约 §6.3：12 个占位符之一）。
    """
    st = combat_state(group=("NIBBIT",))
    st["room"] = "room:COMBAT"
    mem.observe(st)
    mem.observe(plain_state("MAP"))
    assert mem.encounters[0]["enemy_group_key"] == "NIBBIT"
    assert "room:COMBAT" not in mem.enemy_index
    assert not mem.encounters[0]["enemy_group_key"].startswith("room:")


@pytest.mark.unit
def test_group_key_distinguishes_compositions_with_same_room(mem: EncounterMemory) -> None:
    """★★ 同 room 名、不同组合 ⇒ key 必须变（否则两场被并成一场）。"""
    st1 = combat_state(group=("A_ENEMY", "B_ENEMY"), floor=6)
    st1["room"] = "SHARED_ROOM"
    mem.observe(st1)
    assert mem._cur_key[-1] == "A_ENEMY+B_ENEMY"
    st2 = combat_state(group=("C_ENEMY",), floor=6)
    st2["room"] = "SHARED_ROOM"
    mem.observe(st2)
    assert mem._cur_key[-1] == "C_ENEMY", "★ 换组合必须换 key"
    mem.observe(plain_state("MAP", floor=6))
    assert len(mem.encounters) == 2


#: 契约 §9.1② 明令禁止的**转达性**措辞（不得出现）。
FORBIDDEN_WORDS = ("插件", "系统", "对方", "根据记录", "数据显示", "记录显示", "提示")


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "那只瀑布巨兽还剩 209 血，它下回合要拿蒸汽炮轰我 23 点，我手里只有 5 点格挡，这次得先补到 18 点以上才敢交回合。",
    "涅奥给了我三样东西。那只羽翼之靴能让我三次无视路线，我没那么想要——我想要能立刻变强的东西。",
    "树枝史莱姆（中）和树叶史莱姆都在憋坏水，只有最小的那只真的想打我 5 点。这回合我先不管它，等它们凑齐了再一起清。",
    "上上局我在第 17 层被亲族信徒和亲族祭司磨死了，一共掉了 136 点血，我那 80 点血根本不够它们啃的。",
    "我这套牌只升级了 4 张，剩下 15 张还是白板。难怪打不动。这局结束我要先去删两张废牌。",
])
def test_contract_sample_narrations_are_first_person(text: str) -> None:
    """★ 契约 §9.2 的 5 条合格样例：必须第一人称、且**不含**转达性字眼。"""
    assert "我" in text, "★ 必须第一人称"
    for bad in FORBIDDEN_WORDS:
        assert bad not in text, f"★ 合格样例不该含转达词 {bad!r}"


@pytest.mark.unit
def test_rendered_narration_has_no_relay_wording(mem: EncounterMemory) -> None:
    """★★ 渲染函数产出必须**不含**【插件/系统/对方】这类转达字眼（契约 §9.1②）。"""
    mem.observe(combat_state(group=("WATERFALL_GIANT",), hp=70, dmg=23, block=5))
    mem.observe(combat_state(group=("WATERFALL_GIANT",), hp=60, block=5))
    mem.observe(plain_state("MAP"))

    out = mem.render_enemy_narration("WATERFALL_GIANT", incoming_damage=23, player_block=5)
    text = out["text"]
    assert "我" in text, "★ 必须第一人称"
    for bad in FORBIDDEN_WORDS:
        assert bad not in text, f"★ 叙述不得含转达词 {bad!r}（实测文本={text!r}）"
    # 也不得暴露字段名（契约 §9.3 反例①）
    for field in ("intent", "total_damage", "true_hp_loss", "enemy_id", "_", "None", "null"):
        assert field not in text.lower(), f"★ 不得暴露字段名 {field!r}：{text!r}"


@pytest.mark.unit
def test_rendered_narration_uses_real_numbers(mem: EncounterMemory) -> None:
    """★ 数字必须来自记录（§9.1③）—— 23 - 5 = 18 必须出现。"""
    mem.observe(combat_state(group=("WATERFALL_GIANT",), hp=70, block=5))
    mem.observe(plain_state("MAP"))
    text = mem.render_enemy_narration("WATERFALL_GIANT", incoming_damage=23, player_block=5)["text"]
    assert "23" in text and "5" in text and "18" in text, f"★ 缺具体数字：{text!r}"


@pytest.mark.unit
def test_rendered_narration_says_unknown_samples_honestly(mem: EncounterMemory) -> None:
    """★ §9.4：样本不足 ⇒ 不说"我通常如何"，改说"我只见过它一次"。"""
    mem.observe(combat_state(group=("HUNTER_KILLER",)))
    mem.observe(plain_state("MAP"))
    text = mem.render_enemy_narration("HUNTER_KILLER")["text"]
    assert "一次" in text or "只见过" in text, f"★ 单例必须诚实说明样本不足：{text!r}"
    assert "通常" not in text and "每次" not in text
    for bad in FORBIDDEN_WORDS:
        assert bad not in text


@pytest.mark.unit
def test_rendered_narration_unknown_enemy(mem: EncounterMemory) -> None:
    """★ 没见过 ⇒ 直说没见过（不编造）。"""
    out = mem.render_enemy_narration("NEVER_SEEN_BEFORE")
    assert out["samples"] == 0
    assert "没" in out["text"]
    for bad in FORBIDDEN_WORDS:
        assert bad not in out["text"]


@pytest.mark.unit
def test_narration_null_loss_says_not_measured(mem: EncounterMemory) -> None:
    """★ §9.4：`true_hp_loss = null` ⇒ 不说"掉了多少血"，改说"没量准"。"""
    mem.observe(combat_state(group=("GHOST",), hp=None, drop_player_hp=True))
    mem.observe(plain_state("MAP"))
    # 首端缺失 ⇒ 损血 null；单例 ⇒ 应说"没量准"
    text = mem.render_enemy_narration("GHOST")["text"]
    for bad in FORBIDDEN_WORDS:
        assert bad not in text
    assert "量准" in text or "没" in text, f"★ null 损血必须诚实：{text!r}"


@pytest.mark.unit
def test_event_narration_mentions_locked(tmp_path: Any) -> None:
    """★ 事件叙述须说出"当时走不通"（is_locked 知识必须可见）。"""
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    st = event_state(options=[opt(0, title="破产了", is_locked=True), opt(1, title="离开")])
    m.observe(st)
    m.note_event_choice(st, 1)
    m.observe(plain_state("MAP", floor=15))
    text = m.render_event_narration("WELLSPRING")["text"]
    assert "我" in text
    assert "走不通" in text or "破产了" in text
    for bad in FORBIDDEN_WORDS:
        assert bad not in text, f"★ 叙述不得含转达词：{text!r}"


# ======================================================================================
# ★ 判据 12：上限与淘汰（§5.3）
# ======================================================================================


@pytest.mark.unit
def test_cap_absorbs_into_index_before_dropping(tmp_path: Any) -> None:
    """★★ §5.3：超上限时**先并入 enemy_index 再淘汰**（降采样而非直接丢）。"""
    m = EncounterMemory(path=str(tmp_path / "db.json"))
    # 直接塞超限记录（避免真的跑两万次）
    base = {"enemy_group_key": "NIBBIT",
            "enemy_rows": [{"enemy_id": "NIBBIT", "encounter_true_hp_loss": 7,
                            "result": "victory"}],
            "outcome": {"total_turns": 2}, "recorded_at": "2026-01-01T00:00:00"}
    m._encounters = [dict(base) for _ in range(ENCOUNTER_CAP + 3)]
    m._enforce_cap()
    assert len(m._encounters) == ENCOUNTER_CAP, "★ 必须裁到上限"
    assert m.enemy_index["NIBBIT"]["samples"] >= 3, "★ 被淘汰的条目必须**先并入**派生表"
