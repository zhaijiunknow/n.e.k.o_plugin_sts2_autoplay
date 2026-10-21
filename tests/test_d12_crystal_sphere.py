# -*- coding: utf-8 -*-
"""D12（t32）门：NCrystalSphereScreen 未识别 ⇒ 永久卡死。

## 缺陷（实机证据 2026-10-20 03:10:03，`.dsh-artifacts/_t32/live-crystal-sphere-20261020_031003.json`）

```
screen              = 'EVENT'
unknown_screen_type = '...Custom.CrystalSphere.NCrystalSphereScreen'
available_actions   = ['save_and_quit', 'discard_potion']   ← 无任何推进动作
event / selection / modal / bundles / timeline / map / reward / rest / shop / chest
character_select / multiplayer_lobby              = 全 null
```
⇒ 26 个顶层键里除 `run`/`session`/`agent_view` 外**全为 null**。

## 因果链（已逐段取证）

1. mod 认不出该屏的 node 类型 ⇒ `ResolveScreenFromStaticRoom()` 用
   `EventRoom => "EVENT"` **兜底猜**出屏幕名（`GameStateService.cs:736-746`，原文自述
   "认不出的界面不能就此摆烂"，且**已记载** 2026-10-07 同一屏卡了 2 小时 40 分）。
2. 插件看到 `screen=EVENT` 就去找 `choose_event_option`（`heuristic_planner.py:1020-1032`），
   而游戏从没提供该动作 ⇒ 规划不出动作。
3. ⇒ `run_autoplay_step` 走到 `_note_no_operation` ⇒ 只上报、**不推进** ⇒ 永久卡死。

## 为什么已有的游标机制救不了它

`service._advance_custom_event_cursor`（:656）对水晶球确实有推进逻辑，但前置条件是
`event["screen_progress"]` 非空（:672）。本屏 `event is None` ⇒ 恒空 ⇒ **被早退绕过**。
⇒ 水晶球有两个子形态：**有 event 层**（游标可推进，已修）与**无 event 层**（本缺陷）。

## 本门的立场

★ `mod` 侧没有水晶球交互（54 个动作里一个都没有，`GameActionService.cs:99-152`），
  `/state` 也不提供可交互元素 ⇒ **该屏无法从插件侧自动推进**。
  所以本门锁定的是「**至少不要卡死得不明不白**」：
  把原因讲清、给出 mod 侧需补什么、**绝不毁局**。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService

#: 实机抓到的类名（逐字）
CRYSTAL_SPHERE = (
    "MegaCrit.Sts2.Core.Nodes.Events.Custom.CrystalSphere.NCrystalSphereScreen"
)

_LIVE = pathlib.Path(
    r"D:\NekoClaw\n.e.k.o_plugin_sts2_autoplay\.dsh-artifacts\_t32"
    r"\live-crystal-sphere-20261020_031003.json"
)


# ------------------------------------------------------------------ fixtures

class _NullLogger:
    """静音 logger（`_emit_stuck_report` 会 `self.logger.warning(...)`）。"""

    def info(self, *args: Any, **kwargs: Any) -> None: ...
    def error(self, *args: Any, **kwargs: Any) -> None: ...
    def debug(self, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, *args: Any, **kwargs: Any) -> None: ...


class _FakeState:
    """只带 D12 用到的字段（`runtime_state` 不在本任务 inScope，故就地替身）。"""

    def __init__(self) -> None:
        self.stuck_signature = ""
        self.stuck_since = 0.0
        self.stuck_reported = False
        self.stuck_reason = ""
        self.autoplay_state = "running"
        self.transport_state = "connected"
        self.host_digest_enabled = False
        # t33：占卜游标（与 `_advance_custom_event_cursor` 共用同一对字段）。
        self.event_option_cursor = 0
        self.event_option_screen = ""


class _Recorder:
    """**包裹**（不是替换）`_emit_stuck_report`：记录调用，**同时仍走真实写入口**。

    ⚠️ 我第一版把 `_emit_stuck_report` **整个替换**掉了，结果 `stuck_reason` 恒为空
       ⇒ 两条测例假红。根因：`stuck_reason` 的**唯一写入口就在该方法内**
       （`service.py` 的 `_emit_stuck_report`: `self._state.stuck_reason = reason`）。
       ⇒ 替换它 = 把"明确记录"这条判据本身挖掉了，测不出真实行为。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[dict[str, str]] = []

    def __call__(self, *, reason: str, log_line: str, host_text: str) -> None:
        self.calls.append({"reason": reason, "log_line": log_line, "host_text": host_text})
        # ★ 关键：继续调用真实实现 —— 否则 `stuck_reason` 不会被写。
        self._inner(reason=reason, log_line=log_line, host_text=host_text)


@pytest.fixture()
def svc() -> STS2AutoplayService:
    s = object.__new__(STS2AutoplayService)
    s._state = _FakeState()
    s.logger = _NullLogger()
    s._emit_status = lambda: None
    s._push_proactive_to_host = lambda text, **kwargs: None
    # ★ 包裹而非替换（见 _Recorder 注释）
    s._recorder = _Recorder(s._emit_stuck_report)  # type: ignore[attr-defined]
    s._emit_stuck_report = s._recorder  # type: ignore[method-assign,assignment]
    return s


def _snap(
    *,
    screen: str = "EVENT",
    unknown: str = "",
    event: Any = None,
    actions: tuple[str, ...] = ("save_and_quit",),
    floor: int = 23,
    act: str = "1",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "screen": screen,
        "run_id": "C6CM8CZRR9KQ",
        "unknown_screen_type": unknown,
        "event": event,
        "available_actions": list(actions),
        "run": {"act_id": act, "floor": floor, "current_hp": 62, "max_hp": 87},
    }
    if extra:
        raw.update(extra)
    return {"screen": screen, "floor": floor, "raw_state": raw}


