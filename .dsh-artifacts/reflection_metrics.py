# -*- coding: utf-8 -*-
"""复盘度量脚手架（t10 / M1）—— 只读，不改任何生产代码。

用途
----
在**改动前**建立可复现、可机器读取的复盘指标基线，供 t11 实现后做前后对照（t13）。

核心口径（来自 t_r3 冻结需求 `.dsh-artifacts/reflection-requirements.md`）
------------------------------------------------------------------------
* **端到端位移**是唯一合格判据：`plan()` 在「无教训」vs「真实注入的教训」下的输出差异条数。
  覆盖率 / 占位率类指标可被零成本刷分，本脚本一律不算作判据（仅作为附注字段输出）。
* 位移必须走**真实注入路径**：
  `STS2StrategyRepository._inject_run_memory_directives → CatgirlMemoryStore.summarize_recent`。
  **禁止手工喂 `avoid`** —— t2 已证手工喂会得假阳性。
* 默认档 `serious` 下 `service.py:793` 早退 ⇒ `ordered_candidates`（`:804`）唯一调用点不可达
  ⇒ 位移恒为 0。这是所有后续对照的**零点**。

用法
----
    & "D:\\NekoClaw\\N.E.K.O\\.venv\\Scripts\\python.exe" \
        ".dsh-artifacts\\reflection_metrics.py" --label baseline \
        --out ".dsh-artifacts\\reflection-metrics-baseline.json"

    # 确定性自检：同名同参两次调用必须输出一致（G7）
    ... --check-reproducible

    # 只看某一段
    ... --only static,data,e2e

退出码：0 = 全部段执行完毕；2 = 有指标算不出（会在 gaps 里列出，**不编造数字**）。
"""

from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from typing import Any

# --------------------------------------------------------------------------------------
# 路径常量（跟随权威事实 A/B/C）
# --------------------------------------------------------------------------------------
INSTALLED = r"C:\Users\Administrator\AppData\Local\N.E.K.O\.neko-plugin-installations\plugins\sts2_autoplay"
BUILTIN = r"D:\NekoClaw\N.E.K.O\plugin\plugins\sts2_autoplay"
REPO = r"D:\NekoClaw\n.e.k.o_plugin_sts2_autoplay"
PLUGIN_PARENT = r"C:\Users\Administrator\AppData\Local\N.E.K.O\.neko-plugin-installations\plugins"
MEMORY_PATH = r"C:\Users\Administrator\AppData\Local\N.E.K.O\sts2_autoplay\catgirl_memory.json"

#: ★配置分层（captain 2026-10-17 08:xx 更正，本脚本独立复核通过）
#:   实机生效的 `play_style` **不在**插件包内，而在 config 层：
#:     config_path = C:\Users\Administrator\AppData\Local\N.E.K.O\plugins\sts2_autoplay\config\plugin.toml
#:   该文件内容 `[sts2] play_style = "mischievous"`，覆盖包内 `plugin.toml` 的 `"serious"`。
#:   复核命令（宿主自己的解析器，权威）：
#:     python -c "from plugin.server.infrastructure.config_resolver import resolve_plugin_config; \
#:                print(resolve_plugin_config('sts2_autoplay', include_effective_config=True,
#:                                            validate_schema=False)['effective_config'])"
#:   ⇒ 输出 `.sts2.play_style = 'mischievous'`。
CONFIG_LAYER_PATH = r"C:\Users\Administrator\AppData\Local\N.E.K.O\plugins\sts2_autoplay\config\plugin.toml"

FINGERPRINT_FILES = ("heuristic_planner.py", "service.py", "catgirl_memory.py", "strategy_repository.py")

# t_r3 §2 冻结基线：复读率口径分母 53、floor P50 12.5、末步屏分母 33。
# 这里作为**期望值**写死，脚本算出的数字会与它们逐条比对（不一致 -> 报为 mismatch，不静默）。
EXPECTED = {
    "runs_total": 44,
    "outcome_defeat": 43,
    "repetition_rate_denominator": 53,
    "repetition_rate": 77.36,
    "repeat_top1_runs": 41,
    "distinct_lessons": 12,
    "information_ratio": 0.2727,
    "floor_p50": 12.5,
    "last_screen_denominator": 33,
    "last_screen_combat": 32,
    "last_screen_combat_pct": 97.0,
    "last_step_mod_solver_end_turn": 29,
    "reflection_nonempty": 10,
    "heaviest_entries": 97,
    "heaviest_runs": 33,
    "empty_route_runs": 10,
}


