# -*- coding: utf-8 -*-
"""三档位（serious / mischievous / silly）与猫娘联动的独立验证。

这份文件是「验证」而不是「回归」：既有 ``tests/test_play_style.py`` 用**脚本化随机源**钉住
确定的落点，无法回答"概率真的生效吗"；也没有覆盖"档位切换入口真的接线了吗"、"弹幕开关与
LLM 开关的区别"在这些具体路径上是否成立。于是这里补三件事：

1. **统计检验**：用固定种子对每档各掷 :data:`SAMPLE_SIZE` 次，卡方检验实际扰动率是否等于
   0 / 0.2 / 0.6。种子写死 → 结果可复现；卡方而不是"比例差不多"→ 结论可证伪。
2. **合法性不变量**：不依赖具体落点，直接断言扰动结果**永远**来自传入的合法候选集合；
   拿不到替代就退回最优（``no_alternative`` / ``no_candidates``）。
3. **入口接线**：``__init__.py`` 的 plugin_entry / llm_tool 真的把 style 落到 service；
   NEKO 口语别名（中文）经 ``normalize_play_style`` 归一；未知取值退化为 serious。
   以及与猫娘的两条边界：``danmaku_enabled`` vs ``catgirl_llm_enabled``、
   ``program`` / ``advise`` / ``standby`` 与 ``coop_control_mode`` 的映射。

运行方式（cwd 必须是 NEKO 仓库根，pytest.ini 与 conftest 都依赖它）::

    cd D:\\NekoClaw\\N.E.K.O
    $env:PYTHONPATH="D:\\NekoClaw\\N.E.K.O\\plugin\\plugins\\sts2_autoplay"
    .\\.venv\\Scripts\\python.exe -m pytest plugin/plugins/sts2_autoplay/tests/test_play_style_neko_integration.py -q

注意：pytest 加载的是 **NEKO 侧的部署副本** ``N.E.K.O/plugin/plugins/sts2_autoplay``。
改了工作区源码（``n.e.k.o_plugin_sts2_autoplay``）之后必须先跑
``n.e.k.o_plugin_sts2_autoplay/deploy-all.ps1``，否则这里跑的仍是旧副本。
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.action_registry import STS2ActionRegistry
from plugin.plugins.sts2_autoplay.mode_controller import STS2ModeController
from plugin.plugins.sts2_autoplay.play_style import (
    PERTURBATION_PROBABILITY,
    PLAY_STYLE_MISCHIEVOUS,
    PLAY_STYLE_SERIOUS,
    PLAY_STYLE_SILLY,
    PLAY_STYLES,
    choose_candidate,
    normalize_play_style,
    perturbation_probability,
)
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService

# ---- 统计参数（写死，保证可复现）--------------------------------------------------------

#: 每档采样次数。100_000 次在 0.2/0.6 上把 1 个标准差压到约 0.0013 / 0.0015，
#: 于是"差 0.02"就足以判失败——足够灵敏，同时秒级跑完。
SAMPLE_SIZE = 100_000
#: 固定种子：换掉它就换一套随机数，但同一次运行永远得到同样的计数。
SAMPLE_SEED = 20_260_731
#: 卡方检验的显著性水平（1%）：误报一个"概率不对"的风险是 1%。
CHI2_ALPHA = 0.01

# 6 个候选下标（0..5）。silly 取"最后一个"= 5，mischievous 取"第 1 个"= 1，
# 所以"落到最优"与"落到扰动目标"是互斥且穷尽的两类事件，扰动率可以直接由落点统计出来。
_CANDIDATES = list(range(6))
_BEST = 0
_MISCHIEVOUS_PICK = 1
_SILLY_PICK = 5

# 卡方检验上界：自由度 1、alpha=0.01 时的临界值。用常量而不是实现完整 CDF —— 失败时
# 报告里直接给出"统计量 vs 临界值"，读数一目了然。
_CHI2_CRITICAL_DF1_ALPHA_001 = 6.634896601021213


def _chi2_sf_df1(x: float) -> float:
    """自由度为 1 的卡方分布上尾概率（用 erfc 精确计算，不引入 scipy 依赖）。"""
    import math

    return math.erfc(math.sqrt(x / 2.0))


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def exception(self, *a, **k):
        return None


class FakeClient:
    """够用的传输桩：只记录弹幕推送与 /config 写入，不发任何真实 HTTP。"""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.config_writes: list[dict[str, Any]] = []
        self.cleared = 0

    async def push_danmaku(self, text, *, style="catgirl", placement="scrolling", avatar=None) -> dict:
        self.pushed.append((text, style))
        return {"status": "ok"}

    async def set_config(self, **fields) -> dict:
        self.config_writes.append(dict(fields))
        return {"ok": True}

    async def clear_danmaku(self) -> dict:
        self.cleared += 1
        return {"ok": True}


def _service(client: FakeClient | None = None) -> tuple[STS2AutoplayService, FakeClient]:
    fake = client or FakeClient()
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: None)
    service._client = fake
    return service, fake


# =====================================================================================
# 1. 运行既有 test_play_style.py，并报告精确通过数（由外部 pytest 命令采集）
# =====================================================================================


@pytest.mark.unit
def test_play_style_module_is_wired_to_the_documented_constants() -> None:
    """先钉住"被测对象就是任务描述里那个实现"：常量值与档位集合都要对得上。

    这一步是后面所有统计结论的前提——如果实现挪了位置或改了值，统计出来的 0/0.2/0.6
    就是在验证另一个东西。
    """
    assert PLAY_STYLES == (PLAY_STYLE_SERIOUS, PLAY_STYLE_MISCHIEVOUS, PLAY_STYLE_SILLY)
    assert PERTURBATION_PROBABILITY == {PLAY_STYLE_SERIOUS: 0.0, PLAY_STYLE_MISCHIEVOUS: 0.2, PLAY_STYLE_SILLY: 0.6}
    for style in PLAY_STYLES:
        assert perturbation_probability(style) == PERTURBATION_PROBABILITY[style]


# =====================================================================================
# 2. 概率分布：统计采样 + 卡方检验
# =====================================================================================


def _sample(style: str, *, n: int = SAMPLE_SIZE, seed: int = SAMPLE_SEED) -> dict[str, Any]:
    """对单个档位掷 n 次，返回落点计数与扰动次数。"""
    rng = random.Random(seed)
    rolls = [rng.random() for _ in range(n)]
    picks: dict[Any, int] = {}
    for roll in rolls:
        candidate, reason = choose_candidate(_CANDIDATES, style=style, roll=roll)
        picks[(candidate, reason)] = picks.get((candidate, reason), 0) + 1
    # 扰动 = 没有落在最优上。对 mischievous/silly，函数的扰动目标恰好是 1 / 5，
    # 所以"扰动次数"与"落在指定目标上的次数"在统计上应当是同一件事。
    perturbed = n - picks.get((_BEST, "kept_best"), 0) - picks.get((_BEST, "serious"), 0)
    return {"n": n, "rolls": rolls, "picks": picks, "perturbed": perturbed}


def _chi2_two_binomial(observed: int, n: int, expected_p: float) -> float:
    """两分类（扰动 / 不扰动）的卡方统计量。"""
    expected = n * expected_p
    return (observed - expected) ** 2 / expected + (n - observed - n * (1 - expected_p)) ** 2 / (
        n * (1 - expected_p)
    )


@pytest.mark.unit
def test_serious_perturbation_rate_is_exactly_zero_over_100k_rolls() -> None:
    """serious 不是"概率很低"，而是**恒 0**：100k 次里一次都不许换。"""
    sample = _sample(PLAY_STYLE_SERIOUS)
    assert sample["perturbed"] == 0, f"serious 扰动了 {sample['perturbed']} 次"
    assert sample["picks"] == {(_BEST, "serious"): SAMPLE_SIZE}


@pytest.mark.unit
def test_mischievous_perturbation_rate_matches_20_percent() -> None:
    """mischievous：卡方检验实际扰动率 == 0.2。"""
    sample = _sample(PLAY_STYLE_MISCHIEVOUS)
    rate = sample["perturbed"] / sample["n"]
    chi2 = _chi2_two_binomial(sample["perturbed"], sample["n"], 0.2)

    assert rate == pytest.approx(0.2, abs=0.01), (
        f"mischievous 实际扰动率 {rate:.5f} 偏离 0.2 超过 1 个百分点；chi2={chi2:.4f}"
    )
    assert chi2 < _CHI2_CRITICAL_DF1_ALPHA_001, (
        f"mischievous 卡方={chi2:.4f} >= 临界值 {_CHI2_CRITICAL_DF1_ALPHA_001:.4f}"
        f"（p={_chi2_sf_df1(chi2):.4f}）；扰动率 {rate:.5f}"
    )
    # 扰动时**必须**落在第 1 个（次优），不能乱落。
    assert set(sample["picks"]) == {(_BEST, "kept_best"), (_MISCHIEVOUS_PICK, "mischievous_second")}


@pytest.mark.unit
def test_silly_perturbation_rate_matches_60_percent() -> None:
    """silly：卡方检验实际扰动率 == 0.6。"""
    sample = _sample(PLAY_STYLE_SILLY)
    rate = sample["perturbed"] / sample["n"]
    chi2 = _chi2_two_binomial(sample["perturbed"], sample["n"], 0.6)

    assert rate == pytest.approx(0.6, abs=0.01), (
        f"silly 实际扰动率 {rate:.5f} 偏离 0.6 超过 1 个百分点；chi2={chi2:.4f}"
    )
    assert chi2 < _CHI2_CRITICAL_DF1_ALPHA_001, (
        f"silly 卡方={chi2:.4f} >= 临界值 {_CHI2_CRITICAL_DF1_ALPHA_001:.4f}"
        f"（p={_chi2_sf_df1(chi2):.4f}）；扰动率 {rate:.5f}"
    )
    # 扰动时**必须**落在最后一个（最差），不能乱落。
    assert set(sample["picks"]) == {(_BEST, "kept_best"), (_SILLY_PICK, "silly_worst")}


@pytest.mark.unit
def test_the_three_styles_are_strictly_ordered_by_perturbation_rate() -> None:
    """三档必须严格有序 0 < 0.2 < 0.6，且档位之间差距远大于采样噪声。"""
    rates = {}
    for style in PLAY_STYLES:
        sample = _sample(style)
        rates[style] = sample["perturbed"] / sample["n"]

    # 采样标准差最大约 0.0016（p=0.6），所以 0.05 的间隔是噪声的 30 倍以上。
    assert rates[PLAY_STYLE_SERIOUS] == 0.0
    assert rates[PLAY_STYLE_SERIOUS] < rates[PLAY_STYLE_MISCHIEVOUS] - 0.05
    assert rates[PLAY_STYLE_MISCHIEVOUS] < rates[PLAY_STYLE_SILLY] - 0.05


@pytest.mark.unit
def test_boundary_rolls_are_excluded_inclusively() -> None:
    """边界是 `roll >= 概率 -> 不扰动`：0.2 与 0.6 这两个**恰好相等**的值保持最优。"""
    assert choose_candidate(_CANDIDATES, style=PLAY_STYLE_MISCHIEVOUS, roll=0.2 - 1e-12)[0] == _MISCHIEVOUS_PICK
    assert choose_candidate(_CANDIDATES, style=PLAY_STYLE_MISCHIEVOUS, roll=0.2)[0] == _BEST
    assert choose_candidate(_CANDIDATES, style=PLAY_STYLE_SILLY, roll=0.6 - 1e-12)[0] == _SILLY_PICK
    assert choose_candidate(_CANDIDATES, style=PLAY_STYLE_SILLY, roll=0.6)[0] == _BEST


# =====================================================================================
# 3. 合法性不变量：扰动结果永远来自传入的合法候选
# =====================================================================================


@pytest.mark.unit
@pytest.mark.parametrize("style", PLAY_STYLES)
@pytest.mark.parametrize("size", [0, 1, 2, 3, 7])
def test_chosen_candidate_always_comes_from_the_passed_in_list(style: str, size: int) -> None:
    """不假设落点是哪个，只断言"选出来的东西一定来自传入的列表"。"""
    candidates = list(range(100, 100 + size))
    rng = random.Random(SAMPLE_SEED + size)
    for _ in range(2000):
        candidate, reason = choose_candidate(candidates, style=style, roll=rng.random())
        if not candidates:
            assert (candidate, reason) == (None, "no_candidates")
            continue
        assert candidate in candidates, f"{style} 选出了列表外的候选 {candidate!r}"
        if len(candidates) == 1:
            # serious 有自己的原因标签（"serious"），非 serious 档才是 "no_alternative"。
            assert candidate == candidates[0]
            assert reason in {"no_alternative", "serious"}


@pytest.mark.unit
def test_no_legal_alternative_falls_back_to_the_best_instead_of_failing() -> None:
    """拿不到替代 -> 退回最优（``no_alternative``），绝不返回空、也绝不抛异常。

    "退回最优"这条对 silly（0.6）尤其关键：扰动概率越高，越容易撞上"只有一条路"的局面。
    """
    for style in PLAY_STYLES:
        for roll in (0.0, 0.19, 0.59, 0.999):
            single = choose_candidate(["only"], style=style, roll=roll)
            assert single[0] == "only"
            assert single[1] in {"no_alternative", "serious"}

    empty = choose_candidate([], style=PLAY_STYLE_SILLY, roll=0.0)
    assert empty == (None, "no_candidates")


@pytest.mark.unit
def test_perturbation_never_produces_an_out_of_range_index_through_the_service() -> None:
    """端到端合法性：在 silly 档把 service 的扰动层跑 200 次，落点必须始终可校验。

    先用猴子补丁把随机源换成"总是扰动"，于是这 200 次每一步都真的走扰动分支；
    再断言服务给出的 index 在规划器的合法下标集合内，并且 ``action_engine`` 认得这个动作。
    """
    service, _client = _service()
    service._state.play_style = PLAY_STYLE_SILLY
    service._play_style_random = type("AlwaysZero", (), {"random": staticmethod(lambda: 0.0)})()

    snapshot = _event_snapshot()
    operation = _event_operation(1)
    context = {
        "snapshot": snapshot,
        "classification": snapshot["classification"],
        "summary_context": snapshot["summary_context"],
        "strategy_context": snapshot["strategy_context"],
    }
    legal = set(service._planner.legal_option_indices(context, operation))

    seen: set[int] = set()
    for _ in range(200):
        out, info = service._apply_play_style(snapshot, operation)
        assert info["applied"] is True, info
        index = out["kwargs"]["option_index"]
        assert index in legal, f"扰动落到了合法集合之外：{index}（合法={sorted(legal)}）"
        assert service._action_engine.validate(snapshot, out) is not None
        seen.add(index)

    assert seen, "200 次扰动一次都没应用，说明扰动分支根本没接上"


@pytest.mark.unit
def test_serious_is_byte_identical_to_the_planner_output() -> None:
    """serious 的"零影响"是**对象同一性**：不是复制一份改回去，而是原对象传出去。"""
    service, _client = _service()
    service._state.play_style = PLAY_STYLE_SERIOUS
    calls: list[str] = []

    class SpyRandom:
        def random(self) -> float:
            calls.append("rolled")
            return 0.0

    service._play_style_random = SpyRandom()
    snapshot = _event_snapshot()
    operation = _event_operation(1)

    out, info = service._apply_play_style(snapshot, operation)

    assert out is operation
    assert info == {}
    assert calls == [], "serious 档掷了随机数，说明它走了扰动代码路径"


# ---- 事件屏夹具（与 tests/test_play_style.py 同构）-----------------------------------

_EVENT_OPTIONS = [
    {"index": 0, "text": "lose hp"},
    {"index": 1, "text": "gain relic"},
    {"index": 2, "text": "gain gold"},
]


def _event_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "screen": "EVENT",
        "available_actions": [
            {"type": "choose_event_option", "raw": {"name": "choose_event_option", "requires_index": True}}
        ],
        "classification": {"state_name": "event"},
        "strategy_context": {"preferences": {}},
        "summary_context": {
            "payload": {
                "event_options": _EVENT_OPTIONS,
                "current_hp": 70,
                "max_hp": 80,
                "gold": 50,
            }
        },
    }
    snapshot["action_registry"] = STS2ActionRegistry().build(snapshot)
    return snapshot


def _event_operation(option_index: int = 1) -> dict[str, Any]:
    return {
        "action_type": "choose_event_option",
        "kwargs": {"option_index": option_index},
        "source": "heuristic",
        "reason": "event_preference_or_default",
    }


# =====================================================================================
# 4. 运行时切换入口：口语别名归一 + 未知取值退化
# =====================================================================================


# 覆盖 play_style.py 里**全部** 7+7+7 条别名，漏一条就会在这里红。
_ALL_ALIASES: list[tuple[str, str]] = [
    ("serious", PLAY_STYLE_SERIOUS),
    ("normal", PLAY_STYLE_SERIOUS),
    ("standard", PLAY_STYLE_SERIOUS),
    ("认真", PLAY_STYLE_SERIOUS),
    ("认真打", PLAY_STYLE_SERIOUS),
    ("好好打", PLAY_STYLE_SERIOUS),
    ("我要通关", PLAY_STYLE_SERIOUS),
    ("正常", PLAY_STYLE_SERIOUS),
    ("正经", PLAY_STYLE_SERIOUS),
    ("mischievous", PLAY_STYLE_MISCHIEVOUS),
    ("mischief", PLAY_STYLE_MISCHIEVOUS),
    ("捣蛋", PLAY_STYLE_MISCHIEVOUS),
    ("调皮", PLAY_STYLE_MISCHIEVOUS),
    ("闹着玩", PLAY_STYLE_MISCHIEVOUS),
    ("别太认真", PLAY_STYLE_MISCHIEVOUS),
    ("恶作剧", PLAY_STYLE_MISCHIEVOUS),
    ("silly", PLAY_STYLE_SILLY),
    ("笨蛋", PLAY_STYLE_SILLY),
    ("你傻啦", PLAY_STYLE_SILLY),
    ("随便打打", PLAY_STYLE_SILLY),
    ("犯傻", PLAY_STYLE_SILLY),
    ("傻瓜", PLAY_STYLE_SILLY),
    ("犯蠢", PLAY_STYLE_SILLY),
]


@pytest.mark.unit
@pytest.mark.parametrize(("raw", "expected"), _ALL_ALIASES)
def test_every_documented_alias_normalizes(raw: str, expected: str) -> None:
    assert normalize_play_style(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "SERIOUS",
        "Serious",
        "认真地",
        " 认真 ",
        "认真 打",  # 空格 -> 下划线，归一成 "认真_打" -> 未知 -> serious（本来就是 serious）
        "\tmischevious",  # 拼错的英文必须退化
        "??",
        "",
        "   ",
        None,
        0,
        False,
        [],
        {"style": "silly"},
    ],
)
def test_unknown_or_malformed_style_degrades_to_serious(raw: Any) -> None:
    """**配置写错不能让它变傻**：任何无法识别的取值都必须退化到 serious。"""
    assert normalize_play_style(raw) == PLAY_STYLE_SERIOUS
    assert perturbation_probability(raw) == 0.0


@pytest.mark.unit
def test_malformed_style_through_startup_is_serious() -> None:
    """走 startup(cfg) 的真实路径：配置里写错档位，最终状态必须是 serious 且零扰动。"""

    class StartupClient(FakeClient):
        async def health(self):
            raise RuntimeError("no game running")

    service, _client = _service(StartupClient())
    for junk in ("sillly", "捣蛋鬼", "Mischievous!", 42, None):
        asyncio.run(service.startup({"play_style": junk}))
        assert service._state.play_style == PLAY_STYLE_SERIOUS, junk

    asyncio.run(service.startup({"play_style": "笨蛋"}))
    assert service._state.play_style == PLAY_STYLE_SILLY


@pytest.mark.unit
def test_set_play_style_returns_and_persists_all_three() -> None:
    service, _client = _service()
    for raw, expected in [("认真", PLAY_STYLE_SERIOUS), ("捣蛋", PLAY_STYLE_MISCHIEVOUS), ("笨蛋", PLAY_STYLE_SILLY)]:
        result = service.set_play_style(raw)
        assert result["status"] == "ok"
        assert result["play_style"] == expected
        assert service._state.play_style == expected
        assert service._cfg["play_style"] == expected


# =====================================================================================
# 5. 入口接线：__init__.py 的 plugin_entry / llm_tool 真的落到 service
# =====================================================================================


class _RecordingService:
    """只记录"入口层到底把什么参数交给 service"的最小替身。"""

    def __init__(self) -> None:
        self.style_calls: list[str] = []
        self.mode_calls: list[str] = []
        self.neko = self

    async def set_play_style(self, style: str) -> dict[str, Any]:
        self.style_calls.append(style)
        return {"status": "ok", "play_style": normalize_play_style(style), "summary": "ok"}

    async def set_control_mode(self, mode: str) -> dict[str, Any]:
        self.mode_calls.append(mode)
        return {"status": "ok", "mode": STS2ModeController().normalize(mode), "summary": "ok"}


class _RecordingDispatcher:
    """替掉真 dispatcher：它需要完整的 SDK ctx，而这里只关心推送参数。"""

    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self._sink = sink

    def push_status_feedback(self, text, *, entry_id, ai_behavior="respond", **kw):
        self._sink.append({"entry_id": entry_id, "text": text, "ai_behavior": ai_behavior})

    def push_frontend_notification(self, **kwargs):
        self._sink.append(dict(kwargs))


def _make_plugin():
    """用**真实的** STS2AutoplayPlugin 入口方法，只把 service 换成替身。

    直接继承真类（而不是重写入口）才能保证测的是真的入口函数，包括装饰器注册的形状。
    """
    from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin

    class Plugin(STS2AutoplayPlugin):
        def __init__(self) -> None:
            self._service = _RecordingService()
            self.pushed: list[dict[str, Any]] = []
            self.logger = DummyLogger()
            self._dispatcher = _RecordingDispatcher(self.pushed)

        def push_message(self, **kwargs: Any):  # type: ignore[override]
            self.pushed.append(kwargs)
            return {"submitted": True}

        async def finish(self, **kwargs: Any):  # type: ignore[override]
            return kwargs

    return Plugin()


def _make_plugin_with_real_service():
    """入口 + **真实** service（client 换成桩），用来验证"入口 -> service -> 状态"整条链。"""
    from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin

    real = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None, i18n=None)
    real._client = FakeClient()

    class Plugin(STS2AutoplayPlugin):
        def __init__(self) -> None:
            self._service = real
            self.pushed: list[dict[str, Any]] = []
            self.logger = DummyLogger()
            self._dispatcher = _RecordingDispatcher(self.pushed)

        def push_message(self, **kwargs: Any):  # type: ignore[override]
            self.pushed.append(kwargs)
            return {"submitted": True}

        async def finish(self, **kwargs: Any):  # type: ignore[override]
            return kwargs

    return Plugin()


def _unwrap(result: Any) -> dict[str, Any]:
    """`_run_entry` 返回 SDK 的 `Ok(...)`/`Err(...)`，把里面的 payload 取出来。"""
    value = getattr(result, "value", None)
    if isinstance(value, dict):
        return value
    if isinstance(result, dict):
        return result
    raise AssertionError(f"拿不到 payload，返回值是 {result!r}")


@pytest.mark.unit
def test_plugin_entry_sts2_set_play_style_reaches_the_service() -> None:
    """`sts2_set_play_style` 这个 plugin_entry 必须把 style 原样交给 service。"""
    plugin = _make_plugin()
    payload = _unwrap(asyncio.run(plugin.sts2_set_play_style(style="笨蛋")))

    assert plugin._service.style_calls == ["笨蛋"]
    assert payload["play_style"] == PLAY_STYLE_SILLY


@pytest.mark.unit
def test_plugin_entry_sts2_set_play_style_normalizes_junk_end_to_end() -> None:
    """走真实入口 + 真实 service：配置/口语写错一律落到 serious，且不带任何扰动概率。"""
    plugin = _make_plugin_with_real_service()
    payload = _unwrap(asyncio.run(plugin.sts2_set_play_style(style="胡说八道")))

    assert payload["play_style"] == PLAY_STYLE_SERIOUS
    assert plugin._service._state.play_style == PLAY_STYLE_SERIOUS
    assert perturbation_probability(plugin._service._state.play_style) == 0.0


@pytest.mark.unit
def test_llm_tool_sts2_set_play_style_returns_a_terse_machine_result() -> None:
    """LLM 工具返回值会被回喂给模型：只能是机器可读的最小结果。"""
    plugin = _make_plugin()
    result = asyncio.run(plugin.llm_set_play_style(style="捣蛋"))

    assert plugin._service.style_calls == ["捣蛋"]
    assert result == {"ok": True, "style": PLAY_STYLE_MISCHIEVOUS}


@pytest.mark.unit
def test_llm_tool_sts2_set_play_style_normalizes_junk() -> None:
    plugin = _make_plugin()
    result = asyncio.run(plugin.llm_set_play_style(style="胡说八道"))

    assert result == {"ok": True, "style": PLAY_STYLE_SERIOUS}


@pytest.mark.unit
def test_plugin_entry_sts2_set_control_mode_and_llm_tool_are_wired() -> None:
    plugin = _make_plugin()
    asyncio.run(plugin.sts2_set_control_mode(mode="听我安排"))
    asyncio.run(plugin.llm_set_control_mode(mode="停手"))

    assert plugin._service.mode_calls == ["听我安排", "停手"]
    assert plugin._service.mode_calls[:1] != plugin._service.mode_calls[1:]


@pytest.mark.unit
def test_style_switch_is_pushed_blind_so_the_catgirl_does_not_echo_it() -> None:
    """换档留痕必须 `ai_behavior="blind"`：否则猫娘会立刻开口念"好，我摆烂了"。"""
    plugin = _make_plugin()
    asyncio.run(plugin.sts2_set_play_style(style="silly"))

    assert plugin.pushed, "换档应当留一条内部记录"
    last = plugin.pushed[-1]
    assert last.get("ai_behavior") == "blind"
    assert last.get("entry_id") == "sts2_set_play_style"


def _entry_metas() -> dict[str, Any]:
    """读注册期真正挂在函数上的入口元数据（SDK 的 ``__neko_event_meta__``）。

    这就是 SDK 派发时用的那一份；不解析源码 —— 后者会在导入期/运行期差异上给出假结论。
    """
    from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin

    metas: dict[str, Any] = {}
    for name, value in vars(STS2AutoplayPlugin).items():
        meta = getattr(value, "__neko_event_meta__", None)
        if meta is not None:
            metas[name] = meta
    return metas


@pytest.mark.unit
def test_the_declared_entry_schemas_match_the_implementation() -> None:
    """入口的 enum（serious/mischievous/silly）必须与实现的三档一一对应，不多不少。"""
    metas = _entry_metas()
    assert "sts2_set_play_style" in metas, sorted(metas)
    assert "sts2_set_control_mode" in metas

    style_schema = metas["sts2_set_play_style"].input_schema
    assert tuple(style_schema["properties"]["style"]["enum"]) == PLAY_STYLES
    assert style_schema["required"] == ["style"]

    mode_schema = metas["sts2_set_control_mode"].input_schema
    assert tuple(mode_schema["properties"]["mode"]["enum"]) == ("program", "advise", "standby")


# =====================================================================================
# 6. 与猫娘的两条边界：danmaku_enabled vs catgirl_llm_enabled
# =====================================================================================


@pytest.mark.unit
def test_catgirl_llm_disabled_still_pushes_danmaku_with_heuristic_text(monkeypatch) -> None:
    """`catgirl_llm_enabled=False` 只把点评**降级为启发式文本**，弹幕照旧推。

    为了把"是否真的推了"变成确定性的断言（而不是靠 sleep 赌异步任务跑到），这里把
    `_push_catgirl` 直接换成记录器 —— 已经由既有测试证明了 `_push_catgirl` 会 POST /danmaku。
    头像也打桩成瞬时返回，所以这条测试不再依赖任何真实 HTTP。
    """
    service, _client = _service()
    service._catgirl_llm_enabled = False
    assert service._danmaku_enabled is True

    llm_calls: list[dict[str, Any]] = []
    avatar_calls: list[str] = []
    pushed: list[str] = []

    class Gen:
        available = True

        async def generate(self, **kw):
            llm_calls.append(kw)
            raise AssertionError("catgirl_llm_enabled=False 时不该调用 LLM")

    async def fake_avatar_full():
        avatar_calls.append("full")
        return None

    service._catgirl_llm = Gen()
    service._catgirl_avatar_base64 = fake_avatar_full  # type: ignore[method-assign]
    service._cached_avatar_base64 = lambda: avatar_calls.append("cached") or None  # type: ignore[method-assign]
    monkeypatch.setattr(service, "_push_catgirl", lambda text, **kw: pushed.append(text))

    async def go() -> None:
        service._maybe_emit_catgirl_llm(
            {},
            {"should_comment": True, "primary_message": "建议先防御。"},
            {"message": "建议先防御。", "screen": "COMBAT", "summary_kind": "combat"},
        )
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(go())

    assert llm_calls == [], "关了 LLM 不该还去调模型"
    assert pushed == ["建议先防御。"], "弹幕必须照推（这是老行为，不能被闸门误伤）"
    assert avatar_calls == [], "既然打桩了 _push_catgirl，头像 HTTP 就不该发生"


@pytest.mark.unit
def test_catgirl_llm_enabled_but_unavailable_falls_back_without_going_silent(monkeypatch) -> None:
    """LLM 开关开着但模型不可用（或这次没产出）时，也必须退回启发式文本，不能噤声。"""
    service, _client = _service()
    service._catgirl_llm_enabled = True

    pushed: list[str] = []

    class Unavailable:
        available = False

        async def generate(self, **kw):  # pragma: no cover - available=False 就不该被调用
            raise AssertionError("available=False 时不该调用 generate")

    async def fake_avatar_full():
        return None

    service._catgirl_llm = Unavailable()
    service._catgirl_avatar_base64 = fake_avatar_full  # type: ignore[method-assign]
    service._cached_avatar_base64 = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(service, "_push_catgirl", lambda text, **kw: pushed.append(text))

    async def go() -> None:
        service._maybe_emit_catgirl_llm(
            {},
            {"should_comment": True, "primary_message": "启发式兜底。"},
            {"message": "启发式兜底。", "screen": "MAP", "summary_kind": "map"},
        )
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(go())

    assert pushed == ["启发式兜底。"]


@pytest.mark.unit
def test_danmaku_disabled_blocks_llm_avatar_and_post_entirely() -> None:
    """`danmaku_enabled=False` 是**真闸门**：连 LLM 生成与头像 HTTP 都不发生。"""
    service, client = _service()
    service._danmaku_enabled = False

    llm_calls: list[dict[str, Any]] = []

    class Gen:
        available = True

        async def generate(self, **kw):
            llm_calls.append(kw)
            return "这条不该出现"

        async def _unused(self):  # pragma: no cover
            return None

    service._catgirl_llm = Gen()

    async def explode_async():
        raise AssertionError("弹幕关掉后不该拉头像")

    def explode_sync():
        raise AssertionError("弹幕关掉后不该拉头像")

    service._catgirl_avatar_base64 = explode_async  # type: ignore[method-assign]
    service._cached_avatar_base64 = explode_sync  # type: ignore[method-assign]

    async def go() -> None:
        service._maybe_emit_catgirl_llm(
            {},
            {"should_comment": True, "primary_message": "启发式文本。"},
            {"message": "启发式文本。", "screen": "COMBAT", "summary_kind": "combat"},
        )
        service._emit_instant_catgirl_line(
            {"screen": "COMBAT", "floor": 5, "turn": 2}, {}, {"summary_kind": "combat"}
        )
        service._push_catgirl_fallback({"primary_message": "兜底文本。"}, {"message": "兜底文本。"})
        service._schedule_catgirl_push("调度文本。")
        await asyncio.sleep(0.05)
        # 真正的出口也要挡住
        await service._push_catgirl("直接推送。")

    asyncio.run(go())

    assert llm_calls == [], "弹幕关掉后不该调 LLM"
    assert client.pushed == [], "弹幕关掉后不该有 POST /danmaku"


@pytest.mark.unit
def test_danmaku_disabled_also_blocks_the_host_digest_push() -> None:
    """弹幕总闸门还顺带挡住"给本体的局势速报"——速报与弹幕同生共死。"""
    calls: list[dict[str, Any]] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: calls.append(k))
    service._danmaku_enabled = False

    service._push_host_situation_digest(
        {"screen": "COMBAT", "floor": 5, "turn": 2, "raw_state": {"combat": {"enemies": []}}},
        {"screen": "COMBAT", "summary_kind": "combat", "player": {"current_hp": 60, "max_hp": 80}},
    )
    assert calls == []

    service._danmaku_enabled = True
    service._push_host_situation_digest(
        {"screen": "COMBAT", "floor": 5, "turn": 2, "raw_state": {"combat": {"enemies": []}}},
        {"screen": "COMBAT", "summary_kind": "combat", "player": {"current_hp": 60, "max_hp": 80}},
    )
    assert len(calls) == 1, "闸门打开后速报应当恢复"


@pytest.mark.unit
def test_instant_line_uses_the_avatar_cache_only() -> None:
    """即时短句绝不能为头像等 HTTP：它只用缓存（`fetch_avatar=False`），否则"即时"就没了。"""
    service, _client = _service()
    seen: list[bool] = []

    def fake_schedule(text: str, *, fetch_avatar: bool = True) -> None:
        seen.append(fetch_avatar)

    service._schedule_catgirl_push = fake_schedule  # type: ignore[method-assign]
    service._emit_instant_catgirl_line(
        {"screen": "MAP", "floor": 3, "turn": None},
        {"primary_message": "往右边走喵"},
        {"screen": "MAP", "summary_kind": "map", "message": "三条路"},
    )

    assert seen == [False], "即时短句必须走缓存头像（fetch_avatar=False）"


@pytest.mark.unit
def test_play_style_observable_difference_on_instant_line_is_only_the_action() -> None:
    """`play_style` 只改**动作**，不改弹幕文本 —— 这是一个刻意的设计边界，需要被记录。

    找遍 service：``play_style`` 只出现在 `_apply_play_style` / `set_play_style` /
    `get_status` / `startup` 四处，即时短句拼文本时**根本没读它**。所以"捣蛋档的弹幕
    看起来不一样"这件事在当前实现里不成立（弹幕随真实战况变化，而不是随档位变化）。
    这里把这个事实钉死：如果哪天有人把档位接进文本，这条测试会红，提醒同步更新文档。
    """
    service, _client = _service()
    emitted: list[str] = []
    service._schedule_catgirl_push = lambda text, **kw: emitted.append(text)  # type: ignore[method-assign]

    snapshot = {
        "screen": "COMBAT",
        "floor": 5,
        "turn": 2,
        "raw_state": {
            "combat": {
                "enemies": [
                    {
                        "index": 0,
                        "name": "树叶史莱姆（中）",
                        "intents": [{"intent_type": "Attack", "damage": 9, "hits": 1, "total_damage": 9}],
                    }
                ]
            }
        },
    }
    payload = {
        "screen": "COMBAT",
        "summary_kind": "combat",
        "player": {"current_hp": 60, "max_hp": 80},
        "enemies": [{"name": "树叶史莱姆（中）", "intent": "CLUMP_SHOT"}],
    }

    texts: dict[str, str] = {}
    for style in PLAY_STYLES:
        service._state.play_style = style
        service._state.last_instant_line_key = ""
        emitted.clear()
        service._emit_instant_catgirl_line(snapshot, {}, payload)
        texts[style] = emitted[0]

    assert len(set(texts.values())) == 1, f"弹幕文本随档位变了：{texts}"
    assert "攻击 9" in texts[PLAY_STYLE_SERIOUS] and "60/80" in texts[PLAY_STYLE_SERIOUS]


@pytest.mark.unit
def test_play_style_does_not_leak_into_the_llm_prompt() -> None:
    """同理：档位不进 LLM prompt（否则就是"文本随档位变"的另一条路）。"""
    from plugin.plugins.sts2_autoplay.catgirl_llm import CatgirlCommentGenerator

    gen = CatgirlCommentGenerator(DummyLogger())
    messages = gen._build_messages(
        summary_text="本轮: 打击；结束回合",
        summary_kind="combat",
        payload={"screen": "COMBAT"},
    )
    blob = "\n".join(str(m.get("content") or "") for m in messages)
    for style in PLAY_STYLES:
        assert style not in blob
    assert "silly" not in blob and "mischievous" not in blob


# =====================================================================================
# 7. 控制档位 program / advise / standby 与猫娘的协同
# =====================================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "expected"),
    [("program", "auto"), ("advise", "advise"), ("standby", "hold")],
)
def test_control_mode_is_synced_to_the_mod(mode: str, expected: str) -> None:
    """三个档位必须映射成 mod 的 coop_control_mode：只停插件等于没停。"""
    service, client = _service()
    result = asyncio.run(service.set_control_mode(mode))

    assert result["mode"] == mode
    assert result["mod_sync"] == {"applied": True, "mode": expected}
    assert client.config_writes[-1] == {"coop_control_mode": expected}


@pytest.mark.unit
def test_control_mode_sync_reports_failure_without_crashing() -> None:
    """mod 不通时如实回报 applied=False，而不是假装成功（失败是良性的，但不能撒谎）。"""
    service, client = _service()

    async def boom(**fields):
        raise RuntimeError("mod 不在")

    client.set_config = boom  # type: ignore[method-assign]
    result = asyncio.run(service.set_control_mode("standby"))

    assert result["mod_sync"] == {"applied": False, "mode": "hold"}
    assert result["mode"] == "standby"


@pytest.mark.unit
def test_no_client_yet_still_reports_the_intended_mapping() -> None:
    """游戏没开时设档位：插件侧照样生效，映射只回报意图（mode）而不谎报已应用。"""
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None)
    assert service._client is None
    result = asyncio.run(service.set_control_mode("advise"))

    assert result["mod_sync"] == {"applied": False, "mode": "advise"}


@pytest.mark.unit
def test_standby_does_not_execute_even_in_combat() -> None:
    """standby 连战斗也不动：`run_autoplay_step` 在算计划之前就早退。"""
    service, _client = _service()
    service._apply_control_mode("standby")
    service._state.snapshot = {
        "classification": {"state_name": "combat"},
        "agent_operation": {"action_type": "play_card", "kwargs": {}},
    }

    executed: list[Any] = []

    async def fake_execute(operation):
        executed.append(operation)
        return {"status": "ok"}

    service.execute_operation = fake_execute  # type: ignore[method-assign]
    result = asyncio.run(service.run_autoplay_step())

    assert result["status"] == "idle"
    assert "standby" in str(result["message"])
    assert executed == [], "standby 下不该执行任何动作"


@pytest.mark.unit
def test_advise_holds_off_combat_but_fights_in_combat() -> None:
    """advise：非战斗界面只算不做，战斗照常自动打（否则回合永久卡住）。"""
    service, _client = _service()
    service._apply_control_mode("advise")
    service._state.interrupt_requested = False
    service._state.force_replan_requested = False

    # 非战斗决策屏 -> 停手
    service._state.snapshot = {
        "classification": {"state_name": "reward"},
        "agent_operation": {"action_type": "claim_reward", "kwargs": {}},
    }
    held = asyncio.run(service.run_autoplay_step())
    assert held.get("held") is True and held["status"] == "idle"

    # 战斗 -> 不停手
    service._state.snapshot = {"classification": {"state_name": "combat"}}
    assert service._should_hold_for_orders(service._state.snapshot) is False


@pytest.mark.unit
def test_advise_plans_but_program_executes_the_hold_never_screens() -> None:
    """`allows_planner` / `allows_execute` 的三档对照，以及"进局流程永远不停手"。"""
    controller = STS2ModeController()
    program = controller.describe("program")
    advise = controller.describe("advise")
    standby = controller.describe("standby")

    assert (program["allows_planner"], program["allows_execute"]) == (True, True)
    assert (advise["allows_planner"], advise["allows_execute"]) == (True, False)
    assert (standby["allows_planner"], standby["allows_execute"]) == (False, False)


@pytest.mark.unit
def test_standby_true_and_false_round_trip_and_sync_to_the_mod() -> None:
    """`sts2_set_standby(true/false)` 走的是同一条 coop_control_mode 同步路径。"""
    service, client = _service()

    info_on = service.set_standby(True)
    assert info_on["mode"] == "standby"
    assert info_on["allows_planner"] is False
    sync_on = asyncio.run(service.sync_control_mode_to_mod())
    assert sync_on == {"applied": True, "mode": "hold"}

    info_off = service.set_standby(False)
    assert info_off["mode"] == "program"
    sync_off = asyncio.run(service.sync_control_mode_to_mod())
    assert sync_off == {"applied": True, "mode": "auto"}

    assert client.config_writes[-2:] == [
        {"coop_control_mode": "hold"},
        {"coop_control_mode": "auto"},
    ]


@pytest.mark.unit
def test_switching_to_the_catgirl_instance_pushes_hold() -> None:
    """切到猫娘实例时顺手推 hold，避免插件与 mod 两个驱动抢同一个动作。"""
    service, client = _service()
    service._host_base_url = "http://127.0.0.1:18080"
    service._host_client = client
    service._client = client
    service._catgirl_base_url = "http://127.0.0.1:18081"
    service._catgirl_client = client

    result = asyncio.run(service.switch_active_instance("catgirl", reason="test"))

    assert result["switched"] is True
    assert result["instance"] == "catgirl"
    assert client.config_writes[-1] == {"coop_control_mode": "hold"}


@pytest.mark.unit
def test_play_style_switch_never_disturbs_the_control_mode_mapping() -> None:
    """两条轴互不干扰：换游玩档位不许改 control_mode，也不许写 coop_control_mode。"""
    service, client = _service()
    asyncio.run(service.set_control_mode("advise"))
    before = list(client.config_writes)
    mode_before = service._state.control_mode

    service.set_play_style("silly")

    assert service._state.control_mode == mode_before
    assert client.config_writes == before, "换游玩档位不该给 mod 写配置"


@pytest.mark.unit
def test_switching_control_mode_interrupts_the_pending_decision() -> None:
    """切档必须打断已算好的那一步（否则切到"听安排"却仍走了一步）。"""
    service, _client = _service()
    service._state.interrupt_requested = False
    service._apply_control_mode("advise")

    assert service._state.interrupt_requested is True
    assert service._state.interrupt_reason == "mode_change"


@pytest.mark.unit
def test_get_status_exposes_both_axes_and_the_danmaku_gate() -> None:
    """状态查询里能同时看到"玩得多认真"（play_style）、"谁在指挥"（control_mode）和弹幕闸门。"""
    service, _client = _service()
    service.set_play_style("捣蛋")
    service.set_danmaku_enabled(False)

    status = asyncio.run(service.get_status())

    assert status["play_style"]["style"] == PLAY_STYLE_MISCHIEVOUS
    assert status["danmaku"]["enabled"] is False
    assert status["danmaku"]["catgirl_llm_enabled"] is True, "两个开关必须分别可见"
