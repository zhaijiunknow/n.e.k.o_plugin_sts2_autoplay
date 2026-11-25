# -*- coding: utf-8 -*-
"""局末复盘的数据层：玩家**亲手**打的每一步，与插件当时的推荐对照。

三样输入：

* 模组的 ``GET /choices``：玩家刚做的选择。它读的是游戏自己的记录（战斗历史、地图点历史），
  不是按前后局面猜的（见模组 ``Game/PlayerChoiceLog.cs``）。
* 那一刻的推荐：本局角色下载了策略网络模型时，是网络给每个候选的概率（``SOURCE_NETWORK``）；
  没有模型时，是求解器 / 启发式规划出的那一步（``SOURCE_PLANNER``，只能判"一样 / 不一样"）。
* 这一步是不是插件自己走的：是就不算玩家的选择，不进对照。

本模块不 import 游戏侧模块：几个纯函数加一个按局累加的 ``ChoiceReview``，可独立测试。
"""

from __future__ import annotations

from time import time
from typing import Any

SOURCE_NETWORK = "network"
SOURCE_PLANNER = "planner"

#: 一局里最多留多少步对照（一局约 900 步决策，留足）。
MAX_STEPS = 1200
#: 局末挑出来讲的步数。
TOP_STEPS = 5
#: 网络对照里，推荐与玩家选择的概率差低于它不算"差得多"（两个候选网络自己也拿不准）。
MIN_GAP = 0.15
#: 游戏有些记录写得晚（跳过的选牌奖励要到离开房间才写；锻造、开选牌界面的药水要等选完牌才写），
#: 所以被新推荐顶掉、还没等到回答的旧推荐再留几条、留一会儿。
BACKLOG_SIZE = 4
BACKLOG_SECONDS = 180.0

#: 可以互相比较的选择归成一组：组内不一样才算"选得不同"，跨组说明这条选择不属于这一步推荐。
_GROUPS: dict[str, str] = {
    "play_card": "combat", "use_potion": "combat", "end_turn": "combat", "discard_potion": "combat",
    "choose_map_node": "map",
    "card_choice": "card_choice",
    "relic_choice": "relic_choice",
    "event_option": "event",
    "rest_option": "rest",
    "buy_card": "shop", "buy_relic": "shop", "buy_potion": "shop",
    "card_removed": "card_select", "card_upgraded": "card_select", "card_select": "card_select",
}

#: 网络的决策类型 -> 能回答它的选择组（药水在哪一屏都能喝，所以每一屏都认 combat 组里的 use_potion）。
_DECISION_GROUPS: dict[str, set[str]] = {
    "COMBAT": {"combat"},
    "MAP": {"map"},
    "CARD_REWARD": {"card_choice"},
    "CARD_SELECT": {"card_select", "card_choice"},
    "TREASURE": {"relic_choice"},
    "EVENT": {"event"},
    "REST_SITE": {"rest"},
    "SHOP": {"shop"},
}

_ID_KEYS = ("card_id", "enemy_id", "potion_id", "relic_id", "text_key", "option_id")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _same_id(left: Any, right: Any) -> bool:
    return bool(_text(left)) and _text(left).upper() == _text(right).upper()


def _label(candidate: dict[str, Any]) -> str:
    return _text(candidate.get("label"))


def _pick(items: Any, index: Any) -> dict[str, Any] | None:
    """按 `index` 字段找那一项，找不到再按位置取（mod 的列表两种都有）。"""
    if not isinstance(items, list) or not isinstance(index, int) or isinstance(index, bool):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("index") == index:
            return item
    if 0 <= index < len(items) and isinstance(items[index], dict):
        return items[index]
    return None


def collect_names(raw_state: Any, *, limit: int = 400) -> dict[str, str]:
    """局面里出现的 id -> 显示名（卡牌、敌人、药水、遗物），给复盘文字用。"""
    names: dict[str, str] = {}
    stack: list[Any] = [raw_state]
    while stack and len(names) < limit:
        node = stack.pop()
        if isinstance(node, dict):
            name = _text(node.get("name") or node.get("title"))
            for key in _ID_KEYS:
                identifier = _text(node.get(key))
                if identifier and name:
                    names.setdefault(identifier, name)
            stack.extend(value for value in node.values() if isinstance(value, (dict, list)))
        elif isinstance(node, list):
            stack.extend(item for item in node if isinstance(item, (dict, list)))
    return names


def humanize(label: str, names: dict[str, str]) -> str:
    """把候选标签里的 id 换成显示名（`play BASH -> NIBBIT` -> `play 痛击 -> 小啃兽`）。"""
    words = _text(label).split(" ")
    return " ".join(names.get(word, word) for word in words)