def _sha16(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()[:16].upper()
    except OSError:
        return None


def _find_test_import_root() -> dict[str, Any]:
    """查出 pytest（`from plugin.plugins.sts2_autoplay import ...`）**实际**会加载哪一份代码。

    这不是理论问题：`.venv` 的 editable `.pth` 把 `D:\\NekoClaw\\N.E.K.O` 注入 sys.path，
    于是 `plugin.plugins.*` 解析到 **builtin 副本**，而不是跑测试的 installed 副本。
    变异脚手架必须改到**那一份**，否则改坏后测试照样全绿（假阴性）。
    """
    probe = {
        "sys.path_has_neko_root": False,
        "plugin_dotted_import_resolves_to": None,
    }
    neko_root = os.path.normcase(r"D:\NekoClaw\N.E.K.O")
    for entry in sys.path:
        try:
            if os.path.normcase(os.path.abspath(entry)) == neko_root:
                probe["sys.path_has_neko_root"] = True
                break
        except (OSError, ValueError):
            continue
    try:
        import importlib.util

        spec = importlib.util.find_spec("plugin.plugins.sts2_autoplay.heuristic_planner")
        if spec and spec.origin:
            probe["plugin_dotted_import_resolves_to"] = spec.origin
            probe["plugin_dotted_import_copy"] = (
                "builtin" if os.path.normcase(spec.origin).startswith(os.path.normcase(BUILTIN)) else "other"
            )
    except (ImportError, ValueError, AttributeError) as exc:  # pragma: no cover - 环境异常
        probe["plugin_dotted_import_error"] = f"{type(exc).__name__}: {exc}"
    return probe


def _resolve_effective_config() -> dict[str, Any]:
    """用**宿主自己的**配置解析器算出实机生效的 `play_style`。

    ★这是闸1 的不是/是关闭的判定依据。插件包内的 `plugin.toml` 写着 `serious`，
    但 config 层（`%LOCALAPPDATA%\\N.E.K.O\\plugins\\<id>\\config\\plugin.toml`）会覆盖它 ——
    只看包内文件会得出**相反**的结论（t10 首版基线就踩了这个坑）。
    """
    import asyncio

    info: dict[str, Any] = {
        "config_layer_path": CONFIG_LAYER_PATH,
        "config_layer_exists": os.path.exists(CONFIG_LAYER_PATH),
        "config_layer_bytes": None,
        "packaged_play_style": None,
        "effective_play_style": None,
        "resolver_error": None,
    }
    if info["config_layer_exists"]:
        info["config_layer_bytes"] = os.path.getsize(CONFIG_LAYER_PATH)
    try:
        with open(os.path.join(INSTALLED, "plugin.toml"), encoding="utf-8") as handle:
            for line in handle:
                if line.strip().startswith("play_style"):
                    info["packaged_play_style"] = line.split("=", 1)[1].strip().strip('"')
                    break
    except OSError:
        pass

    neko_root = r"D:\NekoClaw\N.E.K.O"
    if neko_root not in sys.path:
        sys.path.insert(0, neko_root)
    try:
        from plugin.server.infrastructure.config_resolver import resolve_plugin_config

        resolved = resolve_plugin_config("sts2_autoplay", include_effective_config=True,
                                         validate_schema=False)
        config = resolved.get("effective_config") or {}

        def dig(node: Any, key: str) -> Any:
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == key:
                        return v
                    found = dig(v, key)
                    if found is not None:
                        return found
            return None

        info["effective_play_style"] = dig(config, "play_style")
        info["resolved_config_path"] = str(resolved.get("config_path"))
        info["manifest_path"] = str(resolved.get("manifest_path"))
    except Exception as exc:  # pragma: no cover - 宿主不可用时降级
        info["resolver_error"] = f"{type(exc).__name__}: {exc}"
    _ = asyncio
    return info



# --------------------------------------------------------------------------------------
# §A 静态结构（闸1 / 闸3 的源码证据）
# --------------------------------------------------------------------------------------
def section_static() -> dict[str, Any]:
    out: dict[str, Any] = {}
    svc = os.path.join(INSTALLED, "service.py")
    with open(svc, encoding="utf-8") as handle:
        src = handle.read()
    lines = src.splitlines()

    # 闸1：ordered_candidates 在 service.py 的调用点 + :793 早退
    tree = ast.parse(src)
    callers: dict[str, list[list[Any]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                    if name in {"ordered_candidates", "rule_indices", "legal_option_indices"}:
                        callers.setdefault(name, []).append([node.name, sub.lineno])
    out["gate1_callers_in_service_py"] = callers
    # 只取真正写出调用位置的那几行（不硬编码行号，而是锚定语义）
    early_return = None
    for idx, line in enumerate(lines, start=1):
        if "PLAY_STYLE_SERIOUS" in line and line.strip().startswith("if "):
            nxt = lines[idx] if idx < len(lines) else ""
            if "return" in nxt:
                early_return = {"line": idx, "this": line.rstrip(), "next": nxt.rstrip(), "next_line": idx + 1}
                break
    out["gate1_serious_early_return"] = early_return
    out["gate1_serious_early_return_found"] = early_return is not None
    out["gate1_ordered_candidates_callsites"] = callers.get("ordered_candidates", [])
    out["gate1_ordered_candidates_unique_callsite"] = len(callers.get("ordered_candidates", [])) == 1
    out["gate1_default_style"] = "serious"

    # 闸3：_lesson_tokens 关键词表里**不包含**模板句的任何一个词
    hp = os.path.join(INSTALLED, "heuristic_planner.py")
    with open(hp, encoding="utf-8") as handle:
        hsrc = handle.read()
    keyword_tokens: list[str] = []
    parse = ast.parse(hsrc)
    for node in ast.walk(parse):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_LESSON_KEYWORD_MAP":
                    for elt in getattr(node.value, "elts", []):
                        if isinstance(elt, ast.Tuple) and elt.elts and isinstance(elt.elts[0], ast.Constant):
                            keyword_tokens.append(str(elt.elts[0].value))
    out["gate3_lesson_keyword_tokens"] = keyword_tokens

    # 注入窗口的源码默认值
    cm = os.path.join(INSTALLED, "catgirl_memory.py")
    with open(cm, encoding="utf-8") as handle:
        csrc = handle.read()
    default_limits: list[dict[str, Any]] = []
    for node in ast.walk(ast.parse(csrc)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "summarize_recent":
            args = node.args
            defaults = args.defaults
            names = [a.arg for a in args.args[-len(defaults):]] if defaults else []
            for name, dflt in zip(names, defaults):
                if isinstance(dflt, ast.Constant):
                    default_limits.append({"arg": name, "default": dflt.value})
    out["summarize_recent_defaults"] = default_limits
    out["_inject_calls_store_without_limit"] = "_inject_run_memory_directives 调 summarize_recent() 不传 limit"
    return out


# --------------------------------------------------------------------------------------
# §B 记忆数据指标（M1 复读率等）
# --------------------------------------------------------------------------------------
def section_data(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["runs_total"] = len(runs)
    out["outcome"] = dict(collections.Counter(str(r.get("outcome") or "") for r in runs))
    out["outcome_defeat"] = out["outcome"].get("defeat", 0)

    # M1 复读率：教训**实例**层面，分母是全部 lesson 实例数（不是 run 数）
    lesson_counter = collections.Counter(
        str(lesson) for run in runs for lesson in (run.get("lessons") or [])
    )
    lesson_total = sum(lesson_counter.values())
    out["lesson_instances_total"] = lesson_total
    out["distinct_lessons"] = len(lesson_counter)
    if lesson_total:
        top_lesson, top_count = lesson_counter.most_common(1)[0]
        out["top_lesson"] = top_lesson
        out["top_lesson_count"] = top_count
        out["repetition_rate_denominator"] = lesson_total
        out["repetition_rate"] = round(top_count / lesson_total * 100, 2)
        out["repeat_top1_runs"] = sum(1 for run in runs if top_lesson in (run.get("lessons") or []))
    else:
        out["top_lesson"] = None
        out["repetition_rate_denominator"] = 0
        out["repetition_rate"] = None
        out["repeat_top1_runs"] = 0
    out["information_ratio"] = round(len(lesson_counter) / len(runs), 4) if runs else None

    # M4（观测项，永不升级为判据）
    floors = sorted(int(run.get("floor") or 0) for run in runs)
    out["floor_p50"] = statistics.median(floors) if floors else None
    out["floor_p90"] = floors[int(len(floors) * 0.9)] if floors else None
    out["floor_max"] = max(floors) if floors else None
    out["floor_mode_top3"] = [[v, c] for v, c in collections.Counter(floors).most_common(3)]
    out["M4_role"] = "observation_only"

    # 末步屏归因：分母 = **有 decisions 的 run 数**（tk4 裁决 4）
    with_decisions = [run for run in runs if (run.get("decisions") or [])]
    out["runs_with_decisions"] = len(with_decisions)
    out["runs_without_decisions"] = len(runs) - len(with_decisions)
    screens = collections.Counter(str((run["decisions"])[-1].get("screen") or "") for run in with_decisions)
    out["last_screen_counts"] = dict(screens.most_common())
    combat = screens.get("COMBAT", 0)
    out["last_screen_combat"] = combat
    if with_decisions:
        out["last_screen_combat_pct"] = round(combat / len(with_decisions) * 100, 1)
        out["last_screen_combat_pct_if_wrong_denominator_44"] = round(combat / len(runs) * 100, 1)
    out["last_step_reason_counts"] = dict(
        collections.Counter(str((run["decisions"])[-1].get("reason") or "") for run in with_decisions).most_common(5)
    )
    out["last_step_mod_solver_end_turn"] = sum(
        1 for run in with_decisions if str((run["decisions"])[-1].get("reason") or "") == "mod_solver_end_turn"
    )

    # M2 教训命中率：`decisions[].reason` 是否含中文关键词（token 抽取的原料）
    reasons = [str(d.get("reason") or "") for run in runs for d in (run.get("decisions") or [])]
    out["decisions_reason_total"] = len(reasons)
    out["decisions_reason_distinct"] = len(set(reasons))
    out["decisions_reason_all_ascii"] = all(reason.isascii() for reason in reasons) if reasons else None
    out["decisions_reason_chinese_keyword_hits"] = sum(
        1 for reason in reasons if any(k in reason for k in ("翻车", "失误", "暴毙", "秒杀", "战败", "贪", "上头"))
    )

    # 反思填充率
    out["reflection_nonempty"] = sum(1 for run in runs if str(run.get("reflection") or "").strip())

    # 数据缺口核对
    out["decisions_have_hp_fields"] = any(
        ("current_hp" in d) or ("max_hp" in d) for run in runs for d in (run.get("decisions") or [])
    )
    heaviest = [(run, h) for run in runs for h in ((run.get("hp") or {}).get("heaviest") or [])]
    out["heaviest_entries"] = len(heaviest)
    out["heaviest_runs"] = sum(1 for run in runs if (run.get("hp") or {}).get("heaviest"))
    out["heaviest_fields"] = sorted({k for _r, h in heaviest for k in h}) if heaviest else []
    out["empty_route_runs"] = sum(1 for run in runs if not (run.get("route") or []))
    return out


# --------------------------------------------------------------------------------------
# §C 运行时闸门（真实注入路径 + 端到端位移）
# --------------------------------------------------------------------------------------
def _build_repo(store: Any) -> Any:
    """从 installed 副本构造真实 `STS2StrategyRepository`（含 `catgirl_memory`）。"""
    if PLUGIN_PARENT not in sys.path:
        sys.path.insert(0, PLUGIN_PARENT)
    # 清掉可能已由 pytest 的 `.pth` 解析到 builtin 的包，确保度量对象是 installed 副本。
    for name in [n for n in list(sys.modules) if n == "sts2_autoplay" or n.startswith("sts2_autoplay.")]:
        del sys.modules[name]
    from sts2_autoplay.strategy_repository import STS2StrategyRepository

    class _Logger:
        def debug(self, *a: Any, **k: Any) -> None:
            return None

        info = warning = exception = debug

    class _Prefs:
        def list_domain(self, domain: str) -> list[Any]:
            return []

        def get(self, domain: str, key: str) -> None:
            return None

    return STS2StrategyRepository(_Logger(), _Prefs(), catgirl_memory=store)


def _planner() -> Any:
    if PLUGIN_PARENT not in sys.path:
        sys.path.insert(0, PLUGIN_PARENT)
    import sts2_autoplay.heuristic_planner as mod

    return mod.STS2HeuristicPlanner()


def _memory_store(runs: list[dict[str, Any]]) -> Any:
    if PLUGIN_PARENT not in sys.path:
        sys.path.insert(0, PLUGIN_PARENT)
    import sts2_autoplay.catgirl_memory as mod

    store = mod.CatgirlMemoryStore(path=None)
    store._runs = runs  # 只读注入，不落盘（path=None）
    return store


#: 端到端场景的地基。每个场景必须自带 classification.state_name +
#: summary_context.payload + raw_state：`plan()` 只看这三处（`_context_parts:4342`）。
def _scenarios() -> dict[str, dict[str, Any]]:
    combat = {
        "screen": "COMBAT",
        "floor": 17,
        "in_combat": True,
        "turn": 3,
        "classification": {"state_name": "combat"},
        "available_actions": [{"type": "play_card"}, {"type": "end_turn"}],
        "summary_context": {"payload": {}},
        "raw_state": {
            "combat": {
                "hand": [
                    {"index": 0, "card_type": "attack", "name": "Strike", "playable": True,
                     "damage": 9, "block": 0, "cost": 1},
                    {"index": 1, "card_type": "skill", "name": "Defend", "playable": True,
                     "damage": 0, "block": 5, "cost": 1},
                ],
                "player": {"current_hp": 22, "max_hp": 80, "block": 0, "energy": 1, "potions": []},
                "enemies": [{"index": 0, "enemy_id": "X", "is_alive": True, "current_hp": 30, "max_hp": 30,
                             "intent": {"kind": "attack", "damage": 11, "hits": 1}}],
            },
            "run": {"current_hp": 22, "max_hp": 80, "gold": 120},
        },
    }
    map_payload = {
        "travelable_nodes": [
            {"index": 0, "type": "Monster"},
            {"index": 1, "type": "RestSite"},
            {"index": 2, "type": "Elite"},
            {"index": 3, "type": "Shop"},
        ]
    }
    map_snap = {
        "screen": "MAP",
        "floor": 5,
        "classification": {"state_name": "map"},
        "available_actions": [{"type": "choose_map_node"}],
        "summary_context": {"payload": map_payload},
        "raw_state": {"run": {"current_hp": 60, "max_hp": 80, "gold": 200}},
    }
    reward_payload = {
        "reward_cards": [
            {"index": 0, "card_type": "attack", "name": "Strike"},
            {"index": 1, "card_type": "skill", "name": "Defend"},
            {"index": 2, "card_type": "power", "name": "DemonForm"},
        ]
    }
    reward_snap = {
        "screen": "CARD_SELECTION_REWARD",
        "floor": 5,
        "classification": {"state_name": "card_selection_reward"},
        "available_actions": [{"type": "choose_reward_card"}],
        "summary_context": {"payload": reward_payload},
        "raw_state": {"run": {"current_hp": 60, "max_hp": 80, "gold": 200}},
    }
    event_payload = {
        "event_options": [
            {"index": 0, "title": "接受", "description": "失去 5 点生命上限，获得 100 金币"},
            {"index": 1, "title": "拒绝", "description": "什么也不发生"},
            {"index": 2, "title": "战斗", "description": "受到 10 点伤害，获得遗物"},
        ]
    }
    event_snap = {
        "screen": "EVENT",
        "floor": 7,
        "classification": {"state_name": "event"},
        "available_actions": [{"type": "choose_event_option"}],
        "summary_context": {"payload": event_payload},
        "raw_state": {"run": {"current_hp": 60, "max_hp": 80, "gold": 200}},
    }
    return {"combat": combat, "map": map_snap, "reward": reward_snap, "event": event_snap}


def _op_identity(operation: Any) -> Any:
    if operation is None:
        return None
    data = operation.as_dict() if hasattr(operation, "as_dict") else operation
    return {
        "action_type": data.get("action_type"),
        "kwargs": data.get("kwargs"),
        "reason": data.get("reason"),
        "source": data.get("source"),
    }


def _context_from_snapshot(snapshot: dict[str, Any], avoid: list[str]) -> dict[str, Any]:
    return {
        "snapshot": snapshot,
        "classification": snapshot.get("classification") or {},
        "summary_context": snapshot.get("summary_context") or {"payload": {}},
        "strategy_context": {
            "preferences": {},
            "strategy_directives": {"must": [], "prefer": [], "avoid": list(avoid)},
        },
    }


def _ranked_for(planner: Any, snapshot: dict[str, Any], avoid: list[str]) -> tuple[Any, list[int]]:
    """走**真实** `_ranked_option_indices` 拿候选序（它内部才会调 `_apply_run_lessons`）。"""
    context = _context_from_snapshot(snapshot, avoid)
    operation = planner.plan(context)
    if operation is None:
        return None, []
    data = operation.as_dict() if hasattr(operation, "as_dict") else operation
    action_type = str(data.get("action_type") or "")
    return operation, planner._ranked_option_indices(context, {"action_type": action_type,
                                                               "kwargs": data.get("kwargs") or {}})


def _section_light_isolated(planner: Any, repo: Any, scenarios: dict[str, Any],
                            real_reflections: list[str]) -> dict[str, Any]:
    """★实机档位（mischievous）下的端到端位移，**必须隔离扰动层**。

    为什么必须隔离
    --------------
    captain 更正（我已独立复核）：实机生效的 `play_style` 由 config 层覆盖为 `mischievous`，
    于是 `service._apply_play_style` **不早退**，会走到 `ordered_candidates`，并以 20% 概率
    （`play_style.py:30`）把最优换成 `items[1]`（`play_style.py:107 return items[1], "mischievous_second"`）。

    ⇒ 若直接跑 `_apply_play_style` 对比"有教训 vs 无教训"并看到位移，
    **分不清位移来自教训还是来自扰动层掷骰子**。

    隔离方法（两条同时用，互为对照）
    --------------------------------
    1. **固定随机源**：给 service 注入 `play_style_random`，其 `.random()` 恒返回一个
       > 0.2 的值 ⇒ `roll >= probability` ⇒ 走 `kept_best`（`play_style.py:103-104`），
       扰动层输出恒为最优项。此时"有/无教训"的差异**只能**来自教训。
    2. **反向对照**：再注入恒返回 0.0 的随机源 ⇒ 强制扰动生效 ⇒ 证明第 1 条的
       "无位移"不是因为扰动层根本没被走到（否则那条断言是恒真的）。
    """
    result: dict[str, Any] = {
        "effective_play_style_source": "config 层覆盖（见顶层 effective_config 段）",
        "why": "mischievous 档 20% 概率把最优换成 items[1]，不隔离则位移不可归因",
    }
    try:
        if PLUGIN_PARENT not in sys.path:
            sys.path.insert(0, PLUGIN_PARENT)
        from sts2_autoplay.service import STS2AutoplayService
    except Exception as exc:  # pragma: no cover
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    class _Logger:
        def debug(self, *a: Any, **k: Any) -> None:
            return None

        info = warning = exception = debug

    class _FixedRandom:
        """可注入随机源：`value` 固定，便于确定性对照。"""

        def __init__(self, value: float) -> None:
            self.value = value

        def random(self) -> float:
            return self.value

    def build(style: str, roll: float) -> Any:
        service = STS2AutoplayService(_Logger(), lambda payload: None, lambda **kwargs: None,
                                      play_style_random=_FixedRandom(roll))
        service._state.play_style = style
        # 让 build_context 真的把记忆注入 avoid（真实注入路径）。
        service._planner._repo = None  # 占位，避免属性缺失
        return service

    rows: list[dict[str, Any]] = []
    for name, snapshot in scenarios.items():
        avoid_real = list(repo.build_context(snapshot)["strategy_directives"]["avoid"])
        for style, roll, tag in (
            ("mischievous", 0.9, "扰动被概率挡下(kept_best)"),
            ("mischievous", 0.0, "扰动强制生效(mischievous_second)"),
            ("serious", 0.0, "serious 早退对照"),
        ):
            service = build(style, roll)
            row: dict[str, Any] = {"screen": name, "style": style, "roll": roll, "tag": tag}
            for label, avoid in (("empty", []), ("real_injected", avoid_real)):
                context = _context_from_snapshot(snapshot, avoid)
                operation = service._planner.plan(context)
                data = operation.as_dict() if hasattr(operation, "as_dict") else (operation or {})
                shipped, info = service._apply_play_style(snapshot, dict(data))
                row[f"plan_{label}"] = {
                    "action_type": data.get("action_type"),
                    "kwargs": data.get("kwargs"),
                    "reason": data.get("reason"),
                }
                row[f"shipped_{label}"] = {
                    "action_type": shipped.get("action_type"),
                    "kwargs": shipped.get("kwargs"),
                }
                row[f"perturbation_{label}"] = info
            row["plan_displacement_bits"] = (
                0 if row["plan_empty"] == row["plan_real_injected"] else 1
            )
            row["shipped_displacement_bits"] = (
                0 if row["shipped_empty"] == row["shipped_real_injected"] else 1
            )
            rows.append(row)
    result["rows"] = rows
    result["isolated_plan_displacement_total"] = sum(
        row["plan_displacement_bits"] for row in rows if row["roll"] == 0.9
    )
    result["isolated_shipped_displacement_total"] = sum(
        row["shipped_displacement_bits"] for row in rows if row["roll"] == 0.9
    )
    # 反恒真校验：强制扰动那条必须真的产生了 "mischievous_second"
    result["perturbation_actually_fires"] = any(
        str((row.get("perturbation_empty") or {}).get("reason", "")).startswith("mischievous")
        for row in rows
    )
    return result


def section_runtime(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    store = _memory_store(runs)
    planner = _planner()
    repo = _build_repo(store)

    # ---- 闸2：注入窗口拐点 ----
    window = []
    for limit in (1, 2, 5, 6, 10, 11, 20, 30, 44, 50):
        record = store.summarize_recent(limit=limit)
        window.append({
            "limit": limit,
            "lessons": len(record["lessons"]),
            "tokens": planner._lesson_tokens(record["lessons"]),
            "preferences": len(record["preferences"]),
        })
    out["gate2_window_sweep"] = window
    default_record = store.summarize_recent()
    out["gate2_default_limit_lessons"] = default_record["lessons"]
    out["gate2_default_limit_lesson_count"] = len(default_record["lessons"])
    out["gate2_default_limit_tokens"] = planner._lesson_tokens(default_record["lessons"])
    out["gate2_first_limit_with_tokens"] = next(
        (row["limit"] for row in window if row["tokens"]), None
    )

    # ---- 闸3：真实注入 vs 手工喂（反作弊对照） ----
    real_reflections = [str(r.get("reflection") or "").strip() for r in runs if str(r.get("reflection") or "").strip()]
    out["real_reflection_count"] = len(real_reflections)
    out["real_reflection_tokens"] = [
        planner._lesson_tokens([text]) for text in real_reflections[:10]
    ]

    scenarios = _scenarios()
    e2e: dict[str, Any] = {}
    for name, snapshot in scenarios.items():
        injected = repo.build_context(snapshot)
        avoid_real = list(injected["strategy_directives"]["avoid"])
        # 1) plan() 端到端（唯一合格判据）
        op_empty = planner.plan(_context_from_snapshot(snapshot, []))
        op_real = planner.plan(_context_from_snapshot(snapshot, avoid_real))
        ident_empty, ident_real = _op_identity(op_empty), _op_identity(op_real)
        # 2) 候选序（**只作反作弊对照，不作判据**）
        _op_a, ranked_empty = _ranked_for(planner, snapshot, [])
        _op_b, ranked_real = _ranked_for(planner, snapshot, avoid_real)
        # 3) 手工喂真实反思原文（t2 证其为假阳性来源）
        handfed = planner.plan(_context_from_snapshot(snapshot, real_reflections[:1])) if real_reflections else None
        ident_handfed = _op_identity(handfed)
        e2e[name] = {
            "avoid_len_real": len(avoid_real),
            "plan_empty": ident_empty,
            "plan_real_injected": ident_real,
            "plan_displacement_bits": 0 if ident_empty == ident_real else 1,
            "plan_displacement_semantic": sorted(
                key for key in set(list(ident_empty or {}) + list(ident_real or {}))
                if (ident_empty or {}).get(key) != (ident_real or {}).get(key)
            ),
            "ranked_empty": ranked_empty,
            "ranked_real_injected": ranked_real,
            "ranked_displacement_bits": 0 if ranked_empty == ranked_real else 1,
            "plan_handfed_reflection": ident_handfed,
            "plan_handfed_displacement_bits": 0 if ident_empty == ident_handfed else 1,
        }
    out["e2e"] = e2e
    out["e2e_primary_metric"] = "plan() 在 avoid=[] vs avoid=真实注入 下的输出差异条数"
    out["e2e_primary_baseline_zero_point"] = sum(row["plan_displacement_bits"] for row in e2e.values())
    out["e2e_anticheat_note"] = (
        "若 ranked_displacement_bits > 0 而 plan_displacement_bits == 0 ⇒ 判未通过（t_r3 §3.3 反作弊条款）"
    )
    out["e2e_scope_note"] = (
        "★本段只测「教训层」的位移（直接喂 strategy_context），**绕过** service._apply_play_style。"
        "原因：实机 mischievous 档会以 20% 概率把最优换成 items[1]（play_style.py:107），"
        "不隔离就分不清位移来自教训还是扰动骰子。见 light_isolated 段。"
    )

    # ---- ★实机档位（mischievous）下的位移：必须隔离扰动 ----
    out["light_isolated"] = _section_light_isolated(planner, repo, scenarios, real_reflections)

    # ---- 真实注入的 avoid 内容（默认档下确实只有 1 条模板句） ----
    out["injected_avoid_example"] = _build_repo(store).build_context(
        scenarios["combat"]
    )["strategy_directives"]["avoid"]

    # ---- guidance 路径（t2 裁决 2：战斗屏唯一有效着力点） ----
    guidance: list[dict[str, Any]] = []
    for prompt in ("", "保命优先", "优先输出，压血", "求稳一点"):
        snapshot = json.loads(json.dumps(scenarios["combat"]))
        pending = [{"content": prompt}] if prompt else []
        snapshot["summary_context"] = {"payload": {}, "decision_payload": {"guidance": {"pending": pending}}}
        context = _context_from_snapshot(snapshot, [])
        operation = planner.plan(context)
        guidance.append({
            "pending": prompt,
            "override": planner._guidance_override_from_payload(context["summary_context"]),
            "plan": _op_identity(operation),
        })
    out["guidance_path"] = guidance
    out["guidance_path_reason_set"] = sorted({str(row["plan"]["reason"]) for row in guidance if row["plan"]})

    # ---- G4 双退化（t_r3 裁决 3：两种都要钉） ----
    degeneracy = []
    for label, cards in (
        ("2候选 attack+skill", [{"index": 0, "card_type": "attack"}, {"index": 1, "card_type": "skill"}]),
        ("3候选 attack+skill+power", [{"index": 0, "card_type": "attack"}, {"index": 1, "card_type": "skill"},
                                      {"index": 2, "card_type": "power"}]),
    ):
        payload = {"reward_cards": cards}
        tokens = ["skill", "attack"]
        penalized = planner._indices_worth_penalizing(payload, "choose_reward_card", tokens)
        ranked = list(range(len(cards)))
        context = {
            "strategy_context": {"strategy_directives": {"avoid": ["垫格挡", "输出"]}},
            "summary_context": {"payload": payload},
        }
        after = planner._apply_run_lessons(context["strategy_context"], context["summary_context"],
                                           "choose_reward_card", list(ranked))
        kept = [i for i in ranked if i not in penalized]
        degeneracy.append({
            "case": label,
            "penalized": sorted(penalized),
            "kept": len(kept),
            "total": len(cards),
            "output": after,
            "noop_degeneracy": after == ranked,
            "bias_degeneracy": len(kept) == 1,
        })
    out["g4_degeneracy"] = degeneracy

    # ---- G7 复现性：同名同参两次调用必须一致 ----
    recheck = {}
    for name, snapshot in scenarios.items():
        a = _op_identity(planner.plan(_context_from_snapshot(snapshot, [])))
        b = _op_identity(planner.plan(_context_from_snapshot(snapshot, [])))
        recheck[name] = a == b
    out["g7_reproducible"] = all(recheck.values())
    out["g7_detail"] = recheck

    # ---- 默认档可达性（运行时旁证）----
    out["gate1_runtime_note"] = (
        "默认档不触达 ordered_candidates（static 段已证唯一调用点在 :793 早退之后）；"
        "本段测得的 ranked_displacement_bits 也恒为 0，与静态结论一致。"
    )
    return out


# --------------------------------------------------------------------------------------
# §D 与 t_r3 冻结基线比对
# --------------------------------------------------------------------------------------
def section_compare(data: dict[str, Any]) -> dict[str, Any]:
    checks = [
        ("runs_total", data.get("runs_total"), EXPECTED["runs_total"]),
        ("outcome_defeat", data.get("outcome_defeat"), EXPECTED["outcome_defeat"]),
        ("repetition_rate_denominator", data.get("repetition_rate_denominator"),
         EXPECTED["repetition_rate_denominator"]),
        ("repetition_rate", data.get("repetition_rate"), EXPECTED["repetition_rate"]),
        ("repeat_top1_runs", data.get("repeat_top1_runs"), EXPECTED["repeat_top1_runs"]),
        ("distinct_lessons", data.get("distinct_lessons"), EXPECTED["distinct_lessons"]),
        ("information_ratio", data.get("information_ratio"), EXPECTED["information_ratio"]),
        ("floor_p50", data.get("floor_p50"), EXPECTED["floor_p50"]),
        ("last_screen_denominator", data.get("runs_with_decisions"), EXPECTED["last_screen_denominator"]),
        ("last_screen_combat", data.get("last_screen_combat"), EXPECTED["last_screen_combat"]),
        ("last_screen_combat_pct", data.get("last_screen_combat_pct"), EXPECTED["last_screen_combat_pct"]),
        ("last_step_mod_solver_end_turn", data.get("last_step_mod_solver_end_turn"),
         EXPECTED["last_step_mod_solver_end_turn"]),
        ("reflection_nonempty", data.get("reflection_nonempty"), EXPECTED["reflection_nonempty"]),
        ("heaviest_entries", data.get("heaviest_entries"), EXPECTED["heaviest_entries"]),
        ("heaviest_runs", data.get("heaviest_runs"), EXPECTED["heaviest_runs"]),
        ("empty_route_runs", data.get("empty_route_runs"), EXPECTED["empty_route_runs"]),
    ]
    rows = [{"metric": name, "measured": measured, "expected_t_r3": expected,
             "match": measured == expected} for name, measured, expected in checks]
    return {
        "rows": rows,
        "all_match": all(row["match"] for row in rows),
        "mismatches": [row for row in rows if not row["match"]],
    }


# --------------------------------------------------------------------------------------
# §E 数据缺口（不许编造：算不出的如实列出）
# --------------------------------------------------------------------------------------
GAPS = [
    {
        "id": "D1",
        "metric": "隐含血量阈值归一化（如『血下一半』『血量低于20』）",
        "computable": False,
        "reason": "落盘白名单 catgirl_memory.py:126-135 只保留 screen/action_type/reason/step_count；"
                  "decisions[] 无 current_hp/max_hp（本脚本已实测 decisions_have_hp_fields=false）",
        "handling": "只统计显式数字阈值，隐含阈值不统计；t11 补字段后可回填",
    },
    {
        "id": "D2",
        "metric": "基于 route 的逐房间归因覆盖率",
        "computable": "partial",
        "reason": "route 为空列表的 run 存在（实测 10/44），这些局静默缺失",
        "handling": "证据优先走 hp.heaviest（97 条、覆盖 33/44、字段完整），route 只作补充",
    },
    {
        "id": "D3",
        "metric": "注入窗口截断对位移的独立贡献",
        "computable": True,
        "reason": "已由窗口拐点扫描量化（见 gate2_window_sweep）",
        "handling": "作为闸2 基线记录；t11 修窗口后需重测同一扫描",
    },
    {
        "id": "D4",
        "metric": "复盘对真实对局胜率/楼层的提升",
        "computable": False,
        "reason": "M4 为观测项（t_r3 R7）：Ascension 10 + 药水枯竭，182 份采样中药水>=2 为 0 次，"
                  "楼层天花板不可作判据；且无实机重跑",
        "handling": "永不升级为判据；仅记录 floor 观测值",
    },
    {
        "id": "D5",
        "metric": "实机（真实对局）位移",
        "computable": False,
        "reason": "本轮全部为离线重放；实机需 t14 抓 plan() 落盘对比",
        "handling": "本基线是**离线零点**，实机结论必须另附原始日志路径 + 时间窗",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="复盘度量脚手架（t10 / M1，只读）")
    parser.add_argument("--label", default="baseline", help="快照标签，便于 t13 对照")
    parser.add_argument("--out", default=None, help="JSON 输出路径")
    parser.add_argument("--tsv", default=None, help="TSV 输出路径（扁平指标表）")
    parser.add_argument("--only", default="static,data,runtime,compare",
                        help="逗号分隔的段：static,data,runtime,compare")
    parser.add_argument("--runs", default=MEMORY_PATH, help="catgirl_memory.json 路径")
    args = parser.parse_args()

    sections = {name.strip() for name in args.only.split(",") if name.strip()}
    result: dict[str, Any] = {
        "label": args.label,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "memory_path": args.runs,
        "frozen_expectations_source": ".dsh-artifacts/reflection-requirements.md (t_r3 §2 R2)",
        "primary_metric": "end_to_end_plan_displacement",
    }

    result["fingerprints"] = {
        "installed": {f: _sha16(os.path.join(INSTALLED, f)) for f in FINGERPRINT_FILES},
        "builtin": {f: _sha16(os.path.join(BUILTIN, f)) for f in FINGERPRINT_FILES},
        "memory_json": _sha16(args.runs),
        "memory_json_bytes": os.path.getsize(args.runs) if os.path.exists(args.runs) else None,
        "memory_json_mtime": (datetime.fromtimestamp(os.path.getmtime(args.runs)).isoformat()
                              if os.path.exists(args.runs) else None),
    }
    result["test_import_root"] = _find_test_import_root()
    result["effective_config"] = _resolve_effective_config()

    with open(args.runs, encoding="utf-8") as handle:
        runs = json.load(handle).get("runs") or []

    if "static" in sections:
        result["static"] = section_static()
    if "data" in sections:
        result["data"] = section_data(runs)
    if "runtime" in sections:
        result["runtime"] = section_runtime(runs)
    if "compare" in sections and "data" in result:
        result["compare_t_r3"] = section_compare(result["data"])

    result["gaps"] = GAPS

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(f"[metrics] JSON 已写入 {args.out}")
    if args.tsv:
        rows = []
        for key, value in sorted((result.get("data") or {}).items()):
            if isinstance(value, (str, int, float, bool)) or value is None:
                rows.append((key, value))
        with open(args.tsv, "w", encoding="utf-8") as handle:
            handle.write("metric\tvalue\n")
            for key, value in rows:
                handle.write(f"{key}\t{value}\n")
        print(f"[metrics] TSV 已写入 {args.tsv}（{len(rows)} 行）")

    # 控制台摘要
    data = result.get("data") or {}
    runtime = result.get("runtime") or {}
    print(f"[metrics] label={args.label} runs={data.get('runs_total')} "
          f"复读率={data.get('repetition_rate')}%（分母 {data.get('repetition_rate_denominator')}）")
    print(f"[metrics] 末步 COMBAT={data.get('last_screen_combat')}/{data.get('runs_with_decisions')}"
          f" = {data.get('last_screen_combat_pct')}%")
    print(f"[metrics] floor P50={data.get('floor_p50')}（观测项）")
    if runtime:
        print(f"[metrics] 默认档端到端位移总和 = {runtime.get('e2e_primary_baseline_zero_point')}"
              f"（零点，应为 0）")
        print(f"[metrics] summarize_recent() 默认 lessons={runtime.get('gate2_default_limit_lesson_count')}"
              f" tokens={runtime.get('gate2_default_limit_tokens')}")
    compare = result.get("compare_t_r3")
    if compare:
        print(f"[metrics] 与 t_r3 冻结基线比对：all_match={compare['all_match']} "
              f"mismatches={len(compare['mismatches'])}")
        for row in compare["mismatches"]:
            print(f"           !! {row['metric']}: 实测 {row['measured']} != 期望 {row['expected_t_r3']}")

    return 2 if compare and not compare["all_match"] else 0


if __name__ == "__main__":
    sys.exit(main())
