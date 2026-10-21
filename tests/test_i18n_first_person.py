"""★第一人称口吻契约（i18n A/B 层）。

## 背景

玩家两次明确口径：

> 「整个插件都需要是第一人称，注意一下包裹关系」
> 「猫娘点评不能出现**插件说**这种转达的字眼，插件就是猫娘的**反射弧**，
>   没人会把反射作为第三者吧」

而改造前 A 层（`companion.*`）与 B 层（`summary.*`）大量使用**旁白口吻**：
"已开启陪玩模式。"、"**建议**优先防御或找减伤线。"、"**提醒：**当前血量偏低"、
"**当前战斗状态**，**玩家**血量…" —— 全是"有个第三方在向玩家转述"的句式。

## 分层判据（唯一一条）

> **这段字是「猫娘对玩家说」，还是「宿主在对玩家描述猫娘」？**

| 层 | 前缀 | 谁在说 | 人称 |
|---|---|---|---|
| **A** | `companion.*` | 猫娘本人 | **第一人称「我」/对玩家「你」** |
| **B** | `summary.*` | 猫娘说她看到的东西 | **第一人称「我」** |
| C | `entries.*` `plugin.*` `tools.*` `errors.*` `status.*` `neko.*` `messages.*` `sync.*` `action_engine.*` `advise.*` `autoplay.*` `coop.*` `danmaku.*` | 宿主在报告猫娘做了什么 | **第三人称/中性（保持）** |

**C 层为什么不改**：那是宿主界面的字（插件名、工具描述、错误码、偏好读写结果）。
玩家在那里看到的**不是猫娘在说话**，而是宿主在报告"猫娘这个插件做了什么"。
改成第一人称会荒谬 —— 错误信息写「我没找到偏好」会让玩家以为宿生在自称。

## 本文件钉住什么

1. **A/B 层不得出现旁白词**（建议 / 提醒： / 玩家 / 已开启 / 已关闭 / 当前位于…）
2. **A 层必须有第一人称视角**（我 / 你）
3. **C 层刻意保持旁白口吻**（防止有人"顺手统一"把宿主界面也改掉）
4. **JSON 与代码内联 default 必须一致**（否则 JSON 命中就盖掉了 default，改一边等于没改）
5. **8 个 locale 键集合必须一致**（本次改造暴露的既有缺口，钉住防劣化）
"""

from __future__ import annotations

import ast
import json
import re
import sys
import unicodedata
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PLUGIN_ROOT.parent.parent.parent
_I18N = _PLUGIN_ROOT / "i18n"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

LOCALES = ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW")

#: ★ A/B 层**不得出现**的旁白词。
#:
#: 每一项都对应改造前真实存在过的措辞（不是假想）：
#:   建议      ← "建议优先防御或找减伤线。"
#:   提醒：    ← "提醒：当前血量偏低…"
#:   玩家      ← "当前战斗状态，玩家血量 18/80。"
#:   已开启/已关闭 ← "已开启陪玩模式。"
#:   当前位于  ← "当前位于 MAP，phase=select…"
BANNED_NARRATION = (
    "建议",
    "提醒：",
    "玩家",
    "已开启",
    "已关闭",
    "当前位于",
    "当前战斗状态",
    "当前地图选择状态",
    "当前奖励状态",
    "当前商店状态",
    "当前休息点状态",
    "当前卡牌选择状态",
)

#: 英文侧的对应旁白词（各 locale 存的是翻译过的文本，必须分别查）。
BANNED_NARRATION_EN = (
    "Companion mode",       # "Companion mode enabled." —— 无主语的系统通知
    "current strategy",     # 旁白
    "Suggestion:",
    "Reminder:",
    "The player",
    "Current ",
    "Advice:",
)

#: ★ 刻意保持旁白口吻的层（**改这些是错的**）。
NARRATION_LAYERS = (
    "entries", "plugin", "tools", "errors", "status", "neko",
    "messages", "sync", "action_engine", "advise", "autoplay", "coop", "danmaku",
)