def choice_label(choice: dict[str, Any]) -> str:
    """模组报来的一条选择，写成与网络候选同一种说法的标签。"""
    kind = _text(choice.get("kind"))
    identifier = _text(choice.get("id"))
    target = _text(choice.get("target"))
    if kind == "end_turn":
        return "end turn"
    if kind in {"play_card", "use_potion"}:
        return f"{'play' if kind == 'play_card' else 'use'} {identifier}" + (f" -> {target}" if target else "")
    if kind == "choose_map_node":
        return f"-> ({identifier})"
    if kind in {"card_choice", "relic_choice"}:
        return f"take {identifier}" if identifier else "skip"
    if kind in {"buy_card", "buy_relic", "buy_potion"}:
        return f"buy {identifier}"
    if kind in {"card_removed", "card_upgraded"}:
        return f"select {identifier}"
    if kind == "discard_potion":
        return f"discard {identifier}"
    return identifier or kind


def match_candidate(candidates: list[Any], choice: dict[str, Any], probs: list[float] | None = None) -> int | None:
    """这条选择对应网络候选列表里的哪一个；对不上返回 None。

    同一张牌在手里有两张时会有两个标签相同的候选：它们是同一个选择，取网络打分高的那个。
    """
    kind = _text(choice.get("kind"))
    identifier = _text(choice.get("id"))

    def where(action_kinds: set[str], predicate: Any) -> list[int]:
        return [
            index for index, candidate in enumerate(candidates)
            if isinstance(candidate, dict) and _text(candidate.get("kind")) in action_kinds and predicate(candidate)
        ]

    if kind == "end_turn":
        hits = where({"EndTurn"}, lambda c: True)
    elif kind in {"play_card", "use_potion"}:
        head = f"{'play' if kind == 'play_card' else 'use'} {identifier}".upper()
        hits = where({"PlayCard" if kind == "play_card" else "UsePotion"}, lambda c: _label(c).split(" -> ")[0].upper() == head)
        hits = _narrow_by_target(candidates, hits, choice)
    elif kind == "discard_potion":
        hits = where({"DiscardPotion"}, lambda c: _label(c).upper() == f"DISCARD {identifier}".upper())
    elif kind == "choose_map_node":
        hits = where({"MapMove"}, lambda c: _label(c).startswith(f"-> ({identifier})"))
    elif kind == "card_choice":
        wanted = {f"TAKE {identifier}".upper(), f"SELECT {identifier}".upper()}
        hits = where({"PickCardReward", "SelectCard"}, lambda c: _label(c).upper() in wanted) if identifier else where({"CardRewardAlt"}, lambda c: True)
        if not identifier and len(hits) != 1:
            hits = []  # 「跳过」之外还有别的替代按钮（重掷等）时分不清，不认
    elif kind == "relic_choice":
        hits = where({"PickRelic"}, lambda c: _label(c).upper() == f"TAKE {identifier}".upper()) if identifier else where({"SkipRelic"}, lambda c: True)
    elif kind == "event_option":
        hits = where({"EventOption"}, lambda c: _same_event_option(_label(c), identifier))
    elif kind == "rest_option":
        hits = where({"RestOption"}, lambda c: _same_id(_label(c), identifier))
    elif kind in {"buy_card", "buy_relic", "buy_potion"}:
        hits = where({"Buy"}, lambda c: _label(c).upper().startswith(f"BUY {identifier} (".upper()))
    elif kind in {"card_removed", "card_upgraded"}:
        hits = where({"SelectCard"}, lambda c: _label(c).upper() == f"SELECT {identifier}".upper())
    else:
        hits = []
    if not hits:
        return None
    if probs:
        return max(hits, key=lambda index: probs[index] if index < len(probs) else 0.0)
    return hits[0]


def _same_event_option(text_key: str, title_key: str) -> bool:
    """事件选项的 TextKey 与游戏历史里记的标题键是不是同一个选项。

    一般标题键就是 ``TextKey + ".title"``；给遗物的选项（先古之民）记的是遗物自己的标题键
    （``LARGE_CAPSULE.title``），这时拿 TextKey 的最后一段去对。
    """
    text_key, title_key = _text(text_key), _text(title_key)
    if not text_key or not title_key:
        return False
    if title_key == text_key or title_key.startswith(text_key + "."):
        return True
    return "." in text_key and text_key.rsplit(".", 1)[-1] == title_key.split(".", 1)[0]


