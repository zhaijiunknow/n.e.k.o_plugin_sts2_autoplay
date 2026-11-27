"""Public, non-secret settings shared by the configuration API and its form."""
from __future__ import annotations

import math
from typing import Any

GROUPS = {
    "policy": "策略网络", "pace": "操作节奏", "commentary": "弹幕与解说",
    "live": "直播模式", "records": "记录与启动偏好",
}


def field(group, label, default, hint, *, minimum=None, maximum=None, choices=None, restart=False):
    result = {"group": group, "label": label, "default": default, "hint": hint,
              "type": "boolean" if isinstance(default, bool) else "string" if isinstance(default, str) else "number",
              "restart": restart}
    if minimum is not None:
        result.update(minimum=minimum, maximum=maximum)
    if choices is not None:
        result["choices"] = choices
    return result


FIELDS = {
    "policy_enabled": field("policy", "启用策略网络", True, "优先使用已下载的角色模型；未下载或推理失败时回退到求解器与启发式。不会自动下载。"),
    "policy_timeout_seconds": field("policy", "推理超时（秒）", 20, "策略网络请求的最长等待时间。", minimum=1, maximum=300),
    "action_interval_seconds": field("pace", "常规操作间隔（秒）", 1.5, "常规自动操作的基础间隔；实际节奏还受动画、求解和等待状态影响。", minimum=0.05, maximum=60),
    "combat_replay_interval_seconds": field("pace", "战斗计划回放间隔（秒）", 0.5, "已有整回合计划时的出牌间隔；太快可能等待游戏动画。", minimum=0.05, maximum=60),
    "poll_interval_active_seconds": field("pace", "活跃轮询间隔（秒）", 1, "轮询模式下自动游玩时的读取间隔；事件流模式优先使用事件。", minimum=0.1, maximum=60),
    "poll_interval_idle_seconds": field("pace", "空闲轮询间隔（秒）", 3, "轮询模式下空闲时的读取间隔。", minimum=0.1, maximum=300),
    "poll_fallback_seconds": field("pace", "事件流兜底轮询（秒）", 5, "事件流没有新通知时的兜底读取间隔，最低 5 秒。", minimum=5, maximum=300),
    "danmaku_enabled": field("commentary", "游戏内弹幕", True, "关闭时停止游戏内弹幕并尝试清屏；游玩控制页的模式切换也可能改变此开关。"),
    "catgirl_llm_enabled": field("commentary", "模型生成点评", True, "关闭后使用启发式点评，不等于关闭弹幕。"),
    "host_digest_enabled": field("commentary", "主动局势速报", True, "允许将当前局势交给 N.E.K.O 本体主动讲解；关闭也会停止弹幕关闭后的聊天点评。"),
    "host_commentary_when_danmaku_off": field("commentary", "关闭弹幕后改用宿主点评", True, "尖塔速报开启且弹幕关闭时，在 N.E.K.O 中听取点评。"),
    "host_commentary_min_interval_seconds": field("commentary", "宿主点评最短间隔（秒）", 45, "限制宿主点评频率，避免频繁打断；最低 5 秒。", minimum=5, maximum=3600),
    "companion_push_probability": field("commentary", "陪伴点评概率", 0.2, "0～1；0 不进行随机点评，关键事件仍可能触发其他提示。", minimum=0, maximum=1),
    "autoplay_push_probability": field("commentary", "自动游玩点评概率", 0.35, "0～1；只影响随机点评，不影响自动操作。", minimum=0, maximum=1),
    "livestream_enabled": field("live", "启用直播模式", False, "延长战斗外选择并接入相关观众评论；不自动开播或连接直播间。"),
    "livestream_profile": field("live", "直播玩法", "casual", "影响非战斗选牌与购物探索，不修改战斗求解器。", choices={"focused": "认真", "casual": "随性", "chaotic": "整活"}),
    "livestream_wait_scale": field("live", "等待时间倍率", 1, "选牌 / 锻造选牌 18 秒、商店 25 秒、事件 22 秒、休息 20 秒，乘以此倍率。", minimum=0.25, maximum=3),
    "run_reflection_enabled": field("records", "局末复盘讲解", True, "关闭只停止模型讲解，结构化历史和选择对照仍保留。"),
    "encounter_memory_enabled": field("records", "采集怪物与事件记录", True, "只控制知识库记录采集，不改变决策方式。"),
    "memory_report_interval_seconds": field("records", "内存诊断日志间隔（秒）", 300, "0 关闭周期诊断；只记录当前插件进程的诊断信息。", minimum=0, maximum=86400),
    "autoplay_on_start": field("records", "启动时自动游玩", False, "仅作为下次启动的初始偏好；已有控制状态、待命和安全闸门仍优先。不会立即开始游玩。", restart=True),
}


def validate_patch(patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch:
        raise ValueError("请提交至少一个要修改的参数")
    if any(key not in FIELDS for key in patch):
        raise ValueError("包含不允许修改的参数")
    clean = {}
    for key, value in patch.items():
        spec = FIELDS[key]
        valid = False
        if spec["type"] == "boolean":
            valid = isinstance(value, bool)
        elif spec["type"] == "string":
            valid = isinstance(value, str) and value in spec["choices"]
        elif type(value) in (int, float):
            valid = spec["minimum"] <= value <= spec["maximum"] and math.isfinite(value)
        if not valid:
            raise ValueError(f"{spec['label']}：类型或范围不正确")
        clean[key] = value
    return clean