# ======================================================================================
# AC① 实机原始 JSON 的形状必须与 fixtre 一致（★ 防止门建在虚构形状上）
# ======================================================================================


@pytest.mark.unit
def test_live_capture_exists_and_has_expected_shape() -> None:
    """★★ 用**实机抓的原始 JSON** 钉住形状 —— 门不许建在虚构 fixture 上。"""
    if not _LIVE.exists():  # pragma: no cover
        pytest.skip(f"实机样本缺失：{_LIVE}")
    env = json.loads(_LIVE.read_text(encoding="utf-8"))
    d = env.get("data") if isinstance(env.get("data"), dict) else env

    assert d["screen"] == "EVENT"
    assert d["unknown_screen_type"] == CRYSTAL_SPHERE, "★ 类名必须逐字一致"
    assert d["available_actions"] == ["save_and_quit", "discard_potion"]
    assert d["run"]["act_id"] == "1" and d["run"]["floor"] == 23

    # ★ 决定性事实：所有交互层为 null
    for layer in ("event", "selection", "modal", "bundles", "timeline", "map",
                  "reward", "rest", "shop", "chest", "character_select",
                  "multiplayer_lobby", "combat", "game_over"):
        assert d.get(layer) is None, f"★ {layer} 实测应为 null"

    # ★ 且确实存在「唯一标识未识别界面」的字段可用
    assert "unknown_screen_type" in d


@pytest.mark.unit
def test_crystal_sphere_detected_on_live_payload(svc: STS2AutoplayService) -> None:
    """★ 用实机载荷直接驱动判定 ⇒ 必须命中。"""
    if not _LIVE.exists():  # pragma: no cover
        pytest.skip(f"实机样本缺失：{_LIVE}")
    env = json.loads(_LIVE.read_text(encoding="utf-8"))
    d = env.get("data") if isinstance(env.get("data"), dict) else env
    diag = svc._crystal_sphere_screen({"screen": d["screen"], "raw_state": d})
    assert diag is not None, "★★ 实机卡死形态必须被识别出来"
    assert diag["unknown"] == CRYSTAL_SPHERE
    assert "discard_potion" in diag["actions"]


# ======================================================================================
# AC③④ 判定：该屏推不动（且必须与「有 event 层的水晶球」区分开）
# ======================================================================================


@pytest.mark.unit
def test_detects_unadvanceable_custom_screen(svc: STS2AutoplayService) -> None:
    """★ 实机形态（EVENT + 无 event 层 + 只有 save_and_quit/discard_potion）必须命中。"""
    diag = svc._crystal_sphere_screen(
        _snap(unknown=CRYSTAL_SPHERE, event=None,
              actions=("save_and_quit", "discard_potion"))
    )
    assert diag is not None
    assert diag["screen"] == "EVENT"
    assert diag["actions"] == ["discard_potion", "save_and_quit"]


@pytest.mark.unit
def test_crystal_sphere_with_event_layer_is_NOT_flagged(
    svc: STS2AutoplayService,
) -> None:
    """★★★ 关键不误伤：**有 event 层**的水晶球必须放过 ⇒ 交给已有的游标机制推进。

    `_advance_custom_event_cursor`（:656）靠 `event.screen_progress` 工作，那条路已验证可用
    （2026-10-07 修过）。若本判定把带 event 层的形态也拦掉，就是把**能修的场景降级成放弃**。
    """
    diag = svc._crystal_sphere_screen(
        _snap(unknown=CRYSTAL_SPHERE,
              event={"screen_progress": "PAGE_1", "options": [{"index": 0}]},
              actions=("choose_event_option", "save_and_quit"))
    )
    assert diag is None, "★★ 有 event 层的水晶球不得被判为推不动"


@pytest.mark.unit
def test_crystal_sphere_ENTRY_screen_with_options_is_NOT_flagged(
    svc: STS2AutoplayService,
) -> None:
    """★★★ 实机新证据（2026-10-20 03:28:15）：水晶球的**入口屏**必须放过。

    在 `save_and_quit` 造成的过渡窗口里抓到过**真事件层**（落盘
    `.dsh-artifacts/_t32/window-IN-WINDOW-032815.json`）：
    ```
    screen='EVENT'  unknown=''      ← ★ 注意 unknown 为空
    event_id='CRYSTAL_SPHERE'  is_finished=False  screen_progress=None
    options = [ {index:0,title:'揭幕未来'}, {index:1,title:'分期付款'} ]
    available_actions = ['save_and_quit','choose_event_option','discard_potion']
    ```
    实机点 `option_index=0` ⇒ **HTTP 200 `status: completed`**（真成功）。
    ⇒ 这是「选玩法」的入口，**可以推进**，绝不能被判为推不动。

    ★ 本测例**特意把 unknown 设为空** —— 因为实机就是这样；
      但它也覆盖了「万一 unknown 带上类名」的假设，两者都必须放过。
    """
    entry_event = {
        "event_id": "CRYSTAL_SPHERE",
        "title": "水晶球",
        "description": "...",
        "is_finished": False,
        "screen_progress": None,
        "options": [
            {"index": 0, "title": "揭幕未来", "is_locked": False, "is_proceed": False},
            {"index": 1, "title": "分期付款", "is_locked": False, "is_proceed": False},
        ],
    }
    for unknown in ("", CRYSTAL_SPHERE):
        diag = svc._crystal_sphere_screen(
            _snap(unknown=unknown, event=entry_event,
                  actions=("save_and_quit", "choose_event_option", "discard_potion"))
        )
        assert diag is None, (
            f"★★ 水晶球入口屏（有 event 层 + choose_event_option，unknown={unknown!r}）"
            "必须放过 —— 实机在该屏点 idx=0 是 HTTP 200 成功的"
        )