#: ★ 无语义人称的键（纯数值/中性短语），不参与"必须第一人称"的断言。
PERSONLESS_KEYS = {
    "summary.delta.block", "summary.delta.energy", "summary.delta.gold",
    "summary.delta.player_hp", "summary.delta.enemy_hp",
    "summary.delta.enemy_attack", "summary.actions.none",
    "summary.combat.enemy_default", "summary.combat.no_hand",
    "summary.combat.no_enemy", "summary.event.unknown",
    "summary.menu.no_character", "summary.menu.can_embark",
    "summary.menu.cannot_embark",
    # 变化量描述：主语是局势变化，不是猫娘
    "summary.delta.screen_change", "summary.delta.entered_cards",
    "summary.delta.left_cards",
}

#: ★ 死键：JSON 里还在、但代码**零引用**。
#: 它们是上一轮"战斗建议改走纯求解器"时被弃用的（现由 `companion.solver.*` 取代），
#: 保留在 JSON 里没清理，所以文本仍是旧口吻 —— 这不是改造遗漏。
DEAD_KEYS = {
    "companion.suggestion.combat_named_with_target",
    "companion.suggestion.combat_named_without_target",
}


def _bundle(locale: str) -> dict[str, str]:
    path = _I18N / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


#: 匹配 `{placeholder}`（只认小写+下划线，与 `interpolate_text` 的用法一致）。
#:
#: ★ 为什么需要它：`interpolate_text` 按**字面名字**匹配。
#:   译文里把 `{item_name}` 写成 `{itemName}` 或漏掉 ⇒ 输出**原样留下花括号**
#:   （`neko.preference.extracted` 的 `{domain}/{key}` 就是这个形态）。
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _code_files() -> list[Path]:
    out = []
    for path in _PLUGIN_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        out.append(path)
    return out


# ======================================================================================
# 1. A/B 层不得有旁白词
# ======================================================================================
@pytest.mark.unit
@pytest.mark.parametrize("locale", LOCALES)
def test_ab_layer_has_no_narration_words(locale: str) -> None:
    """★A/B 层不得出现旁白词 —— 这是"猫娘是唯一主体"的硬约束。

    改造前实例（都是真实存在过的文本）：

        companion.enabled        "已开启陪玩模式。"
        companion.reminder.low_hp "提醒：当前血量偏低…"
        summary.combat           "当前战斗状态，玩家血量 18/80。"
        summary.general          "当前位于 MAP，phase=select…"

    这些句子里**没有猫娘**，只有一个旁白在向玩家转述 ——
    正是玩家说的「插件就是猫娘的反射弧，没人会把反射作为第三者吧」。
    """
    bundle = _bundle(locale)
    marks = BANNED_NARRATION + (BANNED_NARRATION_EN if locale == "en" else ())
    offenders: list[str] = []
    for key, text in bundle.items():
        if not (key.startswith("companion.") or key.startswith("summary.")):
            continue
        if key in PERSONLESS_KEYS or key in DEAD_KEYS:
            continue
        for mark in marks:
            if mark in str(text):
                offenders.append(f"{key}: {mark!r} in {text!r}")
                break
    assert not offenders, (
        f"[{locale}] A/B 层出现旁白口吻（应改为猫娘第一人称）：\n  "
        + "\n  ".join(offenders)
    )


