from __future__ import annotations

from plugin.plugins.sts2_autoplay.candidate_generator import STS2CandidateGenerator
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.loop_runner import _combat_deadlock_signature


def test_candidate_generator_prefers_planned_operation_then_registry_actions() -> None:
    planner = STS2HeuristicPlanner()
    generator = STS2CandidateGenerator(planner)
    context = {
        "classification": {"state_name": "event"},
        "summary_context": {"payload": {}},
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "available_actions": [
                {"type": "choose_event_option", "raw": {"name": "choose_event_option", "index": 1}},
                {"type": "proceed", "raw": {"name": "proceed"}},
            ],
            "action_registry": [
                {"id": "evt-1", "type": "choose_event_option", "default_kwargs": {"option_index": 1}, "category": "event"},
                {"id": "evt-2", "type": "proceed", "default_kwargs": {}, "category": "event"},
            ],
        },
    }

    candidates = generator.generate(context, mode="program")

    assert len(candidates) >= 2
    assert candidates[0]["action_type"] == "choose_event_option"
    assert candidates[0]["priority"] >= candidates[1]["priority"]
    assert any(item["action_id"] == "evt-2" for item in candidates)


def test_candidate_generator_uses_raw_fallback_when_registry_missing() -> None:
    generator = STS2CandidateGenerator(None)
    context = {
        "snapshot": {
            "available_actions": [
                {"type": "open_timeline"},
                {"type": "continue_run"},
            ]
        }
    }

    candidates = generator.generate(context, mode="program")

    assert len(candidates) == 2
    assert candidates[0]["source"] == "raw_fallback_candidate"
    assert candidates[0]["action_type"] == "open_timeline"


