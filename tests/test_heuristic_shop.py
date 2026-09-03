"""商店购买优先级：遗物 → 高价值单卡 → 药水 → 删牌 → 其他卡。"""
from __future__ import annotations

from typing import Any

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner


def _card(index: int, name: str = "卡", dtype: str = "attack", desc: str = "damage") -> dict[str, Any]:
    return {
        "index": index,
        "card_id": name,
        "name": name,
        "card_type": dtype,
        "description": desc,
        "rules_text": desc,
        "price": 50,
        "is_stocked": True,
        "enough_gold": True,
    }


def _ctx(state: str, shop: dict[str, Any], avail: list[str]) -> dict[str, Any]:
    return {
        "classification": {"state_name": state},
        "summary_context": {"payload": dict(shop)},
        "snapshot": {"screen": state, "available_actions": [{"type": a, "name": a} for a in avail]},
        "strategy_context": {"preferences": {}},
    }


def test_shop_opens_inventory_when_closed() -> None:
    op = STS2HeuristicPlanner().plan(_ctx("shop", {}, ["open_shop_inventory"]))
    assert op is not None and op.action_type == "open_shop_inventory"


def test_shop_prefers_relic_first() -> None:
    shop = {
        "shop_relics": [{"index": 0, "is_stocked": True, "enough_gold": True}],
        "shop_cards": [_card(0)],
        "shop_potions": [{"index": 0, "is_stocked": True, "enough_gold": True}],
    }
    op = STS2HeuristicPlanner().plan(_ctx("shop", shop, ["buy_relic", "buy_card", "buy_potion"]))
    assert op is not None and op.action_type == "buy_relic" and op.kwargs.get("option_index") == 0


def test_shop_buys_potion_when_no_relic_or_card() -> None:
    shop = {
        "shop_cards": [],
        "shop_relics": [],
        "shop_potions": [{"index": 3, "is_stocked": True, "enough_gold": True}],
    }
    op = STS2HeuristicPlanner().plan(_ctx("shop", shop, ["buy_potion"]))
    assert op is not None and op.action_type == "buy_potion" and op.kwargs.get("option_index") == 3


def test_shop_removes_card_when_nothing_to_buy() -> None:
    op = STS2HeuristicPlanner().plan(_ctx("shop", {"shop_cards": []}, ["remove_card_at_shop"]))
    assert op is not None and op.action_type == "remove_card_at_shop"


def test_shop_closes_when_nothing_to_do() -> None:
    op = STS2HeuristicPlanner().plan(_ctx("shop", {"shop_cards": []}, ["close_shop_inventory", "proceed"]))
    assert op is not None and op.action_type == "close_shop_inventory"


def test_shop_affordable_items_filters_gold() -> None:
    items = STS2HeuristicPlanner()._shop_affordable_items(
        {
            "payload": {
                "shop_cards": [{"index": 0, "is_stocked": True, "enough_gold": False}],
                "shop_relics": [{"index": 0, "is_stocked": True, "enough_gold": True}],
                "shop_potions": [],
            }
        }
    )
    assert items[0] == [] and len(items[1]) == 1 and items[2] == []
