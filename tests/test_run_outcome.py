# -*- coding: utf-8 -*-
"""局末胜负判定：通关不能被记成 defeat。

缺陷（实机，2026-11-24）：铁甲战士打通第三幕后，记忆库里这一局是 `outcome=defeat`。
mod 的结算屏名恒为 GAME_OVER，旧实现只在屏名等于 victory 时才记通关。
"""
from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


@pytest.mark.unit
@pytest.mark.parametrize(
    "screen, raw_state, expected",
    [
        ("game_over", {"game_over": {"is_victory": True}}, "victory"),
        ("game_over", {"game_over": {"is_victory": False}}, "defeat"),
        ("game_over", {}, "defeat"),
        ("game_over", {"game_over": None}, "defeat"),
        ("victory", {}, "victory"),
        ("victory", {"game_over": {"is_victory": False}}, "defeat"),
    ],
)
def test_run_outcome_reads_the_mods_verdict(screen: str, raw_state: dict, expected: str) -> None:
    assert STS2AutoplayService._run_outcome(screen, raw_state) == expected