#: ★ **片段键**：它们不独立成句，而是被 `str.format` 嵌进**外面那句**。
#:
#: 为什么要单列：对它们要求"必须含我/你"是**错的** ——
#: 人称由宿主句提供，片段再带一次就重复了。实测：
#:
#:   `companion.focus.combat` = "我想先看生存、格挡和怪物意图"  ← 独立句，带人称
#:   嵌进 `companion.eval.template` = "{prefix}，按 {strategy_name} 策略**我想** {focus}。"
#:   ⇒ 若 focus 里再带"我想"，会得到"…策略我想我想先看…"（重复）
#:
#: 所以本测试对片段键只要求"**不得是旁白口吻**"，不要求自带人称。
EMBEDDED_KEYS = {
    # 被 eval.template 的 "…策略我想 {focus}。" 嵌入 ⇒ 片段必须无人称
    "companion.focus.combat", "companion.focus.reward", "companion.focus.selection",
    "companion.focus.map", "companion.focus.event", "companion.focus.shop",
    "companion.focus.rest", "companion.focus.general",
    # 状态标签（"当前局势偏危险"），也是被拼进 template 的
    "companion.eval.high", "companion.eval.medium",
    "companion.eval.low", "companion.eval.default",
    # 是"这回合我打『X』"这句话的**理由从句**，主语在外层
    "companion.suggestion.combat_reason.lethal",
    "companion.suggestion.combat_reason.block_gap",
    "companion.suggestion.combat_reason.target_pressure",
    "companion.suggestion.combat_reason.damage_value",
    "companion.suggestion.combat_reason.block_value",
    "companion.suggestion.combat_reason.generic",
    # 包装器：把内层 template 再套一层
    "companion.eval.combat_turn", "companion.eval.scene_entry",
    "companion.eval.post_action",
    "companion.eval.template",
    # 转述玩家刚做的事（"从这一步结果看，…"），主语因上下文而定
    "companion.suggestion.post_action",
    # 观察/评论片段
    "companion.commentary.combat_pressure",
    "companion.commentary.combat_low_hp",
    "companion.commentary.combat_general",
    "companion.eval.player_operation",
}


@pytest.mark.unit
def test_ab_layer_has_first_person_viewpoint() -> None:
    """★A 层**独立成句**的键必须有第一/第二人称。

    ⚠️ 反面教训 1：只断言"不含某词"是**可被空文本骗过**的，
      所以这里同时要求"含我/你"。

    ⚠️ 反面教训 2：**不能对所有 A 层键都要求人称** —— 有一批是**被嵌入的片段**
       （见 `EMBEDDED_KEYS`）。对它们要求人称会让句子重复人称
       （"…策略我想我想先看…"）。这是**我第一版写错了**，
       `EMBEDDED_KEYS` 就是那次失败留下的修正。

    ⚠️ 覆盖来源必须**不止 JSON**：实测 A 层有 12 个键
       （`companion.focus.*` 8 个 + `companion.suggestion.player_operation.*` 4 个）
       **在 JSON 里完全没有条目**，只存在于代码内联 default。
       只查 JSON 的测试会**假绿**，漏掉它们。
    """
    bundle = _bundle("zh-CN")
    keys: dict[str, str] = {}
    for key, text in bundle.items():
        if key.startswith("companion.") and key not in DEAD_KEYS:
            keys[key] = str(text)

    # 代码内联 default 侧（覆盖 JSON 里没有的键）
    for path in _code_files():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"""self\.t\(\s*["'](companion\.[a-z0-9_.]+)["']\s*,\s*default="([^"]*)\"""", text
        ):
            key, default_text = match.group(1), match.group(2)
            if key not in DEAD_KEYS:
                keys.setdefault(key, default_text)

    assert len(keys) >= 30, f"只收集到 {len(keys)} 个 A 层键，覆盖不足"

    offenders = [
        f"{key}: {text!r}"
        for key, text in sorted(keys.items())
        if key not in EMBEDDED_KEYS and "我" not in text and "你" not in text
    ]
    assert not offenders, (
        "A 层独立句缺第一/第二人称：\n  " + "\n  ".join(offenders)
    )
    # 片段键反过来：**不该**自带人称（否则和外层重复）
    doubled = [
        f"{key}: {text!r}"
        for key, text in sorted(keys.items())
        if key in EMBEDDED_KEYS and ("我想" in text or "我在" in text)
    ]
    assert not doubled, (
        "A 层片段键自带人称，会与外层句子重复：\n  " + "\n  ".join(doubled)
    )


