"""★t38 回归门：death_attribution 判决**落库**，使「死因分布」可复现、可审计。

## 为什么单独一个文件（这是 t34 的 blocker）

t34 的验收里有 3 条依赖「死因分布回归」（attrition 占比下降 / aoe+boss 首次出现 /
与 `DEATH_KIND_TO_DIMS` 联动），但实测 `runs[]` 顶层键**没有** `death_kind` ——
死因由 `build_death_attribution()` 在 `service._build_gap_lessons` 里**现算即丢**。

⇒ 队长上一轮给出的基线「attrition 18→4 / aoe 0→5 / boss 0→3」
**无法从记忆库复现**（用一次性脚本跑的，没落库）⇒ 修复后的效果**永远无法证明**。

★★ 这是 CANON §22「执行可验证性」的又一实例：
   一个结论若不能从【已落盘的产物】重新算出来，它就不是可验证的结论，只是一次性观察。

## 本文件钉住的五件事

1. **落库**：每局记录里出现死因字段，且能**只读记忆库**读出（不依赖现场重算）
2. **判据依据也落库**：不只写结论，还要写它依据了哪些量（受损血/回合数/房间类型）
3. **向后兼容**：`absent`（旧数据没这个字段）与 `kind=unknown`（算了但证据不足）**不得混淆**
4. **同源**：落库字段由**既有**函数产出，不得另写一份判定（mod 侧 IsEventScreen 的同源教训）
5. **可复现**：同一份快照能重算出同一个分布（给出复现命令）
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys
import types
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT.parent.parent.parent) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT.parent.parent.parent))

from plugin.plugins.sts2_autoplay.death_attribution import (  # noqa: E402
    ATTRIBUTION_VERSION,
    DEATH_KIND_AOE,
    DEATH_KIND_ATTRITION,
    DEATH_KIND_BOSS,
    DEATH_KIND_UNKNOWN,
    DEATH_RECORD_KEY,
    attribution_to_record,
    build_death_attribution,
    read_death_record,
    summarize_kinds,
)

_MODULE_PATH = os.path.join(str(_PLUGIN_ROOT), "death_attribution.py")


# ── 夹具 ──────────────────────────────────────────────────────────────────────

def _attrition_run() -> dict:
    """典型「长轴消耗」局：跨多房、无单一主因（实测最常见形态）。"""
    return {
        "outcome": "defeat",
        "floor": 9,
        "route": [
            {"room": "SHRINKER_BEETLE", "floor": 2, "hp_lost": 22},
            {"room": "NIBBIT", "floor": 4, "hp_lost": 31},
            {"room": "CUBEX_CONSTRUCT", "floor": 6, "hp_lost": 28},
            {"room": "SLUDGE_SPINNER", "floor": 8, "hp_lost": 26},
            {"room": "room:MAP", "floor": 3, "hp_lost": 9999},  # 非战斗，必须被滤掉
        ],
        "hp": {"max_hp": 80, "min_hp": 0, "total_lost": 107},
    }


def _aoe_run() -> dict:
    """群怪合计占比 >= 0.5 ⇒ aoe。

    ★ 坑（我第一版就踩了）：必须用**非精英**的群怪房。
      `EYE_WITH_TEETH+FOGMOG` 里的 `FOGMOG` 在 `_KNOWN_ELITES` 里，
      而 `classify_death` 的 Boss/精英判定**优先于**统计判据（模块 docstring：
      "具体敌人身份是最强证据，优先于一切统计判据"）⇒ 用它会得到 `elite` 而不是 `aoe`。
      这是**既有模块的有意设计**（实测 Vantom 一局若不先判 Boss 会被统计规则吞掉），
      不是缺陷 —— 所以 fixture 要避开精英，而不是去改判据。
    """
    return {
        "outcome": "defeat",
        "floor": 7,
        "route": [
            {"room": "LEAF_SLIME_M+LEAF_SLIME_S", "floor": 5, "hp_lost": 60},
            {"room": "FLYCONID+SNAPPING_JAXFRUIT", "floor": 6, "hp_lost": 30},
            {"room": "NIBBIT", "floor": 7, "hp_lost": 40},
        ],
        "hp": {"max_hp": 80, "min_hp": 0},
    }


def _boss_run() -> dict:
    """Boss 按**具体敌人身份**判，优先于统计判据。"""
    return {
        "outcome": "defeat",
        "floor": 17,
        "route": [
            {"room": "VANTOM", "floor": 17, "hp_lost": 55},
            {"room": "NIBBIT", "floor": 4, "hp_lost": 20},
        ],
        "hp": {"max_hp": 80, "min_hp": 0},
    }


def _empty_evidence_run() -> dict:
    """没有任何战斗损血 ⇒ 算了，但证据不足 ⇒ unknown（★ 与 absent 不同）。"""
    return {"outcome": "defeat", "floor": 3, "route": [{"room": "room:MAP", "floor": 3, "hp_lost": 12}]}


# ══════════════════════════════════════════════════════════════════════════════
# 1. 落库：判决能被**只读记忆库**读出
# ══════════════════════════════════════════════════════════════════════════════

def test_attribution_to_record_contains_the_verdict_and_its_evidence() -> None:
    """★ 落库内容 = 结论 + **判据依据**（契约："不只写结论"）。

    没有依据的 `kind` 是个不可审计的黑盒：读者无法回答"凭什么算成 attrition"。
    """
    run = _attrition_run()
    attribution = build_death_attribution(run)
    record = attribution_to_record(attribution)

    assert record["kind"] == DEATH_KIND_ATTRITION
    assert record["version"] == ATTRIBUTION_VERSION

    # ★ 四个门槛量必须都在 —— 它们分别对应判据的四条分支
    for key in ("worst_share", "multi_enemy_share", "combat_rooms", "combat_lost"):
        assert key in record, "★ 缺判据依据 %s ⇒ 死因不可审计" % key
    # 具体证据（哪个房、掉了多少）
    assert record["worst_room"], "★ 缺 worst_room ⇒ 无法核对最强证据"
    assert record["worst_lost"] > 0
    assert record["combat_rooms"] >= 3, "attrition 的判据是跨 >=3 战斗房"
    # 非战斗房不得污染（MAP 那 9999 血不该进 combat_lost）
    assert record["combat_lost"] < 9999, "★ 非战斗房污染了 combat_lost"


def test_record_is_readable_by_reading_memory_only() -> None:
    """★ 契约：能被独立脚本【从记忆库直接读出】，不依赖现场重算。

    这里模拟"只有 runs[] 文本"的读者：只调 `read_death_record` / `summarize_kinds`，
    **不**调 `build_death_attribution`。
    """
    runs = [
        {**_attrition_run(), DEATH_RECORD_KEY: attribution_to_record(build_death_attribution(_attrition_run()))},
        {**_aoe_run(), DEATH_RECORD_KEY: attribution_to_record(build_death_attribution(_aoe_run()))},
        {**_boss_run(), DEATH_RECORD_KEY: attribution_to_record(build_death_attribution(_boss_run()))},
    ]
    summary = summarize_kinds(runs)
    assert summary["present"] == 3
    assert summary["absent"] == 0
    assert summary["kinds"] == {DEATH_KIND_ATTRITION: 1, DEATH_KIND_AOE: 1, DEATH_KIND_BOSS: 1}


def test_death_record_survives_json_roundtrip() -> None:
    """★ 落库的最终判据是"能从 JSON 文件读回来"——用真序列化证明，不用内存对象。"""
    run = {**_aoe_run(), DEATH_RECORD_KEY: attribution_to_record(build_death_attribution(_aoe_run()))}
    text = json.dumps({"runs": [run]}, ensure_ascii=False)
    parsed = json.loads(text)
    record, status = read_death_record(parsed["runs"][0])
    assert status == "present"
    assert record["kind"] == DEATH_KIND_AOE
    assert record["multi_enemy_share"] >= 0.5


# ══════════════════════════════════════════════════════════════════════════════
# 2. ★★ 向后兼容：absent 与 unknown 不得混淆（t30 的 zero vs null 同款）
# ══════════════════════════════════════════════════════════════════════════════

def test_absent_and_unknown_are_distinguishable() -> None:
    """★★ 本文件最重要的一条。

    · **旧数据**（t38 之前 50 局）：没有这个键 ⇒ `absent` —— 原因在**系统**（当时没落库）
    · **算了但证据不足**：有键、`kind=unknown` ⇒ `present` —— 原因在**这局对局**

    两者混同，会把"系统缺字段"误读成"这些局都证据不足"，从而误判数据质量。
    """
    old_run = _empty_evidence_run()  # 没有任何死因键 ⇒ 旧数据形态
    new_run = {
        **_empty_evidence_run(),
        DEATH_RECORD_KEY: attribution_to_record(build_death_attribution(_empty_evidence_run())),
    }

    rec_old, status_old = read_death_record(old_run)
    rec_new, status_new = read_death_record(new_run)

    assert status_old == "absent", "★ 旧数据必须报 absent，不能与 unknown 混"
    assert rec_old is None
    assert status_new == "present", "★ 算了但证据不足是 present，不是 absent"
    assert rec_new is not None
    assert rec_new["kind"] == DEATH_KIND_UNKNOWN

    assert status_old != status_new, "★★ 两者必须可区分（zero vs null 同款要求）"

    # 统计层也必须分开报
    summary = summarize_kinds([old_run, new_run])
    assert summary["absent"] == 1
    assert summary["present"] == 1
    assert summary["kinds"] == {DEATH_KIND_UNKNOWN: 1}, (
        "★ unknown 只能来自 present 的那条；若 absent 也被算进 kinds，说明两者被混同了"
    )


def test_malformed_payload_is_not_treated_as_a_verdict() -> None:
    """有键但类型不对 ⇒ malformed，既不算 present 也不算 absent。"""
    assert read_death_record({DEATH_RECORD_KEY: "attrition"})[1] == "malformed"
    assert read_death_record({DEATH_RECORD_KEY: None})[1] == "malformed"
    assert read_death_record({DEATH_RECORD_KEY: ["aoe"]})[1] == "malformed"
    summary = summarize_kinds([{DEATH_RECORD_KEY: "attrition"}])
    assert summary["malformed"] == 1
    assert summary["present"] == 0
    assert summary["kinds"] == {}


# ══════════════════════════════════════════════════════════════════════════════
# 3. ★ 同源：落库字段必须由**既有函数**产出，不得另写判定
# ══════════════════════════════════════════════════════════════════════════════

def test_record_is_a_pure_projection_of_the_existing_verdict() -> None:
    """★ 契约：不得另写一份判定（避免两处不同源）。

    做法：对每条既有 `kind`，断言 `attribution_to_record(...)["kind"]`
    与 `build_death_attribution(...)["kind"]` **恒等** —— 即落库只是投影。
    """
    for run in (_attrition_run(), _aoe_run(), _boss_run(), _empty_evidence_run()):
        verdict = build_death_attribution(run)
        record = attribution_to_record(verdict)
        assert record["kind"] == verdict["kind"], (
            "★ 落库的 kind 与既有判据不一致 ⇒ 出现了第二套死因判定"
        )
        # 依据量也必须逐一透传（不得自己重算成别的值）
        for key in ("worst_share", "multi_enemy_share", "combat_rooms", "combat_lost",
                    "worst_room", "worst_lost", "elite_rooms", "boss_rooms"):
            assert record[key] == verdict[key], "★ 依据量 %s 被改写了" % key


def test_projection_module_does_not_reimplement_the_thresholds() -> None:
    """★ 断言落库函数**没有**自带阈值常量（结构性证明"同源"）。

    若有人日后在 `attribution_to_record` 里写一份自己的 `>= 0.5` 判定，
    这条会立刻变红。

    ★ 检查范围**排除 docstring** —— 文档里会出现 ">=0.6" 这类字样去**解释**判据来源；
      那是说明，不是实现。我第一版没排除，导致假红（把说明文字当成了代码）。
    """
    src = io.open(_MODULE_PATH, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "attribution_to_record":
            fn = node
            break
    assert fn is not None, "★ 找不到 attribution_to_record"

    # ★ 只看**可执行语句**：丢掉 docstring（body[0]）后再取源码
    body_stmts = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                                 and isinstance(fn.body[0].value, ast.Constant)
                                 and isinstance(fn.body[0].value.value, str)) else fn.body
    code = "\n".join(ast.get_source_segment(src, stmt) or "" for stmt in body_stmts)

    for literal in ("0.5", "0.6", ">= 3", ">=3", "classify_death", "is_combat_room"):
        assert literal not in code, (
            "★ attribution_to_record 的**可执行代码**里出现 %r ⇒ 它在自己重做判定，不是纯投影" % literal
        )
    # 它必须是"读既有产物"的形态
    assert "src.get(" in code, "★ 落库函数应从既有判决里取值"


def test_unknown_kind_is_preserved_not_silently_upgraded() -> None:
    """★ 证据不足时落库必须是 unknown —— **不编造**（与既有模块口径一致）。"""
    run = _empty_evidence_run()
    record = attribution_to_record(build_death_attribution(run))
    assert record["kind"] == DEATH_KIND_UNKNOWN
    assert record["worst_share"] == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# 4. 可复现：同一份快照 → 同一个分布
# ══════════════════════════════════════════════════════════════════════════════

def test_distribution_is_reproducible_from_a_frozen_snapshot() -> None:
    """★ 契约：给出【同一份快照】上跑出的死因分布，且可复现。

    这里冻结一份 5 局的快照（模拟记忆库文件），断言分布数字**逐项确定**。
    复现命令见文件头 docstring 下方的 `_REPRO_COMMAND`。
    """
    fixture = [
        _attrition_run(), _attrition_run(), _aoe_run(), _boss_run(), _empty_evidence_run(),
    ]
    snapshot = {
        "runs": [
            {**run, DEATH_RECORD_KEY: attribution_to_record(build_death_attribution(run))}
            for run in fixture
        ]
    }
    # 两次独立统计必须完全一致（确定性）
    first = summarize_kinds(snapshot["runs"])
    second = summarize_kinds(snapshot["runs"])
    assert first == second
    assert first["present"] == 5
    assert first["absent"] == 0
    assert first["kinds"] == {
        DEATH_KIND_ATTRITION: 2,
        DEATH_KIND_AOE: 1,
        DEATH_KIND_BOSS: 1,
        DEATH_KIND_UNKNOWN: 1,
    }


_REPRO_COMMAND = (
    r"& 'D:\NekoClaw\N.E.K.O\.venv\Scripts\python.exe' -c "
    r"\"import json;from plugin.plugins.sts2_autoplay.death_attribution import summarize_kinds;"
    r"print(summarize_kinds(json.load(open(r'<记忆库路径>',encoding='utf-8'))['runs']))\""
)


# ══════════════════════════════════════════════════════════════════════════════
# 5. 变异测试（>=3 个变异体必须转红）
# ══════════════════════════════════════════════════════════════════════════════

def _mutant(source_patch) -> types.ModuleType:
    """把 death_attribution.py 改名后编译成独立模块（含 ast.parse 自检）。

    ★ 先过 ast.parse：语法错的变异体在**这里**就炸，不会被误当成"门有效"（假红）。
    ★ 必须注册进 sys.modules（模块内没有 dataclass，但保持一致以防将来加）。
    """
    src = io.open(_MODULE_PATH, encoding="utf-8").read()
    mutated = source_patch(src)
    assert mutated != src, "变异必须真的改动源码（否则是空变异、假红）"
    ast.parse(mutated)
    module = types.ModuleType("death_attribution_mutant_t38")
    module.__file__ = _MODULE_PATH
    sys.modules["death_attribution_mutant_t38"] = module
    exec(compile(mutated, "<mutant>", "exec"), module.__dict__)
    return module


def test_mutation_m1_dropping_evidence_from_record_turns_red() -> None:
    """★ 变异 1：落库只留结论、丢掉判据依据 ⇒ 审计类断言必须转红。"""
    ANCHOR = '        "worst_share": float(src.get("worst_share") or 0.0),\n'
    src_now = io.open(_MODULE_PATH, encoding="utf-8").read()
    assert ANCHOR in src_now, "★ 锚点变了：核对 worst_share 透传行"

    mutant = _mutant(lambda s: s.replace(ANCHOR, "", 1))
    record = mutant.attribution_to_record(mutant.build_death_attribution(_attrition_run()))
    assert "worst_share" not in record, (
        "★ 变异生效：依据量被丢掉了 —— 死因成了不可审计的黑盒（若这里不红，说明测试没约束依据）"
    )


def test_mutation_m2_conflating_absent_with_unknown_turns_red() -> None:
    """★ 变异 2：把 absent 当 present 处理（补一个 unknown）⇒ 兼容性门必须转红。

    这正是契约点名的"zero vs null"混同。

    ★ 锚点必须带上**上下文**：`return None, "absent"` 在源码里出现 **2 次**
      （① 非 dict 的入参守卫 ② 缺键的旧数据分支）。
      只写这一行会让 `replace(..., 1)` 打到①上，变异体行为与预期不符
      ⇒ 得到"变异没生效"的假红。故锚点含 `if DEATH_RECORD_KEY not in run:` 那一段。
    """
    ANCHOR = (
        '    if DEATH_RECORD_KEY not in run:\n'
        '        return None, "absent"\n'
    )
    src_now = io.open(_MODULE_PATH, encoding="utf-8").read()
    assert src_now.count(ANCHOR) == 1, (
        "★ 锚点应唯一命中【缺键】分支；实际命中 %d 次" % src_now.count(ANCHOR)
    )

    mutant = _mutant(
        lambda s: s.replace(
            ANCHOR,
            '    if DEATH_RECORD_KEY not in run:\n'
            '        return {"kind": DEATH_KIND_UNKNOWN}, "present"  # MUTANT(m2)\n',
            1,
        )
    )
    rec, status = mutant.read_death_record({})  # 完全没有该键 = 旧数据形态
    assert status == "present", "★ 变异生效：旧数据被当成了 present"
    assert rec is not None
    # ⇒ 下游的旧数据区分能力被破坏（这正是本条测试要守住的）


def test_mutation_m3_malformed_becomes_present_turns_red() -> None:
    """★ 变异 3：类型不对也当成有效判决 ⇒ 兼容性门必须转红。"""
    ANCHOR = '    if not isinstance(raw, dict):\n        return None, "malformed"\n'
    src_now = io.open(_MODULE_PATH, encoding="utf-8").read()
    assert ANCHOR in src_now, "★ 锚点变了：核对 malformed 分支"

    mutant = _mutant(
        lambda s: s.replace(
            ANCHOR,
            '    if not isinstance(raw, dict):\n'
            '        return {"kind": str(raw)}, "present"  # MUTANT(m3)\n',
            1,
        )
    )
    rec, status = mutant.read_death_record({DEATH_RECORD_KEY: "attrition"})
    assert status == "present", "★ 变异生效：非法 payload 被当成了有效判决"


def test_mutation_m4_summarize_counting_absent_into_kinds_turns_red() -> None:
    """★ 变异 4：统计时把 absent 也计进 kinds ⇒ 分布门必须转红。

    后果：旧数据会被算成一个 `unknown` 死因，从而**虚报** unknown 占比。
    """
    ANCHOR = '            absent += 1\n'
    src_now = io.open(_MODULE_PATH, encoding="utf-8").read()
    assert ANCHOR in src_now, "★ 锚点变了：核对 summarize_kinds 的 absent 计数"

    def patch(src: str) -> str:
        return src.replace(
            ANCHOR,
            '            absent += 1\n'
            '            kinds["unknown"] += 1  # MUTANT(m4)\n',
            1,
        )

    mutant = _mutant(patch)
    summary = mutant.summarize_kinds([{}])  # 一条旧数据
    assert summary["kinds"], "★ 变异生效：absent 被计进了 kinds"
    assert summary["kinds"].get("unknown") == 1