@pytest.mark.unit
def test_flag_only_when_event_layer_missing_even_with_unknown_blank(
    svc: STS2AutoplayService,
) -> None:
    """★ 边界：`unknown` 为空但**也没有** event 层 ⇒ 不该由本判定接管（交给通用闸）。

    实机在过渡窗口里出现过 `unknown=''` + `event=None` + 只有 `discard_potion`
    的极短瞬间（`.dsh-artifacts/_t32/` 的窗口日志）。那种时刻**不确定**，
    可能只是过渡态 ⇒ 本判定**不认**（宁可让通用闸去处理，也不误报水晶球）。
    """
    diag = svc._crystal_sphere_screen(
        _snap(unknown="", event=None, actions=("discard_potion",))
    )
    assert diag is None, "★ unknown 为空时不得断言是水晶球（可能只是过渡态）"


@pytest.mark.unit
def test_event_layer_exemption_is_independently_enforced(
    svc: STS2AutoplayService,
) -> None:
    """★★★ **只为 event 层豁免而存在**的测例（否则那条守卫没有门守）。

    ## 为什么必须单列（★ 我踩到的第二个假绿）

    上面那条测例同时带了 `choose_event_option`（推进动作）⇒ 即使把
    「event 层非空 ⇒ 放过」**整条删掉**，也会被「progressing 非空 ⇒ 放过」拦住
    ⇒ **两条豁免互为兜底 ⇒ 那条变异体不可观测 ⇒ 假绿**（实测 20 passed 全绿）。

    ⇒ 本测例**刻意不带任何推进动作**（只有 `save_and_quit`）：
      · 正确实现 ⇒ event 层豁免生效 ⇒ **放过**
      · 删掉该豁免 ⇒ 唯一拦得住的就是它 ⇒ **转红**

    ★ 现实意义：这一形态（有 event 层 + 无推进动作）是真实的 ——
      水晶球的另一子形态就是「event 层给了 `screen_progress`/`options`，
      但当下没有任何推进动作在册」，此时应让游标机制去试下标，**不是**放弃。
    """
    diag = svc._crystal_sphere_screen(
        _snap(unknown=CRYSTAL_SPHERE,
              event={"screen_progress": "PAGE_1", "options": [{"index": 0}]},
              actions=("save_and_quit",))          # ★ 刻意不带推进动作
    )
    assert diag is None, (
        "★★ 有 event 层时必须放过（哪怕当下没有推进动作）——"
        "否则游标机制被绕过，可推进的场景被误判成推不动"
    )


@pytest.mark.unit
def test_empty_event_dict_is_flagged(svc: STS2AutoplayService) -> None:
    """★ 边界：`event` 是**空 dict** ⇒ 等同"没有 event 层" ⇒ 应命中。

    `mod` 实测在认不出界面时会给 `event = null`；空 dict 是它的等价形态，
    两者都不含 `screen_progress` ⇒ 游标机制同样用不了 ⇒ 同样推不动。
    """
    diag = svc._crystal_sphere_screen(
        _snap(unknown=CRYSTAL_SPHERE, event={}, actions=("save_and_quit",))
    )
    assert diag is not None, "★ 空 event dict 不得被当成有 event 层而放过"


@pytest.mark.unit
def test_crystal_sphere_with_progressing_action_is_NOT_flagged(
    svc: STS2AutoplayService,
) -> None:
    """★ 只要还有任意**推进类**动作在册 ⇒ 放过（让规划器去用）。"""
    diag = svc._crystal_sphere_screen(
        _snap(unknown=CRYSTAL_SPHERE, event=None,
              actions=("save_and_quit", "choose_event_option"))
    )
    assert diag is None


# ======================================================================================
# AC⑦ 不误伤：其它 EVENT 屏 / 正常路径行为不得改变
# ======================================================================================


@pytest.mark.unit
@pytest.mark.parametrize("unknown", ["", "MegaCrit.Sts2.Core.Nodes.Events.NEventRoom"])
def test_normal_event_screen_is_NOT_flagged(svc: STS2AutoplayService, unknown: str) -> None:
    """★★ 普通事件屏（无 unknown / 类名不含标记片段）绝不能被判为推不动。"""
    diag = svc._crystal_sphere_screen(
        _snap(unknown=unknown,
              event={"event_id": "WELLSPRING", "is_finished": False,
                     "options": [{"index": 0, "title": "A"}]},
              actions=("choose_event_option", "save_and_quit"))
    )
    assert diag is None, "★★ 普通事件屏被误判 ⇒ 会破坏正常事件流程"


@pytest.mark.unit
def test_normal_combat_is_NOT_flagged(svc: STS2AutoplayService) -> None:
    """★ 战斗路径不得受影响。"""
    diag = svc._crystal_sphere_screen({
        "screen": "COMBAT",
        "raw_state": {"screen": "COMBAT", "unknown_screen_type": "",
                      "available_actions": ["play_card", "end_turn"], "event": None},
    })
    assert diag is None


@pytest.mark.unit
def test_unrecognized_non_crystal_screen_is_NOT_flagged(svc: STS2AutoplayService) -> None:
    """★ 别的未识别屏不归本判定管（各自有各自的缺陷，不许一锅端）。"""
    diag = svc._crystal_sphere_screen(
        _snap(unknown="MegaCrit.Sts2.Core.Nodes.TotallyDifferent.NMysteryScreen",
              event=None, actions=("save_and_quit",))
    )
    assert diag is None


