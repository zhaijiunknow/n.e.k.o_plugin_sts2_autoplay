"""内容目录（engine/catalog/<版本>/catalog.json）的读取与查询。"""
from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

from sts2env.gamever import GAME_VERSION, catalog_path

REPO = Path(__import__("os").environ["STS2_POLICY_ROOT"])


class Catalog:
    def __init__(self, version: str = GAME_VERSION):
        """version 默认为 engine/game_version.txt 里的游戏版本（sts2env.gamever）。"""
        path = catalog_path(version)
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.version = doc["game_version"]
        self.vocab_size = doc["vocab_size"]
        self.entities = {e["key"]: e for e in doc["entities"]}

    def of(self, category: str) -> list[dict]:
        return [e for e in self.entities.values() if e["category"] == category]

    @cached_property
    def vocab(self) -> list[str]:
        return (REPO / "engine" / "catalog" / "vocab.txt").read_text(encoding="utf-8").split("\n")

    def card_pool(self, pool: str, rarities: tuple[str, ...]) -> list[str]:
        return sorted(e["key"].split(".", 1)[1] for e in self.of("CARD") if e.get("pool") == pool and e.get("rarity") in rarities)

    def relic_pool(self, pools: tuple[str, ...], rarities: tuple[str, ...], exclude_obtain_effects: bool = True) -> list[str]:
        out = []
        for e in self.of("RELIC"):
            if e.get("pool") not in pools or e.get("rarity") not in rarities:
                continue
            # 获得时有效果的遗物（选牌、给奖励等）不放进战斗课程，避免开局前出现额外的决策
            if exclude_obtain_effects and "hook:AfterObtained" in e["signature"]:
                continue
            out.append(e["key"].split(".", 1)[1])
        return sorted(out)

    def potion_pool(self, pools: tuple[str, ...], rarities: tuple[str, ...]) -> list[str]:
        return sorted(e["key"].split(".", 1)[1] for e in self.of("POTION")
                      if set(e.get("pools", [])) & set(pools) and e.get("rarity") in rarities)