def _narrow_by_target(candidates: list[Any], hits: list[int], choice: dict[str, Any]) -> list[int]:
    target_index = choice.get("target_index")
    target = _text(choice.get("target"))
    if isinstance(target_index, int) and not isinstance(target_index, bool):
        exact = [i for i in hits if candidates[i].get("target_index") == target_index]
        if exact:
            return exact
    if target:
        narrowed = [i for i in hits if _label(candidates[i]).upper().endswith(f" -> {target}".upper())]
    else:
        narrowed = [i for i in hits if " -> " not in _label(candidates[i])]
    # 只有一种写法时不必再分（对自己用的药水，候选可能不带目标）。
    return narrowed or (hits if len({_label(candidates[i]) for i in hits}) == 1 else [])


def planner_recommendation(operation: Any, raw_state: Any) -> dict[str, Any] | None:
    """把规划器的一步翻译成与 `/choices` 同一套说法 ``{kind, id, target_index}``；认不出返回 None。"""
    if not isinstance(operation, dict):
        return None
    raw = raw_state if isinstance(raw_state, dict) else {}
    action = _text(operation.get("action_type")).lower()
    kwargs = operation.get("kwargs") if isinstance(operation.get("kwargs"), dict) else {}
    option = kwargs.get("option_index")

    def section(name: str) -> dict[str, Any]:
        return raw.get(name) if isinstance(raw.get(name), dict) else {}

    def item_id(items: Any, key: str) -> str | None:
        item = _pick(items, option)
        return _text(item.get(key)) or None if item else None

    if action == "end_turn":
        return {"kind": "end_turn", "id": None}
    if action == "play_card":
        card = _pick(section("combat").get("hand"), kwargs.get("card_index"))
        return {"kind": "play_card", "id": _text(card.get("card_id")), "target_index": kwargs.get("target_index")} if card else None
    if action == "use_potion":
        identifier = item_id(raw.get("potions"), "potion_id")
        return {"kind": "use_potion", "id": identifier, "target_index": kwargs.get("target_index")} if identifier else None
    if action == "choose_map_node":
        node = _pick(section("map").get("available_nodes"), option)
        return {"kind": "choose_map_node", "id": f"{node.get('col')},{node.get('row')}"} if node else None
    if action == "choose_reward_card":
        identifier = item_id(section("reward").get("cards"), "card_id")
        return {"kind": "card_choice", "id": identifier} if identifier else None
    if action in {"skip_reward_cards", "skip_card_reward"}:
        return {"kind": "card_choice", "id": None}
    if action == "choose_rest_option":
        identifier = item_id(section("rest").get("options"), "option_id")
        return {"kind": "rest_option", "id": identifier} if identifier else None
    if action == "choose_event_option":
        identifier = item_id(section("event").get("options"), "text_key")
        return {"kind": "event_option", "id": identifier} if identifier else None
    if action in {"buy_card", "buy_relic", "buy_potion"}:
        noun = action.split("_", 1)[1]
        identifier = item_id(section("shop").get(noun + "s"), noun + "_id")
        return {"kind": action, "id": identifier} if identifier else None
    if action == "select_deck_card":
        identifier = item_id(section("selection").get("cards"), "card_id")
        return {"kind": "card_select", "id": identifier} if identifier else None
    if action == "choose_treasure_relic":
        identifier = item_id(section("chest").get("relic_options"), "relic_id")
        return {"kind": "relic_choice", "id": identifier} if identifier else None
    return None


def compare_planner(recommendation: dict[str, Any], choice: dict[str, Any]) -> bool | None:
    """玩家的选择与规划器推荐是否相同；两者不是同一类选择（这条选择不属于这一步）时返回 None。"""
    group = _GROUPS.get(_text(recommendation.get("kind")))
    if group is None or group != _GROUPS.get(_text(choice.get("kind"))):
        return None
    rec_kind, kind = _text(recommendation.get("kind")), _text(choice.get("kind"))
    if group != "card_select" and rec_kind != kind:
        return False
    rec_id, identifier = _text(recommendation.get("id")), _text(choice.get("id"))
    if kind == "event_option":
        same = _same_event_option(rec_id, identifier)
    elif not rec_id and not identifier:
        same = True
    else:
        same = _same_id(rec_id, identifier)
    if same and kind in {"play_card", "use_potion"}:
        rec_target, target = recommendation.get("target_index"), choice.get("target_index")
        if isinstance(rec_target, int) and isinstance(target, int) and rec_target != target:
            return False
    return same


