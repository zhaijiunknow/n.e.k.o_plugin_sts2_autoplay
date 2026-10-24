"""t47：战斗里「按整回合快照续行」的出牌节奏 —— 有答案的步骤不该再按搜索耗时定价。

## 玩家口径（本文件的存在理由）

> 如果是自动游玩并且场景是战斗，每一回合的出牌顺序在最开始就已经求解出答案了，
> 那么每次出牌是不是可以快一点

对，而旧实现确实在**白等**：`/solver/plan` 返回的是整回合 line（`line[0].steps`），
tick 按 `solver_step_index` 逐步执行、不再重算（日志 `[sts2_plan] 复用快照 step=k/M`），
但 t45 的 `adaptive_tick_interval` 仍拿"上一次搜索耗时"给**每一次 tick** 定价
（下限 = 实测耗时、上限 3s）⇒ 一次 8s 的回合开始搜索，会让该回合后续每张牌都等 3 秒。

## 要钉死的三件事

1. **纯回放 ⇒ 用 `combat_replay_interval_seconds`**（默认 0.5s），与上次搜索耗时无关。
2. **需要重算 ⇒ 仍走自适应节拍**（不清空 t45 的防抢跑保护）。
3. **安全阀**：上一张牌还是 `pending`、或不在战斗、或快照已不可续行 ⇒ 一律退回正常节拍；
   玩家把 `action_interval_seconds` 调得比回放值更小时，配置仍然赢。
"""

from __future__ import annotations

from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.loop_runner import (
    STS2LoopRunner,
    _combat_plan_signature,
)
from plugin.plugins.sts2_autoplay.runtime_state import STS2RuntimeState

pytestmark = pytest.mark.unit


def _card(card_id: str = "STRIKE", *, index: int = 0) -> dict[str, Any]:
    return {
        "index": index,
        "card_id": card_id,
        "name": card_id,
        "energy_cost": 1,
        "playable": True,
        "requires_target": False,
    }


def _snapshot(*, hand: list[dict[str, Any]] | None = None, turn: int = 3, in_combat: bool = True) -> dict[str, Any]:
    return {
        "screen": "COMBAT" if in_combat else "MAP",
        "in_combat": in_combat,
        "floor": 7,
        "available_actions": [{"type": "play_card"}, {"type": "end_turn"}],
        "raw_state": {
            "run_id": "T47RUN",
            "turn": turn,
            "combat": {
                "turn": turn,
                "player": {"energy": 3, "current_hp": 60, "max_hp": 80},
                "hand": hand if hand is not None else [_card("STRIKE", index=0), _card("DEFEND", index=1)],
                "enemies": [{"index": 0, "enemy_id": "SLIME", "current_hp": 30, "is_alive": True}],
            },
            "run": {"potions": []},
        },
    }


def _plan(*, cards: tuple[str, ...] = ("STRIKE", "DEFEND")) -> dict[str, Any]:
    steps = [{"kind": "play_card", "card_id": cid, "card_index": i} for i, cid in enumerate(cards)]
    steps.append({"kind": "end_turn"})
    return {
        "in_combat": True,
        "turn": 3,
        "search_status": "complete",
        "budget_ms": 8000,
        "state_fingerprint": "F00D",
        "line": [{"turn": 3, "steps": steps}],
    }


class _Logger:
    def info(self, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, *args: Any, **kwargs: Any) -> None: ...
    def error(self, *args: Any, **kwargs: Any) -> None: ...
    def debug(self, *args: Any, **kwargs: Any) -> None: ...


class _Svc:
    """节拍只读三样东西：logger、runtime state、两条配置读取。"""

    def __init__(self, *, base: float = 1.0, replay: float = 0.5) -> None:
        self.logger = _Logger()
        self._state = STS2RuntimeState()
        self._base = base
        self._replay = replay

    def _cfg_action_interval(self) -> float:
        return self._base

    def _cfg_combat_replay_interval(self) -> float:
        return self._replay


def _runner(*, base: float = 1.0, replay: float = 0.5) -> tuple[STS2LoopRunner, _Svc]:
    svc = _Svc(base=base, replay=replay)
    runner = STS2LoopRunner(svc)
    return runner, svc