@pytest.mark.unit
def test_marker_matches_variants(svc: STS2AutoplayService) -> None:
    """★ 片段匹配要能覆盖同族子屏（不同命名空间前缀也应命中）。"""
    for cls in (
        CRYSTAL_SPHERE,
        "Some.Other.Namespace.Events.Custom.CrystalSphereScreen",
        "X.Y.Events.Custom.DivinationScreen",
        "X.Y.Events.Custom.ProphecyScreen",
    ):
        diag = svc._crystal_sphere_screen(_snap(unknown=cls, event=None,
                                               actions=("save_and_quit",)))
        assert diag is not None, f"★ {cls} 应命中"


@pytest.mark.unit
def test_other_namespace_crystal_sphere_is_NOT_flagged(svc: STS2AutoplayService) -> None:
    """★★★ **不误伤**：别的命名空间下的同名类不得被本判定接管。

    ## 为什么必须有这条（★ 我打坏过 t29 的实测教训）

    我最初把标记写成单个片段 `"CrystalSphere"`，于是 t29 的 `test_stuck_screen.py` 里
    用的 `MegaCrit.Sts2.Core.Nodes.Screens.CrystalSphereScreen`（**旧命名空间**的另一个类）
    也被一起命中 ⇒ 走专用文案 ⇒ 打坏了 t29 的既有契约
    （全量实测：`1242 passed, 2 failed`，两条都在 `test_stuck_screen.py`）。

    ⇒ 现在要求「片段 + `Events` + `Custom` + `screen==EVENT`」同时成立。
      本测例钉住那个旧类名**必须不被接管**。
    """
    diag = svc._crystal_sphere_screen(
        _snap(screen="UNKNOWN",
              unknown="MegaCrit.Sts2.Core.Nodes.Screens.CrystalSphereScreen",
              event=None, actions=("save_and_quit",))
    )
    assert diag is None, (
        "★★ 别的命名空间的同名类不得被接管（否则会打坏 t29 的通用卡住上报）"
    )


@pytest.mark.unit
def test_non_event_screen_with_marker_is_NOT_flagged(svc: STS2AutoplayService) -> None:
    """★ 标记类名但**不在 EVENT 屏**上 ⇒ 不接管（本缺陷的症状只出现在 EVENT 兜底屏）。"""
    diag = svc._crystal_sphere_screen(
        _snap(screen="COMBAT", unknown=CRYSTAL_SPHERE,
              event=None, actions=("save_and_quit",))
    )
    assert diag is None


# ======================================================================================
# AC⑥ 降级方案：上报一次、讲清原因、给出 mod 需补什么、**绝不毁局**
# ======================================================================================


def _stall(svc: STS2AutoplayService, snap: dict[str, Any], *, ticks: int = 2) -> None:
    """驱动 `_note_no_operation` 的**共享闸**到"已上报"状态。

    ★ 为什么不直接调 `_note_unadvanceable_screen`：那条路**故意**把时序交给共享闸
      （`_note_no_operation` 先建指纹、再按阈值/已报判）。直接调会绕过时序，
      测不出真实调用路径 —— 而且我最初就是这么写，结果**没发现**自己打坏了 t29 的测例。
      ⇒ 门必须走**真实调用路径**（`_note_no_operation`），把 `NO_OPERATION_STUCK_SECONDS`
        置 0 来免等待。
    """
    svc.NO_OPERATION_STUCK_SECONDS = 0.0
    for _ in range(ticks):
        svc._note_no_operation(snap)


@pytest.mark.unit
def test_degradation_reports_once_not_every_tick(svc: STS2AutoplayService) -> None:
    """★★ 同一指纹只报**一次**（复用既有 `stuck_*` 记账，不刷屏）。"""
    snap = _snap(unknown=CRYSTAL_SPHERE, event=None,
                 actions=("save_and_quit", "discard_potion"))
    _stall(svc, snap)
    n1 = len(svc._recorder.calls)  # type: ignore[attr-defined]
    # 之后很多 tick：不得再报
    for _ in range(50):
        svc._note_no_operation(snap)
    n2 = len(svc._recorder.calls)  # type: ignore[attr-defined]
    assert n1 == 1, f"★ 首次应报 1 次，实测 {n1}"
    assert n2 == 1, f"★★ 同指纹重复上报 ⇒ 刷屏（实测 {n2} 次）"


@pytest.mark.unit
def test_degradation_reports_again_when_screen_changes(svc: STS2AutoplayService) -> None:
    """★ 换屏后（指纹变了）应能再次上报。"""
    a = _snap(unknown=CRYSTAL_SPHERE, event=None, actions=("save_and_quit",))
    _stall(svc, a)
    assert len(svc._recorder.calls) == 1, "★ 首个屏应报 1 次"  # type: ignore[attr-defined]
    b = _snap(unknown="X.Y.Events.Custom.DivinationScreen", event=None,
              actions=("save_and_quit",))
    _stall(svc, b)
    assert len(svc._recorder.calls) == 2, "★ 换屏后应重新上报"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_degradation_message_names_screen_and_needs(svc: STS2AutoplayService) -> None:
    """★★ 上报必须讲清：哪个屏 + 为什么推不动 + 需要 mod 补什么。"""
    snap = _snap(unknown=CRYSTAL_SPHERE, event=None, actions=("save_and_quit",))
    _stall(svc, snap)
    call = svc._recorder.calls[0]  # type: ignore[attr-defined]
    assert CRYSTAL_SPHERE in call["reason"], "★ reason 必须含未识别界面类型名"
    assert "mod" in call["log_line"], "★ 日志必须指出需 mod 侧新增"
    assert "screen_progress" in call["log_line"], "★ 必须给出可选的 mod 补法"
    assert "无法推进" in call["reason"], "★ 不许含糊其辞"


