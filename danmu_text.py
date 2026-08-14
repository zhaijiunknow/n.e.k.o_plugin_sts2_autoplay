# -*- coding: utf-8 -*-
"""STS2 弹幕 — narration 观众弹幕。

- 条件弹幕：DanmakuSpire 规则（detect_trigger）命中时抽取词条
- 氛围弹幕：按当前屏幕从 danmu_corpus.json 分桶抽取（进入战斗/奖励/商店等场景时填充）
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from .danmu_spire import detect_trigger, pick_rule_phrase

_CORPUS_PATH = Path(__file__).resolve().parent / "danmu_corpus.json"
_CORPUS: dict[str, list[str]] | None = None


def _load_corpus() -> dict[str, list[str]]:
    global _CORPUS
    if _CORPUS is None:
        try:
            data = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
            _CORPUS = data if isinstance(data, dict) else {}
        except Exception:
            _CORPUS = {}
    return _CORPUS


# 特定场面关键词（氛围弹幕排除这些词条，避免语境不符）
_AMBIENT_EXCLUDE_KEYWORDS = (
    "精英", "冲", "战未来", "删牌", "咔咔", "建筑师", "卷轴", "血上限",
    "格挡", "红色", "裸奔", "女王", "灯", "铁斩波", "实验体", "盛碗",
    "雕刻", "sl", "死战", "尽孝", "美容", "神化", "banana", "不拿", "弹幕",
    "指数", "翻倍",  # 克隆附魔相关
)


def pick_ambient_bucket(bucket: str) -> str | None:
    """抽一条氛围弹幕：从场景桶（danmu_corpus）抽中性词条，避开特定场面词条。"""
    items = _load_corpus().get(bucket) or []
    neutral = [t for t in items if not any(k in t for k in _AMBIENT_EXCLUDE_KEYWORDS)]
    if not neutral:
        neutral = items  # 桶内无中性词条 → 退回全部（语境场景仍对应）
    return random.choice(neutral) if neutral else None


def build_viewer_danmu(
    payload: dict[str, Any],
    previous: dict[str, Any] | None = None,
    *,
    seen_before: bool = False,
) -> dict[str, str] | None:
    """从局面数据选一条社区弹幕；无可生成内容返回 None。

    **只保留条件触发的弹幕**：规则命中（detect_trigger）才返回；
    不做分桶/低血的"无条件兜底"（避免没触发条件也弹）。

    返回 {"text": str, "style": "catgirl"|"narration"}（风格已映射，供 bridge 直接推送）。

    payload 关键字段：screen / summary_kind / player{current_hp,max_hp,block} /
    enemies[{name,intent}]。
    previous：前一快照（可选），用于事件型规则（血上限掉/满血被破等）。
    seen_before：该屏幕/敌人组合是否之前见过（重遇 → EncounteredBefore）。
    """
    screen = str(payload.get("screen") or "unknown").upper()
    # 无任何局面信息 → 不推（避免无上下文乱出弹幕）
    if not screen or screen == "UNKNOWN":
        return None

    # 条件弹幕：DanmakuSpire 规则命中才返回
    trigger = detect_trigger(payload, previous, seen_before=seen_before)
    if trigger:
        hit = pick_rule_phrase(trigger, payload)
        if hit:
            return hit
    return None


__all__ = ["build_viewer_danmu"]
