# -*- coding: utf-8 -*-
"""STS2 陪玩点评 — Qt 透明弹幕浮层（保底方案，原生 Qt 渲染）

在游戏画面上叠一层透明置顶的持续滚动弹幕，订阅 N.E.K.O 插件路由的
SSE 事件流（/plugin/sts2_autoplay/ui-api/events）接收点评文本，
用原生 Qt 渲染（多轨道、速度归一化、渐入渐出、去重）。

依赖：PyQt6（可选安装，不进主 requirements）：
    pip install PyQt6

用法：
    python qt_overlay.py --url http://127.0.0.1:48916/plugin/sts2_autoplay/ui-api/events
    python qt_overlay.py --url <SSE URL> --screen 1 --speed 180 --font-size 26

渲染行为移植自 danmuai（D:\\NekoClaw\\danmuai\\app\\danmu_engine\\）：
- 多轨道：line_height 40 * DPI，上边距 50、下边距 80，轨道数 12~20
- 速度归一化：每帧 x -= speed * (dt / (1/60))，speed 默认 180 px/s
- 选轨：空闲优先 → 入口区逆密度加权随机
- 渐入渐出：右侧 120px 渐入、左侧 90px 渐出（FADE_IN/OUT_PX）
- 文本：QPainterPath 描边 + 填充
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QPointF, QRect, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

FADE_IN_PX = 120.0
FADE_OUT_PX = 90.0
ENTRY_ZONE_PX = 300.0
LINE_HEIGHT_BASE = 40.0
TOP_MARGIN_BASE = 50.0
BOTTOM_MARGIN_BASE = 80.0
DANMU_LINES_MIN = 12
DANMU_LINES_MAX = 20
DEFAULT_SPEED_PX_S = 180.0
FALLBACK_CHAR_WIDTH = 25.0
# 猫娘头像尺寸与间距（对齐 danmaku.js CFG.avatarSize / avatarGap）
AVATAR_SIZE = 36
AVATAR_GAP = 6
# 顶部弹幕驻留时长（对齐 DanmakuSpire TopDurationSeconds）
TOP_DURATION_SEC = 4.6

# Qt 弹幕浮层实现版本（供弹幕信息页展示）
QT_OVERLAY_VERSION = "1.0.0"
# 浮层贴合游戏窗口时，左右各内缩的像素（避免盖到游戏边缘）
OVERLAY_EDGE_INSET = 10
# 可显示弹幕高度占窗口高度的百分比（默认 30，靠顶）
DEFAULT_HEIGHT_PERCENT = 30


def find_window_rect(title_keyword: str):
    """按标题关键字找可见窗口的屏幕矩形 (x, y, w, h)；找不到返回 None。

    用于把透明浮层贴合到游戏窗口（如 --window "Slay the Spire"）。取标题匹配中
    **在虚拟屏幕内**的最大窗口；跳过离屏/最小化的隐藏窗口。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[tuple[int, int, int, int]] = []
    keyword = str(title_keyword or "").strip().lower()
    if not keyword:
        return None

    # 虚拟屏幕范围（多显示器合并桌面）
    vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN

    def _on_screen(r) -> bool:
        return not (
            r.right <= vx or r.left >= vx + vw
            or r.bottom <= vy or r.top >= vy + vh
        )

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if keyword not in buf.value.lower():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w > 0 and h > 0 and _on_screen(rect):
            found.append((rect.left, rect.top, w, h))
        return True

    user32.EnumWindows(_enum_cb, 0)
    if not found:
        return None
    # 取面积最大的（最可能是游戏主窗口）
    return max(found, key=lambda r: r[2] * r[3])


def parse_rect(text: str):
    """解析 ``WxH+X+Y``（如 1600x900+0+0）→ (x, y, w, h)；非法返回 None。"""
    m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", str(text or "").strip())
    if not m:
        return None
    return (int(m.group(3)), int(m.group(4)), int(m.group(1)), int(m.group(2)))

# 秒级去重（与 web 版同思路：deque 窗口 + 精确 TTL）
DEDUP_WINDOW = 30
DEDUP_TTL_MS = 30000

# 描边偏移（fast-path drawText 渲染，对 CJK 稳健）
_OUTLINE_OFFSETS = [
    (-1, 0), (1, 0), (0, -1), (0, 1),
    (-1, -1), (1, -1), (-1, 1), (1, 1),
]