@pytest.mark.unit
def test_degradation_never_emits_destructive_action(svc: STS2AutoplayService) -> None:
    """★★★ **绝不毁局**：降级路径不得产出任何破坏性动作。

    实测这局已到 act1/floor23，是当晚最深的一局。
    `save_and_quit`/`return_to_main_menu`/`abandon_run` 会**毁掉玩家进度**，绝不能自动发。

    ⚠️ 我第一版写错了：拿 `json.dumps(calls)` 全文搜动作名 ⇒ 必然失败，
       因为 `log_line` 会**原样列出观测到的动作表**（含 `save_and_quit`），
       那是**观测事实**不是**派发意图**。搜文本证明不了"没派发"。
    ⇒ 正确的门是：**降级路径根本不产出 operation**（它只上报），
       且上报文本里不得把它写成**建议/要执行的动作**。
    """
    snap = _snap(unknown=CRYSTAL_SPHERE, event=None,
                 actions=("save_and_quit", "discard_potion"))
    diag = svc._crystal_sphere_screen(snap)
    result = svc._note_unadvanceable_screen(snap, diag)
    # ★ 关键断言①：降级**不返回任何操作**（没有 operation 就没有派发）
    assert result is None, "★★ 降级路径不得产出 operation（否则可能真发出去）"
    # ★ 关键断言②：**不动 autoplay 意图**（玩家没喊停就不许自己停）
    assert svc._state.autoplay_state == "running"
    # ★ 关键断言③：上报文本里不得把破坏性动作写成"建议"/"要执行"
    for c in svc._recorder.calls:  # type: ignore[attr-defined]
        for bad in ("save_and_quit", "return_to_main_menu", "abandon_run"):
            for hint in ("建议", "执行", "派发", "发送"):
                assert f"{hint}{bad}" not in c["reason"], "★ 不得建议破坏性动作"
                assert f"{hint}{bad}" not in c["host_text"], "★ 不得建议破坏性动作"


@pytest.mark.unit
def test_degradation_observation_text_may_list_actions_but_not_advise_them(
    svc: STS2AutoplayService,
) -> None:
    """★ 把上一条的口径单独钉住：**列出**观测动作 OK，**建议**它们不 OK。"""
    snap = _snap(unknown=CRYSTAL_SPHERE, event=None, actions=("save_and_quit",))
    _stall(svc, snap)
    log_line = svc._recorder.calls[0]["log_line"]  # type: ignore[attr-defined]
    # 观测事实（原样列出动作表）——允许，方便人定位
    assert "save_and_quit" in log_line
    # 但**必须**同时讲清"推不动"而不是"去点 save_and_quit"
    assert "推不动" in log_line or "无法推进" in log_line


@pytest.mark.unit
def test_non_progressing_actions_set_is_complete() -> None:
    """★ 常量集合必须真的覆盖那四个「点了也不推进」的动作。"""
    for a in ("save_and_quit", "return_to_main_menu", "abandon_run", "discard_potion"):
        assert a in STS2AutoplayService.NON_PROGRESSING_ACTIONS
    # ★ 推进类动作**绝不能**在里面（否则会把可推进的屏误判成推不动）
    for a in ("choose_event_option", "play_card", "choose_map_node", "continue_run",
              "collect_rewards_and_proceed", "confirm_selection", "proceed"):
        assert a not in STS2AutoplayService.NON_PROGRESSING_ACTIONS, (
            f"★ {a} 是推进类动作，不得列入 NON_PROGRESSING_ACTIONS"
        )


@pytest.mark.unit
def test_advice_explains_mod_side_requirement(svc: STS2AutoplayService) -> None:
    """★ AC④：读不到 ⇒ 必须明确说「/state 不提供」并给出 mod 侧需新增什么。"""
    adv = STS2AutoplayService._unadvanceable_screen_advice({"unknown": CRYSTAL_SPHERE})
    assert "null" in adv, "★ 必须点明 /state 相关层为 null"
    assert "event.screen_progress" in adv or "新增专用动作" in adv


# ======================================================================================
# 接线：`_note_no_operation` 必须真的把该屏导流到专用降级
# ======================================================================================


@pytest.mark.unit
def test_note_no_operation_routes_crystal_sphere_to_special_report(
    svc: STS2AutoplayService,
) -> None:
    """★★ 接线门：`_note_no_operation` 命中该屏时走**专用**上报（而非笼统的"没有可执行动作"）。

    ★ 走共享闸 ⇒ 需要两次 tick（第 1 次只建指纹，见 service.py `_note_no_operation`）。
    """
    snap = _snap(unknown=CRYSTAL_SPHERE, event=None,
                 actions=("save_and_quit", "discard_potion"))
    _stall(svc, snap)
    calls = svc._recorder.calls  # type: ignore[attr-defined]
    assert calls, "★★ 命中该屏时必须上报（不得静默空转）"
    assert "自定义小游戏" in calls[0]["reason"], (
        f"★★ 应走专用文案，实测 reason={calls[0]['reason']!r}"
    )
    assert "mod" in calls[0]["log_line"]


