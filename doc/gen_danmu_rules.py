# -*- coding: utf-8 -*-
"""从 DanmakuSpire 模组反编译源码生成完整弹幕词条 JSON。

数据源：D:\\Steam\\steamapps\\workshop\\content\\2868840\\3779807977\\decompiled\\
        DanmakuSpire.DanmakuSpireCode.Data\\DanmakuLibrary.cs
（ilspycmd 反编译产物，见插件 doc/TRANSPARENT_OVERLAY_HANDOFF.md 交接说明）

输出结构：
    {"RuleName": [{"text": "...", "style": "character"|"narration", "variant": ""|"weak"|...}]}

- 词条按 (text, variant) 去重（对齐模组 AddEntries 去重语义）
- 键按 DanmakuRuleIds 的 A1→I3 顺序排列
- style：模组 Character=角色弹幕（仓库映射 catgirl 带头像）、Narration=旁白
- variant 保留（weak/strong/waiting/completed/timeout/removal），仓库检测暂不产 variant

用法：
    python scripts/gen_danmu_rules.py \\
        --src <DanmakuLibrary.cs 路径> \\
        --out plugin/plugins/sts2_autoplay/danmu_spire_rules.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_CALL_RE = re.compile(r'(Character|Narration)\(\s*"([^"]*)"(?:\s*,\s*"([^"]*)")?\s*\)')
_RULE_ANCHOR_RE = re.compile(r'(?:Group|AddEntries)\(\s*(?:groups,\s*)?DanmakuTrigger\.(\w+)')

# 规则 ID 排序（DanmakuRuleIds.cs）：A1-A6/B1-B8/C1-C9/D1-D7/E1-E13/F1-F2/G1-G7/H1/I1-I3
_RULE_ORDER = [
    "StrongMonster", "StrongMonsterKill", "EncounteredBefore", "Reconviction",
    "LowHpElite", "EliteStreak",
    "DefenseLack", "OffenseLack", "HasBlockNoPlay", "StartupCard", "BigTurn",
    "FakeThinking", "CombatBinge", "DrawOverflow",
    "NakedHit", "NumberExtreme", "BowlbugRockExtreme", "OneTurnKill",
    "SingleCardHighDamage", "StreakBreak", "ExperimentChipDamage", "PlayerDeath",
    "NoDamageStreak",
    "CounterMatch", "QueenTorchhead", "QueenDamaged", "SculptorPreChant",
    "SculptorChant", "ScrollMaxHpLost", "ScrollMaxHpProtected",
    "DraftFutureCard", "DuplicateCard", "GotKeyCard", "GotOverpoweredCard",
    "AttackDefenseCard", "MissedKeyCard", "RejectFutureCard", "FullApotheosis",
    "SkipCardReward", "HardChoice", "BigDeck", "CardThreeVisits", "CollectiblePair",
    "BuyPremiumRelic", "ShopCardRemoval",
    "ArchitectWithPotion", "BridgeEvent", "CloneEnchantment", "RestSiteSleep",
    "FullHpRestSiteSleep", "LowHpSkippedRest", "UpgradeStreak",
    "SaveLoad",
    "MultiplayerRewardSelect", "MultiplayerShopPurchase", "MultiplayerRestSite",
]


def extract(src: str) -> dict[str, list[dict]]:
    anchors = [(m.start(), m.group(1)) for m in _RULE_ANCHOR_RE.finditer(src)]
    collected: dict[str, list[dict]] = {}
    for i, (pos, rule) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(src)
        segment = src[pos:end]
        entries = collected.setdefault(rule, [])
        for style, text, variant in _CALL_RE.findall(segment):
            if any(e["text"] == text and e["variant"] == variant for e in entries):
                continue
            entries.append(
                {
                    "text": text,
                    "style": "character" if style == "Character" else "narration",
                    "variant": variant or "",
                }
            )
    return collected


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 DanmakuSpire 完整弹幕词条 JSON")
    parser.add_argument("--src", required=True, help="反编译 DanmakuLibrary.cs 路径")
    parser.add_argument("--out", required=True, help="输出 JSON 路径")
    args = parser.parse_args()

    src_path = Path(args.src)
    if not src_path.is_file():
        print(f"错误: 找不到 {src_path}")
        return 1
    src = src_path.read_text(encoding="utf-8")
    extracted = extract(src)

    # 按规则 ID 顺序输出；未知规则追加在后
    ordered: dict[str, list[dict]] = {}
    for rule in _RULE_ORDER:
        if rule in extracted:
            ordered[rule] = extracted[rule]
    for rule in sorted(k for k in extracted if k not in ordered):
        ordered[rule] = extracted[rule]

    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    total = sum(len(v) for v in ordered.values())
    print(f"已生成 {out_path}")
    print(f"规则数: {len(ordered)}  词条总数: {total}")
    print(f"空键: {[k for k, v in ordered.items() if not v]}")
    return 0


_CARD_SET_RE = re.compile(
    r'public static readonly HashSet<string> (\w+)\s*=\s*new HashSet<string>\s*\{\s*(.*?)\s*\};',
    re.S,
)


def extract_card_categories(src: str) -> dict[str, list[str]]:
    """从 CardCategories.cs 提取卡牌分类（Attack/Block/Draw/Aoe/MultiHit/XCost）。"""
    out: dict[str, list[str]] = {}
    for name, body in _CARD_SET_RE.findall(src):
        ids = re.findall(r'"([A-Z0-9_]+)"', body)
        if ids:
            out[name] = ids
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 DanmakuSpire 完整弹幕词条 JSON")
    parser.add_argument("--src", required=True, help="反编译 DanmakuLibrary.cs 路径")
    parser.add_argument("--out", required=True, help="输出 JSON 路径")
    parser.add_argument("--card-src", help="反编译 CardCategories.cs 路径（可选，生成卡牌分类）")
    parser.add_argument("--card-out", help="卡牌分类 JSON 输出路径")
    args = parser.parse_args()

    src_path = Path(args.src)
    if not src_path.is_file():
        print(f"错误: 找不到 {src_path}")
        return 1
    src = src_path.read_text(encoding="utf-8")
    extracted = extract(src)

    # 按规则 ID 顺序输出；未知规则追加在后
    ordered: dict[str, list[dict]] = {}
    for rule in _RULE_ORDER:
        if rule in extracted:
            ordered[rule] = extracted[rule]
    for rule in sorted(k for k in extracted if k not in ordered):
        ordered[rule] = extracted[rule]

    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    total = sum(len(v) for v in ordered.values())
    print(f"已生成 {out_path}")
    print(f"规则数: {len(ordered)}  词条总数: {total}")
    print(f"空键: {[k for k, v in ordered.items() if not v]}")

    if args.card_src and args.card_out:
        card_src_path = Path(args.card_src)
        if card_src_path.is_file():
            categories = extract_card_categories(card_src_path.read_text(encoding="utf-8"))
            card_out_path = Path(args.card_out)
            card_out_path.write_text(
                json.dumps(categories, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8",
            )
            print(f"已生成卡牌分类 {card_out_path}")
            print(f"分类: { {k: len(v) for k, v in categories.items()} }")
        else:
            print(f"警告: 找不到 CardCategories.cs {card_src_path}")
    return 0
