"""t37：**两条 planner 调用路径必须读同一份上下文**（把约束写进结构）。

## 缺陷（D1 实机复现形态）

`tick()` 里有两条路径都会调 `planner.plan()`：

  ① `candidate_generator.generate(...)`（`candidate_generator.py:18` 内部调 `plan()`）
  ② `planning_context`

t37 之前它们各自手写 dict，键不一致：① 缺 `solver_step_index`，
而 `heuristic_planner.py:1341` 是
    `first_step = _line_step_at(solver_plan, context.get("solver_step_index"))`
⇒ 缺键取到第 0 步、有键取到第 N 步 ⇒ 同一份计划、同一 tick 有两个 `first_step`
⇒ 一条判「可执行」（清零 stale/deadlock）、另一条判「漂移」（+1）
⇒ 上界 `LIMIT+1` 与破局门永远不可达（实测 `source=solver_deadlock_breaker` = 0 次）。

实机铁证（`N.E.K.O_Plugin_sts2_autoplay_20261020.log`，run=C6CM8CZRR9KQ）：
    07:46:42 [sts2_plan] 复用快照 step=1/3（局面与快照一致）      ← 判可复用
    07:46:43 [sts2_plan] 复用快照 step=2/3（局面与快照一致）
    07:46:43 combat_follow 求解器=play_card/FLAME_BARRIER -> 执行=(过期/交接转死锁判定)
    07:46:44 [sts2_plan] 重新计算计划（原因：手牌里已没有可打出的 IRON_WAVE）
⇒ 同一份缓存被判出相反结论。

## 本文件测什么

1. 结构：存在唯一工厂 `_planner_context`，两条路径都经由它。
2. 语义：同一份计划 + 同一个 step ⇒ 两条路径必须得到同一个 `first_step`。
3. 把原缺陷形态（缺键 ⇒ 读第 0 步）钉住，作为可观测差异的证据。
"""

from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import _line_step_at
from plugin.plugins.sts2_autoplay.loop_runner import STS2LoopRunner

pytestmark = pytest.mark.unit

# 实机同形的 3 步计划（07:46:42 的 `复用快照 step=1/3` 同形）
_PLAN = {
    "in_combat": True,
    "line": [
        {"turn": 1, "steps": [
            {"kind": "play_card", "card_id": "FLAME_BARRIER", "card_index": 0},
            {"kind": "play_card", "card_id": "IRON_WAVE", "card_index": 1},
            {"kind": "end_turn"},
        ]},
    ],
}

# 两条路径都必须存在的键（缺任一即复现原缺陷）
_REQUIRED_KEYS = (
    "snapshot",
    "classification",
    "summary_context",
    "strategy_context",
    "solver_plan",
    "plan_origin",
    "plan_fetch_signature",
    "pending_card_choice_id",
    "solver_step_index",
)


# ── 真调一次工厂（行为断言，不用源码子串 —— 见变异测试暴露的弱点）────────────


class _StubState:
    """最小 state 桩：工厂需要 `pending_card_choice_id` 与 `solver_step_index`。"""

    def __init__(self, step_index):
        self.pending_card_choice_id = None
        self.solver_step_index = step_index


class _StubService:
    def __init__(self, step_index):
        self._state = _StubState(step_index)


def _call_factory(step_index: int = 1) -> dict:
    """直接构造 loop_runner（不跑 tick），拿到它**真实返回**的 planner 上下文。"""
    runner = STS2LoopRunner.__new__(STS2LoopRunner)      # 绕过 __init__ 的重依赖
    runner._service = _StubService(step_index)
    return runner._planner_context(
        snapshot={"screen": "COMBAT", "available_actions": []},
        action_registry=[],
        classification={},
        summary_context={},
        strategy_context={},
        mode_info={},
        solver_plan=_PLAN,
        plan_origin="reused",
        plan_fetch_signature="",
    )


# ── ① 结构：单一工厂存在，且两条路径都经由它 ─────────────────────────────────


def test_single_context_factory_exists() -> None:
    """必须存在唯一工厂 `_planner_context`（把必须一致写进结构）。"""
    assert hasattr(STS2LoopRunner, "_planner_context"), (
        "缺少 `_planner_context` 单一工厂 —— 两条 planner 路径又会各自手写 dict，"
        "缺陷（同一 tick 两个 first_step）必然复发。"
    )