@pytest.mark.unit
def test_summary_layer_uses_first_person() -> None:
    """★B 层（情况速报）也必须是猫娘自述「我在…」。

    改造前：`summary.combat` = "当前战斗状态，玩家血量 {hp}…"
    改造后：`summary.combat` = "我正在战斗，我的血量 {hp}…"
    """
    bundle = _bundle("zh-CN")
    checked = 0
    for key, text in bundle.items():
        if not key.startswith("summary."):
            continue
        if key in PERSONLESS_KEYS or key in ("summary.combat.enemy_line",):
            continue
        assert "我" in str(text), f"B 层 {key} 缺第一人称，实际：{text!r}"
        checked += 1
    assert checked >= 8, f"B 层只检查到 {checked} 个键，覆盖不足"


# ======================================================================================
# 2. C 层**刻意保持**旁白口吻（防止被顺手"统一"掉）
# ======================================================================================
@pytest.mark.unit
def test_c_layer_keeps_narration_voice_on_purpose() -> None:
    """★C 层（宿主后台）**必须保持**旁白口吻 —— 改成第一人称是错的。

    判据：`entries.*` / `tools.*` / `plugin.*` 这些是**宿主界面**的字，
    玩家看到的是宿主在**描述猫娘这个插件**，不是猫娘在说话。

    实测锚点（改造期间差点被全局替换误伤的实例）：

        neko_interface.py:218
            self.t("neko.strategy_context", default="当前策略上下文：{strategy_name}。")

    它和 A 层某键的中文片段**同为"当前策略"**。
    若做全局字符串替换，就会把这个 C 层键一起改掉。
    """
    bundle = _bundle("zh-CN")
    # C 层必须仍然存在（说明没被删/没被改名）
    for key in ("plugin.name", "tools.sts2_get_status.description", "neko.strategy_context"):
        assert key in bundle, f"C 层键 {key} 不该消失"

    # ★ C 层不得被第一人称改写。
    #   ⚠️ 这里**只查"宿主在报告"的那几类键**，不查 plugin.name ——
    #      插件名是"杀戮尖塔 2 陪玩"这种**名词短语**，本来就无人称，
    #      拿它断言"必须含 sts2/N.E.K.O"是**我第一版写错了**（名词没有这个要求）。
    for key in ("neko.strategy_context", "neko.preference.fetched",
                "neko.planned_operation.current", "status.running"):
        text = str(bundle.get(key, ""))
        assert "我" not in text or "插件" in text, (
            f"C 层 {key} 不该是猫娘第一人称（宿主工具返回），实际：{text!r}"
        )


@pytest.mark.unit
def test_neko_interface_default_is_untouched() -> None:
    """★★ 用**源码级**断言钉住那个差点被误伤的 C 层 default。

    只查 JSON 不够：`neko_interface.py` 的内联 default 是另一份拷贝，
    而且是**全局字符串替换最容易误伤**的地方。
    """
    text = (_PLUGIN_ROOT / "neko_interface.py").read_text(encoding="utf-8")
    assert 'default="当前策略上下文：{strategy_name}。"' in text, (
        "neko_interface.py 的 C 层 default 被改动（它是宿主工具返回，必须保持中性）"
    )


# ======================================================================================
# 3. JSON 与代码内联 default 必须一致
# ======================================================================================
@pytest.mark.unit
def test_json_and_inline_default_agree_on_placeholders() -> None:
    """★★ 占位符必须一致 —— 否则运行时少一个变量或多一个 KeyError。

    `I18n.t()` 的优先级是 **JSON > default**（`plugin/sdk/shared/i18n.py:96-103`）：

        value = bundle.get(normalized_key)
        if isinstance(value, str):
            return interpolate_text(value, params)     # JSON 命中就返回
        return interpolate_text(default or key, params)  # 没命中才用 default

    ⇒ JSON 存在就用 JSON。若两边占位符不同，**改 JSON 的人以为生效了，
       实际另一条路径会炸**。本次改造正是靠这条查出 91 处配对。
    """
    call_re = re.compile(
        r"""self\.t\(\s*["']([a-z0-9_.]+)["']\s*,\s*default=(".*?"|'.*?')""", re.S
    )
    bundle = _bundle("zh-CN")
    mismatches: list[str] = []
    checked = 0
    for path in _code_files():
        text = path.read_text(encoding="utf-8")
        for match in call_re.finditer(text):
            key, raw = match.group(1), match.group(2)
            if key not in bundle:
                continue
            try:
                default_text = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                continue
            checked += 1
            p_code = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", default_text))
            p_json = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", str(bundle[key])))
            if p_code != p_json:
                mismatches.append(f"{path.name} {key}: 代码{sorted(p_code)} vs JSON{sorted(p_json)}")
    assert checked > 50, f"只配对到 {checked} 处，覆盖不足（正则可能失效）"
    assert not mismatches, "占位符不一致：\n  " + "\n  ".join(mismatches)


