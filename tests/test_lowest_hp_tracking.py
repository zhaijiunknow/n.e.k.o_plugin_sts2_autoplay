# -*- coding: utf-8 -*-
r"""t34 / D21：`lowest_hp` 跨 tick 累积与「损血可读性」的回归门。

## 这个文件为什么存在

`_close_route_room()` 用 `hp_lost = max(0, hp_start - lowest_hp)` 记录每个房间掉了多少血，
这个值一路喂给 `death_attribution` 的死因判据。**它的定义是契约**（t30 契约 §1），不许改。

## ★ 本轮实测到的两件事（与队长的初始假设**不同**，必须分开报）

1. 【假设被证伪】队长推测根因是 "service.py 写 current 副本但未回写 previous ⇒ 跨 tick 不累积"。
   实测 **:2658 `self._state.route_last = current` 确实回写了**，且：
   - 全库 2309 条 route 里 **2044 条** 的 `lowest_hp` 真正低于两端 ⇒ 累积是 work 的
   - 322 条 COMBAT 的 `lowest_hp==0` **全部落在 28 个「整局全 0」的历史局**里（10-17 ~ 10-19），
     而 22 个 mixed 局的 447 条 COMBAT 里 **0 条** 有 0
   ⇒ 那 322 条是**历史数据**（旧代码形态产出），不是当前代码产生的。
   ⇒ 详见 `.dsh-artifacts/_t34_diag.log` / `_t34_root.log` / `_t34_verdict.log` / `_t34_sim.log`。

2. 【当前真正的缺口】把 :2649-2658 逐字抽出来跑（`_t34_sim.py`）证明：
   - hp 可读时，当前代码**产生不出** `lowest_hp==0`（实验1：0/2）
   - hp **读不到**时（`isinstance(hp,(int,float))` 为假），`:2657` 仍写入 `lowest_hp = None`，
     而 `_close_route_room` 的 `isinstance(lowest,(int,float))` 为假 ⇒ **`hp_lost` 静默变成 0**
   ⇒ 这个 0 与"这房间真的没掉血"的 0 **无法区分** ⇒ 是"无痕迹的观察缺口"
     （CANON 口径），正是 D21 里真正还活着的部分。

## 本文件锁住的东西

- `hp_lost` 的**定义**不许变（`max(0, hp_start - lowest_hp)`）—— 变异 (b) 会转红
- `lowest_hp` **必须**跨 tick 累积（取 min）—— 变异 (a) 会转红
- 采集**不许漏屏**（COMBAT 必须被 `_read_hp` 读到 hp）—— 变异 (c) 会转红
- 「损血未知」必须**可分辨**（新增 `hp_loss_known`），不许静默退化成 0

★ 口径提醒：本文件测的是**采集侧**语义。`death_attribution` 的行为在输入不变时不得改变，
  那一侧由它自己的测例锁（`tests/test_death_attribution*.py`）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ★ 与既有测例**逐字同款**的导入方式（test_d12_crystal_sphere.py:46 /
#   test_stuck_screen.py:20）：直接 `from plugin.plugins.sts2_autoplay.service import ...`。
#
# ★ 踩坑记录（我试了两种错法，都失败，故此处只保留正确写法）：
#   ① 用文件路径 import service  ⇒ service.py 内部 `from .action_engine import` 是相对导入
#      ⇒ `ImportError: attempted relative import with no known parent package`
#   ② 再把 `N.E.K.O/plugin` 也塞进 sys.path ⇒ `plugin.sdk` 解析到 `utils.*` 时命中了
#      **别处的** utils 包 ⇒ `ModuleNotFoundError: No module named 'utils.plugin_card_fields'`
#   ⇒ 正确做法是**不要自己动 sys.path**，完全依赖契约 verify 命令里的
#     `PYTHONPATH=D:\NekoClaw\N.E.K.O`（既有 1282 条用例都这么跑）。
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService as service

SERVICE_SRC = (Path(__file__).resolve().parent.parent / "service_history.py").read_text(encoding="utf-8")


# ======================================================================================
# 一个最小宿主：只驱动 `_remember_route_step` / `_close_route_room`
# ======================================================================================


class _FakeState:
    def __init__(self) -> None:
        self.route_log: list[dict] = []
        self.route_room_key = ""
        self.route_start: dict = {}
        self.route_last: dict = {}
        self.hp_trail: list = []
        self.hp_trail_run_id = ""
        self.route_max = 80


class _Host:
    """把 service 里那两个方法**原样**绑到一个最小对象上（不构造整个插件）。"""

    _ROUTE_MAX_ROOMS = 80
    _REWARD_LIKE_SCREENS: set[str] = set()

    def __init__(self) -> None:
        self._state = _FakeState()

    # 依赖的三个小工具，语义与 service 保持一致的**最小**实现
    def _safe_int(self, value):  # noqa: ANN001
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _room_identity(self, screen: str, raw_state: dict) -> str:  # noqa: ANN001
        return str(raw_state.get("room") or "ROOM")

    def _run_snapshot(self, raw_state: dict):  # noqa: ANN001
        # ★★ 必须调用 **service 上的真品**，不能自己抄一份！
        #   我第一版在这里内联复制了 :2588-2609 的实现 —— 结果是变异 M34-E
        #   （把 run.current_hp 兜底删掉）**转不红**：变异改的是 service.py 里的真品，
        #   而我的测试走的是自己抄的副本 ⇒ 门形同虚设。
        #   ⇒ 现在改为直接借用 service 的方法（`_Host` 上没有绑定版本时走这里）。
        return service._run_snapshot(self, raw_state)

    # 把被测方法从 service 上**原样**取来
    _remember_route_step = service._remember_route_step
    _close_route_room = service._close_route_room


class _Logger:
    def info(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass


def _host() -> _Host:
    h = _Host()
    h.logger = _Logger()  # type: ignore[attr-defined]
    return h


def _tick(host: _Host, *, screen: str, hp, floor: int = 1, room: str = "R", run_id: str = "RUN1") -> None:
    """推一个 tick 进 `_remember_route_step`。"""
    raw = {"run": {"current_hp": hp, "gold": 0}, "room": room}
    snap = {"raw_state": raw, "screen": screen, "floor": floor, "run_id": run_id}
    host._remember_route_step(snap)


# ======================================================================================
# ① 跨 tick 累积（变异 (a) 的目标）
# ======================================================================================


@pytest.mark.unit
def test_lowest_hp_accumulates_across_ticks() -> None:
    """★★ 同一房间内多次 tick，`lowest_hp` 必须取【历史最低】，不是最后一次。

    ★ 这条直接锁住队长假设里"未跨 tick 累积"的失败形态：若改成只写当前值，
      `lowest` 会等于 `hp_end` 而不是过程中真正到过的最低点 ⇒ 转红。
    """
    h = _host()
    _tick(h, screen="COMBAT", hp=64)
    _tick(h, screen="COMBAT", hp=52)   # 掉到 52
    _tick(h, screen="COMBAT", hp=60)   # 回血到 60（药水/遗物）

    assert h._state.route_last["lowest_hp"] == 52, (
        "★★ lowest_hp 必须是【历史最低】(52)，不能是最后一次的 hp"
    )

    _tick(h, screen="MAP", hp=60)      # 换房间 ⇒ 结算
    entry = h._state.route_log[-1]
    assert entry["lowest_hp"] == 52
    assert entry["hp_start"] == 64
    assert entry["hp_lost"] == 12, "64 - 52 = 12（不是 64-60=4，也不是 64-64=0）"


@pytest.mark.unit
def test_lowest_hp_resets_per_room() -> None:
    """★ 换房间后 `lowest_hp` 必须重新起算 —— 否则会把上一个房间的低点带过来。

    ★ 我先写过一版**错的期望**，记录在此免得后人重踩：
      我断言「第二个房间 `hp_start` 必须 == 40（上一房间结束时的血）」——
      **那是我对语义的误读**。实际语义是 `route_start` 取**该房间第一次 tick 的 current**；
      若房间是在玩家已掉到 38 之后才开的，`hp_start` 就该是 38。
      ⇒ 正确的判据是「**损血不带过来**」：第二个房间的 `hp_lost` 只算它自己那一段。
    """
    h = _host()
    _tick(h, screen="COMBAT", hp=64, floor=1)
    _tick(h, screen="COMBAT", hp=40, floor=1)
    _tick(h, screen="MAP", hp=40, floor=2)      # 换房间结算（MAP 与 COMBAT 的 key 不同）
    first = h._state.route_log[-1]
    assert first["hp_start"] == 64
    assert first["lowest_hp"] == 40
    assert first["hp_lost"] == 24, "第一个房间：64 → 40 = 24"

    # 第二个房间：开房那一 tick 的血是 40，然后掉到 38。
    _tick(h, screen="COMBAT", hp=40, floor=2)
    _tick(h, screen="COMBAT", hp=38, floor=2)
    _tick(h, screen="EVENT", hp=38, floor=2)
    second = h._state.route_log[-1]
    assert second["hp_start"] == 40, "第二个房间从 40 起算（开房那一 tick）"
    assert second["lowest_hp"] == 38, (
        "★★ 必须是本房间自己的低点 38 —— 把上一房间的 40 或全局 64 带过来就是错的"
    )
    assert second["hp_lost"] == 2, "第二个房间只掉了 2（不许把 24 带过来）"


@pytest.mark.unit
def test_hp_readable_produces_no_zero_lowest() -> None:
    """★★ 只要 hp 可读，就**绝不**产生 lowest_hp 为 0/None 的条目。

    ★ 这是把 `_t34_sim.py` 实验1 的结论固化成门：当前代码在 hp 可读时产生不出 0。
      （历史上那 322 条 0 来自旧代码形态；这条门保证它不会回来。）
    """
    h = _host()
    _tick(h, screen="COMBAT", hp=64)
    _tick(h, screen="COMBAT", hp=49)
    _tick(h, screen="MAP", hp=49)
    _tick(h, screen="COMBAT", hp=49)
    _tick(h, screen="REST", hp=49)
    for e in h._state.route_log:
        assert e["lowest_hp"] not in (0, None), f"hp 可读时不该出现 0/None：{e}"


# ======================================================================================
# ② hp_lost 定义不许变（变异 (b) 的目标）
# ======================================================================================


@pytest.mark.unit
def test_hp_lost_definition_is_start_minus_lowest() -> None:
    """★★★ `hp_lost` 的定义是契约：`max(0, hp_start - lowest_hp)`。

    ★ 必须与「净损血」(hp_start - hp_end) 区分开：
      - 净损血会低估（若中途掉到 30 又回血到 60，净损=4，但实际承受过 34）
      - `lowest_hp` 口径记录的是**承受过的最大深度**，死因判据依赖它
    ★ 契约（t30 §1）明确要求保持本语义。换成净损血 ⇒ 本测例转红。
    """
    h = _host()
    _tick(h, screen="COMBAT", hp=64)
    _tick(h, screen="COMBAT", hp=30)   # 掉到 30
    _tick(h, screen="COMBAT", hp=60)   # 回血
    _tick(h, screen="MAP", hp=60)
    e = h._state.route_log[-1]
    assert e["lowest_hp"] == 30
    assert e["hp_start"] == 64
    assert e["hp_end"] == 60
    assert e["hp_lost"] == 34, "★ 必须是 64-30=34（承受深度），不是 64-60=4（净损血）"
    assert e["hp_lost"] != max(0, e["hp_start"] - e["hp_end"]), "★ 两种口径必须可区分"


@pytest.mark.unit
def test_hp_lost_never_negative() -> None:
    """★ 回血超过起始值时 `hp_lost` 夹到 0（`max(0, ...)` 不许丢）。"""
    h = _host()
    _tick(h, screen="COMBAT", hp=50)
    _tick(h, screen="COMBAT", hp=70)
    _tick(h, screen="MAP", hp=70)
    assert h._state.route_log[-1]["hp_lost"] == 0


@pytest.mark.unit
def test_hp_lost_source_line_is_locked() -> None:
    """★ 源码级锁：`hp_lost = max(0, int(start_hp) - int(lowest))` 必须原样存在。

    变异 (b) 把这里换成 `int(start_hp) - int(last.get("hp"))` 时本测例转红。
    """
    assert "hp_lost = max(0, int(start_hp) - int(lowest))" in SERVICE_SRC, (
        "★★★ hp_lost 的定义被改了 —— 契约要求保持 max(0, hp_start - lowest_hp)"
    )


# ======================================================================================
# ③ 采集不许漏屏（变异 (c) 的目标）
# ======================================================================================


@pytest.mark.unit
def test_run_snapshot_reads_both_hp_sources_source_level() -> None:
    """★★★ `_run_snapshot` 必须**同时**读两个来源：`combat.player.current_hp` 与 `run.current_hp`。

    ★ 为什么必须再补一条**源码级**断言（而不是只靠行为断言）：
      我第一版只写了行为断言，结果变异 M34-E（删掉 `run.current_hp` 兜底）**转不红** ——
      因为行为断言里的"期望值"和"实际值"都经过**同一个** `_run_snapshot`，
      删掉兜底后两边一起变成 None，断言反而成立 ⇒ 门形同虚设。
      ⇒ 补这条直接读源码的门，把"两个来源都在"钉死。
    """
    import re as _re
    m = _re.search(r"def _run_snapshot\(self.*?\n(?=    def )", SERVICE_SRC, _re.S)
    assert m, "★ 找不到 _run_snapshot"
    body = m.group(0)
    assert 'player.get("current_hp")' in body, "★★ 必须读 combat.player.current_hp"
    assert 'run.get("current_hp")' in body, (
        "★★★ 必须保留 run.current_hp 兜底（删掉它会让战斗外/读不到时的 hp 全丢）"
    )
    # 兜底必须是 isinstance 判断，不是无条件覆盖
    assert "if not isinstance(hp, (int, float)):" in body, (
        "★ 兜底必须是「读不到才退」，不能无条件覆盖"
    )


@pytest.mark.unit
def test_hp_read_from_combat_player_then_run_fallback() -> None:
    """★ hp 的读取顺序：先 `combat.player.current_hp`，读不到才退 `run.current_hp`。

    ★ 行为侧断言（与上面那条**源码级**断言互补 —— 行为断言单独用会因
      "期望和实际走同一个函数"而失守，见那条的注释）。

    ★ 为避免自己抄副本导致门失效，这里**直接调 service 上的真品**算期望值。
    """
    h = _host()
    # 只有 combat.player 有
    h._remember_route_step({
        "raw_state": {"combat": {"player": {"current_hp": 21}}, "run": {"gold": 0}, "room": "R"},
        "screen": "COMBAT", "floor": 1, "run_id": "R1",
    })
    assert h._state.route_last["hp"] == 21, "★ 必须优先读 combat.player.current_hp"

    # 退回到 run.current_hp：期望值**硬编码**（不经过被测函数），这样删掉兜底就会红
    h2 = _host()
    h2._remember_route_step({
        "raw_state": {"run": {"current_hp": 33, "gold": 0}, "room": "R"},
        "screen": "COMBAT", "floor": 1, "run_id": "R1",
    })
    assert h2._state.route_last["hp"] == 33, "★ combat 读不到时必须退 run.current_hp"


@pytest.mark.unit
def test_combat_room_records_hp_not_dropped() -> None:
    """★★ COMBAT 屏必须被采集到（不许因为屏名判断把战斗漏掉）。

    ★ 这是变异 (c)「采集漏掉某类屏」的直接目标：真正的损血就发生在 COMBAT 里，
      漏掉它等于 D21 的核心数据全丢。
    """
    h = _host()
    _tick(h, screen="COMBAT", hp=64)
    _tick(h, screen="COMBAT", hp=40)
    _tick(h, screen="MAP", hp=40)
    combat = [e for e in h._state.route_log if str(e.get("screen")).upper() == "COMBAT"]
    assert combat, "★ COMBAT 房间必须被记进 route_log"
    assert combat[-1]["hp_lost"] == 24, "★ COMBAT 的损血必须被算出来"


# ======================================================================================
# ④ 「损血未知」必须可分辨（本轮新增的可观测性门）
# ======================================================================================


@pytest.mark.unit
def test_unreadable_hp_is_marked_unknown_not_silent_zero() -> None:
    """★★★ hp 读不到时，条目必须**标出损血未知**，而不是静默给 hp_lost=0。

    ★ 根因（实测）：`_remember_route_step` 在 `isinstance(hp,(int,float))` 为假时
      仍写 `current["lowest_hp"] = None`；`_close_route_room` 的 isinstance 为假
      ⇒ `hp_lost` 保持 0。这个 0 与"真的没掉血"**无法区分**。
    ★ 修法：加一个显式标记（`hp_loss_known`），**不动 hp_lost 的定义**。
    """
    h = _host()
    _tick(h, screen="COMBAT", hp=None)     # hp 读不到
    _tick(h, screen="COMBAT", hp=None)
    _tick(h, screen="MAP", hp=None)
    e = h._state.route_log[-1]
    assert e.get("hp_loss_known") is False, (
        "★★★ hp 读不到时必须显式标记 hp_loss_known=False（不许静默当 0）"
    )


@pytest.mark.unit
def test_known_zero_loss_is_distinguishable_from_unknown() -> None:
    """★★ 「确实没掉血」与「读不到所以不知道」**必须可区分**。

    ★ 这是 CANON 口径里"有可区分痕迹"的要求：两者 hp_lost 都是 0，
      但 `hp_loss_known` 必须一真一假。
    """
    h = _host()
    # 真的没掉血
    _tick(h, screen="REST", hp=50)
    _tick(h, screen="MAP", hp=50)
    known = h._state.route_log[-1]
    # 读不到
    _tick(h, screen="COMBAT", hp=None)
    _tick(h, screen="REST", hp=None)
    unknown = h._state.route_log[-1]

    assert known["hp_lost"] == 0 and unknown["hp_lost"] == 0
    assert known.get("hp_loss_known") is True
    assert unknown.get("hp_loss_known") is False
    assert known["hp_loss_known"] != unknown["hp_loss_known"], (
        "★★ 两种 0 必须可区分（否则死因判据无法知道哪条可信）"
    )


@pytest.mark.unit
def test_hp_loss_known_is_true_when_hp_readable() -> None:
    """★ 正常路径下 `hp_loss_known` 必须是 True（不许把正常数据也标成未知）。"""
    h = _host()
    _tick(h, screen="COMBAT", hp=64)
    _tick(h, screen="COMBAT", hp=48)
    _tick(h, screen="MAP", hp=48)
    assert h._state.route_log[-1]["hp_loss_known"] is True


@pytest.mark.unit
def test_route_entry_keeps_hp_start_and_hp_end() -> None:
    """★ 既有字段不许丢（hp_start / hp_end / lowest_hp / hp_lost 全在）。"""
    h = _host()
    _tick(h, screen="COMBAT", hp=64)
    _tick(h, screen="COMBAT", hp=48)
    _tick(h, screen="MAP", hp=48)
    e = h._state.route_log[-1]
    for k in ("hp_start", "hp_end", "lowest_hp", "hp_lost", "hp_loss_known"):
        assert k in e, f"★ 缺字段 {k}"


# ======================================================================================
# ⑤ 与死因判据的接口：标记不许干扰既有消费者
# ======================================================================================


@pytest.mark.unit
def test_new_field_does_not_change_hp_lost_value() -> None:
    """★★ 新增标记**不得**改变 `hp_lost` 的数值 —— 既有消费者的输入必须不变。

    ★ 这条是"不误伤"的护栏：`death_attribution` 在**输入不变**时行为不得改变，
      而它的输入就是这些数值字段。
    """
    import json

    h = _host()
    _tick(h, screen="COMBAT", hp=64)
    _tick(h, screen="COMBAT", hp=49)
    _tick(h, screen="MAP", hp=49)
    e = dict(h._state.route_log[-1])
    known_flag = e.pop("hp_loss_known", None)
    # 去掉新字段后，条目必须与"旧口径"逐字段一致
    assert json.dumps(e, sort_keys=True) == json.dumps({
        "room": "R", "screen": "COMBAT", "floor": 1,
        "hp_start": 64, "hp_end": 49, "lowest_hp": 49, "hp_lost": 15,
        "gold_gained": 0, "cards_gained": [], "relics_gained": [],
        "potions_gained": [], "ended_by": "next_room",
    }, sort_keys=True), f"★ 既有数值字段被改动了：{e}"
    assert known_flag is True, "★ 标记本身要为 True（数据是可读的）"
