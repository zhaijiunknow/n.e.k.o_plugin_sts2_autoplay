"""Strict sidecar content alignment without importing torch into the host interpreter."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("policy_sidecar_under_test", ROOT / "policy_sidecar" / "alphadefect_server.py")
sidecar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sidecar)


def _net(names=("a", "b", "c")):
    return SimpleNamespace(content_names=names, cfg=SimpleNamespace(vocab_size=3000, content_enchant=True, content_upgrade=True))


def _blob(width=2, **kw):
    return {"state": {"content_proj.weight": SimpleNamespace(shape=(192, width))}, **kw}


def test_legacy_features_resolved_by_pinned_training_version_not_width():
    resolve = Mock(return_value=("b", "a"))
    names = sidecar.validated_content_names(_net(), _blob(), {"content_catalog": "v0.111.0"}, resolve)
    assert names == ("b", "a")
    resolve.assert_called_once_with(3000, "v0.111.0", True, True)


def test_equal_width_still_resolves_names_not_positional_fallback():
    resolve = Mock(return_value=("b", "a"))
    assert sidecar.validated_content_names(_net(("a", "b")), _blob(), {"content_catalog": "old"}, resolve) == ("b", "a")
    resolve.assert_called_once()


def test_saved_names_do_not_need_legacy_catalog():
    resolve = Mock(side_effect=AssertionError("must use checkpoint names"))
    assert sidecar.validated_content_names(_net(), _blob(content_names=["b", "a"]), {}, resolve) == ("b", "a")


@pytest.mark.parametrize("names", [[], ["a"], ["a", "a"], ["a", None], "ab", ["x", "y"]])
def test_invalid_saved_names_fail_closed(names):
    with pytest.raises(sidecar.ContentAlignmentError):
        sidecar.validated_content_names(_net(), _blob(content_names=names), {}, Mock())


def test_no_training_version_cannot_silently_pad():
    with pytest.raises(sidecar.ContentAlignmentError, match="拒绝按位置补零"):
        sidecar.validated_content_names(_net(), _blob(), {}, Mock())


def test_wrong_training_catalog_width_cannot_silently_truncate():
    with pytest.raises(sidecar.ContentAlignmentError, match="列数不匹配"):
        sidecar.validated_content_names(_net(), _blob(), {"content_catalog": "old"}, Mock(return_value=("a", "b", "c")))


def test_network_without_content_prior_supported():
    assert sidecar.validated_content_names(SimpleNamespace(), {"state": {}}, {}, Mock()) is None


@pytest.mark.parametrize("missing", ["weight", "network_names"])
def test_content_structure_mismatch_rejected(missing):
    net, blob = _net(), _blob()
    if missing == "weight":
        blob["state"] = {}
    else:
        del net.content_names
    with pytest.raises(sidecar.ContentAlignmentError, match="结构不一致"):
        sidecar.validated_content_names(net, blob, {}, Mock())


def _catalog_policies(tmp_path):
    data = b'{"game_version":"v0.111.0"}'
    item = {"kind": "catalog", "dest": "engine/catalog/v0.111.0/catalog.json", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    p = sidecar.Policies.__new__(sidecar.Policies)
    p.root, p.manifest = tmp_path, {"runtime": [item]}
    path = tmp_path / item["dest"]
    path.parent.mkdir(parents=True)
    return p, path, data


def test_catalog_requires_exact_hash_not_just_size(tmp_path):
    p, path, data = _catalog_policies(tmp_path)
    with pytest.raises(sidecar.ContentAlignmentError, match="缺少"):
        p._verify_catalog("v0.111.0")
    path.write_bytes(b'x' * len(data))
    with pytest.raises(sidecar.ContentAlignmentError, match="校验失败"):
        p._verify_catalog("v0.111.0")
    path.write_bytes(data)
    p._verify_catalog("v0.111.0")
    with pytest.raises(sidecar.ContentAlignmentError, match="清单没有固定"):
        p._verify_catalog("v9.9.9")


def test_release_manifest_pins_every_models_training_catalog():
    manifest = json.loads((ROOT / "policy_manifest.json").read_text(encoding="utf8"))
    files = {s.get("dest"): s for s in manifest["runtime"] if s["kind"] == "catalog"}
    old = files["engine/catalog/v0.111.0/catalog.json"]
    assert old["size"] == 572346
    assert old["sha256"] == "fda991c75d9cd9efc10194d564d74a15a2cc3fcbaaf44ab3147b29ef0d084b69"
    for character, spec in manifest["models"].items():
        version = spec["content_catalog"]
        assert version == ("v0.111.0" if character in ("SILENT", "DEFECT", "REGENT") else "v0.113.1")
        catalog = files[f"engine/catalog/{version}/catalog.json"]
        assert len(catalog["urls"]) >= 2
        assert all(manifest["upstream"]["commit"] in url for url in catalog["urls"])


@pytest.mark.parametrize("failure", [False, True])
def test_health_attests_alignment_only_after_successful_load(failure):
    policies = SimpleNamespace(manifest={"upstream": {"game_version": "v0.114.0", "commit": "test"}}, available=Mock(return_value={}), obs_format=Mock(return_value=23))
    if failure:
        policies.obs_format.side_effect = sidecar.ContentAlignmentError("missing legacy catalog")
    handler_class = sidecar.make_handler(policies)
    handler = handler_class.__new__(handler_class)
    handler.path = "/health?character=SILENT"
    handler._send = Mock()
    handler.do_GET()
    code, reply = handler._send.call_args.args
    assert code == 200
    if failure:
        assert reply["ok"] is False and "missing legacy catalog" in reply["error"]
        assert "content_alignment_version" not in reply
    else:
        assert reply["ok"] is True and reply["obs_format"] == 23
        assert reply["content_alignment_version"] == 1
    policies.obs_format.assert_called_once_with("SILENT")