@pytest.mark.unit
def test_ab_layer_code_defaults_are_also_first_person() -> None:
    """★★ 代码内联 default 也必须第一人称 —— 只改 JSON 等于没改。

    这是本次改造**最容易被漏掉**的点：A 层有 27 个键**不在 JSON 里**，
    全靠内联 default 兜底。只改 JSON 的人会以为做完了，实际运行时仍是旧口吻。
    （实测：`companion.suggestion.combat_value` 等 3 个战斗兜底键
      第一轮就漏了，是**实渲染探针**跑出来才发现的。）
    """
    bundle = _bundle("zh-CN")
    offenders: list[str] = []
    checked = 0
    for path in _code_files():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"""self\.t\(\s*["'](companion\.[a-z0-9_.]+)["']\s*,\s*default="([^"]*)\"""", text):
            key, default_text = match.group(1), match.group(2)
            if key in DEAD_KEYS:
                continue
            checked += 1
            for mark in ("建议", "提醒：", "玩家"):
                if mark in default_text:
                    offenders.append(f"{path.name} {key}: {mark!r} in {default_text!r}")
                    break
    assert checked >= 20, f"只检查到 {checked} 处 A 层 default，覆盖不足"
    assert not offenders, (
        "A 层代码内联 default 仍是旁白口吻：\n  " + "\n  ".join(offenders)
    )
    # 同时确认 JSON 侧也一致（两边都不能漏）
    for key in ("companion.suggestion.map", "companion.suggestion.shop",
                "companion.suggestion.general", "companion.reminder.low_hp"):
        assert "建议" not in str(bundle.get(key, "")), f"JSON {key} 仍是旁白口吻"


# ======================================================================================
# 4. 8 个 locale 键集合一致
# ======================================================================================
@pytest.mark.unit
@pytest.mark.parametrize("locale", [x for x in LOCALES if x != "zh-CN"])
def test_locale_key_sets_match_zh_cn(locale: str) -> None:
    """★各 locale 键集合必须与 zh-CN **完全一致**（缺口 0）。

    ⚠️ 历史（已修）：这曾是**既有缺口** —— en 缺 6 个、es/ja/ko/pt/ru 各缺 19 个、
       zh-TW 缺 17 个，合计 **118** 个。缺口来自新增的 `entries.*` / `tools.*`
       条目当时只写了 zh-CN。

    修法：见 `.dsh-artifacts/_fill_i18n_gaps.py`（25 个键 × 各缺失 locale）。
    按既定口径分层翻译：`companion.*` / `advise.*` / `messages.*` 用第一人称，
    `entries.*` / `tools.*` / `action_engine.*` 保持第三人称。

    ★ 现在这条测试**不再允许任何缺口** —— 之前的"只许缩小、不许扩大"
      基线是个临时措施，缺口清零后它就该升级成硬约束，
      否则下一个漏翻的键会悄悄通过。
    """
    base = set(_bundle("zh-CN"))
    keys = set(_bundle(locale))
    missing = base - keys
    extra = keys - base
    assert not extra, f"[{locale}] 有 zh-CN 没有的键：{sorted(extra)[:8]}"
    assert not missing, (
        f"[{locale}] 缺 {len(missing)} 个键（基线已清零，不许再有缺口）：{sorted(missing)[:8]}"
    )


