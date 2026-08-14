/**
 * STS2 弹幕信息页「当前游戏信息状态」监控面板。
 *
 * 订阅插件 SSE（/plugin/{id}/ui-api/events），只处理 type === 'game_status'：
 * 后端把游戏信息 + 57 个触发条件计数打包成 JSON 放在 text 字段（type 过滤掉弹幕），
 * 这里 JSON.parse(msg.text) 直接渲染。弹幕（type === 'danmu'）由 danmaku.js 处理，互不干扰。
 */
(function (global) {
    'use strict';

    function resolveEventsUrl() {
        return '../ui-api/events';
    }

    // 游戏信息展示字段（key 对应后端 data.game 的键）
    var GAME_FIELDS = [
        { key: 'screen',          label: '屏幕' },
        { key: 'screen_class',    label: '场景分类' },
        { key: 'floor',           label: '楼层' },
        { key: 'act',             label: '幕数' },
        { key: 'hp',              label: '生命' },
        { key: 'gold',            label: '金币' },
        { key: 'turn',            label: '回合' },
        { key: 'summary_kind',    label: '态势' },
        { key: 'strategy_name',   label: '策略' },
        { key: 'autoplay_state',  label: '自动运行' },
        { key: 'transport_state', label: '传输' },
        { key: 'standby',         label: '待机' },
        { key: 'last_error',      label: '错误' },
    ];

    function fmtHp(g) {
        if (g.hp == null && g.max_hp == null) return '—';
        return g.max_hp == null ? String(g.hp) : g.hp + '/' + g.max_hp;
    }

    function buildGameInfo(container) {
        container.textContent = '';
        GAME_FIELDS.forEach(function (f) {
            var row = document.createElement('div');
            row.className = 'item';
            var k = document.createElement('span');
            k.className = 'k';
            k.textContent = f.label;
            var v = document.createElement('span');
            v.className = 'v dim';
            v.id = 'g-' + f.key;
            v.textContent = '—';
            row.appendChild(k);
            row.appendChild(v);
            container.appendChild(row);
        });
    }

    function setGame(g) {
        var vals = {
            screen: g.screen || '—',
            screen_class: g.screen_class || '—',
            floor: g.floor != null ? String(g.floor) : '—',
            act: g.act != null ? String(g.act) : '—',
            hp: fmtHp(g),
            gold: g.gold != null ? String(g.gold) : '—',
            turn: g.turn != null ? String(g.turn) : '—',
            summary_kind: g.summary_kind || '—',
            strategy_name: g.strategy_name || '—',
            autoplay_state: g.autoplay_state || '—',
            transport_state: g.transport_state || '—',
            standby: g.standby ? '是' : '否',
            last_error: g.last_error || '',
        };
        Object.keys(vals).forEach(function (key) {
            var el = document.getElementById('g-' + key);
            if (!el) return;
            el.textContent = vals[key];
            el.className = 'v' + (key === 'last_error' && vals[key] ? ' bad' : '');
        });
    }

    var chips = {};
    var chipCounts = {};
    var lastNames = '';

    function buildTriggerGrid(grid, names) {
        grid.textContent = '';
        chips = {};
        chipCounts = {};
        names.forEach(function (name) {
            var chip = document.createElement('div');
            chip.className = 'trigger-chip';
            var nm = document.createElement('span');
            nm.className = 't-name';
            nm.textContent = name;
            nm.title = name;
            var ct = document.createElement('span');
            ct.className = 't-count';
            ct.textContent = '0';
            chip.appendChild(nm);
            chip.appendChild(ct);
            grid.appendChild(chip);
            chips[name] = chip;
            chipCounts[name] = ct;
        });
    }

    function setTriggers(counts) {
        counts = counts || {};
        Object.keys(chipCounts).forEach(function (name) {
            var n = counts[name] || 0;
            chipCounts[name].textContent = String(n);
            chips[name].classList.toggle('fired', n > 0);
        });
    }

    function renderParams(container, params) {
        container.textContent = '';
        Object.keys(params).sort().forEach(function (k) {
            var v = params[k];
            var row = document.createElement('div');
            row.className = 'param-item';
            var pk = document.createElement('span');
            pk.className = 'pk';
            pk.textContent = k;
            pk.title = k;
            var pv = document.createElement('span');
            pv.className = 'pv';
            pv.textContent = (v !== null && typeof v === 'object')
                ? JSON.stringify(v)
                : String(v == null ? '—' : v);
            row.appendChild(pk);
            row.appendChild(pv);
            container.appendChild(row);
        });
    }

    function init() {
        var live = document.getElementById('status-live');
        var updatedEl = document.getElementById('status-updated');
        var infoEl = document.getElementById('game-info');
        var gridEl = document.getElementById('trigger-grid');
        var paramsEl = document.getElementById('params');
        if (!infoEl || !gridEl) return;

        buildGameInfo(infoEl);
        if (typeof global.EventSource === 'undefined') {
            if (live) live.textContent = '不支持 SSE';
            return;
        }

        var es = new global.EventSource(resolveEventsUrl());
        es.onopen = function () { if (live) live.textContent = '在线'; };
        es.onerror = function () { if (live) live.textContent = '离线'; };
        es.onmessage = function (ev) {
            var msg;
            try { msg = JSON.parse(ev.data); } catch (err) { return; }
            if (!msg || msg.type !== 'game_status') return;  // 只处理状态事件，弹幕交给 danmaku.js
            var d;
            try { d = JSON.parse(msg.text); } catch (err) { return; }
            if (live) live.textContent = '更新中';
            if (d.game) setGame(d.game);
            if (d.trigger_names && d.trigger_names.join('|') !== lastNames) {
                lastNames = d.trigger_names.join('|');
                buildTriggerGrid(gridEl, d.trigger_names);
            }
            if (d.triggers) setTriggers(d.triggers);
            if (paramsEl && d.params) renderParams(paramsEl, d.params);
            if (updatedEl) updatedEl.textContent = new Date().toLocaleTimeString();
            if (live) live.textContent = '在线';
        };
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})(window);
