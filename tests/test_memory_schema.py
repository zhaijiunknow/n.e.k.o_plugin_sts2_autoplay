"""Storage migration is lazy; unsupported documents are never overwritten."""
import json

import pytest
from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore


@pytest.mark.parametrize("version", [None, 0, 1])
def test_legacy_and_current_roundtrip(tmp_path, version):
    path = tmp_path / "memory.json"
    data = {"runs": [{"run_id": "old", "custom_run_field": 5}], "custom_root_field": {"keep": True}}
    if version is not None:
        data["schema_version"] = version
    original = json.dumps(data).encode()
    path.write_bytes(original)
    store = CatgirlMemoryStore(str(path))
    assert path.read_bytes() == original  # loading never migrates on disk
    store.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1
    assert saved["runs"] == data["runs"]
    assert saved["custom_root_field"] == data["custom_root_field"]
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("version", [2, 99, -1, "1", True, None, {}, []])
def test_unknown_or_invalid_schema_is_read_only(tmp_path, version):
    path = tmp_path / "memory.json"
    original = json.dumps({"schema_version": version, "runs": [{"run_id": "future"}], "new_data": [1, 2]}).encode()
    path.write_bytes(original)
    store = CatgirlMemoryStore(str(path))
    store.runs.append({"run_id": "in-memory"})
    store.save()
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_future_schema_with_trailing_corruption_is_not_downgraded(tmp_path):
    path = tmp_path / "memory.json"
    original = '{"schema_version":99,"runs":[{"run_id":"future"}]} trailing'
    path.write_text(original, encoding="utf-8")
    store = CatgirlMemoryStore(str(path))
    assert store.runs[0]["run_id"] == "future"
    store.save()
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("raw", [b"not json", b"\xff\xfe", b"null", b"[]"])
def test_unreadable_or_invalid_document_not_overwritten(tmp_path, raw):
    path = tmp_path / "memory.json"
    path.write_bytes(raw)
    store = CatgirlMemoryStore(str(path))
    store.save()
    assert path.read_bytes() == raw


def test_explicit_reload_after_repair_unblocks_writes(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text('{"schema_version":99}', encoding="utf-8")
    store = CatgirlMemoryStore(str(path))
    path.write_text('{"runs":[{"run_id":"repaired"}]}', encoding="utf-8")
    store.load()
    store.save()
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
