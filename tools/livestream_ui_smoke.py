"""Live controls browser check with intercepted local assets and a mock host only.

Run with the host Python: tools/livestream_ui_smoke.py
Requires Playwright; never connects to the actual plugin, game, or livestream.
"""
from __future__ import annotations

import json
import mimetypes
from copy import deepcopy
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://livestream-ui.test"


def main():
    live = {"enabled": False, "profile": "casual", "wait_scale": 1,
            "waiting": False, "remaining_seconds": 0}
    tasks, calls, errors = {}, [], []
    output = ROOT / "dist" / "livestream-ui-review"
    output.mkdir(parents=True, exist_ok=True)

    def intercept(route):
        path = route.request.url.removeprefix(ORIGIN).split("?")[0]

        def reply(data):
            route.fulfill(content_type="application/json", body=json.dumps(data))

        if not route.request.url.startswith(ORIGIN + "/"):
            route.abort()
        elif path == "/runs":
            request = route.request.post_data_json
            calls.append(request)
            entry = request["entry_id"]
            if entry == "sts2_set_livestream":
                live.update(request["args"])
                result = {"summary": "设置已保存（模拟宿主）", "saved": True,
                          "mod_sync": {"applied": True}, "livestream": deepcopy(live)}
            else:
                assert entry == "sts2_get_status", request
                result = {"livestream": deepcopy(live), "map_policy": {"policy": "auto", "vote_wait_seconds": 20}}
            key = str(len(tasks) + 1)
            tasks[key] = result
            reply({"run_id": key})
        elif path.startswith("/runs/"):
            key = path.split("/")[2]
            reply({"items": [{"json": {"data": tasks[key]}}]} if path.endswith("/export") else {"status": "succeeded"})
        else:
            asset = (ROOT / "static" / path.lstrip("/")).resolve()
            if asset.is_relative_to((ROOT / "static").resolve()) and asset.is_file():
                route.fulfill(path=str(asset), content_type=mimetypes.guess_type(str(asset))[0] or "application/octet-stream")
            else:
                route.fulfill(status=404)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.route("**/*", intercept)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(ORIGIN + "/sts2.html")
            expect(page.locator("#livestream-save")).to_be_enabled()
            expect(page.locator("#livestream-enabled")).to_have_value("false")
            page.locator("#livestream-enabled").select_option("true")
            page.locator("#livestream-profile").select_option("chaotic")
            page.locator("#livestream-scale").fill("1.5")
            # Refresh must not overwrite unsaved edits.
            page.locator('[data-entry="sts2_get_status"]').first.click()
            expect(page.locator("#livestream-save")).to_be_enabled()
            expect(page.locator("#livestream-profile")).to_have_value("chaotic")
            page.locator("#livestream-save").click()
            expect(page.locator("#feedback")).to_contain_text("设置已保存")
            saved = [call for call in calls if call["entry_id"] == "sts2_set_livestream"]
            assert len(saved) == 1
            assert saved[0]["args"] == {"enabled": True, "profile": "chaotic", "wait_scale": 1.5}
            page.reload()
            expect(page.locator("#livestream-profile")).to_have_value("chaotic")
            expect(page.locator("#livestream-scale")).to_have_value("1.5")
            live.update(waiting=True, remaining_seconds=12)
            page.locator('[data-entry="sts2_get_status"]').first.click()
            expect(page.locator("#livestream-state")).to_contain_text("12 秒")
            for width in (1440, 768, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                page.locator("#livestream-form").scroll_into_view_if_needed()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(output / f"mock-live-{width}.png"), full_page=True)
            page.locator("#livestream-scale").fill("4")
            assert not page.locator("#livestream-scale").evaluate("el => el.checkValidity()")
            assert not errors, errors
        finally:
            browser.close()
    print("PASS: mocked live settings load/edit/refresh/save/reload, validation, desktop/tablet/mobile; no live actions.")


if __name__ == "__main__":
    main()