# ---------------------------------------------------------------------------
# 弹幕数据模型（精简版 danmuai danmu_engine_models.py）
# ---------------------------------------------------------------------------
class DanmuItem:
    __slots__ = ("content", "x", "y", "speed", "width", "pixmap", "expire_at")

    def __init__(self, content: str, x: float, y: float, speed: float, width: float, expire_at: float = 0.0):
        self.content = content
        self.x = x
        self.y = y
        self.speed = speed
        self.width = width
        self.pixmap = None
        self.expire_at = expire_at  # >0 表示顶部弹幕到期时间（monotonic）；0 表示横向滚动

    def right_edge(self) -> float:
        w = self.width if self.width > 0 else len(self.content) * FALLBACK_CHAR_WIDTH
        return self.x + w


class Track:
    def __init__(self, y: float):
        self.y = y
        self.items: list[DanmuItem] = []

    def can_accept(self, item_width: float, screen_width: float, min_gap: float) -> bool:
        if not self.items:
            return True
        last = self.items[-1]
        return last.right_edge() + min_gap < screen_width

    def entry_zone_count(self, screen_width: float, zone: float = ENTRY_ZONE_PX) -> int:
        zone_left = screen_width - zone
        return sum(1 for it in self.items if it.right_edge() > zone_left and it.x < screen_width)

    def add(self, item: DanmuItem):
        item.y = self.y
        self.items.append(item)

    def update(self, speed_factor: float, dt_sec: float) -> list[DanmuItem]:
        """推进所有弹幕；返回移出左屏或到期的条目。"""
        scale = dt_sec / (1.0 / 60.0)
        now = time.monotonic()
        removed: list[DanmuItem] = []
        kept: list[DanmuItem] = []
        for item in self.items:
            if item.expire_at and now >= item.expire_at:
                item.pixmap = None  # 显式释放预渲染像素，避免 GC 延迟累积
                removed.append(item)  # 顶部弹幕到期移除
                continue
            item.x -= item.speed * speed_factor * scale
            if item.right_edge() <= 0:
                item.pixmap = None  # 移出左屏即释放
                removed.append(item)
            else:
                kept.append(item)
        self.items = kept
        return removed


