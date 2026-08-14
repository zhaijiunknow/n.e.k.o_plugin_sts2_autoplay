"""发射机制对齐 mod 的测试：强度分级 / 分批抽选 / 顶部概率 / 延迟 / 密度。"""

from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.danmu_spire import burst_profile, create_delays, pick_rule_burst, rule_intensity
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


@pytest.mark.unit
def test_rule_intensity_mapping() -> None:
    assert rule_intensity("NakedHit") == "light"
    assert rule_intensity("StrongMonster") == "medium"
    assert rule_intensity("PlayerDeath") == "strong"
    assert rule_intensity("Unknown") == "medium"  # 兜底


@pytest.mark.unit
def test_burst_profile_count_by_intensity() -> None:
    # light 4-6 / medium 8-12 / strong 15-22（密度 100%）
    for _ in range(30):
        assert 4 <= burst_profile("NakedHit", 100)[0] <= 6
        assert 8 <= burst_profile("StrongMonster", 100)[0] <= 12
        assert 15 <= burst_profile("PlayerDeath", 100)[0] <= 22


@pytest.mark.unit
def test_burst_profile_density_scales() -> None:
    # 50% → 减半；200% → 加倍
    for _ in range(30):
        assert 4 <= burst_profile("StrongMonster", 50)[0] <= 6
        assert 16 <= burst_profile("StrongMonster", 200)[0] <= 24
    # 越界 clamp
    assert 4 <= burst_profile("StrongMonster", 10)[0] <= 6  # <50 → 50
    assert burst_profile("StrongMonster", 999)[0] >= 1


@pytest.mark.unit
def test_burst_profile_top_probability() -> None:
    assert burst_profile("NakedHit", 100)[1] == 0.0    # light
    assert burst_profile("StrongMonster", 100)[1] == 0.15  # medium
    assert burst_profile("PlayerDeath", 100)[1] == 0.3  # strong


@pytest.mark.unit
def test_pick_rule_burst_all_narration() -> None:
    burst = pick_rule_burst("StrongMonster", {}, count=6)
    assert len(burst) >= 1
    styles = {b["style"] for b in burst}
    assert styles == {"narration"}  # 规则词条统一旁白（catgirl 轨道只放 LLM 弹幕）
    texts = [b["text"] for b in burst]
    assert len(texts) == len(set(texts))  # 组内不重复


@pytest.mark.unit
def test_pick_rule_burst_respects_count() -> None:
    burst = pick_rule_burst("NakedHit", {}, count=3)
    assert len(burst) <= 3
    assert all(b["style"] in ("catgirl", "narration") for b in burst)


@pytest.mark.unit
def test_pick_rule_burst_variant() -> None:
    # CollectiblePair completed 变体词条
    burst = pick_rule_burst("CollectiblePair", {"item": "冰淇淋"}, variant="completed", count=2)
    assert burst  # completed 变体词条可解析
    assert all("{" not in b["text"] and "}" not in b["text"] for b in burst)


@pytest.mark.unit
def test_create_delays_length_and_range() -> None:
    for n in (1, 2, 5, 12):
        delays = create_delays(n)
        assert len(delays) == n
        assert all(0 <= d <= 6 for d in delays)


@pytest.mark.unit
def test_danmu_placement_by_probability() -> None:
    # 角色弹幕永不置顶（对齐 mod）
    assert STS2AutoplayService._danmu_placement("catgirl", 0.3) == "scrolling"
    assert STS2AutoplayService._danmu_placement("catgirl", 1.0) == "scrolling"
    # narration 按概率置顶
    assert STS2AutoplayService._danmu_placement("narration", 0.0) == "scrolling"
    assert STS2AutoplayService._danmu_placement("narration", 1.0) == "top"