def test_planner_uses_default_option_index_for_map_when_no_preference() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "map"},
            "summary_context": {"payload": {"travelable_nodes": [{"index": 2, "type": "monster"}] }},
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "MAP",
                "available_actions": [
                    {"type": "choose_map_node", "raw": {"name": "choose_map_node", "requires_index": True}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.action_type == "choose_map_node"
    assert operation.kwargs["option_index"] == 2








def _combat_context(*, solver_plan=None, hand=None, energy=2, block=0, enemies=None) -> dict:
    """构造战斗 context。默认手牌是一张攻击 + 一张防御。

    ★ `solver_plan` 放在 **context 顶层** —— 与生产一致：
      `loop_runner.py:225` 的 planning_context 就是
      `{..."solver_plan": solver_plan, "solver_step_index": ...}`。
      planner 读的是 `context.get("solver_plan")`（heuristic_planner.py:1272），
      **不是** `summary_context["payload"]` 里的那份
      （payload 那份是给猫娘弹幕用的，两条消费路径，别混）。
    """
    ctx: dict = {
        "classification": {"state_name": "combat"},
        "summary_context": {"payload": {}},
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "screen": "COMBAT",
            "available_actions": [
                {"type": "play_card", "raw": {"name": "play_card"}},
                {"type": "end_turn", "raw": {"name": "end_turn"}},
            ],
            "raw_state": {
                "combat": {
                    "player": {"energy": energy, "block": block},
                    "hand": hand
                    if hand is not None
                    else [
                        {"index": 0, "card_id": "STRIKE", "name": "Strike", "damage": 6, "energy_cost": 1, "playable": True},
                        {"index": 1, "card_id": "DEFEND", "name": "Defend", "block": 5, "energy_cost": 1, "playable": True},
                    ],
                    "enemies": enemies if enemies is not None else [{"current_hp": 20, "intent_damage": 6}],
                }
            },
        },
    }
    if solver_plan is not None:
        ctx["solver_plan"] = solver_plan
        # ★▼ t45：准入门槛（`loop_runner.solver_play_gate`）新增两条硬条件 ——
        #   「搜索已算出结果（search_status ∈ MapStatus 值域）」与「这份计划基于**当前**局面」。
        #   本文件的计划是**当场按当前局面造的**，语义上等价于生产的 `refetched` 分支，
        #   所以把 loop_runner 在那一分支里记录的来源与取回指纹一并填上。
        #   不填的后果不是"测试挂了"，而是这些用例**测的是一条已经被闸门封死的路径**。
        ctx["plan_origin"] = "refetched"
        ctx["plan_fetch_signature"] = _combat_deadlock_signature(ctx["snapshot"])
    return ctx


def _plan(*steps) -> dict:
    # ★ t45：`search_status` 是准入门槛的判据之一（合法值域见
    #   loop_runner.SOLVER_SEARCH_STATUSES，依据 CombatSolverFacade.cs:409-412）。
    #   "complete" ⇔ SearchBoundaryReason.None（搜索自然穷尽）。
    return {
        "in_combat": True,
        "search_status": "complete",
        "state_fingerprint": "TESTFP0000000000",
        "line": [{"turn": 1, "steps": list(steps)}],
    }


def test_combat_obeys_solver_play_card() -> None:
    """★ 有求解器计划 ⇒ 照它打，且 `source` 标明来源是求解器。"""
    planner = STS2HeuristicPlanner()
    ctx = _combat_context(
        solver_plan=_plan(
            {"kind": "play_card", "card_index": 1, "card_id": "DEFEND", "card_name": "Defend", "target_index": None}
        )
    )

    operation = planner.plan(ctx)

    assert operation is not None, "有求解器计划却不出手"
    # 打的是求解器指定的那张（index 1 = Defend），而不是"启发式认为该打的"
    assert operation.kwargs["card_index"] == 1
    # ★ source 是留痕字段（reason 是 mod_combat_search，记的是动作来源分类）
    assert operation.source == "mod_solver", f"source 未标明求解器：{operation.source!r}"


def test_combat_obeys_solver_target() -> None:
    """★ 求解器指定的目标必须原样透传（不许自己挑目标）。"""
    planner = STS2HeuristicPlanner()
    ctx = _combat_context(
        solver_plan=_plan(
            {"kind": "play_card", "card_index": 0, "card_id": "STRIKE", "card_name": "Strike", "target_index": 1}
        ),
        enemies=[{"current_hp": 10, "intent_damage": 12}, {"current_hp": 6, "intent_damage": 4}],
        hand=[
            {
                "index": 0,
                "card_id": "STRIKE",
                "name": "Strike",
                "damage": 6,
                "energy_cost": 1,
                "playable": True,
                "requires_target": True,
                "valid_target_indices": [0, 1],
            }
        ],
    )

    operation = planner.plan(ctx)

    assert operation is not None
    # ★ 求解器说打 1 号（血量更低的那个）。启发式过去会因"高意图优先"选 0 号。
    assert operation.kwargs["target_index"] == 1, "没有原样照做求解器的目标"


def test_combat_obeys_solver_end_turn() -> None:
    """★ 求解器说收手 ⇒ 就收手（不许自作主张再打一张）。"""
    planner = STS2HeuristicPlanner()
    ctx = _combat_context(solver_plan=_plan({"kind": "end_turn", "card_id": ""}))

    operation = planner.plan(ctx)

    assert operation is not None
    assert operation.action_type == "end_turn"


def test_combat_without_solver_plan_emits_no_action() -> None:
    """★ **没有**求解器计划时不出手 —— 这是"纯求解器"的核心断言。

    为什么不能降级到启发式：玩家明确说"启发了会导致更多变量导致失败"。
    宁可本 tick 不出手（让上层重取计划），也不要打出启发式认为对的牌。
    """
    planner = STS2HeuristicPlanner()

    # 手上有可打的牌、能量足够 —— 诱使启发式出手的条件全都满足
    operation = planner.plan(_combat_context(solver_plan=None))

    assert operation is None, (
        f"无求解器计划却出手了（说明启发式仍在兜底）：{operation!r}"
    )


def test_combat_solver_says_not_in_combat_emits_no_action() -> None:
    """★ 求解器明说 `in_combat=False` ⇒ 同样不出手（等重算）。"""
    planner = STS2HeuristicPlanner()
    ctx = _combat_context(solver_plan={"in_combat": False, "line": [{"turn": 1, "steps": []}]})

    operation = planner.plan(ctx)

    assert operation is None, f"求解器说不在战斗却出手了：{operation!r}"


def test_planner_remove_skips_eternal_cards() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "card_selection_delet"},
            "summary_context": {
                "payload": {
                    "selection_cards": [
                        {"index": 0, "name": "Strike", "description": "Eternal. Deal 6 damage."},
                        {"index": 1, "name": "Strike", "description": "Deal 6 damage."},
                    ]
                }
            },
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "CARD_SELECTION_DELET",
                "available_actions": [
                    {"type": "select_deck_card", "raw": {"name": "select_deck_card"}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1


def test_planner_remove_prefers_curse_before_starter() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "card_selection_delet"},
            "summary_context": {
                "payload": {
                    "selection_cards": [
                        {"index": 0, "name": "Strike", "description": "Deal 6 damage."},
                        {"index": 1, "name": "Curse of Pain", "description": "Curse."},
                    ]
                }
            },
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "CARD_SELECTION_DELET",
                "available_actions": [
                    {"type": "select_deck_card", "raw": {"name": "select_deck_card"}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1












def test_planner_reward_uses_selection_cards_when_reward_cards_missing() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "card_selection"},
            "summary_context": {
                "payload": {
                    "selection_cards": [
                        {"index": 0, "name": "Meteor Strike", "description": "3 cost attack. Big damage."},
                        {"index": 1, "name": "Coolheaded", "description": "Gain 5 block. Draw 1 card. Channel 1 Frost."},
                    ],
                    "deck": {
                        "cards": [
                            {"name": "Zap"},
                            {"name": "Coolheaded"},
                            {"name": "Charge Battery"},
                        ]
                    },
                }
            },
            "strategy_context": {
                "preferences": {
                    "records": [
                        {
                            "value": {
                                "instruction_type": "deck_policy",
                                "prefer_tags": ["draw", "defense"],
                                "avoid_tags": ["expensive_attack"],
                                "archetype_bias": ["orb_focus"],
                            }
                        }
                    ]
                }
            },
            "snapshot": {
                "screen": "CARD_SELECTION",
                "available_actions": [
                    {"type": "choose_reward_card", "raw": {"name": "choose_reward_card", "requires_index": True}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1






def test_planner_reward_energy_refund_is_rewarded() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "card_selection"},
            "summary_context": {
                "payload": {
                    "selection_cards": [
                        {
                            "index": 0,
                            "name": "御血术",
                            "resolved_rules_text": "失去2点生命。 造成15点伤害。",
                            "dynamic_values": [
                                {"name": "Damage", "current_value": 15},
                                {"name": "HpLoss", "current_value": 2},
                            ],
                            "card_type": "Attack",
                        },
                        {
                            "index": 1,
                            "name": "战斗专注",
                            "resolved_rules_text": "获得1点能量。 抽1张牌。",
                            "dynamic_values": [
                                {"name": "Energy", "current_value": 1},
                                {"name": "Cards", "current_value": 1},
                            ],
                            "card_type": "Skill",
                        },
                    ],
                    "deck": {"cards": [{"name": "Strike"}, {"name": "Defend"}]},
                }
            },
            "strategy_context": {"preferences": {"records": []}},
            "snapshot": {
                "screen": "CARD_SELECTION",
                "available_actions": [
                    {"type": "choose_reward_card", "raw": {"name": "choose_reward_card", "requires_index": True}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1




def test_planner_event_respects_defensive_guidance() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "event"},
            "summary_context": {
                "payload": {
                    "current_hp": 20,
                    "max_hp": 80,
                    "gold": 90,
                    "event_options": [
                        {"index": 0, "label": "Lose 10 HP"},
                        {"index": 1, "label": "Gain Relic"},
                    ],
                },
                "decision_payload": {
                    "guidance": {
                        "pending": [{"content": "保血，别贪"}],
                        "generation": 1,
                    }
                },
            },
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "EVENT",
                "available_actions": [
                    {"type": "choose_event_option", "raw": {"name": "choose_event_option", "requires_index": True}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1


def test_planner_reward_respects_defensive_guidance() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "card_selection"},
            "summary_context": {
                "payload": {
                    "selection_cards": [
                        {
                            "index": 0,
                            "name": "御血术",
                            "resolved_rules_text": "失去2点生命。 造成15点伤害。",
                            "dynamic_values": [
                                {"name": "Damage", "current_value": 15},
                                {"name": "HpLoss", "current_value": 2},
                            ],
                            "card_type": "Attack",
                        },
                        {
                            "index": 1,
                            "name": "血墙",
                            "resolved_rules_text": "失去2点生命。 获得16点格挡。",
                            "dynamic_values": [
                                {"name": "Block", "current_value": 16},
                                {"name": "HpLoss", "current_value": 2},
                            ],
                            "card_type": "Skill",
                        },
                    ],
                    "deck": {"cards": [{"name": "Strike"}, {"name": "Defend"}]},
                },
                "decision_payload": {
                    "guidance": {
                        "pending": [{"content": "先防一下，别贪"}],
                        "generation": 1,
                    }
                },
            },
            "strategy_context": {"preferences": {"records": []}},
            "snapshot": {
                "screen": "CARD_SELECTION",
                "available_actions": [
                    {"type": "choose_reward_card", "raw": {"name": "choose_reward_card", "requires_index": True}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.kwargs["option_index"] == 1
