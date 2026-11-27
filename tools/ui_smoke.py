"""Isolated browser regression checks. Never calls the live plugin or game.

Run: <host-python> tools/ui_smoke.py [--browser <chrome.exe>]
Requires Playwright and a Chromium browser. Screenshots use clearly synthetic
fixtures, written to dist/ui-review; no fixture is shipped in the UI.
"""
import argparse
import copy
import json
import runpy
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'dist' / 'ui-review'
SETTINGS_SCHEMA = runpy.run_path(str(ROOT / 'settings_config.py'))


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def fixtures():
    manifest = json.loads((ROOT / 'policy_manifest.json').read_text(encoding='utf-8'))
    models = []
    labels = ['铁甲战士', '静默猎手', '故障机器人', '储君', '亡灵契约师', '愚者']
    for (character, model), label in zip(manifest['models'].items(), labels):
        models.append(dict(character=character, label=label, file=model['file'], size=model['size'],
                           ready=character in ('IRONCLAD', 'SILENT'), state='idle', done_bytes=0,
                           total_bytes=0, message='', error=''))
    policy = dict(root='C:/Example/N.E.K.O/sts2_autoplay/policy_runtime',
                  runtime=dict(ready=True, state='idle', download_bytes=161547059), models=models)
    runs = []
    for index in range(15):
        runs.append(dict(run_id=f'UI-DEMO-{index:03}', character=models[index % 6]['character'],
                         outcome='victory' if index % 3 == 0 else 'defeat', floor=48 if index % 3 == 0 else 17 + index,
                         ended_at=1795708000 - index * 3600,
                         hp=dict(total_lost=83 + index, loss_events=12, min_hp=11, max_hp=80, final_hp=26),
                         heaviest=[dict(room='精英战斗', lost=24, hp_before=68, hp_after=44)],
                         lessons=['优先保留足够的格挡，再寻找输出机会。'],
                         decisions=[dict(step_count=1, screen='COMBAT', action_type='play_card', reason='先建立防御')],
                         route=[dict(room='普通战斗', floor=i + 1, hp_start=80-i*4, hp_end=76-i*4,
                                     hp_lost=4, cards_gained=['防御'], relics_gained=[], gold_gained=15) for i in range(5)],
                         review=dict(compared=20, agreed=16, explanation='这是一份用于界面验证的模拟复盘，不是真实战绩。',
                                     steps=[dict(floor=6, round=2, chosen='打击', recommended='防御', source='network')])) )
    history = dict(summary=dict(runs=15, victories=5, defeats=10, best_floor=48, total_hp_lost=1350), runs=runs)
    status = dict(server=dict(state='connected'), run=dict(floor=23, screen='COMBAT'),
                  decision_engine=dict(engine='policy',phase='ready',character='IRONCLAD',detail='AlphaDefect 模型已完成本步推理'),
                  autoplay=dict(state='paused', standby=False), companion_mode=dict(enabled=True), danmaku=dict(enabled=False),
                  policy=dict(enabled=True, runtime_ready=True, decisions=286, fallbacks=3, characters=['IRONCLAD', 'SILENT'],
                              last_note='最近决策：COMBAT · play DEFEND'), map_policy=dict(policy='follow', vote_wait_seconds=20))
    status.update(host_digest=dict(enabled=True), run_reflection=dict(enabled=False))
    return policy, history, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT / 'static')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    policy, history, status = fixtures()
    calls, tasks, errors = [], {}, []
    mode = {'not_ready': False, 'history_error': False, 'model_fail': False, 'stale_policy': None, 'preferences_error': False}

    preferences = dict(status='ok', fields=SETTINGS_SCHEMA['FIELDS'], groups=SETTINGS_SCHEMA['GROUPS'],
                       settings={key: spec['default'] for key, spec in SETTINGS_SCHEMA['FIELDS'].items()})

    def route_api(route):
        path = route.request.url.removeprefix(origin).split('?')[0]
        def send(data, code=200):
            route.fulfill(status=code, content_type='application/json', body=json.dumps(data, ensure_ascii=False))
        if path == '/runs' and route.request.method == 'POST':
            request = route.request.post_data_json
            calls.append(request)
            if mode['not_ready']:
                send(dict(error=dict(code='NOT_READY', message='Plugin not running')), 503)
                return
            entry, entry_args = request['entry_id'], request['args']
            if entry == 'sts2_get_settings' and mode['preferences_error']:
                send(dict(error=dict(message='测试：通用参数接口暂不可用')), 503)
                return
            if entry == 'sts2_policy_download_model':
                model = next(m for m in policy['models'] if m['character'] == entry_args['character'])
                model.update(state='failed' if mode['model_fail'] else 'running', total_bytes=model['size'], done_bytes=15000000,
                             error='测试：连接中断，断点保留' if mode['model_fail'] else '')
            if entry == 'sts2_policy_remove_model':
                next(m for m in policy['models'] if m['character'] == entry_args['character']).update(ready=False, state='idle')
            if entry == 'sts2_set_map_policy':
                status['map_policy'] = dict(policy=entry_args['policy'], vote_wait_seconds=entry_args['vote_wait_seconds'])
            if entry in ('sts2_set_host_digest', 'sts2_set_run_reflection'):
                status[entry.removeprefix('sts2_set_')]['enabled'] = entry_args['enabled']
            if entry == 'sts2_start_autoplay':
                status['autoplay']['state'] = 'running'
            if entry == 'sts2_set_danmaku':
                status['danmaku']['enabled'] = entry_args['enabled']
            result = policy if entry.startswith('sts2_policy_') else status if entry == 'sts2_get_status' else dict(message='测试操作已完成', **(status['map_policy'] if entry == 'sts2_set_map_policy' else {}))
            if entry == 'sts2_update_settings':
                preferences['settings'].update(entry_args['patch'])
            if entry in ('sts2_get_settings', 'sts2_update_settings'):
                result = dict(**preferences, summary='参数测试保存完成')
            task_id = str(len(tasks) + 1)
            tasks[task_id] = copy.deepcopy(result)
            if entry.startswith('sts2_policy_'):
                tasks[task_id]['updated_at'] = time.time()
            send(dict(run_id=task_id))
        elif path.startswith('/runs/'):
            task_id = path.split('/')[2]
            send(dict(items=[dict(json=dict(data=tasks[task_id]))]) if path.endswith('/export') else dict(status='succeeded'))
        elif path == '/policy_status.json':
            send(mode['stale_policy'] or dict(**policy, updated_at=time.time()))
        elif path == '/autoplay_history.json':
            send(dict(error='测试：快照不可用'), 500) if mode['history_error'] else send(history)
        else:
            route.continue_()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, **({'executable_path': args.browser} if args.browser else {}))
            context = browser.new_context(viewport=dict(width=1440, height=1040), device_scale_factor=1)
            context.route('**/*', route_api)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/policy.html')
            expect(page.locator('.character')).to_have_count(6)
            expect(page.locator('#model-name')).to_have_text('铁甲战士')
            initial_calls = len(calls)
            page.get_by_role('button', name='储君 · 待下载', exact=True).click()
            expect(page.locator('#model-name')).to_have_text('储君')
            assert len(calls) == initial_calls, 'Selecting a character must not download a model'
            page.get_by_role('button', name='储君 · 待下载', exact=True).hover()
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / 'policy-desktop.png'), full_page=True)
            mode['stale_policy'] = dict(**copy.deepcopy(policy), updated_at=time.time() - 60)
            page.locator('#model-download').click()
            expect(page.locator('#model-download')).to_have_text('正在下载…')
            expect(page.locator('#model-bar')).to_be_visible()
            expect(page.locator('#model-download')).to_be_disabled()
            assert calls[-1]['args'] == dict(character='REGENT')
            # Background polling must not recreate focused character buttons.
            selected = page.locator('.character[aria-expanded=true]')
            selected.focus()
            page.wait_for_timeout(1300)
            expect(selected).to_be_focused()
            expect(page.locator('#model-download')).to_have_text('正在下载…')
            mode['stale_policy'] = None
            selected.press('ArrowLeft')
            expect(page.locator('#model-name')).to_have_text('故障机器人')
            mode['model_fail'] = True
            page.locator('#model-download').click()
            expect(page.locator('#model-error')).to_contain_text('连接中断')
            expect(page.locator('#model-download')).to_have_text('重试下载')
            mode['model_fail'] = False
            # Running badges must not inherit the history run-card layout.
            policy['runtime'].update(ready=False, state='running', total_bytes=161547059, done_bytes=12345678)
            page.reload()
            expect(page.locator('#rt-badge')).to_have_text('准备中')
            expect(page.locator('#rt-bar')).to_be_visible()
            expect(page.locator('#runtime-repair')).to_be_disabled()
            assert page.locator('#rt-badge').bounding_box()['height'] < 40
            page.set_viewport_size(dict(width=390, height=900))
            assert page.locator('#rt-badge').bounding_box()['height'] < 40
            page.set_viewport_size(dict(width=1440, height=1040))
            # Failed environment must have a persistent, explicit repair control.
            policy['runtime'].update(ready=False, state='failed', error='测试：目录校验失败', repair_needed=True, repair_bytes=559000)
            page.reload()
            expect(page.locator('#runtime-repair')).to_have_text('重试修复')
            expect(page.locator('#rt-err')).to_contain_text('目录校验失败')
            page.screenshot(path=str(OUT / 'policy-repair.png'), full_page=True)
            mode['not_ready'] = True
            page.locator('#runtime-repair').click()
            expect(page.locator('#page-err')).to_contain_text('插件尚未运行')
            mode['not_ready'] = False
            policy['runtime'].update(ready=True, state='idle', error='')
            page.locator('#page-retry').click()
            expect(page.locator('#page-error')).to_be_hidden()
            # Model deletion has an explicit confirmation, both cancel and accept.
            page.get_by_role('button', name='铁甲战士 · 已下载', exact=True).click()
            before = len(calls)
            page.once('dialog', lambda dialog: dialog.dismiss())
            page.locator('#model-remove').click()
            assert len(calls) == before
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator('#model-remove').click()
            expect(page.locator('#model-remove')).to_be_hidden()
            assert not policy['models'][0]['ready']

            page.goto(origin + '/sts2.html')
            expect(page.locator('#live-floor')).to_have_text('23')
            expect(page.locator('#map-form')).to_have_count(0)
            expect(page.locator('#live-brain')).to_have_text('未接管')
            page.get_by_role('button', name='开始自动玩 ↗', exact=True).click()
            expect(page.locator('#autoplay-title')).to_have_text('正在攀登中')
            expect(page.locator('[data-entry="sts2_get_status"]').first).to_be_enabled()
            expect(page.locator('#live-brain')).to_have_text('策略网络')
            expect(page.locator('#live-brain-detail')).to_contain_text('铁甲战士')
            page.screenshot(path=str(OUT / 'control-desktop.png'), full_page=True)
            status['decision_engine'].update(engine='solver', phase='planning', detail='没有下载 SILENT 的模型')
            # The visible page refreshes without any gameplay action.
            before_poll = len(calls)
            expect(page.locator('#live-brain')).to_have_text('求解器', timeout=10000)
            expect(page.locator('#live-brain-phase')).to_have_text('规划中')
            expect(page.locator('#live-brain-detail')).to_contain_text('没有下载 SILENT')
            assert all(c['entry_id'] == 'sts2_get_status' for c in calls[before_poll:])
            status['decision_engine'].update(engine='heuristic', phase='ready')
            page.locator('[data-entry="sts2_get_status"]').click()
            expect(page.locator('#live-brain')).to_have_text('启发式后备')
            status['server']['state'] = 'error'
            page.locator('[data-entry="sts2_get_status"]').click()
            expect(page.locator('#live-brain')).to_have_text('未连接')
            status['server']['state'] = 'connected'
            saved_brain = status.pop('decision_engine')
            page.locator('[data-entry="sts2_get_status"]').click()
            expect(page.locator('#live-brain')).to_have_text('等待确认')
            mode['not_ready'] = True
            page.locator('[data-entry="sts2_get_status"]').click()
            expect(page.locator('#live-brain')).to_have_text('状态未知')
            mode['not_ready'] = False
            status['decision_engine'] = saved_brain
            saved_brain.update(engine='policy', phase='waiting', detail='等待敌方行动结束')
            page.locator('[data-entry="sts2_get_status"]').click()
            expect(page.locator('#live-brain')).to_have_text('策略网络')
            expect(page.locator('#live-brain-phase')).to_have_text('等待行动')


            expect(page.locator('#digest-state')).to_have_text('已开启')
            expect(page.locator('#reflection-state')).to_have_text('已关闭')
            for name, badge in [('host_digest', 'digest-state'), ('run_reflection', 'reflection-state')]:
                off = page.locator(f'[data-entry="sts2_set_{name}"][data-args*=false]')
                on = page.locator(f'[data-entry="sts2_set_{name}"][data-args*=true]')
                off.click()
                expect(page.locator('#' + badge)).to_have_text('已关闭')
                expect(off).to_have_attribute('aria-pressed', 'true')
                on.click()
                expect(page.locator('#' + badge)).to_have_text('已开启')
                expect(on).to_have_attribute('aria-pressed', 'true')
            page.reload()
            expect(page.locator('#digest-state')).to_have_text('已开启')
            expect(page.locator('#reflection-state')).to_have_text('已开启')
            mode['not_ready'] = True
            page.locator('[data-entry="sts2_get_status"]').click()
            expect(page.locator('#digest-state')).to_have_text('状态未知')
            expect(page.locator('#reflection-state')).to_have_text('状态未知')
            mode['not_ready'] = False
            saved_digest = status.pop('host_digest')
            page.locator('[data-entry="sts2_get_status"]').click()
            expect(page.locator('#digest-state')).to_have_text('状态未知')
            status['host_digest'] = saved_digest
            page.locator('[data-entry="sts2_get_status"]').click()
            expect(page.locator('#digest-state')).to_have_text('已开启')

            before_settings = len(calls)
            page.get_by_role('link', name='参数配置', exact=True).click()
            expect(page.locator('nav [aria-current="page"]')).to_have_text('参数配置')
            expect(page.locator('#map-wait')).to_have_value('20')
            expect(page.locator('#preferences-load-state')).to_have_text('已载入 21 项通用参数')
            expect(page.locator('#preferences-fields input, #preferences-fields select')).to_have_count(21)
            expect(page.locator('.preferences-nav a')).to_have_count(6)
            assert '?v=' in page.url, 'Navigation must bypass an older cached HTML page'
            for group in ('policy', 'pace', 'commentary', 'live', 'records'):
                page.locator(f'.preferences-nav a[href="#settings-{group}"]').click()
                expect(page.locator('#settings-' + group)).to_be_in_viewport()
            # If only the general-settings API fails, it must not look like a map-only page.
            mode['preferences_error'] = True
            page.reload()
            expect(page.locator('#map-wait')).to_have_value('20')
            expect(page.locator('#preferences-load-state')).to_have_text('读取失败')
            expect(page.locator('#preferences-overview-note')).to_contain_text('不是只有联机地图')
            expect(page.locator('#preferences-fields section')).to_have_count(5)
            expect(page.locator('#preferences-fields input')).to_have_count(0)
            expect(page.locator('#preferences-save')).to_be_disabled()
            mode['preferences_error'] = False
            page.locator('#preferences-retry').click()
            expect(page.locator('#preferences-load-state')).to_have_text('已载入 21 项通用参数')
            expect(page.locator('#preferences-retry')).to_be_hidden()
            page.locator('#map-wait').fill('37.5')
            page.locator('#map-save').click()
            expect(page.locator('#feedback')).to_contain_text('测试操作已完成')
            assert next(c for c in reversed(calls) if c['entry_id'] == 'sts2_set_map_policy')['args']['vote_wait_seconds'] == 37.5
            assert all(c['entry_id'] in ('sts2_get_status', 'sts2_get_settings') for c in calls[before_settings:-1])
            page.locator('#map-policy').select_option('wait')
            expect(page.locator('#map-wait')).to_be_disabled()
            page.locator('#map-save').click()
            expect(page.locator('#feedback')).to_contain_text('测试操作已完成')
            assert calls[-1]['args'] == dict(policy='wait', vote_wait_seconds=37.5)
            page.locator('#map-policy').select_option('auto')
            expect(page.locator('#map-wait')).to_be_disabled()
            page.locator('#map-save').click()
            expect(page.locator('#feedback')).to_contain_text('测试操作已完成')
            assert calls[-1]['args']['policy'] == 'auto'
            page.locator('#map-policy').select_option('follow')
            expect(page.locator('#map-wait')).to_be_enabled()
            page.locator('#map-wait').fill('0')
            page.locator('#map-save').click()
            expect(page.locator('#feedback')).to_contain_text('测试操作已完成')
            assert calls[-1]['args']['vote_wait_seconds'] == 0
            page.locator('#map-wait').fill('86401')
            before_invalid = len(calls)
            page.locator('#map-save').click()
            assert len(calls) == before_invalid
            page.locator('#map-wait').fill('42')
            page.once('dialog', lambda dialog: dialog.dismiss())
            page.locator('#map-refresh').click()
            expect(page.locator('#map-wait')).to_have_value('42')
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator('#map-refresh').click()
            expect(page.locator('#map-wait')).to_have_value('0')
            page.locator('#map-wait').fill('37.5')
            mode['not_ready'] = True
            page.locator('#map-save').click()
            expect(page.locator('#feedback')).to_contain_text('插件尚未运行')
            expect(page.locator('#map-wait')).to_have_value('37.5')
            expect(page.locator('#map-save')).to_be_enabled()
            page.reload()
            expect(page.locator('#feedback')).to_contain_text('插件尚未运行')
            expect(page.locator('#map-save')).to_be_disabled()
            mode['not_ready'] = False
            page.locator('#map-refresh').click()
            expect(page.locator('#map-save')).to_be_enabled()
            page.locator('#map-wait').fill('37.5')
            page.locator('#map-save').click()
            expect(page.locator('#feedback')).to_contain_text('测试操作已完成')

            page.locator('#preferences-refresh').click()
            expect(page.locator('#pref-policy_enabled')).to_be_checked()
            expect(page.locator('#preferences-save')).to_be_disabled()
            expect(page.locator('#preferences-fields .preference-field')).to_have_count(len(SETTINGS_SCHEMA['FIELDS']))
            page.locator('#pref-policy_enabled').uncheck()
            page.locator('#pref-autoplay_push_probability').fill('0')
            page.locator('#preferences-save').click()
            expect(page.locator('#preferences-feedback')).to_contain_text('参数测试保存完成')
            assert calls[-1]['args'] == {'patch': {'policy_enabled': False, 'autoplay_push_probability': 0}}
            expect(page.locator('#preferences-save')).to_be_disabled()
            page.locator('#pref-policy_timeout_seconds').fill('301')
            before_invalid = len(calls)
            page.locator('#preferences-save').click()
            assert len(calls) == before_invalid
            page.locator('#pref-policy_timeout_seconds').fill('12.5')
            page.once('dialog', lambda dialog: dialog.dismiss())
            page.locator('#preferences-refresh').click()
            expect(page.locator('#pref-policy_timeout_seconds')).to_have_value('12.5')
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator('#preferences-refresh').click()
            expect(page.locator('#pref-policy_timeout_seconds')).to_have_value('20')
            page.locator('#pref-policy_timeout_seconds').fill('12.5')
            mode['not_ready'] = True
            page.locator('#preferences-save').click()
            expect(page.locator('#preferences-feedback')).to_contain_text('插件尚未运行')
            expect(page.locator('#pref-policy_timeout_seconds')).to_have_value('12.5')
            expect(page.locator('#preferences-save')).to_be_enabled()
            mode['not_ready'] = False
            page.locator('#preferences-save').click()
            expect(page.locator('#preferences-feedback')).to_contain_text('参数测试保存完成')
            assert calls[-1]['args'] == {'patch': {'policy_timeout_seconds': 12.5}}

            page.goto(origin + '/history.html')
            expect(page.locator('.run-choice')).to_have_count(12)
            expect(page.locator('.route')).to_be_visible()
            page.locator('#next').click()
            expect(page.locator('.run-choice')).to_have_count(3)
            page.locator('#prev').click()
            page.locator('#filter-outcome').select_option('victory')
            expect(page.locator('.run-choice')).to_have_count(5)
            page.locator('#filter-outcome').select_option('')
            page.screenshot(path=str(OUT / 'history-desktop.png'), full_page=True)
            page.locator('#search').fill('UI-DEMO-007')
            expect(page.locator('.run-choice')).to_have_count(1)
            page.locator('#search').fill('no-such-run')
            expect(page.locator('#runs')).to_contain_text('没有匹配的对局')
            page.locator('#search').fill('')
            history['runs'][0]['lessons'] = ['<img src=x onerror="window.XSS=1">']
            page.locator('#refresh').click()
            expect(page.locator('#runs')).to_contain_text('<img src=x onerror=')
            assert page.evaluate('window.XSS') is None
            mode['history_error'] = True
            page.locator('#refresh').click()
            expect(page.locator('#status')).to_contain_text('保留上次')
            mode['history_error'] = False
            history['runs'][0]['lessons'] = ['优先保留足够的格挡，再寻找输出机会。']
            for width, label in [(1440, 'desktop'), (768, 'tablet'), (390, 'mobile')]:
                page.set_viewport_size(dict(width=width, height=900))
                for name in ('index', 'policy', 'sts2', 'settings', 'history'):
                    page.goto(origin + '/' + name + '.html')
                    if name == 'policy':
                        expect(page.locator('.character')).to_have_count(6)
                    elif name == 'history':
                        expect(page.locator('.run-choice')).to_have_count(12)
                    elif name == 'settings':
                        expect(page.locator('#map-wait')).to_have_value('37.5')
                        expect(page.locator('#preferences-load-state')).to_have_text('已载入 21 项通用参数')
                        assert page.locator('.preferences-overview').evaluate("""panel => {
                            const box=panel.getBoundingClientRect();
                            return [...panel.querySelectorAll('.preferences-nav a')].every(a=>{
                                const r=a.getBoundingClientRect();
                                return r.bottom<=box.bottom && r.right<=box.right && r.left>=box.left;
                            });
                        }"""), 'Parameter navigation must not overflow its panel'

                    elif name == 'sts2':
                        expect(page.locator('#live-floor')).to_have_text('23')
                    page.wait_for_timeout(150)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), f'{name} overflows at {width}'
                    assert page.locator('img').evaluate_all('(imgs)=>imgs.every(img=>img.complete && img.naturalWidth > 0)'), f'{name} missing artwork'
                    if width != 768:
                        page.screenshot(path=str(OUT / f'{name}-{label}.png'), full_page=True)
            # Empty data and a fresh failure are distinct, not fake victories.
            history['runs'] = []
            history['summary'] = {}
            page.reload()
            expect(page.locator('#runs')).to_contain_text('旅程尚未落笔')
            mode['history_error'] = True
            page.reload()
            expect(page.locator('#runs')).to_contain_text('还读不到历史快照')
            # Honor reduced motion for keyboard and touch users.
            page.emulate_media(reduced_motion='reduce')
            page.goto(origin + '/policy.html')
            expect(page.locator('.character')).to_have_count(6)
            assert page.locator('.character').first.evaluate('el=>getComputedStyle(el).transitionDuration') == '0s'
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    (OUT / 'RESULTS.txt').write_text('PASS: isolated mocked-host browser checks; desktop/tablet/mobile; no live game actions.\nScreenshots contain synthetic UI-DEMO data, not actual gameplay.\n', encoding='utf-8')
    print('ALL PASS — screenshots and report:', OUT)


if __name__ == '__main__':
    main()
