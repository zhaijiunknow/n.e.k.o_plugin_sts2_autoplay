from __future__ import annotations

from typing import Any, Dict, List


def _extract_action_list(payload: Any) -> List[Any]:
    """从动作响应里取出动作数组，**兼容两种真实嵌套**。

    ★★ 为什么必须兼容（t26/D10 实测，2026-10-20 00:4x，`_t26_actions_shape.log`）：
    mod 的 `/actions/available` **真实**结构是

        {"ok": true, "request_id": "...", "data": {"screen": "EVENT",
                                                   "actions": [{"name": "save_and_quit"}, ...]}}

    —— 动作在 **`data.actions`**，**顶层没有 `actions` 键**（实测：顶层键 =
    `['ok','request_id','data']`，`'actions' in payload = False`）。

    而修复前本函数只读顶层 `payload["actions"]`
    ⇒ 对真实响应**恒返回空列表**（实测 `normalize_actions(真实 payload) = 0 个`，
       而 `normalize_actions(data) = 3 个`）
    ⇒ `available_action_count` 恒为 0 ⇒ 插件"看不到任何动作"。

    ▶ 这是 D10 的**后半段根因**：契约要求的"降级到 /actions/available 取动作集"，
      若取回来仍解析不出动作，等于**没降级**。故网络层与解析层必须同时修。

    兼容顺序（从最具体到最宽，避免误解包）：
      1. `payload.data.actions`    ← ★ 实机形态（mod 统一信封 `{ok,data}`）
      2. `payload.actions`         ← 测试/旧形态的直给
      3. `payload.data` 本身是 list ← 极端简写
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("actions"), list):
        return data["actions"]
    if isinstance(payload.get("actions"), list):
        return payload["actions"]
    if isinstance(data, list):
        return data
    return []


def normalize_actions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions = _extract_action_list(payload)
    normalized: List[Dict[str, Any]] = []
    for item in actions:
        if isinstance(item, str):
            # 少数来源直接给动作名字符串（如 `/state` 的 `available_actions`）。
            if item:
                normalized.append({"type": item, "label": item, "raw": item})
            continue
        if not isinstance(item, dict):
            continue
        action_type = item.get("type") or item.get("action") or item.get("name") or "unknown"
        normalized.append(
            {
                "type": str(action_type),
                "label": str(item.get("label") or item.get("description") or item.get("name") or action_type),
                "raw": item,
            }
        )
    return normalized


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


#: ★★ D10（t26）：`/state` 不可用时，`screen` 的占位取值。
#:
#: 为什么不能留空串/None：下游 `state_machine.classify` 靠 `screen` 分支，
#: 空串会让它落到"未知界面"⇒ `allows_planner=False` ⇒ 自动玩**合法地**什么都不做。
#: 而 D10 的真实情形恰恰相反：`/state` 挂了但 `/actions/available` 是好的，
#: **有**动作可发。用一个显式占位值把它标记出来，让降级在数据上也自证。
STATE_UNAVAILABLE_SCREEN = "STATE_UNAVAILABLE"


def normalize_snapshot(state_payload: Dict[str, Any], actions_payload: Dict[str, Any]) -> Dict[str, Any]:
    """把 mod 的 `/state` + `/actions/available` 归一成插件内部 snapshot。

    ★★ D10（t26）修复：**`/state` 缺失不得把动作集清零**。
    ---------------------------------------------------------------------------
    缺陷（实机，2026-10-19 23:55:51–23:59:36）：游戏停在主菜单时 mod 的 `/state`
    返回 HTTP 500，而 `/actions/available` 同刻正常返回 200 并列出
    `continue_run` 等动作。修复前 `loop_runner.tick()` 里 `await client.get_state()`
    先抛异常 ⇒ **整个 tick 中止** ⇒ `/actions/available` 根本没被调用 ⇒
    `available_action_count` 恒为 0 ⇒ 插件把"状态查询失败"误判成"无事可做"
    ⇒ 永远不试 `continue_run` ⇒ 每次重启永久卡死（8 秒一轮，见 `_autoplay_retry_backoff`）。

    这就是 D1（计划过期误判为无解）、D9（pending 误判为已执行）的**同族第三例**：
    **状态获取失败被当成语义结论**。

    修法（本函数内只做数据层，网络层降级见 `loop_runner.tick`）：
      `state_payload` 为空/非 dict ⇒ 视为 `/state` 不可用，但**仍然**用
      `actions_payload` 归一化出动作集，并发 `state_unavailable=True` + 占位 screen。
      ⇒ 「状态查询失败」与「真的没有动作」在数据上**可区分**（后者 `len(actions)==0`）。

    ★ 不误伤：正常对局中 `state_payload` 非空 ⇒ 这条分支完全不进，行为逐位不变。
    """
    state_ok = isinstance(state_payload, dict) and bool(state_payload)
    if not state_ok:
        state_payload = {}
    actions = normalize_actions(actions_payload)
    run = state_payload.get("run") if isinstance(state_payload.get("run"), dict) else {}
    combat = state_payload.get("combat") if isinstance(state_payload.get("combat"), dict) else {}
    turn = state_payload.get("turn")
    if turn is None:
        turn = combat.get("turn")
    screen = state_payload.get("screen") or state_payload.get("screen_type")
    if not screen:
        # `/state` 不可用时**不**把 screen 判成"未知界面"，而是显式标出降级，
        # 让"状态取不到"这件事在数据和日志里都看得见（不得静默）。
        screen = STATE_UNAVAILABLE_SCREEN if not state_ok else "unknown"
    return {
        "screen": screen,
        # 真实数据 floor/act/character 常在 run 里（run.floor / run.act_id / run.character_id）
        "floor": _safe_int(state_payload.get("floor") or state_payload.get("act_floor") or run.get("floor")),
        "act": _safe_int(state_payload.get("act") or run.get("act") or run.get("act_id")),
        "in_combat": bool(state_payload.get("in_combat", False)),
        "turn": _safe_int(turn) if turn is not None else None,
        "run_id": state_payload.get("run_id"),
        "character": state_payload.get("character") or state_payload.get("character_id") or run.get("character_id") or "",
        "available_actions": actions,
        # ★ 动作数**只**由 `/actions/available` 决定，与 `/state` 成败无关。
        "available_action_count": len(actions),
        # ★★ 降级标记（新增，供 loop_runner 记可观测日志 + 测试断言）；
        #     正常路径恒为 False ⇒ 对既有消费者是纯增量字段。
        "state_unavailable": not state_ok,
        "raw_state": state_payload,
        "raw_actions": actions_payload,
    }


__all__ = ["normalize_actions", "normalize_snapshot", "STATE_UNAVAILABLE_SCREEN"]