class ChoiceReview:
    """一局里"玩家的选择 vs 当时的推荐"的累加器。换局时由 `begin_run` 清空。"""

    def __init__(self) -> None:
        self.run_id = ""
        self.steps: list[dict[str, Any]] = []
        self.pending: dict[str, Any] | None = None
        #: 被新推荐顶掉、还没等到回答的旧推荐（新的在后）。见 BACKLOG_SIZE。
        self.backlog: list[dict[str, Any]] = []
        #: `/choices` 读到哪了；None = 还没和这个 mod 进程对过表（第一次只对表，不认领旧选择）。
        self.since: int | None = None
        #: 已经认领过的最大序号：同一条选择即使被再报一遍，也不回答第二条推荐。
        self.seen_seq = 0

    def begin_run(self, run_id: str) -> None:
        if run_id and run_id != self.run_id:
            self.run_id = run_id
            self.steps = []
            self.drop_pending()

    def pending_key(self) -> str:
        return _text(self.pending.get("key")) if self.pending else ""

    def drop_pending(self) -> None:
        self.pending = None
        self.backlog = []

    def _hang(self, pending: dict[str, Any]) -> None:
        if self.pending is not None:
            self.backlog = (self.backlog + [self.pending])[-BACKLOG_SIZE:]
        self.pending = pending

    # ---- 记下这一刻的推荐 -----------------------------------------------------------------

    def capture_network(self, scored: dict[str, Any], *, context: dict[str, Any]) -> None:
        candidates = [dict(c) for c in scored.get("candidates") or [] if isinstance(c, dict)]
        probs = [float(p) for p in scored.get("probs") or []]
        choice = scored.get("choice")
        if not candidates or len(probs) != len(candidates) or not isinstance(choice, int):
            return
        self._hang({
            "source": SOURCE_NETWORK,
            "key": _text(scored.get("decision_id")),
            "decision": _text(scored.get("kind")),
            "candidates": candidates,
            "probs": probs,
            "choice": choice,
            "value": scored.get("value"),
            "context": dict(context),
            "captured_at": time(),
        })

    def capture_planner(self, operation: dict[str, Any], raw_state: Any, *, key: str, context: dict[str, Any]) -> None:
        recommendation = planner_recommendation(operation, raw_state)
        if recommendation is None:
            return
        if self.pending and self.pending.get("key") == key:
            return
        self._hang({
            "source": SOURCE_PLANNER,
            "key": key,
            "decision": _text(operation.get("action_type")),
            "recommendation": recommendation,
            "decision_source": _text(operation.get("source")),
            "context": dict(context),
            "captured_at": time(),
        })

    # ---- 认领模组报来的选择 ---------------------------------------------------------------

    def resolve(self, choices: list[Any], *, last_plugin_action_at: float = 0.0) -> list[dict[str, Any]]:
        """用新到的选择回答挂着的推荐；返回这次新记下的对照。

        一条推荐只被回答一次，一条选择也只回答一条推荐：先问最新的那条，它不认（不是同一类选择）
        再问被顶掉的旧推荐。推荐挂出之后插件自己动过手（`last_plugin_action_at` 更晚）时，
        那一步是插件走的，不记。
        """
        now = time()
        waiting = [p for p in self.backlog if now - float(p.get("captured_at") or 0.0) <= BACKLOG_SECONDS]
        if self.pending is not None:
            waiting.append(self.pending)
        waiting = [p for p in waiting if last_plugin_action_at <= float(p.get("captured_at") or 0.0)]
        done: list[dict[str, Any]] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            for pending in reversed(waiting):
                step = self._answer(pending, choice)
                if step is None:
                    continue
                waiting.remove(pending)
                done.append(step)
                break
        self.pending = waiting.pop() if waiting and waiting[-1] is self.pending else None
        self.backlog = waiting[-BACKLOG_SIZE:]
        self.steps = (self.steps + done)[-MAX_STEPS:]
        return done

    def _answer(self, pending: dict[str, Any], choice: dict[str, Any]) -> dict[str, Any] | None:
        context = pending.get("context") if isinstance(pending.get("context"), dict) else {}
        names = context.get("names") if isinstance(context.get("names"), dict) else {}
        step: dict[str, Any] = {
            "source": pending["source"],
            "decision": pending.get("decision"),
            "floor": choice.get("floor") if choice.get("floor") is not None else context.get("floor"),
            "act": choice.get("act") if choice.get("act") is not None else context.get("act"),
            "round": choice.get("round"),
            "room": _text(context.get("room")),
            "situation": _text(context.get("situation")),
            "hp": context.get("hp"),
            "max_hp": context.get("max_hp"),
        }
        if pending["source"] == SOURCE_NETWORK:
            group = _GROUPS.get(_text(choice.get("kind")))
            allowed = _DECISION_GROUPS.get(_text(pending.get("decision")), set())
            if group not in allowed and _text(choice.get("kind")) != "use_potion":
                return None
            candidates, probs = pending["candidates"], pending["probs"]
            index = match_candidate(candidates, choice, probs)
            if index is None:
                return None
            best = int(pending["choice"])
            same = index == best or _label(candidates[index]) == _label(candidates[best])
            step.update(
                chosen=humanize(_label(candidates[index]), names),
                recommended=humanize(_label(candidates[best]), names),
                same=same,
                p_chosen=round(probs[index], 4),
                p_recommended=round(probs[best], 4),
                gap=0.0 if same else round(max(0.0, probs[best] - probs[index]), 4),
                value=pending.get("value"),
                candidates=len(candidates),
            )
            return step
        recommendation = pending["recommendation"]
        same = compare_planner(recommendation, choice)
        if same is None:
            return None
        step.update(
            chosen=humanize(choice_label(choice), names),
            recommended=humanize(choice_label({**recommendation, "target": ""}), names),
            same=same,
            decision_source=pending.get("decision_source"),
        )
        return step

    # ---- 局末 -----------------------------------------------------------------------------

    def summary(self, *, top: int = TOP_STEPS) -> dict[str, Any]:
        """本局对照的汇总：对了多少步、差得最多的几步。没有任何对照时 ``compared`` 为 0。"""
        compared = len(self.steps)
        agreed = sum(1 for step in self.steps if step.get("same"))
        network = [s for s in self.steps if s.get("source") == SOURCE_NETWORK]
        return {
            "compared": compared,
            "agreed": agreed,
            "network_steps": len(network),
            "planner_steps": compared - len(network),
            "steps": divergent_steps(self.steps, top=top),
            "explanation": "",
        }