@pytest.mark.unit
def test_degradation_stops_repeating_and_does_not_advance(svc: STS2AutoplayService) -> None:
    """★★★ 队长①：降级触发后必须「明确记录 + 停止无效重试」，不得继续每 tick 空转。

    ## 实测现状（为什么"停止重试"这件事要单独钉）

    `_note_no_operation` 每次 tick 都会**早期 return**（:`if stuck_reported: return`），
    ⇒ 触发后**不再产生任何后续上报**（`calls` 恒为 1），
      也不会产出 operation（降级路径无返回值）⇒ **不做无效重试**。

    ★ 同时钉住**不变的那部分**：`autoplay_state` 保持 `running`
      （t29 的契约：卡住只报告、**绝不动 autoplay 意图**）。
    """
    snap = _snap(unknown=CRYSTAL_SPHERE, event=None,
                 actions=("save_and_quit", "discard_potion"))
    _stall(svc, snap)
    assert len(svc._recorder.calls) == 1, "★ 触发后应恰好 1 次"  # type: ignore[attr-defined]
    assert svc._state.stuck_reported is True, "★ 必须标记已报（否则会每 tick 重报）"

    # 模拟之后 200 个 tick（≈100 秒 @0.5s）
    for _ in range(200):
        svc._note_no_operation(snap)
    assert len(svc._recorder.calls) == 1, (  # type: ignore[attr-defined]
        "★★ 触发后不得继续每 tick 重报 —— 这正是「停止无效重试」的机械判据"
    )
    assert svc._state.stuck_reason, "★ stuck_reason 必须保留（状态查询/前端看得到）"
    assert svc._state.autoplay_state == "running", "★ 绝不动 autoplay 意图"


@pytest.mark.unit
def test_degradation_returns_no_operation_every_tick(svc: STS2AutoplayService) -> None:
    """★ `_note_no_operation` 是 `None` 返回（它只上报，不产出动作）⇒ 不派发 = 不重试。"""
    snap = _snap(unknown=CRYSTAL_SPHERE, event=None, actions=("save_and_quit",))
    svc.NO_OPERATION_STUCK_SECONDS = 0.0
    for _ in range(5):
        assert svc._note_no_operation(snap) is None, "★ 降级路径不得产出 operation"


@pytest.mark.unit
def test_stuck_reason_is_queryable_after_degradation(svc: STS2AutoplayService) -> None:
    """★ 队长①：触发后必须「明确记录」—— `stuck_reason` 要写进状态供查询。"""
    snap = _snap(unknown=CRYSTAL_SPHERE, event=None, actions=("save_and_quit",))
    _stall(svc, snap)
    reason = svc._state.stuck_reason
    assert reason, "★★ 必须写进 stuck_reason（否则状态页看不到卡在哪）"
    assert CRYSTAL_SPHERE in reason, "★ 必须含未识别界面类型名，方便定位"


@pytest.mark.unit
def test_note_no_operation_still_generic_for_other_screens(
    svc: STS2AutoplayService,
) -> None:
    """★ 不误伤：非本次缺陷的普通"没动作"仍走**原有**通用路径。

    ⚠️ 通用路径需要**两次** tick：第一次只建立指纹并返回（见 service.py:915-920
       `if signature != self._state.stuck_signature: ... return`）。
       我第一版只调一次 ⇒ 必然拿不到上报，那是**我的测试写错**，不是产品缺陷。
    """
    snap = _snap(screen="REWARD", floor=12, unknown="",
                 actions=("save_and_quit",))
    svc.NO_OPERATION_STUCK_SECONDS = 0.0
    svc._note_no_operation(snap)          # 第 1 次：建立指纹
    svc._note_no_operation(snap)          # 第 2 次：指纹相同 ⇒ 上报
    calls = svc._recorder.calls  # type: ignore[attr-defined]
    assert calls, "★ 通用路径必须仍然上报（两次 tick 后）"
    assert "自定义小游戏" not in calls[0]["reason"], (
        "★★ 普通屏不得被套上水晶球专用文案"
    )


@pytest.mark.unit
def test_note_no_operation_generic_path_requires_two_ticks(
    svc: STS2AutoplayService,
) -> None:
    """★ 把"两次 tick"这个既有语义钉住（防止以后误以为一次就该报）。"""
    snap = _snap(screen="REWARD", floor=12, unknown="", actions=("save_and_quit",))
    svc.NO_OPERATION_STUCK_SECONDS = 0.0
    svc._note_no_operation(snap)
    assert not svc._recorder.calls, "★ 第 1 次只建立指纹，不该上报"  # type: ignore[attr-defined]
    svc._note_no_operation(snap)
    assert len(svc._recorder.calls) == 1, "★ 第 2 次才上报"  # type: ignore[attr-defined]


# ======================================================================================
# t33：占卜**真正可处理** —— mod 补 `crystal_sphere` 层 + `divine_crystal_sphere` 动作，
#      插件补 override（规划器认不出这一屏，必须抢在它前面出动作）
# ======================================================================================
#
# ★ 与 t32 的立场差别（必须说清，否则会以为两份门矛盾）：
#   t32 锁的是「**认不出时至少不要卡死**」——那一版 mod 侧确实一个水晶球动作都没有
#   （`GameActionService.cs` 的 54 个动作里零命中，已核实）。
#   t33 之后 mod **有了** `divine_crystal_sphere`，于是「推不动」这个前提**不再成立**。
#   ⇒ 这两组测例不冲突：t32 的门在「没有可推进动作」时仍然必须成立（上面那些测例
#     用的正是 `available_actions=('save_and_quit',)` 且**没有** `crystal_sphere` 层），
#     t33 的门在「有了动作」时必须真的发出去。


