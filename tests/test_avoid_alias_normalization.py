"""avoid 匹配必须归一 Mod 真实下发的枚举名。

背景（两个独立证据源，都是实机/复算得出，不是推测）：

1. `pathfinder` 在真实对局里复算发现：同一教训、同一消费点，**短键** `type='rest'` 能重排
   （`[1,2,3,0]` → `[2,3,0,1]`），而 Mod **真实下发的枚举名** `node_type='RestSite'` **不能**
   （顺序不变）。选牌域同理不变。
2. 根因在 `heuristic_planner._indices_worth_penalizing`：它当时只做了
   `.strip().lower()`，**没有复用**既有的 `_MAP_NODE_TYPE_ALIASES`（`restsite → rest`），
   而 `_LESSON_KEYWORD_MAP` 产出的 token 全是**规范短键**。于是 `RestSite` 永远匹配不上 `rest`。

⇒ 后果：`avoid` 的消费点虽然"活着"，但对 Mod 的真实下发格式**不可达**，复盘在实机里
   仍然无法让 Mod 进步 —— 只是失败方式更隐蔽了（静默不匹配，而非字段没人读）。

本测试钉住的契约：**消费点认得出 Mod 真实枚举名**，与短键等价。
将来若有人再加一条 `_MAP_NODE_TYPE_ALIASES` 别名，这里应同步补一行。
"""

from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import (
    _MAP_NODE_TYPE_ALIASES,
    STS2HeuristicPlanner,
)

# Mod 真实下发的枚举名 → 期望的规范短键。
# 右值必须与 `_LESSON_KEYWORD_MAP` 产出的 token 同域，否则消费点不可达。
_REAL_ENUM_NAMES = {
    "RestSite": "rest",
    "Treasure": "treasure",
    "Shop": "shop",
    "Elite": "elite",
    "Monster": "monster",
    "Unknown": "question",
    "Event": "event",
}


@pytest.mark.unit
def test_map_node_weight_key_normalizes_real_enum_names() -> None:
    """`_map_node_weight_key` 必须把 Mod 真实枚举名归一到规范短键。"""
    planner = STS2HeuristicPlanner()
    for raw, expected in _REAL_ENUM_NAMES.items():
        assert planner._map_node_weight_key({"type": raw}) == expected, f"{raw} 未归一到 {expected}"
        # node_type 键与 type 键等价（Mod 两种都可能下发）。
        assert planner._map_node_weight_key({"node_type": raw}) == expected


@pytest.mark.unit
def test_alias_targets_are_lowercase_canonical() -> None:
    """别名表的值（规范短键）必须全为小写——否则大小写敏感比对会再次静默失效。"""
    for raw, canonical in _MAP_NODE_TYPE_ALIASES.items():
        assert raw == raw.lower(), f"别名键 {raw!r} 应为小写"
        assert canonical == canonical.lower(), f"别名值 {canonical!r} 应为小写"


