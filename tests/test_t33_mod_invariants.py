# -*- coding: utf-8 -*-
r"""t33：mod 侧（C#）**源码级不变量**测试。

★ 为什么不是"跑 C# 单测"：`game_mod/nekospire` 没有测试工程（csproj 只产 DLL），
  而本次改动是 `GameStateService` 的**私有静态方法**，从外部无法调用。
  ⇒ 我用**源码级断言**把不变量钉住。这是诚实的降级：它证明不了"运行起来对"，
    但能证明"以后谁改坏了结构会被立刻发现"。
  ★ 报告里必须如实标注这一点，不能包装成"跑过单测"。

跑法：
    $env:PYTHONPATH="D:\NekoClaw\N.E.K.O"
    & "D:\NekoClaw\N.E.K.O\.venv\Scripts\python.exe" -m pytest tests/test_t33_mod_invariants.py -q
"""
from __future__ import annotations

import pathlib
import re

import pytest


#: mod 源码根（repo，不是交付副本）。
#: ★ 不能写死 `D:\NekoClaw\...`：CI 是 Linux，那个路径永远不存在，
#:   而这里没有 exists() 守卫，会直接 FileNotFoundError ⇒ 用例失败而非跳过。
#:   改成从本文件向上找含 `game_mod/nekospire` 的仓库根，可移植且能在 CI 上真跑。
def _find_repo_root() -> pathlib.Path | None:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / "game_mod" / "nekospire").is_dir():
            return parent
    return None


_REPO_ROOT = _find_repo_root()
_GS = _REPO_ROOT / "game_mod/nekospire/Game/GameStateService.cs" if _REPO_ROOT else None
_GA = _REPO_ROOT / "game_mod/nekospire/Game/GameActionService.cs" if _REPO_ROOT else None


def _src() -> str:
    """读 mod 源码；仓库根找不到时**在测试内**跳过（模块级 skip 会变成 collection error）。"""
    if _GS is None:  # pragma: no cover
        pytest.skip("找不到 game_mod/nekospire（只装了插件产物）⇒ 无法核对 mod 源码")
    return _GS.read_bytes().decode("utf-8")


# ======================================================================================
# 不变量 1：两处守卫必须**同源**（队长 ③ 的核心约束）
# ======================================================================================


@pytest.mark.unit
def test_both_event_guards_share_one_predicate() -> None:
    """★★ `CanChooseEventOption` 与 `BuildEventPayload` 必须都调 `IsEventScreen`。

    ★ 这就是防「屏名说能处理、动作守卫说不能」的自相矛盾 ——
      只改一处会比"两处都不改"更难查，因为现象与代码看起来一致。
    """
    s = _src()
    assert s.count("if (!IsEventScreen(currentScreen))") == 2, (
        "★ 必须恰好两处调用 IsEventScreen（CanChooseEventOption + BuildEventPayload）"
    )
    # 旧写法的守卫必须**彻底消失**（否则就是"改了一处、漏了一处"）
    assert "currentScreen is not NEventRoom" not in s, (
        "★ 不得残留 `currentScreen is not NEventRoom` 守卫"
    )


@pytest.mark.unit
def test_is_event_screen_is_single_definition() -> None:
    """★ `IsEventScreen` 只能有一处定义（防止有人再抄一份各自演化）。"""
    s = _src()
    assert len(re.findall(r"private static bool IsEventScreen\(", s)) == 1


@pytest.mark.unit
def test_is_event_screen_keeps_nevroom_true() -> None:
    """★★ 队长 ④：等价性 —— `IsEventScreen` 对 `NEventRoom` 必须返回 **true**。

    这是本改动**最容易被忽略的回归点**：抽公共函数时把 NEventRoom 漏了，
    常规事件屏会全部退化。源码级钉住。
    """
    s = _src()
    m = re.search(
        r"private static bool IsEventScreen\(IScreenContext\? currentScreen\)\s*\{(.*?)\n    \}",
        s, re.S,
    )
    assert m, "★ 找不到 IsEventScreen 定义"
    body = m.group(1)
    assert "currentScreen is NEventRoom" in body, "★★ 必须首先判 NEventRoom ⇒ true"
    assert re.search(r"if \(currentScreen is NEventRoom\)\s*\{\s*return true;", body), (
        "★★ NEventRoom 必须 return true（本改动最易漏的等价点）"
    )


