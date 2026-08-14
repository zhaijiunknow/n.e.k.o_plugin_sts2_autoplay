from __future__ import annotations

from typing import Any, Dict, List


def normalize_actions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    normalized: List[Dict[str, Any]] = []
    for item in actions:
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


def normalize_snapshot(state_payload: Dict[str, Any], actions_payload: Dict[str, Any]) -> Dict[str, Any]:
    actions = normalize_actions(actions_payload)
    run = state_payload.get("run") if isinstance(state_payload.get("run"), dict) else {}
    return {
        "screen": state_payload.get("screen") or state_payload.get("screen_type") or "unknown",
        # 真实数据 floor/act/character 常在 run 里（run.floor / run.act_id / run.character_id）
        "floor": _safe_int(state_payload.get("floor") or state_payload.get("act_floor") or run.get("floor")),
        "act": _safe_int(state_payload.get("act") or run.get("act") or run.get("act_id")),
        "in_combat": bool(state_payload.get("in_combat", False)),
        "run_id": state_payload.get("run_id"),
        "character": state_payload.get("character") or state_payload.get("character_id") or run.get("character_id") or "",
        "available_actions": actions,
        "available_action_count": len(actions),
        "raw_state": state_payload,
        "raw_actions": actions_payload,
    }


__all__ = ["normalize_actions", "normalize_snapshot"]
