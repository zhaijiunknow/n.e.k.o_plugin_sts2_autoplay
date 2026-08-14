/**
 * STS2 陪玩点评弹幕浮层 — 渲染引擎 + 数据通道（最小测试）
 *
 * 唯一对外入口：window.Sts2DanmakuOverlay.push(text)
 *   - MVP：由 /events SSE 或"自动演示"按钮调用
 *   - 生产：由 Electron preload / executeJavaScript 注入调用（见 README 集成预留）
 *
 * 行为借鉴 danmuai（D:\NekoClaw\danmuai\app\danmu_engine\）：
 *   - 速度归一化：duration = travel / speedPxS，合成器线性执行，无 JS render loop
 *   - 选轨：空闲优先 → 入口区逆密度加权 → 全满丢弃（扩展位见 pickLane）
 *   - min_gap 防重叠（同速不变换 → 只需查 lane 末尾）
 *   - 去重：deque 窗口 + Levenshtein 相似度
 */
(function (global) {
    'use strict';

    // ------------------------------------------------------------------
    // 配置
    // ------------------------------------------------------------------
    var CFG = {
        speedPxS: 120,          // 像素/秒；danmuai 默认约 180px/s，字幕窗约 106px/s
        lanes: 12,              // 轨道数（danmuai DANMU_LINES_MIN=12 ~ MAX=20）
        lineHeight: 40,         // 行高，对齐 --dm-line-height
        top: 50,                // 上边距（danmuai TRACK_TOP_MARGIN_BASE=50）
        bottom: 80,             // 下边距（danmuai TRACK_BOTTOM_MARGIN_BASE=80）
        minGapWidthFactor: 0.5, // minGap = max(minGapBase, width * factor)
        minGapBase: 80,
        entryZone: 300,         // 右缘入口区宽度（逆密度加权用）
        maxChunkChars: 40,      // 单条弹幕最大字符数，超出按长度切
        dedupWindow: 30,        // 去重窗口 deque 容量
        dedupThreshold: 0.5,    // Levenshtein 相似度阈值（0~1）
        dedupTtlMs: 30000,      // 去重记忆 TTL
        dedupFuzzy: true,       // 是否启用模糊去重（可关）
        fontSizePx: 26,         // 兜底测量用字号，须与 CSS --dm-font-size 一致
        avatarSize: 36,         // 猫娘头像尺寸（px），须与 CSS --dm-avatar-size 一致
        avatarGap: 6,           // 头像与文字间距（px）
        topDurationMs: 4600,    // 顶部弹幕驻留时长（对齐 DanmakuSpire TopDurationSeconds）
    };

    // ------------------------------------------------------------------
    // 文本工具：Levenshtein / 相似度 / 标点切句
    // ------------------------------------------------------------------
    function levenshtein(a, b) {
        var m = a.length, n = b.length, i, j;
        if (m === 0) return n;
        if (n === 0) return m;
        var prev = new Array(n + 1), curr = new Array(n + 1);
        for (j = 0; j <= n; j++) prev[j] = j;
        for (i = 1; i <= m; i++) {
            curr[0] = i;
            for (j = 1; j <= n; j++) {
                var cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
                curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost);
            }
            var tmp = prev; prev = curr; curr = tmp;
        }
        return prev[n];
    }

    function similarityRatio(a, b) {
        if (!a && !b) return 1;
        var maxLen = Math.max(a.length, b.length);
        if (maxLen === 0) return 1;
        return 1 - levenshtein(a, b) / maxLen;
    }

    // 切句标点（借自 static/subtitle/subtitle-shared.js splitSubtitleDanmakuSegments）
    var BOUNDARY_PUNCT = ',\uff0c.\u3002!?\uff01\uff1f;?\uff1b\uff1a:\u3001\u2026';
    var CLOSING_PUNCT = '"\'\u201c\u201d\u2019\uff09\uff3d)}]\u300b\u300d\u3011\u3010';

    function isBoundaryPunctuation(ch) {
        return BOUNDARY_PUNCT.indexOf(ch) !== -1;
    }
    function isClosingPunctuation(ch) {
        return CLOSING_PUNCT.indexOf(ch) !== -1;
    }

    /**
     * 按标点把点评切段（两个连续标点处切开），借自 subtitle-shared。
     * 返回段数组；MVP 里每段即一条弹幕。
     */
    function splitByPunctuation(text) {
        var normalized = String(text || '').replace(/\s+/g, ' ').trim();
        var segments = [], start = 0, punctuationCount = 0, i, end, segment;
        if (!normalized) return segments;
        for (i = 0; i < normalized.length; i += 1) {
            if (!isBoundaryPunctuation(normalized.charAt(i))) continue;
            punctuationCount += 1;
            if (punctuationCount < 2) continue;
            end = i + 1;
            while (end < normalized.length && isClosingPunctuation(normalized.charAt(end))) {
                end += 1;
            }
            segment = normalized.slice(start, end).trim();
            if (segment) segments.push(segment);
            start = end;
            i = end - 1;
            punctuationCount = 0;
        }
        segment = normalized.slice(start).trim();
        if (segment) segments.push(segment);
        return segments;
    }

    /**
     * 把点评文本切成弹幕块：先按标点切句，超长段再按最大字符数硬切。
     * 每块去除首尾空白与尾部标点，避免弹幕以"、"、"。"收尾。
     * 参考 danmuai add_text 一次一条；这里一条点评可能拆成多条弹幕。
     */
    function splitChunks(text, maxChars) {
        var chunks = [];
        var segments = splitByPunctuation(text);
        if (!segments.length && String(text || '').trim()) {
            segments = [String(text || '').trim()];
        }
        segments.forEach(function (seg) {
            var clean = trimTrailingPunct(seg);
            if (!clean) return;
            if (clean.length <= maxChars) {
                chunks.push(clean);
            } else {
                var re = new RegExp('.{1,' + maxChars + '}', 'g');
                var parts = clean.match(re);
                if (parts) chunks.push.apply(chunks, parts);
            }
        });
        return chunks;
    }

    /** 去掉段尾的标点（，。！？；：、…）与空白。 */
    function trimTrailingPunct(text) {
        var s = String(text || '').trim();
        while (s.length && isBoundaryPunctuation(s.charAt(s.length - 1))) {
            s = s.slice(0, -1).trim();
        }
        return s;
    }

    // ------------------------------------------------------------------
    // 文本宽度测量
    // ------------------------------------------------------------------
    var measureEl = null;
    var measureCache = Object.create(null);

    function heuristicWidth(text, fontSize) {
        var w = 0, i;
        for (i = 0; i < text.length; i += 1) {
            w += /[\u1100-\uFFFF]/.test(text.charAt(i)) ? fontSize : fontSize * 0.58;
        }
        return w;
    }

    function measureText(text, fontSize) {
        var cacheKey = fontSize + '|' + text;
        if (measureCache[cacheKey] != null) return measureCache[cacheKey];
        var w = 0;
        try {
            if (!measureEl) {
                measureEl = document.createElement('span');
                measureEl.className = 'dm-item';
                measureEl.style.cssText = 'position:absolute;visibility:hidden;left:-9999px;top:0;';
                document.body.appendChild(measureEl);
            }
            measureEl.textContent = text;
            measureEl.style.fontSize = fontSize + 'px';
            w = measureEl.getBoundingClientRect().width;
        } catch (err) {
            w = 0;
        }
        if (!(w > 0)) w = heuristicWidth(text, fontSize);
        measureCache[cacheKey] = w;
        return w;
    }

    // ------------------------------------------------------------------
    // 去重窗口：精确 TTL 集 + 可选 Levenshtein 模糊
    // ------------------------------------------------------------------
    function DedupWindow(cfg) {
        this.cfg = cfg;
        this.recent = [];               // deque(30)
        this.exactAt = Object.create(null); // text -> lastSeenTs
    }

    DedupWindow.prototype.isDuplicate = function (text) {
        if (!text) return false;
        var now = Date.now();
        if (this.exactAt[text] != null && now - this.exactAt[text] < this.cfg.dedupTtlMs) {
            return true;
        }
        if (this.cfg.dedupFuzzy) {
            var i;
            for (i = 0; i < this.recent.length; i += 1) {
                if (similarityRatio(this.recent[i], text) >= this.cfg.dedupThreshold) {
                    return true;
                }
            }
        }
        return false;
    };

    DedupWindow.prototype.remember = function (text) {
        if (!text) return;
        this.exactAt[text] = Date.now();
        this.recent.push(text);
        if (this.recent.length > this.cfg.dedupWindow) {
            var dropped = this.recent.shift();
            delete this.exactAt[dropped];
        }
    };

    // ------------------------------------------------------------------
    // 弹幕引擎
    // ------------------------------------------------------------------
    function DanmakuOverlay(stage, cfg) {
        this.cfg = cfg;
        this.stage = stage;
        this.stageW = stage.clientWidth || document.documentElement.clientWidth || 1920;
        this.lanes = [];
        this.laneItems = [];            // laneItems[i] = [{el, width, insertedAt, duration, travel}]
        this.dedup = new DedupWindow(cfg);
        this.stats = { shown: 0, dup: 0, busy: 0 };
        this._onStats = null;           // 状态条刷新钩子
        this.topLanes = {};             // laneIdx -> 顶部弹幕占用截止时间(ms)
        this.pendingQueue = [];         // 全忙时排队，等轨道空出来再放（不丢弃）
        this._drainTimer = null;

        var i, lane;
        for (i = 0; i < cfg.lanes; i += 1) {
            lane = document.createElement('div');
            lane.className = 'dm-lane';
            lane.style.top = (cfg.top + i * cfg.lineHeight) + 'px';
            stage.appendChild(lane);
            this.lanes.push(lane);
            this.laneItems.push([]);
        }

        var self = this;
        window.addEventListener('resize', function () {
            self.stageW = stage.clientWidth || document.documentElement.clientWidth || 1920;
        });
    }

    /**
     * 唯一对外入口：推送一条点评文本。
     * opts.style === 'catgirl' 时渲染猫娘头像（opts.avatar 为 dataUrl）。
     * 去重 → 切块 → 逐块入轨。
     */
    DanmakuOverlay.prototype.push = function (text, opts) {
        var t = String(text || '').replace(/\s+/g, ' ').trim();
        if (!t) return;
        if (this.dedup.isDuplicate(t)) {
            this.stats.dup += 1;
            this._emitStats();
            return;
        }
        var o = opts || {};
        var style = o.style === 'catgirl' ? 'catgirl' : 'narration';
        var avatar = (style === 'catgirl' && o.avatar) ? String(o.avatar) : '';
        var placement = o.placement === 'top' ? 'top' : 'scrolling';
        var chunks = splitChunks(t, this.cfg.maxChunkChars);
        var i;
        for (i = 0; i < chunks.length; i += 1) {
            this.pushItem(chunks[i], style, avatar, placement);
        }
        this.dedup.remember(t);
        this._emitStats();
    };

    DanmakuOverlay.prototype.pushItem = function (text, style, avatar, placement) {
        var el = document.createElement('span');
        el.className = 'dm-item';
        el.classList.add(style === 'catgirl' ? 'dm-item-catgirl' : 'dm-item-narration');
        var avatarW = 0;
        if (style === 'catgirl' && avatar) {
            var avatarEl = document.createElement('img');
            avatarEl.className = 'dm-avatar';
            avatarEl.src = avatar;
            el.appendChild(avatarEl);
            avatarW = this.cfg.avatarSize + this.cfg.avatarGap;
        }
        var textEl = document.createElement('span');
        textEl.className = 'dm-item-text';
        textEl.textContent = text;
        el.appendChild(textEl);
        var w = measureText(text, this.cfg.fontSizePx) + avatarW;

        var isTop = placement === 'top';
        var laneIdx = isTop ? this.pickTopLane() : this.pickLane(w, style === 'catgirl');
        if (laneIdx < 0) {
            // 全忙：排队等轨道空出来，不丢弃（避免弹幕太稀疏）
            this.pendingQueue.push({ el: el, w: w, isTop: isTop, style: style });
            this._scheduleQueueDrain();
            return;
        }
        this._attachItem(el, w, laneIdx, isTop);
    };

    DanmakuOverlay.prototype._attachItem = function (el, w, laneIdx, isTop) {
        var duration, travel, rec;
        if (isTop) {
            this.topLanes[laneIdx] = Date.now() + this.cfg.topDurationMs;
            el.classList.add('dm-item-top');
            el.style.left = Math.max(20, Math.round((this.stageW - w) / 2)) + 'px';
            duration = this.cfg.topDurationMs / 1000;
            travel = 0;
        } else {
            travel = this.stageW + w;
            duration = travel / this.cfg.speedPxS;
            el.style.setProperty('--dm-travel', travel + 'px');
        }
        el.style.animationDuration = duration.toFixed(3) + 's';
        this.lanes[laneIdx].appendChild(el);

        if (!isTop) {
            rec = {
                el: el,
                width: w,
                insertedAt: performance.now(),
                duration: duration,
                travel: travel,
            };
            this.laneItems[laneIdx].push(rec);
        }
        this.stats.shown += 1;

        var self = this;
        var fallbackTimer = setTimeout(function () {
            if (isTop) {
                if (el.parentNode) el.parentNode.removeChild(el);
            } else {
                self.removeItem(laneIdx, rec);
            }
        }, (duration + 0.5) * 1000);
        el.addEventListener('animationend', function () {
            clearTimeout(fallbackTimer);
            if (isTop) {
                if (el.parentNode) el.parentNode.removeChild(el);
            } else {
                self.removeItem(laneIdx, rec);
            }
        }, { once: true });
    };

    DanmakuOverlay.prototype._drainQueue = function () {
        if (!this.pendingQueue.length) return;
        var i = 0;
        while (i < this.pendingQueue.length) {
            var item = this.pendingQueue[i];
            var laneIdx = item.isTop ? this.pickTopLane() : this.pickLane(item.w, item.style === 'catgirl');
            if (laneIdx < 0) break;  // 仍无空位，等下一轮
            this.pendingQueue.splice(i, 1);
            this._attachItem(item.el, item.w, laneIdx, item.isTop);
        }
    };

    DanmakuOverlay.prototype._scheduleQueueDrain = function () {
        var self = this;
        if (this._drainTimer) return;
        this._drainTimer = setTimeout(function () {
            self._drainTimer = null;
            self._drainQueue();
        }, 200);
    };

    /** 选一个当前无顶部弹幕的轨道；全满返回 -1。 */
    DanmakuOverlay.prototype.pickTopLane = function () {
        var now = Date.now();
        var free = [];
        for (var i = 0; i < this.cfg.lanes; i += 1) {
            if ((this.topLanes[i] || 0) <= now) free.push(i);
        }
        return free.length ? free[Math.floor(Math.random() * free.length)] : -1;
    };

    /** 当前某条弹幕 item 左缘的 x（用于选轨判定）。 */
    DanmakuOverlay.prototype.currentX = function (rec, now) {
        return this.stageW - ((now - rec.insertedAt) / rec.duration) * rec.travel;
    };

    /**
     * 选轨：空闲优先随机 → 入口区逆密度加权随机 → 全满返回 -1。
     * 同速不变换：一条 lane 里"最右"永远是最后插入的 item，所以只查末尾即可满足 min_gap。
     */
    DanmakuOverlay.prototype.pickLane = function (w, force) {
        var minGap = Math.max(this.cfg.minGapBase, w * this.cfg.minGapWidthFactor);
        var now = performance.now();
        var safe = [];
        var idle = [];
        var i, items, last, rightEdge, busy;

        for (i = 0; i < this.laneItems.length; i += 1) {
            if (this.topLanes[i] && this.topLanes[i] > Date.now()) continue; // 顶部弹幕占用中
            items = this.laneItems[i];
            last = items[items.length - 1];
            if (!last) {
                safe.push({ idx: i, entryBusy: 0 });
                idle.push(i);
                continue;
            }
            rightEdge = this.currentX(last, now) + last.width;
            if (rightEdge > this.stageW - minGap) continue; // 末尾 item 离右缘太近，拒绝
            busy = this._countEntryZoneBusy(items, now);
            safe.push({ idx: i, entryBusy: busy });
            if (busy === 0 && items.length === 0) idle.push(i);
        }

        if (!safe.length) {
            // 全忙：默认丢弃。catgirl（猫娘声音）强制选最不忙轨道塞入，避免被弹幕潮淹没。
            if (force) {
                var least = -1, leastBusy = Infinity;
                for (i = 0; i < this.laneItems.length; i += 1) {
                    if (this.topLanes[i] && this.topLanes[i] > Date.now()) continue;
                    busy = this._countEntryZoneBusy(this.laneItems[i], now);
                    if (busy < leastBusy) { leastBusy = busy; least = i; }
                }
                if (least >= 0) return least;
            }
            return -1;
        }

        if (idle.length) {
            // 优先上方：低索引（顶部）轨道权重更高
            return weightedPick(idle.map(function (i) { return { idx: i }; }), function (s) { return 1 / (1 + s.idx); });
        }

        // 入口区逆密度加权 + 顶部偏好：entryBusy 越小、轨道越靠上权重越高
        return weightedPick(safe, function (s) { return (1 / (1 + s.entryBusy)) * (1 / (1 + s.idx)); });
    };

    DanmakuOverlay.prototype._countEntryZoneBusy = function (items, now) {
        var count = 0, i, x;
        for (i = 0; i < items.length; i += 1) {
            x = this.currentX(items[i], now);
            if (x + items[i].width > this.stageW - this.cfg.entryZone && x < this.stageW) {
                count += 1;
            }
        }
        return count;
    };

    DanmakuOverlay.prototype.removeItem = function (laneIdx, rec) {
        var arr = this.laneItems[laneIdx];
        if (arr && rec) {
            var idx = arr.indexOf(rec);
            if (idx >= 0) arr.splice(idx, 1);
        }
        if (rec && rec.el && rec.el.parentNode) rec.el.parentNode.removeChild(rec.el);
        this._drainQueue();  // 轨道空出 → 排队的弹幕入轨
    };

    DanmakuOverlay.prototype.activeCount = function () {
        var count = 0, i;
        for (i = 0; i < this.laneItems.length; i += 1) count += this.laneItems[i].length;
        return count;
    };

    DanmakuOverlay.prototype._emitStats = function () {
        if (typeof this._onStats === 'function') this._onStats(this.stats, this.activeCount());
    };

    function weightedPick(items, weightFn) {
        var total = 0, i, w;
        for (i = 0; i < items.length; i += 1) {
            w = weightFn(items[i]);
            total += w;
        }
        var roll = Math.random() * total;
        for (i = 0; i < items.length; i += 1) {
            w = weightFn(items[i]);
            roll -= w;
            if (roll <= 0) return items[i].idx;
        }
        return items[items.length - 1].idx;
    }

    // ------------------------------------------------------------------
    // 页面装配：SSE 订阅 + 演示模式 + 状态条
    // ------------------------------------------------------------------

    /**
     * 解析 SSE 事件流地址。
     * 页面由插件静态路由服务（/plugin/{id}/ui/），同源走 ../ui-api/events。
     */
    function resolveEventsUrl() {
        var m = window.location.pathname.match(/^\/plugin\/([^/]+)\/ui\/?/);
        if (m) return '../ui-api/events';
        return '../ui-api/events';
    }

    function initPage() {
        var stage = document.getElementById('dm-stage');
        if (!stage) return;
        var overlay = new DanmakuOverlay(stage, CFG);
        global.Sts2DanmakuOverlay = overlay; // 未来 IPC 注入的唯一入口

        var sseState = document.getElementById('dm-sse-state');
        var shownEl = document.getElementById('dm-stat-shown');
        var activeEl = document.getElementById('dm-stat-active');
        var dupEl = document.getElementById('dm-stat-dup');
        var busyEl = document.getElementById('dm-stat-busy');

        overlay._onStats = function (stats, active) {
            shownEl.textContent = String(stats.shown);
            activeEl.textContent = String(active);
            dupEl.textContent = String(stats.dup);
            busyEl.textContent = String(stats.busy);
        };
        overlay._emitStats();

        // SSE 订阅（数据通道）：插件路由 /plugin/{id}/ui/ → ../ui-api/events（同源）
        var es = null;
        if (typeof global.EventSource !== 'undefined') {
            try {
                es = new global.EventSource(resolveEventsUrl());
                es.onopen = function () { sseState.textContent = '在线'; };
                es.onerror = function () { sseState.textContent = '离线'; };
                es.onmessage = function (ev) {
                    var msg;
                    try { msg = JSON.parse(ev.data); } catch (err) { return; }
                    if (msg && msg.type === 'danmu' && msg.text) {
                        overlay.push(msg.text, { style: msg.style, avatar: msg.avatar, placement: msg.placement });
                    }
                };
            } catch (err) {
                sseState.textContent = 'N/A';
            }
        } else {
            sseState.textContent = 'N/A';
        }

        // 演示模式：定时从样例数组推流（不依赖服务器）
        var demoBtn = document.getElementById('dm-demo-btn');
        var demoOn = false;
        var demoIdx = 0;
        var demoTimer = null;
        var samples = (global.ST_SAMPLE_COMMENTARY && global.ST_SAMPLE_COMMENTARY.length)
            ? global.ST_SAMPLE_COMMENTARY
            : ['这是一条测试弹幕，用来验证滚动效果。'];

        function demoStop() {
            demoOn = false;
            if (demoTimer) { clearInterval(demoTimer); demoTimer = null; }
            demoBtn.textContent = '▶ 自动演示';
            demoBtn.classList.remove('dm-demo-on');
        }
        function demoTick() {
            var sample = samples[demoIdx % samples.length];
            demoIdx += 1;
            overlay.push(sample);
        }
        function demoStart() {
            demoOn = true;
            demoIdx = 0;
            demoTick();
            demoTimer = setInterval(demoTick, 1200);
            demoBtn.textContent = '■ 停止演示';
            demoBtn.classList.add('dm-demo-on');
        }
        if (demoBtn) {
            demoBtn.addEventListener('click', function () {
                if (demoOn) demoStop(); else demoStart();
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPage);
    } else {
        initPage();
    }
})(window);