@pytest.mark.unit
def test_is_event_screen_still_restricts_to_event_fallback() -> None:
    """★ 兜底路径必须**只**认 EVENT（不许把 REST/SHOP/CHEST 也当事件屏）。

    这条对应变异体 M33-D：把 `== "EVENT"` 放宽成"非 null" ⇒ 立即红。
    """
    s = _src()
    m = re.search(
        r"private static bool IsEventScreen\(IScreenContext\? currentScreen\)\s*\{(.*?)\n    \}",
        s, re.S,
    )
    body = m.group(1)
    assert 'ResolveScreenFromStaticRoom() == "EVENT"' in body, (
        '★ 兜底必须仍限定 == "EVENT"'
    )


@pytest.mark.unit
def test_is_event_screen_defers_to_resolve_non_modal_screen() -> None:
    """★ 兜底只在**屏类型认不出**时启用 —— 与 `ResolveScreen` 的时序一致。

    否则：一个已知的 REST 屏若静态房间是 EventRoom，就会被误判成事件屏。
    """
    s = _src()
    m = re.search(
        r"private static bool IsEventScreen\(IScreenContext\? currentScreen\)\s*\{(.*?)\n    \}",
        s, re.S,
    )
    body = m.group(1)
    assert 'ResolveNonModalScreen(currentScreen) != "UNKNOWN"' in body, (
        "★ 必须先确认 ResolveNonModalScreen 认不出，才吃静态房间兜底"
    )


@pytest.mark.unit
def test_event_room_fallback_still_maps_to_event() -> None:
    """★ `ResolveScreenFromStaticRoom` 的 `EventRoom => "EVENT"` 必须仍在。

    它是 t33 的地基：没有它，水晶球屏名不会变成 EVENT，插件也就不会去找事件动作。
    （变异体 M33-A：改掉它 ⇒ 立即红）
    """
    s = _src()
    assert '                EventRoom => "EVENT",' in s, (
        '★ ResolveScreenFromStaticRoom 必须仍把 EventRoom 兜底成 "EVENT"'
    )


@pytest.mark.unit
def test_divine_progress_wait_watches_real_progress_not_child_count() -> None:
    """★★ 进度判定必须看**真进度**（DivinationCount / IsHidden），不能只看 child count。

    ★ 原来那条 `GetParent().GetChildCount()` **永远不变**：格子在游戏过程中不增删
      （官方 handler 之所以能靠"可点格数变化"，是因为它**逐个剔除**已揭的格子；
       我们不动节点 ⇒ 那条路对我不适用）⇒ 即使真揭开了也会被判 `pending`（假阴性）。
    ⇒ 本测试钉住：必须观察 DivinationCount 与 IsHidden。
    """
    ga = _GA.read_bytes().decode("utf-8")
    m = re.search(r"private static async Task<bool> WaitForCrystalSphereProgressAsync\((.*?)\n    \}", ga, re.S)
    assert m, "★ 找不到 WaitForCrystalSphereProgressAsync"
    body = m.group(1)
    assert "DivinationCount" in body, "★★ 必须看 DivinationCount（CellClicked 里会 -1）"
    assert "IsHidden" in body, "★★ 必须看 IsHidden 翻转"
    assert "GetChildCount" not in body, (
        "★★ 不许再靠 GetChildCount 判进度（它恒定 ⇒ 假阴性）"
    )


# ======================================================================================
# 不变量 3：`IsEventScreen` 的等价性（队长 ④ 点名要求的回归点）
# ======================================================================================


@pytest.mark.unit
def test_crystal_sphere_candidates_use_row_major_like_cells() -> None:
    """★ `candidates` 必须按 `cells[,]` 的行优先顺序收集（插件按序号对齐）。

    ★ 但**序号解释权在 mod**：`CollectClickableCells` 走的是**场景树节点顺序**，
      与 `cells[,]` 的行优先顺序**未必一致** ⇒ mod 必须在日志里报出"真正点了哪一格"，
      不能让插件靠顺序猜（否则 candidates[i] 与 option_index=i 会错位）。
    """
    s = _src()
    ga = _GA.read_bytes().decode("utf-8")
    assert "candidates.Add(new CrystalSphereCellPayload" in s, "★ candidates 必须实际收集"
    assert "clicked=(" in ga, (
        "★★ mod 必须报出被点格子的真实坐标（序号↔坐标的权威映射），"
        "否则插件只能靠顺序猜，存在错位风险"
    )


@pytest.mark.unit
def test_crystal_sphere_payload_exposes_candidates() -> None:
    """★★ 队长 ② 的能力缺口：必须输出**可点候选的坐标清单**。

    ★ 只有 `cells_hidden` 数字时，插件无法决定"揭哪块"，只能瞎点 ——
      与 CANON §23「枚举要给**有哪些可能**，不只是**有多少**」同源。
    """
    s = _src()
    assert "candidates = candidates.ToArray()" in s, (
        "★★ 必须把 candidates 接进返回（不许只声明字段不赋值）"
    )
    assert "public CrystalSphereCellPayload[] candidates" in s, (
        "★ payload 类型必须有 candidates 字段"
    )
    # 隐藏格分支必须真的往里加（否则 candidates 恒空）
    assert "candidates.Add(new CrystalSphereCellPayload" in s, (
        "★ 隐藏格分支必须实际收集坐标"
    )


