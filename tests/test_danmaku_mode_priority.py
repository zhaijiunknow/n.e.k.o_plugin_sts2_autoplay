"""★ 弹幕优先级回归门（玩家口径 2026-10-19）

玩家裁决（原话）：
    「autoplay 优先，coop」＋「coop模式>autoplay>陪玩」

★ 为什么需要这组门（这是被玩家指出的真实缺陷）：
  老实现是「陪玩 if 在前，autoplay elif 在后」⇒ **陪玩一开就压过 autoplay**
  ⇒ 自动游玩时弹幕反而**开着**，与玩家指令「自动游玩的时候关闭弹幕」**正好相反**。
  实机证据：0 条 `[sts2_danmaku_policy]` 日志（因为 want==current 直接 return）
  ＋ 93 条 `pushed catgirl danmu` ⇒ 弹幕确实没被关掉。

★ 优先级（从高到低，先命中者胜）：
    autoplay（含压过 coop） > coop > 陪玩
  - autoplay 在跑          ⇒ **关**（没人看屏幕，弹幕刷出去没人读）
  - coop 且没在 autoplay   ⇒ **开**（联机要让同伴看见猫娘点评）
  - 只有陪玩               ⇒ **开**
  - 都没有                 ⇒ 不动（`no_policy`）
"""

from __future__ import annotations

from typing import Any

from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class _Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, *args: Any, **kwargs: Any) -> None:
        self.lines.append(" ".join(str(a) for a in args))

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.lines.append(" ".join(str(a) for a in args))

    def error(self, *args: Any, **kwargs: Any) -> None:
        self.lines.append(" ".join(str(a) for a in args))

    def debug(self, *args: Any, **kwargs: Any) -> None:
        self.lines.append(" ".join(str(a) for a in args))


def _build(*, companion: bool, autoplay: str, coop: bool, danmaku: bool = True) -> Any:
    """造一个只带弹幕策略所需状态的服务实例（不启动网络）。"""
    svc = STS2AutoplayService.__new__(STS2AutoplayService)
    svc.logger = _Logger()
    svc._cfg = {"companion_mode_enabled": companion}
    svc._danmaku_enabled = danmaku
    svc._state = type("S", (), {})()
    svc._state.autoplay_state = autoplay
    svc._state.active_instance = "catgirl" if coop else "host"
    svc._coop_room_open = coop
    svc._danmaku_calls = []

    def _set(enabled: bool) -> dict[str, Any]:
        svc._danmaku_calls.append(enabled)
        svc._danmaku_enabled = enabled
        return {"enabled": enabled}

    svc.set_danmaku_enabled = _set  # type: ignore[method-assign]
    return svc


def test_autoplay_beats_companion() -> None:
    """★ 玩家指令的核心：自动游玩时弹幕必须**关**，哪怕陪玩开着。

    这是被玩家指出与指令相反的缺陷 —— 老实现此处会返回 'companion_mode' 并保持开启。
    """
    svc = _build(companion=True, autoplay="running", coop=False, danmaku=True)
    out = svc._apply_mode_danmaku_policy()
    assert out["reason"] == "autoplay", out
    assert out["enabled"] is False, "自动游玩时弹幕必须关闭（压过陪玩）"
    assert svc._danmaku_calls == [False], svc._danmaku_calls


def test_autoplay_beats_coop() -> None:
    """★ autoplay 也压过 coop：自动游玩时不管是不是联机，都关。"""
    svc = _build(companion=True, autoplay="running", coop=True, danmaku=True)
    out = svc._apply_mode_danmaku_policy()
    assert out["reason"] == "autoplay", out
    assert out["enabled"] is False


def test_coop_beats_companion() -> None:
    """coop 高于陪玩：联机且未自动游玩 ⇒ 开（让同伴看见猫娘点评）。"""
    svc = _build(companion=True, autoplay="idle", coop=True, danmaku=False)
    out = svc._apply_mode_danmaku_policy()
    assert out["reason"] == "coop", out
    assert out["enabled"] is True