# ---------------------------------------------------------------------------
# 透明弹幕窗口
# ---------------------------------------------------------------------------
class DanmuOverlayWindow(QWidget):
    def __init__(self, speed_px_s: float = DEFAULT_SPEED_PX_S, font_size: int = 20,
                 screen_index: int = 0, rect=None, height_percent: int = DEFAULT_HEIGHT_PERCENT):
        super().__init__()
        self.speed_px_s = speed_px_s
        self._dedup: dict[str, float] = {}   # content -> last_seen_ts
        self._dedup_order: list[str] = []

        # 透明置顶窗口（对齐 danmuai overlay 约束）
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("STS2 弹幕浮层 (Qt)")

        # 字体与度量（优先从单 TTF 文件加载，避免 TTC 在部分系统上 CJK 字形加载失败）
        self.font = self._load_cjk_font(font_size)
        self.font_metrics = QFontMetrics(self.font)
        # 运行时诊断
        try:
            from PyQt6.QtGui import QFontInfo
            print(f"[qt_overlay] font_family={QFontInfo(self.font).family()} "
                  f"inFontUcs4(中)={self.font_metrics.inFontUcs4(0x4E2D)}", flush=True)
        except Exception as exc:
            print(f"[qt_overlay] font_diag_error={exc}", flush=True)

        # 轨道布局：rect 给定则贴合该窗口矩形（如游戏 1600x900），否则全屏
        app = QApplication.instance()
        screen = app.screens()[screen_index] if screen_index < len(app.screens()) else app.primaryScreen()
        if rect:
            x, y, w, h = rect
            # 左右各内缩 OVERLAY_EDGE_INSET，避免盖到游戏窗口边缘
            self.screen_geometry = QRect(
                x + OVERLAY_EDGE_INSET,
                y,
                max(1, w - 2 * OVERLAY_EDGE_INSET),
                h,
            )
        else:
            self.screen_geometry = screen.geometry()
        scale = max(1.0, float(screen.devicePixelRatio()))
        # 行高 = max(弹幕实际高度, 字号×1.6)：保证相邻轨道不垂直重叠，且字号大→轨道高→轨道少
        line_height = max(float(self.font_metrics.height()) + 16, float(font_size) * 1.6) * scale
        top = TOP_MARGIN_BASE * scale
        bottom = BOTTOM_MARGIN_BASE * scale
        # 可显示弹幕高度 = 窗口高度 × height_percent%（默认 30，靠顶）
        percent = max(1, min(100, int(height_percent or DEFAULT_HEIGHT_PERCENT)))
        display_height = float(self.screen_geometry.height()) * (percent / 100.0)
        usable = max(0.0, display_height - top - bottom)
        lane_count = max(1, min(int(usable / line_height), DANMU_LINES_MAX))
        self.lane_count = lane_count
        self.tracks = [Track(top + i * line_height) for i in range(lane_count)]
        # 顶部弹幕占用轨道直到时间（monotonic）
        self._top_lane_until: dict[int, float] = {}
        self._pending: list[dict] = []  # 全忙时排队，轨道空出来再放（不丢弃）

        # 渲染循环
        self._screen_width = float(self.screen_geometry.width())
        self._last_tick = None
        self._timer = None
        self._setup_timer()

        self.setGeometry(self.screen_geometry)
        # 不用 setStyleSheet("background: transparent") —— QSS 可能干扰 painter 字体
        # 渲染（CJK 字形变 tofu），透明由 WA_TranslucentBackground 保证。
        self._apply_win32_click_through()

    def _load_cjk_font(self, font_size: int) -> QFont:
        """加载支持 CJK 的字体。

        优先从单 TTF 文件直接加载（simhei.ttf 等），绕过系统字体数据库对 TTC
        （微软雅黑 msyh.ttc 是 TTC）的 CJK 字形加载问题。逐个尝试，命中返回；
        文件不存在时退回按已注册家族名，最后兜底微软雅黑。
        """
        candidates = [
            ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
            ("C:/Windows/Fonts/simsun.ttc", "SimSun"),
            ("C:/Windows/Fonts/msyh.ttc", "Microsoft YaHei"),
            ("C:/Windows/Fonts/msyhbd.ttc", "Microsoft YaHei Bold"),
        ]
        for path, fallback in candidates:
            try:
                font_file = Path(path)
                if font_file.is_file():
                    font_id = QFontDatabase.addApplicationFont(str(font_file))
                    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
                    if families:
                        return QFont(families[0], font_size)
                # 文件缺失 → 退回按系统已注册家族名
                if QFontDatabase.hasFamily(fallback):
                    return QFont(fallback, font_size)
            except Exception:
                continue
        # 兜底：按名字
        return QFont("Microsoft YaHei", font_size)

    def _setup_timer(self):
        from PyQt6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        now = time.monotonic()
        dt = (now - self._last_tick) if self._last_tick is not None else (1.0 / 60.0)
        self._last_tick = now
        dt = min(dt, 0.1)  # 防掉帧跳跃
        for track in self.tracks:
            track.update(1.0, dt)
        self._drain_pending()  # 轨道空出 → 排队的弹幕入轨
        self.update()

    # ---- 对外入口：推送一条点评文本 ----
    def push(self, text: str, style: str = "narration", avatar: str = "", placement: str = "scrolling"):
        content = re.sub(r"\s+", " ", str(text or "")).strip()
        if not content:
            return
        # 去重（按 style+placement+text）
        now_ms = time.time() * 1000
        dedup_key = f"{style}|{placement}|{content}"
        if dedup_key in self._dedup and now_ms - self._dedup[dedup_key] < DEDUP_TTL_MS:
            return
        self._dedup[dedup_key] = now_ms
        self._dedup_order.append(dedup_key)
        if len(self._dedup_order) > DEDUP_WINDOW:
            old = self._dedup_order.pop(0)
            self._dedup.pop(old, None)
        self._push_item(content, style, avatar, placement)

    def _push_item(self, content: str, style: str = "narration", avatar: str = "", placement: str = "scrolling"):
        avatar_pm = None
        if style == "catgirl" and avatar:
            avatar_pm = self._load_avatar(avatar)
        avatar_w = float(avatar_pm.width()) + AVATAR_GAP if avatar_pm is not None else 0.0
        width = float(self.font_metrics.horizontalAdvance(content)) + avatar_w
        lane_idx = self._pick_top_lane() if placement == "top" else self._pick_track(width, max(80.0, width * 0.5), style == "catgirl")
        if lane_idx < 0:
            # 全忙：排队等轨道空出来，不丢弃（避免弹幕太稀疏）
            self._pending.append(
                {"content": content, "style": style, "avatar": avatar, "placement": placement, "width": width, "avatar_pm": avatar_pm}
            )
            return
        self._place_item(content, style, placement, width, avatar_pm, lane_idx)

    def _place_item(self, content: str, style: str, placement: str, width: float, avatar_pm, lane_idx: int) -> None:
        if placement == "top":
            # 顶部弹幕：静止、水平居中、驻留 TOP_DURATION_SEC 后移除
            now = time.monotonic()
            self._top_lane_until[lane_idx] = now + TOP_DURATION_SEC
            x = max(20.0, (self._screen_width - width) / 2.0)
            item = DanmuItem(
                content=content, x=x, y=0.0, speed=0.0, width=width,
                expire_at=now + TOP_DURATION_SEC,
            )
            item.pixmap = self._render_text_pixmap(content, avatar_pm)
            self.tracks[lane_idx].add(item)
            return
        x = self._screen_width + 20.0 + (random.random() * 70.0)
        item = DanmuItem(content=content, x=x, y=0.0, speed=self.speed_px_s / 60.0, width=width)
        item.pixmap = self._render_text_pixmap(content, avatar_pm)
        self.tracks[lane_idx].add(item)

    def _drain_pending(self) -> None:
        if not self._pending:
            return
        remaining: list[dict] = []
        for item in self._pending:
            lane_idx = self._pick_top_lane() if item["placement"] == "top" else self._pick_track(item["width"], max(80.0, item["width"] * 0.5), item["style"] == "catgirl")
            if lane_idx < 0:
                remaining.append(item)
                continue
            self._place_item(item["content"], item["style"], item["placement"], item["width"], item["avatar_pm"], lane_idx)
        self._pending = remaining

    def _pick_top_lane(self) -> int:
        """选一个当前无顶部弹幕的轨道；全满返回 -1。"""
        now = time.monotonic()
        free = [i for i in range(self.lane_count) if self._top_lane_until.get(i, 0.0) <= now]
        if not free:
            return -1
        return free[random.randrange(len(free))]

    def _load_avatar(self, data_url: str):
        """从 base64 dataUrl 加载头像并裁成圆形；失败返回 None。"""
        try:
            b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
            raw = base64.b64decode(b64)
            src = QPixmap()
            if not src.loadFromData(raw):
                return None
            size = AVATAR_SIZE
            dst = QPixmap(size, size)
            dst.fill(Qt.GlobalColor.transparent)
            painter = QPainter(dst)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addEllipse(0, 0, size, size)
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, size, size, src)
            painter.end()
            return dst
        except Exception:
            return None

    def _render_text_pixmap(self, content: str, avatar_pm=None):
        """把弹幕文本（可带头像）预渲染到 QPixmap。在独立 pixmap 上 drawText，
        避免窗口 QSS/上下文干扰导致 CJK 变 tofu。与 danmuai overlay 的预渲染一致。"""
        avatar_w = float(avatar_pm.width()) + AVATAR_GAP if avatar_pm is not None else 0.0
        text_width = float(self.font_metrics.horizontalAdvance(content))
        width = text_width + avatar_w
        height = float(self.font_metrics.height())
        dpr = self.devicePixelRatio() or 1.0
        # QPixmap 需要 int 尺寸；int(float*float) 会传 float 参数触发 TypeError，
        # 导致 pixmap 创建失败 → item.pixmap=None → 窗口全透明不可见。
        w_px = max(1, int(round((width + 16) * dpr)))
        h_px = max(1, int(round((height + 16) * dpr)))
        pm = QPixmap(w_px, h_px)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self.font)
        text_x = 8
        if avatar_pm is not None:
            av_y = max(0, int(round(((height + 16) - AVATAR_SIZE) / 2)))
            painter.drawPixmap(text_x, av_y, avatar_pm)
            text_x += int(round(avatar_w))
        baseline_y = 8 + self.font_metrics.ascent()
        outline = QPen(QColor(0, 0, 0, 200))
        for dx, dy in _OUTLINE_OFFSETS:
            painter.setPen(outline)
            painter.drawText(text_x + dx, baseline_y + dy, content)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(text_x, baseline_y, content)
        painter.end()
        return pm

    def _pick_track(self, item_width: float, min_gap: float, force: bool = False) -> int:
        safe: list[tuple[int, int]] = []
        idle: list[int] = []
        for idx, track in enumerate(self.tracks):
            if not track.can_accept(item_width, self._screen_width, min_gap):
                continue
            busy = track.entry_zone_count(self._screen_width)
            safe.append((idx, busy))
            if busy == 0 and not track.items:
                idle.append(idx)
        if not safe:
            if force:
                # catgirl 弹幕（猫娘声音）全忙也强制入最不忙轨道，避免被弹幕潮淹没
                best_idx = -1
                best_busy: int | None = None
                for idx, track in enumerate(self.tracks):
                    if self._top_lane_until.get(idx, 0.0) > time.monotonic():
                        continue
                    busy = track.entry_zone_count(self._screen_width)
                    if best_busy is None or busy < best_busy:
                        best_busy = busy
                        best_idx = idx
                return best_idx
            return -1
        if idle:
            # 优先上方：低索引（顶部）轨道权重更高
            weights = [1.0 / (1.0 + idx) for idx in idle]
            total = sum(weights)
            roll = random.random() * total
            acc = 0.0
            for idx, w in zip(idle, weights):
                acc += w
                if roll <= acc:
                    return idx
            return idle[-1]
        # 入口区逆密度加权 + 顶部偏好
        weights = [(1.0 / (1.0 + busy)) * (1.0 / (1.0 + idx)) for idx, busy in safe]
        total = sum(weights)
        roll = random.random() * total
        acc = 0.0
        for (idx, busy), w in zip(safe, weights):
            acc += w
            if roll <= acc:
                return idx
        return safe[-1][0]

    # ---- 绘制 ----
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        for track in self.tracks:
            for item in track.items:
                if item.right_edge() < 0 or item.x > self._screen_width + FADE_IN_PX:
                    continue
                if item.pixmap is None:
                    continue
                opacity = self._item_opacity(item)
                if opacity <= 0.0:
                    continue
                painter.setOpacity(opacity)
                painter.drawPixmap(QPointF(item.x, item.y), item.pixmap)
        painter.setOpacity(1.0)

    def _item_opacity(self, item: DanmuItem) -> float:
        if item.expire_at:
            # 顶部弹幕：前 0.4s 淡入、后 0.6s 淡出
            now = time.monotonic()
            remaining = item.expire_at - now
            elapsed = TOP_DURATION_SEC - remaining
            fade_in = max(0.0, min(1.0, elapsed / 0.4))
            fade_out = max(0.0, min(1.0, remaining / 0.6))
            return min(fade_in, fade_out)
        sw = self._screen_width
        enter = 1.0
        if item.x > sw - FADE_IN_PX:
            enter = max(0.0, min(1.0, (sw - item.x) / FADE_IN_PX))
        exit_ = 1.0
        right = item.right_edge()
        if right < FADE_OUT_PX:
            exit_ = max(0.0, min(1.0, right / FADE_OUT_PX))
        return min(enter, exit_)

    # ---- Win32 点击穿透（WS_EX_LAYERED | WS_EX_TRANSPARENT）----
    def _apply_win32_click_through(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            GWL_EXSTYLE = -20
            GetWindowLongW = ctypes.windll.user32.GetWindowLongW
            SetWindowLongW = ctypes.windll.user32.SetWindowLongW
            style = GetWindowLongW(hwnd, GWL_EXSTYLE)
            SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SSE 订阅（QThread）
# ---------------------------------------------------------------------------
class SseWorker(QThread):
    danmu_received = pyqtSignal(str, str, str, str)  # (text, style, avatar, placement)

    # 断线重连延迟（秒）；实测连接一旦失败/掉线，无重连会永久静默
    RECONNECT_DELAY_SEC = 3.0

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        attempt = 0
        while not self.isInterruptionRequested():
            attempt += 1
            try:
                resp = urllib.request.urlopen(self.url, timeout=60)
            except Exception as exc:
                print(f"[qt_overlay] SSE 连接失败(第{attempt}次): {exc}", flush=True)
                self._sleep_or_stop(self.RECONNECT_DELAY_SEC)
                continue
            try:
                print(f"[qt_overlay] SSE 已连接: {self.url}", flush=True)
                self._drain(resp)
            except Exception as exc:
                print(f"[qt_overlay] SSE 读取异常: {exc}", flush=True)
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            if self.isInterruptionRequested():
                break
            print(f"[qt_overlay] SSE 断开，{self.RECONNECT_DELAY_SEC}s 后重连", flush=True)
            self._sleep_or_stop(self.RECONNECT_DELAY_SEC)

    def _sleep_or_stop(self, seconds: float) -> None:
        """分段睡眠以便响应 requestInterruption()。"""
        step = 0.2
        waited = 0.0
        while waited < seconds and not self.isInterruptionRequested():
            self.msleep(int(step * 1000))
            waited += step

    def _drain(self, resp) -> None:
        """逐字节读 SSE 流，`data: {...}\n\n` 帧解析，弹幕帧 emit 到主线程。"""
        buf = b""
        while not self.isInterruptionRequested():
            chunk = resp.read(1)
            if not chunk:
                break
            buf += chunk
            if buf.endswith(b"\n\n"):
                line = buf.decode("utf-8", "replace")
                buf = b""
                if line.startswith("data: "):
                    payload = line[len("data: "):].strip()
                    try:
                        msg = json.loads(payload)
                    except Exception:
                        continue
                    if isinstance(msg, dict) and msg.get("type") == "danmu" and msg.get("text"):
                        text = str(msg["text"])
                        style = str(msg.get("style") or "narration")
                        avatar = str(msg.get("avatar") or "")
                        placement = str(msg.get("placement") or "scrolling")
                        print(f"[qt_overlay] SSE 收到弹幕: {text[:40]}", flush=True)
                        self.danmu_received.emit(text, style, avatar, placement)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
def _parse_args(argv):
    parser = argparse.ArgumentParser(description="STS2 陪玩点评 Qt 透明弹幕浮层")
    parser.add_argument("--url", required=True, help="SSE 事件流地址，如 .../ui-api/events")
    parser.add_argument("--screen", type=int, default=0, help="目标显示器索引（默认 0）")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED_PX_S, help="滚动速度 px/s（默认 180）")
    parser.add_argument("--font-size", type=int, default=20, help="字体大小（默认 20）")
    parser.add_argument("--height-percent", type=int, default=DEFAULT_HEIGHT_PERCENT, help="可显示弹幕高度百分比（默认 30，靠顶）")
    parser.add_argument("--window", default="", help="贴合到标题含此关键字的窗口（如 \"Slay the Spire\"）")
    parser.add_argument("--rect", default="", help="手动指定几何 WxH+X+Y（如 1600x900+0+0）")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    # 透明置顶窗口 + GPU 合成在部分 Windows 上会令 CJK 文字渲染成方块（tofu）。
    # 强制软件渲染（raster）以稳定文字字形。
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    rect = None
    if args.window:
        rect = find_window_rect(args.window)
        if rect is None:
            print(f"[qt_overlay] 未找到标题含 '{args.window}' 的窗口，回退全屏", flush=True)
    if rect is None and args.rect:
        rect = parse_rect(args.rect)
        if rect is None:
            print(f"[qt_overlay] --rect 格式非法: {args.rect}（应为 WxH+X+Y）", flush=True)
    window = DanmuOverlayWindow(
        speed_px_s=args.speed,
        font_size=args.font_size,
        screen_index=args.screen,
        rect=rect,
        height_percent=args.height_percent,
    )
    if rect:
        window.show()
        print(f"[qt_overlay] 已贴合窗口矩形: {rect[2]}x{rect[3]}@{rect[0]},{rect[1]}", flush=True)
    else:
        window.showFullScreen()

    worker = SseWorker(args.url)
    worker.danmu_received.connect(window.push)
    worker.start()

    print(f"[qt_overlay] 已启动：SSE={args.url} screen={args.screen} "
          f"lanes={window.lane_count} speed={args.speed}px/s", flush=True)
    rc = app.exec()
    worker.requestInterruption()
    worker.wait(3000)
    return rc


if __name__ == "__main__":
    sys.exit(main())
