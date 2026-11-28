"""Offline contracts for the shared UI and its progress snapshots.

Actual interaction/layout checks live in tools/ui_smoke.py (mock host only).
"""
from __future__ import annotations

import json
import re
import tomllib
from fnmatch import fnmatchcase
from html.parser import HTMLParser
from pathlib import Path

import pytest
from plugin.plugins.sts2_autoplay import policy_assets

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = []
        self.resources = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        key = "src" if tag in ("img", "script") else "href" if tag == "link" else None
        if key and attrs.get(key):
            self.resources.append(attrs[key])


@pytest.mark.parametrize("name", ["index", "policy", "sts2", "history", "settings"])
def test_page_resources_are_local_and_present(name):
    page = Page((STATIC / f"{name}.html").read_text(encoding="utf-8"))
    assert len(page.ids) == len(set(page.ids)), "Duplicate DOM ids"
    assert "masthead" in page.ids
    assert {"ui.js", "ui.css"} <= {resource.split("?")[0] for resource in page.resources}
    assert all("?v=" in resource for resource in page.resources if resource.split("?")[0].endswith((".js", ".css")))
    for resource in page.resources:
        assert not resource.startswith(("http:", "https:", "//"))
        path = (STATIC / resource.split("?")[0]).resolve()
        assert path.is_relative_to(STATIC.resolve()) and path.is_file()


@pytest.mark.parametrize("name", ["policy", "sts2", "settings"])
def test_controller_dom_targets_exist(name):
    page = Page((STATIC / f"{name}.html").read_text(encoding="utf-8"))
    script = (STATIC / f"{name}.js").read_text(encoding="utf-8")
    targets = set(re.findall(r"\$\(['\"]([^'\"]+)['\"]\)", script))
    assert targets <= set(page.ids), targets - set(page.ids)


def test_game_art_is_optional_and_excluded_from_distribution():
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"local-art"' in config
    assert "static/local-art/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert not list((STATIC / "assets").glob("*.webp"))
    css = (STATIC / "ui.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css
    assert "gradient" in css
    script = (STATIC / "policy.js").read_text(encoding="utf-8")
    assert "img.hidden=true" in script
    assert "character-copy" in script
    assert "local-art/" in (STATIC / "ui.js").read_text(encoding="utf-8")


def test_entry_and_file_snapshots_share_host_timestamp(tmp_path, monkeypatch):
    assets = policy_assets.PolicyAssets(root=tmp_path / "runtime")
    assets.status_path = tmp_path / "policy_status.json"
    monkeypatch.setattr(policy_assets, "time", lambda: 100.0)
    assets.publish()
    published = json.loads(assets.status_path.read_text(encoding="utf-8"))
    assert published["updated_at"] == 100.0
    monkeypatch.setattr(policy_assets, "time", lambda: 101.0)
    direct = assets.status()
    assert direct["updated_at"] > published["updated_at"]
    assert direct["models"] == published["models"]
    assert not (tmp_path / "runtime").exists(), "Reading UI state must not install anything"


def test_map_configuration_lives_only_on_settings_page():
    control = (STATIC / "sts2.html").read_text(encoding="utf-8")
    controller = (STATIC / "sts2.js").read_text(encoding="utf-8")
    settings = (STATIC / "settings.html").read_text(encoding="utf-8")
    assert "联机地图协作" not in control
    assert "map-form" not in Page(control).ids
    assert "sts2_set_map_policy" not in controller
    assert "联机地图协作" in settings
    assert "map-form" in Page(settings).ids
    assert "['settings','参数配置']" in (STATIC / "ui.js").read_text(encoding="utf-8")


def test_settings_inventory_is_visible_before_api_loads():
    from plugin.plugins.sts2_autoplay.settings_config import GROUPS

    page = Page((STATIC / "settings.html").read_text(encoding="utf-8"))
    assert {"preferences-load-state", "preferences-retry", "settings-map"} <= set(page.ids)
    assert {"settings-" + group for group in GROUPS} <= set(page.ids)


def test_navigation_and_asset_versions_match():
    script = (STATIC / "ui.js").read_text(encoding="utf-8")
    revision = re.search(r"const uiRevision='([^']+)'", script).group(1)
    assert '.html?v=${uiRevision}' in script
    for name in ("index", "policy", "sts2", "history", "settings"):
        page = Page((STATIC / f"{name}.html").read_text(encoding="utf-8"))
        for resource in page.resources:
            if resource.split("?")[0].endswith((".js", ".css")):
                assert resource.endswith("?v=" + revision)


def test_coop_ui_separates_create_and_resume_without_autoplay():
    html = (STATIC / "sts2.html").read_text(encoding="utf-8")
    assert '新建联机房间' in html and '继续联机存档' in html
    assert '"resume":true' in html and '"resume":false' in html
    assert 'data-confirm=' not in html
    assert '替换原多人进度' in html
    js = (STATIC / "sts2.js").read_text(encoding="utf-8")
    assert "catgirl_adoption?.adopted" in js
    assert "button.dataset.confirm" not in js
    shared = (STATIC / "ui.js").read_text(encoding="utf-8")
    assert "entryId === 'sts2_open_coop_room' ? 120000 : 45000" in shared


def test_runtime_snapshots_and_logs_are_excluded_from_distribution():
    rules = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["neko"]["build"]
    for name in ("autoplay_history.json", "control_status.json", "policy_status.json", "mod-uploader.log"):
        assert any(fnmatchcase(name, pattern) for pattern in rules["exclude_files"]), name