def _prime_cache(runner: STS2LoopRunner, svc: _Svc, *, step_index: int = 1, snapshot: dict[str, Any] | None = None) -> None:
    """把"整回合快照已缓存 + 已打到第 step_index 步"这个真实状态摆好。"""
    snap = snapshot if snapshot is not None else _snapshot()
    svc._state.snapshot = snap
    svc._state.solver_step_index = step_index
    cached = _plan()
    runner._solver_plan_cached = cached
    runner._solver_plan_sig = _combat_plan_signature(snap)
    runner._solver_last_fetch_seconds = 8.0  # 回合开始那次搜索很慢（8s）
    runner._solver_last_budget_ms = 8000


def test_cached_replay_uses_the_fast_interval_not_the_search_time() -> None:
    """★ 核心：这一 tick 不会打搜索 ⇒ 间隔是回放值（0.5），不是被 8s 搜索顶到 3s。"""
    runner, svc = _runner(base=1.0, replay=0.5)
    _prime_cache(runner, svc, step_index=1)

    assert runner._next_tick_interval() == pytest.approx(0.5)

    # 对照：同一份实测耗时，但**没有可续行的快照** ⇒ 回到 t45 的自适应节拍（顶 3s 上限）
    runner._solver_plan_cached = None
    assert runner._next_tick_interval() == pytest.approx(3.0)


def test_replay_never_beats_the_player_configured_interval() -> None:
    """玩家把 action_interval_seconds 调到比回放值还小 ⇒ 以玩家配置为准。"""
    runner, svc = _runner(base=0.3, replay=0.5)
    _prime_cache(runner, svc, step_index=1)

    assert runner._next_tick_interval() == pytest.approx(0.3)


def test_replay_interval_has_a_floor() -> None:
    """回放值配成 0/负数不许变成"零间隔空转"（每 tick 还各打一次 /state）。"""
    runner, svc = _runner(base=1.0, replay=0.0)
    _prime_cache(runner, svc, step_index=1)

    assert runner._next_tick_interval() == pytest.approx(0.05)


def test_no_fast_path_outside_combat() -> None:
    """非战斗屏（地图/事件）仍按自适应节拍走，不打快节奏。

    ★ 期望值是 3.0 而不是 base(1.0)：这一 tick **可能**要重算，于是按
      `min(3.0, max(1.0, 上次实测 8s))` = 3.0 —— t45 的防抢跑保护原样保留。
      要断言的正是"没走 0.5 的快路径"。
    """
    runner, svc = _runner(base=1.0, replay=0.5)
    _prime_cache(runner, svc, step_index=1, snapshot=_snapshot(in_combat=False))

    assert runner._next_tick_interval() == pytest.approx(3.0)


def test_no_fast_path_when_the_turn_line_is_exhausted() -> None:
    """步骤用尽（该重算了）⇒ 不许快跑，回到自适应节拍。"""
    runner, svc = _runner(base=1.0, replay=0.5)
    _prime_cache(runner, svc, step_index=99)

    assert runner._next_tick_interval() == pytest.approx(3.0)


def test_pending_action_disables_the_fast_path() -> None:
    """上一张牌还没落地（pending 且局面未变）⇒ 不快跑，交给正常节拍与退避。"""
    runner, svc = _runner(base=1.0, replay=0.5)
    _prime_cache(runner, svc, step_index=1)
    svc._state.last_action_status = "pending"
    svc._state.invalid_action_count = 2

    # 退回自适应节拍（上次实测 8s ⇒ 顶到 3s 上限），而不是 0.5 的快路径
    assert runner._next_tick_interval() == pytest.approx(3.0)


def test_fast_path_survives_a_broken_state() -> None:
    """这条路径每 tick 都跑：状态残缺时必须退回安全节拍，绝不许抛。"""
    runner, svc = _runner(base=1.0, replay=0.5)
    svc._state.snapshot = None  # type: ignore[assignment]

    assert isinstance(runner._next_tick_interval(), float)