@pytest.mark.unit
def test_no_dead_variable_in_crystal_sphere_payload() -> None:
    """★ 死变量已清除（队长 ③）。

    `occupiedHiddenCount` 原来只自增、从未进 return，且名字与语义相反
    （加在"未隐藏"分支）。这与 t25 的 `require_no_new_key` 死形参同形态。
    """
    s = _src()
    # ⚠️ 不能只写 `"occupiedHiddenCount" not in s` —— 我在删掉它的地方留了**注释说明**
    #    （就是文档里那段"原来有个 occupiedHiddenCount++"），于是文本匹配会命中注释 ⇒ 假红。
    #    ★ 这正是"断言要针对代码、不要针对文本"的一般教训：
    #      必须匹配**代码形态**（`occupiedHiddenCount++` / `= 0`），不能匹配光秃秃的标识符。
    code_lines = [
        ln for ln in s.splitlines()
        if not ln.lstrip().startswith("//")
    ]
    code = "\n".join(code_lines)
    assert not re.search(r"\boccupiedHiddenCount\s*(\+\+|--|=)", code), (
        "★ 不许留'看起来实现了、实则没接到'的变量（CANON §22.4 E1）"
    )


@pytest.mark.unit
def test_divine_action_registered_in_both_places() -> None:
    """★ `divine_crystal_sphere` 必须在**处理表**与**可用动作清单**两处都挂上。

    只挂一处 = 要么动作永远不出现在 `/state`（插件看不见），
    要么插件发了却被判 `invalid_action`（409）。
    """
    s = _src()
    ga = _GA.read_bytes().decode("utf-8")
    assert 'names.Add("divine_crystal_sphere")' in s, "★ 可用动作清单里必须加"
    assert '"divine_crystal_sphere" => ExecuteDivineCrystalSphereAsync(request)' in ga, (
        "★ 处理表里必须加"
    )


@pytest.mark.unit
def test_divine_uses_official_forceclick_not_raw_signal() -> None:
    """★★ 官方自动化是 `ForceClick()`，**不是**单发 `Released` 信号。

    ★ 这是 t33 里我**读错 IL 后改正**的地方，实测证据（2026-10-20 07:03，
      run C6CM8CZRR9KQ / act1 / floor23）：
      · 只发 `EmitSignal(NClickableControl.SignalName.Released)` ⇒ 序号 0..96/102 全部
        HTTP 200 但 `cells_hidden` 恒 97、`divinations_left` 恒 3，连发 5 次 + 等 10 秒无变化；
      · 反编译 `NClickableControl.ForceClick()` = `OnRelease()` + `EmitSignal(Released)`；
      · 而官方 `AutoSlay.Helpers.UiHelper.Click` 的 `<Click>d__0.MoveNext` 只有
        `callvirt ForceClick` + `call Task.Delay`。
    ⇒ 必须用 `ForceClick()`。这条测试把该结论钉死在源码不变量上。
    """
    ga = _GA.read_bytes().decode("utf-8")
    assert "target.ForceClick();" in ga, "★★ 必须调 ForceClick（官方同款两步点击）"
    # 不许再出现"只发信号"那行（它已被证明无效）
    assert "target.EmitSignal(NClickableControl.SignalName.Released)" not in ga, (
        "★★ 不许退回「只发 Released 信号」—— 实测无效，会永久卡死该屏"
    )
    assert '%Cells' in ga, "★ 必须用官方同款具名节点路径 %Cells"


@pytest.mark.unit
def test_crystal_sphere_layer_is_wired_into_state_payload() -> None:
    """★ 层必须真的出现在 `/state` 顶层（否则插件永远读不到）。"""
    s = _src()
    assert "crystal_sphere = crystalSphere," in s, "★ 必须挂进 GameStatePayload"
    assert "public CrystalSpherePayload? crystal_sphere { get; init; }" in s, (
        "★ payload 类型必须有该字段"
    )
    # 必须在 BuildAvailableActionNames **之前**算，否则动作清单拿不到它
    i_cs = s.index("var crystalSphere = BuildCrystalSpherePayload(currentScreen);")
    i_act = s.index("var availableActions = BuildAvailableActionNames(")
    assert i_cs < i_act, "★★ crystalSphere 必须在 availableActions 之前算（否则动作清单为空）"