@pytest.mark.unit
def test_avoid_penalty_reaches_real_enum_name() -> None:
    """端到端：教训点名小怪，而地图给的是真实枚举名 `Enemy` 时，该节点**必须**被降权。

    这是实机复算里失败的那条路径（枚举名匹配不上短键）。

    ⚠️ **样本选择有严格前提**——必须避开三个恒真坑（前两个我踩过）：

    1. **operation 必须是 dict**：`_operation_dict` 只认 `dict` / `as_dict()`，
       传裸类会拿到 `{}` → `action_type=''` → 直接走 `else: return []`，
       测试压根进不了 avoid 层（失败形态 `assert [] != []`，看着像"没生效"，其实是没进去）。
    2. **被点名项不能恰是规划器 `best`**：`ordered_candidates:4109` 把最优解硬插第 0 位，
       降权被结构性抵消。实测 `RestSite` 在任何 HP/金币组合下恒为 `best`，
       所以**拿 rest 当样本的断言必然恒真**——这是 `test_run_lessons_affect_decision.py` L77
       早已警示过的坑。
    3. **必须选"超出大小写差异"的枚举名**：别名表只对那些 lower() 后仍不等的名字起作用。
       `Shop`→`shop`、`Treasure`→`treasure` 靠 `.lower()` 就够了，**测不出别名**；
       只有 `Enemy`→`monster`、`RestSite`→`rest`、`Unknown`→`question` 这类才真正依赖别名表。
       这里用 `Enemy`（位 0，非 best，且必须经别名才能命中 `monster`）。
    """
    planner = STS2HeuristicPlanner()
    nodes = [
        {"index": 0, "node_type": "Enemy"},      # 被点名；非 best；必须经别名 -> monster
        {"index": 1, "node_type": "RestSite"},
        {"index": 2, "node_type": "Shop"},
        {"index": 3, "node_type": "Elite"},
    ]
    payload = {"payload": {"travelable_nodes": nodes, "current_hp": 70, "max_hp": 80, "gold": 50}}

    def rank(avoid: list[str]) -> list[int]:
        directives = {"must": [], "prefer": [], "avoid": list(avoid)}
        ctx = {
            "classification": {"state_name": "map"},
            "summary_context": payload,
            "strategy_context": {"preferences": {}, "strategy_directives": directives},
        }
        operation = {"action_type": "choose_map_node", "kwargs": {"option_index": 0}}  # 坑 1
        return planner._ranked_option_indices(ctx, operation)

    lesson = "别打小怪"
    assert planner._lesson_tokens([lesson]) == ["monster"], "前提：该教训应抽出 monster token"

    no_lesson = rank([])
    with_lesson = rank([lesson])

    # 坑 2 自检：被点名项若恰是最优解，本测试恒真 —— 直接判失败而不是假绿。
    assert no_lesson[0] != 0, (
        f"前提不成立：被点名的 Enemy(0) 恰是规划器最优解，降权被结构性抵消，本测试将恒真。"
        f"基线顺序={no_lesson}"
    )
    # 坑 3 自检：确认该枚举名确实需要别名（lower 后不等于规范短键）。
    assert "enemy".lower() != "monster", "前提：Enemy 必须依赖别名表，否则测不到别名逻辑"

    assert no_lesson != with_lesson, (
        f"教训点名 Enemy 后顺序应当改变——若相同说明归一失效，消费点对真实枚举名不可达。"
        f"基线={no_lesson} 有教训={with_lesson}"
    )
    assert with_lesson.index(0) > no_lesson.index(0), (
        f"被点名的 Enemy 节点应靠后：基线位置={no_lesson.index(0)}，有教训={with_lesson.index(0)}"
    )
    assert sorted(no_lesson) == sorted(with_lesson), "降权不得删除任何候选"


@pytest.mark.unit
def test_enum_name_and_short_key_are_equivalent() -> None:
    """**对称性**：Mod 真实枚举名与规范短键必须产出逐字相同的顺序。

    这正是实机复算里曾经失败的不对称点——修复后两者应当不可区分。
    用 `Enemy`/`monster`（真正依赖别名的样本，且非恒真的非-best 位）。
    """
    planner = STS2HeuristicPlanner()

    def rank(node_type_value: str) -> list[int]:
        nodes = [
            {"index": 0, "node_type": node_type_value},
            {"index": 1, "node_type": "RestSite"},
            {"index": 2, "node_type": "Shop"},
            {"index": 3, "node_type": "Elite"},
        ]
        payload = {"payload": {"travelable_nodes": nodes, "current_hp": 70, "max_hp": 80, "gold": 50}}
        directives = {"must": [], "prefer": [], "avoid": ["别打小怪"]}
        ctx = {
            "classification": {"state_name": "map"},
            "summary_context": payload,
            "strategy_context": {"preferences": {}, "strategy_directives": directives},
        }
        operation = {"action_type": "choose_map_node", "kwargs": {"option_index": 0}}
        return planner._ranked_option_indices(ctx, operation)

    enum_order = rank("Enemy")
    short_order = rank("monster")
    assert enum_order == short_order, (
        f"枚举名与短键必须等价：Enemy={enum_order}  monster={short_order}"
    )

    # 别让"两者都没生效"冒充"两者等价"：确认确实发生了降权。
    def rank_without_lesson() -> list[int]:
        nodes = [
            {"index": 0, "node_type": "Enemy"},
            {"index": 1, "node_type": "RestSite"},
            {"index": 2, "node_type": "Shop"},
            {"index": 3, "node_type": "Elite"},
        ]
        payload = {"payload": {"travelable_nodes": nodes, "current_hp": 70, "max_hp": 80, "gold": 50}}
        ctx = {
            "classification": {"state_name": "map"},
            "summary_context": payload,
            "strategy_context": {
                "preferences": {},
                "strategy_directives": {"must": [], "prefer": [], "avoid": []},
            },
        }
        operation = {"action_type": "choose_map_node", "kwargs": {"option_index": 0}}
        return planner._ranked_option_indices(ctx, operation)

    assert enum_order != rank_without_lesson(), "前提不成立：Enemy 样本未被降权，'等价'断言将恒真"