@pytest.mark.unit
def test_all_locales_are_valid_utf8_json() -> None:
    """★8 个 locale 都必须是**合法 UTF-8 JSON**（防止合并冲突残留 / BOM 问题）。"""
    for locale in LOCALES:
        path = _I18N / f"{locale}.json"
        assert path.is_file(), f"缺少 {path.name}"
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} 带 BOM"
        data = json.loads(raw.decode("utf-8"))
        assert isinstance(data, dict), f"{path.name} 顶层不是对象"
        # 不得有控制字符/未规范化字符混入
        for key, value in data.items():
            assert isinstance(value, str), f"{path.name} 的 {key} 不是字符串"
            assert unicodedata.normalize("NFC", value) == value, (
                f"{path.name} 的 {key} 含未规范化字符"
            )


# ======================================================================================
# 5. ★补键不得破坏占位符 / 分层口径
# ======================================================================================
@pytest.mark.unit
def test_placeholders_match_across_locales() -> None:
    """★★ 同一键在各 locale 的**占位符集合必须与 zh-CN 完全一致**。

    为什么这条至关重要：`interpolate_text` 按**字面名字**匹配占位符。

      * 改了名字（`{domain}` → `{pref_domain}`，或漏掉 `{item_name}`）
        ⇒ 输出里**原样留下** `{domain}` / `{item_name}`
        ⇒ 玩家看到花括号。这正是本轮修掉的
          `neko.preference.extracted` 的 `{domain}/{key}` 缺陷形态。
      * 多了占位符 ⇒ 插值拿到 None，输出 `None`。

    ⚠️ 新增 118 条手写译文时，这是最容易被破坏的不变量，
      所以必须由测试而不是"我检查过了"来保证。
    """
    zh = _bundle("zh-CN")
    problems: list[str] = []
    for key, template in zh.items():
        want = set(_PLACEHOLDER.findall(template))
        if not want:
            continue
        for locale in LOCALES:
            if locale == "zh-CN":
                continue
            text = _bundle(locale).get(key)
            if text is None:
                continue
            got = set(_PLACEHOLDER.findall(text))
            if got != want:
                problems.append(
                    f"[{locale}] {key}: {sorted(got)} != zh-CN {sorted(want)}"
                )
    assert not problems, "占位符不一致（会导致输出裸花括号）：\n  " + "\n  ".join(problems[:12])


@pytest.mark.unit
def test_newly_filled_a_layer_keys_stay_first_person() -> None:
    """★★ 补的 A/B 层译文必须保持**第一人称**（猫娘自己在说话）。

    分层口径（与 i18n 第一人称改造同一把尺）：
      * A 层 `companion.*` / `advise.*` —— 猫娘对玩家说 ⇒ 第一人称
      * C 层 `entries.*` / `tools.*` / `action_engine.*` / **`messages.*`**
        —— 宿主描述猫娘 / 动作回执 ⇒ 第三人称

    ⚠️ 这里只覆盖**本次补的** A 层键（含英文的 6 个 `*_named`），
      不重测全量 —— 全量口径由本文件上面的 `PERSONLESS_KEYS` 等契约负责。

    ⚠️ `messages.set_instance.done` **不在**这里 —— 它归 C 层，见下一条测试。
      我第一版把它当 B 层（以为"话是猫娘说的"），但实测证明那是**想当然**：
      同族 `messages.*` 全是无人称「已…」回执，且该键 `grep` **零命中**（陈旧键）。
      是测试当场转红逼我回去查证的。
    """
    a_layer = (
        "companion.suggestion.event_named",
        "companion.suggestion.map_named",
        "companion.suggestion.reward_named",
        "companion.suggestion.selection_named",
        "companion.suggestion.shop_card_named",
        "companion.suggestion.shop_relic_named",
        "advise.holding",
    )
    #: 各 locale 的**第一人称标记**（**大小写不敏感**匹配）。
    #:
    #: ★★ 教训（踩了两次，都是断言写得太天真）：
    #:
    #: 坑 1：只列代词（es "yo "/pt "eu "/ru "я "）⇒ 误报一堆既有译文。
    #:   实测这些 locale 的第一人称**经常省略代词**，人称由动词变位承载：
    #:     es `quiero` / `prefiero` / `elegiré`；pt `quero` / `prefiro` / `vou`；
    #:     ru `выберу` / `хочу`；ja 「〜したいな」「〜するよ」（**不出现** わたし／私）。
    #:
    #: 坑 2：区分大小写 ⇒ `Quiero {item_name} de la tienda.`（句首大写）被误判。
    #:   ⇒ 改成 `.lower()` 后再比。
    #:
    #: ★ 这张表的判据不是"有没有某个词"，而是"这句话的人称是不是第一人称"。
    #:   所以它按**语言真实的形态**写，不按英语的形态写。
    first_person: dict[str, tuple[str, ...]] = {
        "en": ("i ", "i'", "me ", "my ", "listening", "holding", "changed"),
        "es": ("yo ", "me ", "mi ", "quiero", "prefiero", "elegir", "cambi"),
        "ja": ("わたし", "ぼく", "私", "俺", "したい", "するよ", "選ぶ", "欲しい", "待ち", "切り替え"),
        "ko": ("나", "내", "저", "할게", "원해", "싶어", "택할래", "고를게", "바꿨", "들을게"),
        "pt": ("eu ", "me ", "minha", "quero", "prefiro", "escolher", "troquei"),
        "ru": ("я ", "мне", "меня", "моя", "выберу", "хочу", "убр", "переключила"),
        "zh-CN": ("我",),
        "zh-TW": ("我",),
    }
    problems: list[str] = []
    for key in a_layer:
        for locale in LOCALES:
            text = _bundle(locale).get(key)
            if text is None:
                problems.append(f"[{locale}] {key} 缺译文")
                continue
            marks = first_person[locale]
            if not any(mark in text.lower() for mark in marks):
                problems.append(
                    f"[{locale}] {key} 没有第一人称标记 {marks}：{text[:70]}"
                )
    assert not problems, "A/B 层译文丢了第一人称：\n  " + "\n  ".join(problems[:12])