# ======================================================================================
# 不变量 4：D10 的 mod 侧根因（`BuildMainMenuPayload` 的 NRE）不许复活
# ======================================================================================


def _code_only(src: str) -> str:
    """去掉 `//` 行注释 —— ★ 断言必须针对**代码**，不能针对文本。

    ★ 这个坑我踩过两次：把坏写法改掉后又在**原地留了注释说明**
      （"原来是 `RunManager.Instance.NetService...`"），于是文本断言命中注释 ⇒ 假红。
      ⇒ 凡是"某坏写法不许出现"的断言，都必须先剥注释。
    """
    return "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("//")
    )


def _method_body(src: str, signature: str) -> str:
    r"""取出 `signature` 开头那个方法的**完整方法体**（按大括号配平，不用正则贪婪）。

    ★ 为什么不用 `re.search(r"...\{(.*?)^\s*\}", re.M)`：那样会在**第一个行首 `}`**
      就停 —— 而方法体里常有嵌套的 `if { ... }`，第一个行首 `}` 往往是内层的
      ⇒ 拿到的 body 被截断，断言变成假红（我踩过一次）。
    """
    i = src.find(signature)
    assert i >= 0, f"★ 找不到方法签名：{signature}"
    # 从签名后的第一个 `{` 开始配平
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j + 1:k]
    raise AssertionError(f"★ 方法体未闭合：{signature}")


@pytest.mark.unit
def test_main_menu_payload_does_not_naked_deref_net_service() -> None:
    """★★★ `BuildMainMenuPayload` 里`RunManager.Instance.NetService.Type` 的 NRE 已修，不许复活。

    ★ 这是 **D10 的 mod 侧根因**（t26 只做了插件侧降级绕过，mod 侧从未修）：
      现象：MAIN_MENU 出现约 9 秒后 `/state` **永久 500**，`/health` 与 `/actions/available` 仍 200
      （它们不经过本方法）⇒ 自动游玩彻底失明。

    ★ 实机证据（2026-10-20，_t33_state500.log 与 godot.log）：
      · 原版 DLL 917C76F6C3F9E66A：/state 在 t=9s 起 0/20 全 500
      · 诊断定位：`RunManager.Instance null=False  NetService null=True  SubmenuStack null=False`
        ⇒ ★ **就是 `NetService`**（sts2.dll 把它标注成 `NotNull`，**标注是错的** ——
          该字段只在 `InitializeShared` 里赋值，主菜单早期尚未跑）
      · 修复后（EDB0285E261F9B78）：越过危险窗 5/5 成功，MAIN_MENU 下 27 个顶层键
    """
    s = _src()
    # ⚠️ 不能全文查 —— 同文件 :195 / :1206 等处**仍有**裸解引用（它们在别的屏才走到，
    #   目前没有实测到 NRE）。本测试只钉 `BuildMainMenuPayload` 这一处
    #   （★ 也是唯一实测到崩溃的那一处）。
    body = _code_only(_method_body(s, "private static MainMenuPayload? BuildMainMenuPayload("))
    assert "RunManager.Instance.NetService.Type.IsMultiplayer()" not in body, (
        "★★★ BuildMainMenuPayload 不许裸解引用 NetService（MAIN_MENU 下必 NRE ⇒ /state 永久 500）"
    )
    assert "is_multiplayer_run = IsMultiplayerRunSafe()," in body, (
        "★ BuildMainMenuPayload 必须走安全判定"
    )
    assert "private static bool IsMultiplayerRunSafe()" in s, "★ 安全判定必须存在"
    # 安全判定内必须逐级判空 + 兜底
    body = _method_body(s, "private static bool IsMultiplayerRunSafe(")
    assert "RunManager.Instance?.NetService" in body, "★★ 必须用 ?. 逐步判空"
    assert "return false;" in body, "★ 任一级缺失都当单人（不许抛）"


@pytest.mark.unit
def test_main_menu_payload_keeps_other_guards() -> None:
    """★ 修 NRE 时不许把其它字段的判空一起改掉（回归点）。"""
    s = _src()
    body = _method_body(s, "private static MainMenuPayload? BuildMainMenuPayload(")
    for needed in (
        "mainMenu.SubmenuStack?.SubmenusOpen",
        "CanContinueRun(currentScreen)",
        "CanAbandonRun(currentScreen)",
        "CanOpenMultiplayerMenu(currentScreen)",
        "CanSaveAndQuit(currentScreen, runState)",
        "is_multiplayer_run = IsMultiplayerRunSafe()",
    ):
        assert needed in body, f"★ 不许丢失既有字段/判空：{needed}"