def test_coop_alone_enables() -> None:
    """只有 coop（没开陪玩）⇒ 也开。"""
    svc = _build(companion=False, autoplay="idle", coop=True, danmaku=False)
    out = svc._apply_mode_danmaku_policy()
    assert out["reason"] == "coop", out
    assert out["enabled"] is True


def test_companion_alone_enables() -> None:
    """只有陪玩 ⇒ 开。"""
    svc = _build(companion=True, autoplay="idle", coop=False, danmaku=False)
    out = svc._apply_mode_danmaku_policy()
    assert out["reason"] == "companion_mode", out
    assert out["enabled"] is True


def test_no_mode_does_not_touch_user_choice() -> None:
    """都没有 ⇒ 不动用户的选择（不产生 set 调用）。"""
    for danmaku in (True, False):
        svc = _build(companion=False, autoplay="idle", coop=False, danmaku=danmaku)
        out = svc._apply_mode_danmaku_policy()
        assert out["changed"] is False and out["reason"] == "no_policy", out
        assert svc._danmaku_calls == [], "没有主动模式时不得改弹幕"


def test_paused_autoplay_still_counts_as_autoplay() -> None:
    """`paused` 仍算 autoplay（暂停不等于交还控制权）⇒ 关。"""
    svc = _build(companion=True, autoplay="paused", coop=False, danmaku=True)
    out = svc._apply_mode_danmaku_policy()
    assert out["reason"] == "autoplay" and out["enabled"] is False, out


def test_idempotent_when_already_correct() -> None:
    """已经是目标值 ⇒ 不重复 set（避免日志刷屏）。"""
    svc = _build(companion=True, autoplay="running", coop=False, danmaku=False)
    out = svc._apply_mode_danmaku_policy()
    assert out["changed"] is False, out
    assert svc._danmaku_calls == [], "值已正确时不应再 set"


def test_coop_detection_uses_room_flag_when_instance_is_host() -> None:
    """★ 保守方向：`active_instance` 停在 host（接管不到猫娘）时，
    `_coop_room_open` 仍应让 coop 判定成立。"""
    svc = _build(companion=False, autoplay="idle", coop=False, danmaku=False)
    svc._state.active_instance = "host"
    svc._coop_room_open = True
    assert svc._is_coop_active() is True
    out = svc._apply_mode_danmaku_policy()
    assert out["reason"] == "coop" and out["enabled"] is True


def test_coop_detection_false_when_neither() -> None:
    """两者都不成立 ⇒ 不是 coop（保守方向，避免单人局凭空开弹幕）。"""
    svc = _build(companion=False, autoplay="idle", coop=False, danmaku=False)
    assert svc._is_coop_active() is False


def test_priority_table_is_exhaustive() -> None:
    """★ 穷举优先级表：把"谁高听谁的"钉死，防止将来又改回 if/elif 顺序。"""
    # (companion, autoplay_running, coop) -> (期望 reason, 期望 enabled or None=不动)
    table = [
        ((False, False, False), ("no_policy", None)),
        ((True, False, False), ("companion_mode", True)),
        ((False, False, True), ("coop", True)),
        ((True, False, True), ("coop", True)),      # coop > 陪玩
        ((False, True, False), ("autoplay", False)),
        ((True, True, False), ("autoplay", False)),  # ★ autoplay > 陪玩（玩家指令）
        ((False, True, True), ("autoplay", False)),  # ★ autoplay > coop
        ((True, True, True), ("autoplay", False)),   # ★ 三者同开也听 autoplay
    ]
    for (companion, running, coop), (want_reason, want_enabled) in table:
        svc = _build(
            companion=companion,
            autoplay="running" if running else "idle",
            coop=coop,
            danmaku=want_enabled is not True,  # 初值取反，确保会真的发生变更
        )
        out = svc._apply_mode_danmaku_policy()
        assert out["reason"] == want_reason, (companion, running, coop, out)
        if want_enabled is None:
            assert svc._danmaku_calls == [], (companion, running, coop, svc._danmaku_calls)
        else:
            assert out["enabled"] is want_enabled, (companion, running, coop, out)