def divergent_steps(steps: list[dict[str, Any]], *, top: int = TOP_STEPS) -> list[dict[str, Any]]:
    """差得最多的几步，按发生顺序给出。

    网络的对照按"推荐的概率 − 玩家所选的概率"排；旧策略的对照没有差距大小，按当时血量越低越靠前
    （越危险的时候选得不同，越值得讲）。有网络的对照时只讲网络的。
    """
    different = [dict(step, order=order) for order, step in enumerate(steps) if not step.get("same")]
    network = [s for s in different if s.get("source") == SOURCE_NETWORK and float(s.get("gap") or 0.0) >= MIN_GAP]
    if network:
        picked = sorted(network, key=lambda s: -float(s.get("gap") or 0.0))[:top]
    else:
        planner = [s for s in different if s.get("source") == SOURCE_PLANNER]

        def danger(step: dict[str, Any]) -> float:
            hp, max_hp = step.get("hp"), step.get("max_hp")
            return hp / max_hp if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)) and max_hp > 0 else 1.0

        picked = sorted(planner, key=danger)[:top]
    picked.sort(key=lambda s: s["order"])
    for step in picked:
        step.pop("order", None)
    return picked


def review_digest(record: dict[str, Any], review: dict[str, Any]) -> str:
    """交给 LLM 的提示词正文：这局是谁、打到哪，以及差得最多的那几步。"""
    lines = [
        f"结局:{record.get('outcome') or '?'}｜角色:{record.get('character') or '?'}｜打到第{record.get('floor') or 0}层｜"
        f"玩家亲手打的 {review.get('compared')} 步里有 {review.get('agreed')} 步和推荐一样"
    ]
    for number, step in enumerate(review.get("steps") or [], start=1):
        where = f"第{step.get('floor')}层" + (f" 第{step.get('round')}回合" if step.get("round") else "")
        if step.get("room"):
            where += f"（{step['room']}）"
        line = f"{number}. {where}"
        if step.get("situation"):
            line += f"｜局面:{step['situation']}"
        if step.get("source") == SOURCE_NETWORK:
            line += (
                f"｜玩家选:{step.get('chosen')}（网络给它 {float(step.get('p_chosen') or 0) * 100:.0f}%）"
                f"｜推荐:{step.get('recommended')}（{float(step.get('p_recommended') or 0) * 100:.0f}%）"
            )
        else:
            line += f"｜玩家选:{step.get('chosen')}｜推荐:{step.get('recommended')}（求解器/启发式，只知道不一样）"
        lines.append(line)
    return "\n".join(lines)


__all__ = [
    "SOURCE_NETWORK", "SOURCE_PLANNER", "TOP_STEPS", "MIN_GAP", "BACKLOG_SIZE", "ChoiceReview",
    "match_candidate", "planner_recommendation", "compare_planner", "divergent_steps",
    "review_digest", "choice_label", "collect_names", "humanize",
]