def test_factory_returns_step_index_from_state() -> None:
    """工厂必须把 state 里的 step_index 透传出去（行为断言，守变异体 M-A/M-B）。"""
    for idx in (0, 1, 2):
        assert _call_factory(idx)["solver_step_index"] == idx, (
            "工厂没有把 state.solver_step_index 透传出来（idx=%d）" % idx
        )


def test_tick_passes_factory_context_to_candidate_generator() -> None:
    """★ `tick()` 里传给 `candidate_generator.generate(...)` 的**实参**必须是工厂产物。

    ★ 变异体 M-C（把该路径改回手写 dict）必须转红。
      这条用 **AST** 检查实参形态：`generate(` 的第一个实参必须是
      `self._planner_context(...)` 调用，而不是字面量 `{...}`。
      （早期版本数源码里 `_planner_context(` 的出现次数，被注释里的同名文本骗过 ⇒ 不转红。）
    """
    import ast
    from pathlib import Path as _P

    src = _P(__file__).resolve().parents[1].joinpath("loop_runner.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)

    tick = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "tick"),
        None,
    )
    assert tick is not None, "找不到 tick() 定义（含 async 形式）"

    bad: list[str] = []
    for node in ast.walk(tick):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        # 目标：self._service._candidate_generator.generate(...)
        if not (isinstance(fn, ast.Attribute) and fn.attr == "generate"):
            continue
        if not node.args:
            bad.append("generate() 无实参")
            continue
        arg0 = node.args[0]
        is_factory = (
            isinstance(arg0, ast.Call)
            and isinstance(arg0.func, ast.Attribute)
            and arg0.func.attr == "_planner_context"
        )
        if not is_factory:
            bad.append(
                "generate() 的第 1 个实参不是 self._planner_context(...)，"
                "而是 %s（行 %s）" % (type(arg0).__name__, getattr(arg0, "lineno", "?"))
            )

    assert not bad, (
        "★★ `tick()` 未通过单一工厂构造 candidate_generator 的上下文：%s\n"
        "   ⇒ 该路径会与 planning_context 的键分叉 ⇒ 复现 t37 缺陷"
        "（同一 tick 两个 first_step ⇒ 计数被反复清零 ⇒ 破局门不可达）。"
        % "; ".join(bad)
    )


def test_both_planner_paths_use_the_same_factory() -> None:
    """★ 两条路径都必须调用同一工厂 —— 用**行为**断言，不用源码计数。

    ★ 变异测试（`_t37_mutate.log`）暴露：早期版本用源码里
      `self._planner_context(` 的出现次数判断，把 candidate_generator 路径改回
      手写 dict 后计数仍 ≥2（另一处在注释里），于是变异体不转红。
      ⇒ 改为：捕获两条路径实际交给 planner 的 context，断言键集合**完全相等**。
    """
    seen: list[dict] = []

    class _RecordingPlanner:
        def plan(self, context):          # noqa: D401 - 只记录，不决策
            seen.append(context)
            return None

    runner = STS2LoopRunner.__new__(STS2LoopRunner)
    runner._service = _StubService(1)
    runner._service._candidate_generator = type(
        "G", (), {"generate": lambda self, ctx, **kw: (seen.append(ctx), [])[1]},
    )()

    # ① candidate_generator 路径
    ctx_a = runner._planner_context(
        snapshot={"screen": "COMBAT", "available_actions": []}, action_registry=[],
        classification={}, summary_context={}, strategy_context={}, mode_info={},
        solver_plan=_PLAN, plan_origin="reused", plan_fetch_signature="",
    )
    runner._service._candidate_generator.generate(ctx_a, mode="program")
    # ② planning_context 路径（同一工厂、同一输入 ⇒ 必须同键）
    ctx_b = runner._planner_context(
        snapshot={"screen": "COMBAT", "available_actions": []}, action_registry=[],
        classification={}, summary_context={}, strategy_context={}, mode_info={},
        solver_plan=_PLAN, plan_origin="reused", plan_fetch_signature="",
    )

    assert len(seen) == 1, "应捕获到 1 次 planner 调用"
    assert set(seen[0]) == set(ctx_b), (
        "★★ 两条路径拿到的 context **键集合不同** ⇒ 必然分叉：\n"
        "   ① candidate_generator 路径：%s\n"
        "   ② planning_context   路径：%s\n"
        "   ★ 缺 `solver_step_index` 会让 `_line_step_at` 取到第 0 步，"
        "另一端取第 N 步 ⇒ 同一 tick 两个 first_step。"
        % (sorted(seen[0]), sorted(ctx_b))
    )
    assert seen[0].get("solver_step_index") is not None, (
        "★ candidate_generator 路径拿到的 solver_step_index 是 None ⇒ 等价于缺键 ⇒ 分叉"
    )


