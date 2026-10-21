"""面向本体的提示词不得预设"观众"。

背景：这几处提示词原本写的是「用你自己的口吻**跟观众**说一句」。那是留给**后续直播插件联动**
的措辞 —— 届时由直播插件提供听众人设。当前插件是**单人陪伴**场景，叫观众会让猫娘对空气喊话。

本测试钉住两件事：

* 三类主动投递（开局开场 / 局末复盘 / 局势速报）的提示词里**不含**朝向听众的称呼；
* 但投递本身**仍然发生**（不能为了删词把功能一起删掉）。

将来接直播插件时，若要让猫娘重新称呼听众，应当由直播侧提供人设、或在这里显式加回并同步更新本测试。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_AVOID_WORDS = ("观众", "audience", "viewers")

# 插件源码根（tests/ 的上一级）。
_ROOT = Path(__file__).resolve().parents[1]


def _source(name: str) -> str:
    return (_ROOT / name).read_text(encoding="utf-8")


@pytest.mark.unit
def test_no_viewer_address_in_host_prompts() -> None:
    """service.py 发给本体的**提示词**不得含观众称呼。

    只查真正会被发出去的字符串，不查注释与文档 —— 注释里出现"观众"是有意的设计说明
    （在解释"为什么现在不这么说"），把它判红就成了假阳性。

    做法：用 AST 取出所有字符串字面量，再排除文档字符串（docstring）。
    """
    import ast

    text = _source("service.py")
    tree = ast.parse(text)

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings:
            continue  # 文档说明不算提示词
        if any(word in node.value for word in _AVOID_WORDS):
            offenders.append(f"service.py:{node.lineno}: {node.value[:80]!r}")
    assert not offenders, "发给本体的提示词不应再出现朝向听众的称呼:\n" + "\n".join(offenders)


@pytest.mark.unit
def test_no_viewer_address_in_entry_texts() -> None:
    """入口描述（用户/LLM 可见文案）同样不得提观众。"""
    import ast

    text = _source("__init__.py")
    tree = ast.parse(text)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    offenders = [
        f"__init__.py:{node.lineno}: {node.value[:80]!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and any(word in node.value for word in _AVOID_WORDS)
    ]
    assert not offenders, "入口文案不应再出现朝向听众的称呼:\n" + "\n".join(offenders)


@pytest.mark.unit
def test_livestream_intent_is_documented() -> None:
    """删词不能删掉意图：源码里要留下"这是留给直播联动"的说明，免得后人以为可以随便加回。"""
    text = _source("service.py")
    assert "直播" in text, "应保留'直播联动'的注记，说明观众称呼是后续计划"


@pytest.mark.unit
def test_host_pushes_still_happen(monkeypatch) -> None:
    """功能没被删掉：局势速报仍然会向本体投递（只是措辞变了）。"""
    from plugin.plugins.sts2_autoplay.service import STS2AutoplayService

    class _Logger:
        def info(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

    service = STS2AutoplayService(_Logger(), lambda p: None, lambda **k: None)
    pushed: list[str] = []
    monkeypatch.setattr(service, "_push_proactive_to_host", lambda text, **kw: pushed.append(text))

    class _Notifier:
        pass

    service._frontend_notifier = _Notifier()
    service._danmaku_enabled = True
    service._state.host_digest_enabled = True
    snapshot = {"screen": "COMBAT", "floor": 3}
    payload = {
        "summary_kind": "combat",
        "screen": "COMBAT",
        "player": {"current_hp": 40, "max_hp": 80},
        "enemies": [{"name": "史莱姆"}],
    }
    service._push_host_situation_digest(snapshot, payload)

    assert pushed, "局势速报必须仍然投递（不能为了删词把功能删了）"
    joined = " ".join(pushed)
    for word in _AVOID_WORDS:
        assert word not in joined, f"投递内容不应出现 {word!r}: {joined}"