def _divine_snap(
    *,
    hidden: int = 121,
    divinations: int = 3,
    finished: bool = False,
    actions: tuple[str, ...] = ("save_and_quit", "divine_crystal_sphere", "discard_potion"),
    floor: int = 23,
    cursor_screen: str = "divine|EVENT|23",
    candidates: Any = "auto",
    revealed: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """屏B（图块小游戏）在 **t33 修复后**的形象：有 `crystal_sphere` 层 + 有动作。"""
    if candidates == "auto":
        # 行优先坐标，与 mod 的 C# 遍历顺序一致（11x11）。
        cands = [{"x": i % 11, "y": i // 11, "item_type": "", "item_name": ""}
                 for i in range(hidden)]
    else:
        cands = candidates
    raw: dict[str, Any] = {
        "screen": "EVENT",
        "run_id": "C6CM8CZRR9KQ",
        "unknown_screen_type": CRYSTAL_SPHERE,
        "event": None,
        "available_actions": list(actions),
        "crystal_sphere": {
            "active": True,
            "grid_width": 11,
            "grid_height": 11,
            "divinations_left": divinations,
            "is_finished": finished,
            "tool": "Big",
            "cells_total": 121,
            "cells_hidden": hidden,
            "cells_revealed": 121 - hidden,
            "revealed_cells": revealed if revealed is not None else [],
            "candidates": cands,
        },
        "run": {"act_id": "1", "floor": floor, "current_hp": 62, "max_hp": 87},
    }
    return {
        "screen": "EVENT",
        "floor": floor,
        "available_actions": list(actions),
        "raw_state": raw,
        "_cursor_screen": cursor_screen,
    }


@pytest.mark.unit
def test_divine_override_fires_when_crystal_sphere_layer_present(
    svc: STS2AutoplayService,
) -> None:
    """★★ 核心：有了 `crystal_sphere` 层 + `divine_crystal_sphere` 在册，插件必须**真的发动作**。"""
    op = svc._crystal_sphere_override(_divine_snap())
    assert op is not None, "★★ 占卜屏必须产出动作，否则又退回'空转'"
    assert op["action_type"] == "divine_crystal_sphere"
    assert isinstance(op["kwargs"].get("option_index"), int)


@pytest.mark.unit
def test_divine_override_advances_index_each_tick(svc: STS2AutoplayService) -> None:
    """★ 不能死盯一块空点：每次派发必须往前推一格（与 :656 游标同款理由）。"""
    seen = [svc._crystal_sphere_override(_divine_snap(hidden=5))["kwargs"]["option_index"]
            for _ in range(7)]
    assert seen[:5] == [0, 1, 2, 3, 4], f"★ 应依次推进，实际 {seen}"
    assert seen[5] == 0 and seen[6] == 1, "★ 到尾应回绕"


@pytest.mark.unit
def test_divine_override_respects_cells_hidden_as_upper_bound(
    svc: STS2AutoplayService,
) -> None:
    """★ 序号上界取自 mod 报的 `cells_hidden`，不能越界（越界会被判 invalid_target）。"""
    seen = [svc._crystal_sphere_override(_divine_snap(hidden=2))["kwargs"]["option_index"]
            for _ in range(5)]
    assert seen == [0, 1, 0, 1, 0], f"★ 隐藏格只有 2 ⇒ 只在 0/1 里轮，实际 {seen}"


@pytest.mark.unit
def test_divine_override_returns_None_without_action_in_register(
    svc: STS2AutoplayService,
) -> None:
    """★ 不误伤：动作**不在册**时绝不硬发（否则只会换来 '没有合法动作'）。"""
    snap = _divine_snap(actions=("save_and_quit", "discard_potion"))
    assert svc._crystal_sphere_override(snap) is None


@pytest.mark.unit
def test_divine_override_returns_None_when_finished(svc: STS2AutoplayService) -> None:
    """★ 小游戏结束后不该再揭格子（那时该走 proceed）。"""
    assert svc._crystal_sphere_override(_divine_snap(finished=True)) is None


@pytest.mark.unit
def test_divine_override_returns_None_on_normal_screens(
    svc: STS2AutoplayService,
) -> None:
    """★ 不误伤：常规事件屏 / 战斗 / 无 `crystal_sphere` 层时一律不管。"""
    normal_event = _snap(
        screen="EVENT", unknown="",
        event={"event_id": "BIG_FISH", "options": [{"index": 0, "title": "x"}]},
        actions=("save_and_quit", "choose_event_option"),
    )
    assert svc._crystal_sphere_override(normal_event) is None
    combat = _snap(screen="COMBAT", unknown="", actions=("end_turn", "play_card"))
    assert svc._crystal_sphere_override(combat) is None


@pytest.mark.unit
def test_divine_override_never_emits_destructive_action(
    svc: STS2AutoplayService,
) -> None:
    """★ 铁律：占卜路径**只**能发 `divine_crystal_sphere`，绝不许发毁局动作。"""
    op = svc._crystal_sphere_override(_divine_snap())
    assert op["action_type"] not in svc.NON_PROGRESSING_ACTIONS
    assert op["action_type"] == "divine_crystal_sphere"


# ------------------------------------------------ 与 t32 降级路径的关系（防自相矛盾）


@pytest.mark.unit
def test_crystal_sphere_screen_not_flagged_when_sphere_layer_active(
    svc: STS2AutoplayService,
) -> None:
    """★★ t33 关键：**`crystal_sphere.active` 为真时不算"推不动"**。

    即使某一版 mod 只补了层的读法、还没把动作挂进册，也不许喊"卡住了" ——
    否则会出现最坏的自相矛盾：屏上明明能占卜，插件却在报卡死。
    """
    snap = _divine_snap(actions=("save_and_quit", "discard_potion"))  # 故意不带动作
    assert svc._crystal_sphere_screen(snap) is None, (
        "★★ 有 crystal_sphere 层却仍判'推不动' ⇒ 自相矛盾"
    )


@pytest.mark.unit
def test_crystal_sphere_screen_still_flagged_without_sphere_layer(
    svc: STS2AutoplayService,
) -> None:
    """★ t32 的门必须仍然成立：完全没有 `crystal_sphere` 层时照旧判"推不动"。"""
    snap = _snap(unknown=CRYSTAL_SPHERE, event=None, actions=("save_and_quit",))
    diag = svc._crystal_sphere_screen(snap)
    assert diag is not None, "★ 无 crystal_sphere 层 ⇒ t32 降级路径必须仍然生效"


@pytest.mark.unit
def test_divine_wins_over_degradation_in_tick(svc: STS2AutoplayService) -> None:
    """★ 修复后的屏上，`_note_no_operation` 不该再被触发到。

    这是"占卜可处理"与"不再空转"两条的**接缝**：override 产出了动作 ⇒ 不再走降级。
    """
    snap = _divine_snap()
    op = svc._crystal_sphere_override(snap)
    assert op is not None, "★ 有动作可发时不走降级"
    # 反过来：降级判定也必须认为这一屏可推进（双保险，见 _crystal_sphere_screen 的注释）
    assert svc._crystal_sphere_screen(snap) is None


@pytest.mark.unit
def test_divine_override_first_person_no_system_wording(
    svc: STS2AutoplayService,
) -> None:
    """★ 第一人称：占卜日志里不许出现「插件/系统/上游」这类转达字眼。"""
    import io
    import logging

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    logger = logging.getLogger("t33.divine.firstperson")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    svc.logger = logger
    svc._crystal_sphere_override(_divine_snap())
    text = buf.getvalue()
    assert text, "★ 占卜这一手必须留一句可读日志"
    for banned in ("插件", "系统", "上游", "mod ", "mod侧"):
        assert banned not in text, f"★ 不许出现转达字眼 {banned!r}：{text!r}"
    assert "这一手" in text, f"★ 应是第一人称口吻，实际 {text!r}"


# ======================================================================================
# t33 补：队长 ②「能力缺口」—— 枚举要给**有哪些可能**（坐标清单），不只是**有多少**
# ======================================================================================


@pytest.mark.unit
def test_divine_override_uses_candidates_not_just_count(
    svc: STS2AutoplayService,
) -> None:
    """★★ 有 `candidates` 时必须用它（而不是 `cells_hidden` 计数）定上界。

    ★ 构造一个**两者不等**的局面：`cells_hidden` 说 100，但 `candidates` 只给了 3 块。
      合格实现必须以 candidates 为准（只有 3 块真能点）；
      若误用 cells_hidden，序号会跑到 3..99 ⇒ mod 判 invalid_target。
    """
    snap = _divine_snap(hidden=100, candidates=[{"x": 1, "y": 0}, {"x": 2, "y": 0}, {"x": 3, "y": 0}])
    seen = [svc._crystal_sphere_override(snap)["kwargs"]["option_index"] for _ in range(5)]
    assert seen == [0, 1, 2, 0, 1], (
        f"★★ 上界必须取自 candidates(3) 而非 cells_hidden(100)，实际 {seen}"
    )


@pytest.mark.unit
def test_divine_override_falls_back_to_count_without_candidates(
    svc: STS2AutoplayService,
) -> None:
    """★ 兼容：mod 没给 `candidates`（旧版本）时，退回用 `cells_hidden` 计数，不许崩。"""
    snap = _divine_snap(hidden=4, candidates=[])
    seen = [svc._crystal_sphere_override(snap)["kwargs"]["option_index"] for _ in range(5)]
    assert seen == [0, 1, 2, 3, 0], f"★ 应退回计数上界 4，实际 {seen}"


@pytest.mark.unit
def test_divine_log_names_the_coordinate(svc: STS2AutoplayService) -> None:
    """★ 报告要能读出"揭的是哪一块" —— 日志须带坐标，而不只是"第几块"。

    这正是「数量无法支撑决策」的另一面：只有序号时，人（和后续分析）
    无法判断它揭到哪了；有坐标才能复盘。
    """
    import io
    import logging

    buf = io.StringIO()
    logger = logging.getLogger("t33.divine.coord")
    logger.handlers = [logging.StreamHandler(buf)]
    logger.setLevel(logging.INFO)
    svc.logger = logger
    snap = _divine_snap(candidates=[{"x": 5, "y": 7}, {"x": 6, "y": 7}])
    svc._crystal_sphere_override(snap)
    text = buf.getvalue()
    assert "5" in text and "7" in text, f"★ 日志须含坐标 (5,7)，实际 {text!r}"


@pytest.mark.unit
def test_divine_override_tolerates_malformed_candidates(
    svc: STS2AutoplayService,
) -> None:
    """★ 稳健性：`candidates` 里混入非 dict 项不许抛（mod 形状变了也不能把自动玩弄停）。"""
    op = svc._crystal_sphere_override(_divine_snap(candidates=["bad", None, 3]))
    assert op is not None, "★ 畸形 candidates 应被容忍，不该让占卜停摆"
    assert isinstance(op["kwargs"]["option_index"], int)


@pytest.mark.unit
def test_divine_override_zero_candidates_falls_back_not_crash(
    svc: STS2AutoplayService,
) -> None:
    """★ 边界：`candidates` 为空列表且 `cells_hidden` 也是 0 ⇒ 退回无上界，仍出动作。"""
    snap = _divine_snap(hidden=0, candidates=[])
    op = svc._crystal_sphere_override(snap)
    assert op is not None
    assert op["kwargs"]["option_index"] == 0