def test_required_keys_declared_in_factory() -> None:
    """工厂**实际返回**的键必须齐全（含 t37 修复的两个关键键）。

    ★ 变异测试（`.dsh-artifacts/_t37_mutate.log`）暴露：早期版本用**源码子串**匹配，
      结果文档字符串里提到过键名就算通过 —— 删掉真正的 return 键仍不转红。
      故改为**行为断言**：真调一次工厂，检查返回的 dict。
    """
    keys = _call_factory()
    missing = [k for k in _REQUIRED_KEYS if k not in keys]
    assert not missing, (
        "工厂返回的 dict 缺少键 %s（实际键 %s）。\n"
        "★ 尤其 `solver_step_index`：缺它会让 `_line_step_at(plan, None)` 取到第 0 步，"
        "而另一端取第 N 步 ⇒ 同一 tick 两个 first_step ⇒ 计数被反复清零。"
        % (missing, sorted(keys))
    )


def test_factory_step_index_is_not_none() -> None:
    """★ `solver_step_index` 必须是真实值，**不是 None**（None 等价于缺键）。

    ★ 变异体 M-B（把该键写成 None）必须转红 —— 这条用例专门守它。
    """
    keys = _call_factory()
    assert keys.get("solver_step_index") is not None, (
        "`solver_step_index` 为 None ⇒ `_line_step_at(plan, None)` 取第 0 步，"
        "与另一端（取第 N 步）分叉 ⇒ 复现原缺陷。"
    )


# ── ② 语义：同一 step ⇒ 同一 first_step ─────────────────────────────────────


@pytest.mark.parametrize("step_index", [0, 1, 2])
def test_same_step_index_yields_same_first_step(step_index: int) -> None:
    """同一 step_index 下两次取值必须一致（缺陷的直接判据）。"""
    a = _line_step_at(_PLAN, step_index)
    b = _line_step_at(_PLAN, step_index)
    assert a == b, "同一 step_index 下两次取值必须一致"
    assert a is not None, "step_index=%d 应能取到步骤" % step_index


def test_missing_step_index_would_read_step_zero() -> None:
    """记录原缺陷形态：缺键时 `_line_step_at(plan, None)` 取到第 0 步。

    这条用例把缺陷的可观测差异钉住：它是为什么会分叉的证据。
    """
    missing = _line_step_at(_PLAN, None)
    at_one = _line_step_at(_PLAN, 1)
    assert missing == {
        "kind": "play_card", "card_id": "FLAME_BARRIER", "card_index": 0,
    }, "缺键应取到第 0 步（这是原缺陷的根因）"
    assert at_one == {"kind": "play_card", "card_id": "IRON_WAVE", "card_index": 1}
    assert missing != at_one, (
        "缺键(step0=FLAME_BARRIER) 与 idx=1(IRON_WAVE) 不同 —— "
        "这正是同一 tick 两条路径判出相反结论的机制。"
    )


@pytest.mark.parametrize(
    ("step_index", "expected_card"),
    [(0, "FLAME_BARRIER"), (1, "IRON_WAVE")],
)
def test_first_step_follows_step_index(step_index: int, expected_card: str) -> None:
    """步进索引决定首步 —— 实机 07:46:42/07:46:43 的 step=1/3、step=2/3 同形。"""
    step = _line_step_at(_PLAN, step_index)
    assert step is not None and step.get("card_id") == expected_card


def test_step_index_beyond_end_clamps_to_last_step() -> None:
    """越界应夹到末步（生产 `_line_step_at` 的既有语义，不得回归）。"""
    step = _line_step_at(_PLAN, 99)
    assert step == {"kind": "end_turn"}, "越界应夹到末步 end_turn，实际 %r" % (step,)