@pytest.mark.unit
def test_newly_filled_c_layer_keys_stay_third_person() -> None:
    """★补的 C 层译文**不得**变成第一人称（宿主在描述猫娘，不是她的话）。

    ⚠️ 方向与上一条相反。C 层若写成第一人称，会违反既定契约：
      `entries.*` / `tools.*` 是"给玩家/模型看的说明书"。
    """
    c_layer = (
        "action_engine.executed_action",
        "messages.set_instance.done",
        "action_engine.no_legal_action",
        "entries.sts2_continue_run.name",
        "entries.sts2_continue_run.description",
        "entries.sts2_set_control_mode.name",
        "entries.sts2_set_control_mode.description",
        "entries.sts2_set_danmaku.name",
        "entries.sts2_set_danmaku.description",
        "entries.sts2_set_instance.name",
        "entries.sts2_set_instance.description",
        "entries.sts2_set_play_style.name",
        "entries.sts2_set_play_style.description",
        "entries.sts2_set_route.name",
        "entries.sts2_set_route.description",
        "tools.sts2_set_control_mode.description",
        "tools.sts2_set_play_style.description",
        "tools.sts2_set_route.description",
    )
    #: C 层**开头不该**是第一人称（句中出现的"我"可能是引用玩家原话，如
    #: 「玩家让你自己玩」里的引号内容，所以只查句首）
    bad_starts = {
        "en": ("I ", "I'"),
        "es": ("Yo ", "Me "),
        "ja": ("わたし", "私", "ぼく"),
        "ko": ("나 ", "저 ", "내 "),
        "pt": ("Eu ", "Me "),
        "ru": ("Я ", "Мне"),
        "zh-CN": ("我",),
        "zh-TW": ("我",),
    }
    problems: list[str] = []
    for key in c_layer:
        for locale in LOCALES:
            text = _bundle(locale).get(key)
            if text is None:
                problems.append(f"[{locale}] {key} 缺译文")
                continue
            if text.startswith(bad_starts[locale]):
                problems.append(
                    f"[{locale}] {key} 句首是第一人称（C 层应第三人称）：{text[:60]}"
                )
    assert not problems, "C 层译文误用第一人称：\n  " + "\n  ".join(problems[:12])
