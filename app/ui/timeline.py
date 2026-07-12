"""时间轴组件 —— 刻度尺 + 片段可视化 + 播放游标。

单项依赖: 只消费 Project 数据，不修改。
信号通知外部: seekRequested, clipHovered, clipSelected。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPen, QWheelEvent,
)
from PySide6.QtWidgets import QWidget


ACTOR_COLORS: dict[str, QColor] = {
    "自己": QColor(76, 175, 80),
    "队友": QColor(33, 150, 243),
    "敌人": QColor(244, 67, 54),
}
FALLBACK_COLOR = QColor(158, 158, 158)
RULER_H = 22
TRACK_H = 22
PAD_LEFT = 4


class TimelineWidget(QWidget):
    """时间轴主控件。"""

    seekRequested = Signal(float)
    clipHovered = Signal(int)
    clipSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clips: list[dict] = []
        self._duration: float = 1.0
        self._position: float = 0.0
        self._pps: float = 1.0
        self._hovered_idx: int = -1
        self._selected_idx: int = -1
        self._dragging_playhead: bool = False
        self._font = QFont("Segoe UI", 9)
        self._fm = QFontMetrics(self._font)
        self.setMinimumHeight(RULER_H + TRACK_H + 2)
        self.setMouseTracking(True)

    def set_clips(self, clips: list[dict]):
        self._clips = sorted(clips, key=lambda c: c["start"])
        self.update()

    def set_duration(self, seconds: float):
        self._duration = max(seconds, 1.0)
        self._fit_pps()
        self.update()

    def set_position(self, seconds: float):
        self._position = max(0.0, min(seconds, self._duration))
        if not self._dragging_playhead:
            self.update()

    def select_clip(self, idx: int):
        self._selected_idx = idx
        self.update()

    # ── zoom ───────────────────────────────────────────────

    def _fit_pps(self):
        w = self.width() - PAD_LEFT * 2
        self._pps = max(w / self._duration, 0.01)

    def wheelEvent(self, event: QWheelEvent):
        old_pps = self._pps
        mx = event.position().x() - PAD_LEFT
        anchor_sec = mx / max(old_pps, 0.01)
        if event.angleDelta().y() > 0:
            self._pps = min(old_pps * 1.5, 2000.0)
        else:
            self._pps = max(old_pps / 1.5, self.width() / self._duration * 0.5)
        self.update()

    # ── paint ──────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#1e1e1e"))
        p.setFont(self._font)
        self._draw_ruler(p, w)
        self._draw_track(p, w)
        self._draw_playhead(p, h)

    def _sec_to_x(self, sec: float) -> float:
        return PAD_LEFT + sec * self._pps

    def _x_to_sec(self, x: float) -> float:
        return (x - PAD_LEFT) / max(self._pps, 0.01)

    def _draw_ruler(self, p: QPainter, w: int):
        y0, y1 = 0, RULER_H
        p.fillRect(0, y0, w, y1, QColor("#2a2a2a"))
        target_spacing = 60
        nice = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
        interval = 1.0
        for n in nice:
            if n * self._pps >= target_spacing:
                interval = n
                break
        else:
            interval = 600

        p.setPen(QPen(QColor("#666"), 1))
        t = 0.0
        while t <= self._duration + interval:
            x = self._sec_to_x(t)
            if x > w:
                break
            p.drawLine(int(x), y1 - 8, int(x), y1)
            minor_n = 4 if interval >= 1 else 10
            for i in range(1, minor_n):
                mx = self._sec_to_x(t + i * interval / minor_n)
                if mx > w:
                    break
                p.drawLine(int(mx), y1 - 4, int(mx), y1)
            p.setPen(QColor("#aaa"))
            mins, secs = int(t // 60), int(t % 60)
            p.drawText(int(x) + 2, y1 - 10, f"{mins:02d}:{secs:02d}")
            p.setPen(QPen(QColor("#666"), 1))
            t += interval

    def _draw_track(self, p: QPainter, w: int):
        y0, track_h = RULER_H, TRACK_H
        p.fillRect(0, y0, w, track_h, QColor("#252525"))
        p.setPen(QPen(QColor("#444"), 1))
        p.drawLine(0, y0, w, y0)

        for i, c in enumerate(self._clips):
            x0 = self._sec_to_x(c["start"])
            x1 = self._sec_to_x(c["end"])
            if x1 < 0 or x0 > w:
                continue
            x0, x1 = max(x0, 0), min(x1, w)
            rw = max(x1 - x0, 2.0)
            color = ACTOR_COLORS.get(c.get("actor", ""), FALLBACK_COLOR)
            if i == self._selected_idx:
                color = color.lighter(140)
            elif i == self._hovered_idx:
                color = color.lighter(120)
            p.fillRect(int(x0), y0 + 2, int(rw), track_h - 4, color)
            p.setPen(QPen(color.darker(130), 1))
            p.drawRect(int(x0), y0 + 2, int(rw), track_h - 4)
            label = c.get("action", "?")
            p.setPen(QColor("#fff"))
            if self._fm.horizontalAdvance(label) < rw - 4:
                p.drawText(int(x0) + 2, y0 + track_h - 6, label)

    def _draw_playhead(self, p: QPainter, h: int):
        x = self._sec_to_x(self._position)
        if x < 0 or x > self.width():
            return
        p.setPen(QPen(QColor("#ff5252"), 2))
        p.drawLine(int(x), 0, int(x), h)
        sz = 6
        pts = [(int(x) - sz, 0), (int(x) + sz, 0), (int(x), sz)]
        p.setBrush(QColor("#ff5252"))
        p.setPen(Qt.NoPen)
        p.drawPolygon([QPoint(*pt) for pt in pts])

    # ── mouse ──────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton:
            return
        mx = event.position().x()
        t = self._x_to_sec(mx)
        for i, c in enumerate(self._clips):
            x0 = self._sec_to_x(c["start"])
            x1 = self._sec_to_x(c["end"])
            if x0 - 2 <= mx <= x1 + 2:
                self._selected_idx = i
                self.clipSelected.emit(i)
                self.update()
                return
        self._selected_idx = -1
        self.clipSelected.emit(-1)
        self._dragging_playhead = True
        self._do_seek(t)

    def mouseMoveEvent(self, event: QMouseEvent):
        mx = event.position().x()
        if self._dragging_playhead:
            self._do_seek(self._x_to_sec(mx))
            return
        found = -1
        for i, c in enumerate(self._clips):
            x0 = self._sec_to_x(c["start"])
            x1 = self._sec_to_x(c["end"])
            if x0 - 2 <= mx <= x1 + 2:
                found = i
                break
        if found != self._hovered_idx:
            self._hovered_idx = found
            self.clipHovered.emit(found)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._dragging_playhead = False

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        mx = event.position().x()
        for i, c in enumerate(self._clips):
            x0 = self._sec_to_x(c["start"])
            x1 = self._sec_to_x(c["end"])
            if x0 - 2 <= mx <= x1 + 2:
                self.seekRequested.emit(c["start"])
                return

    def leaveEvent(self, _event):
        self._hovered_idx = -1
        self.clipHovered.emit(-1)
        self.update()

    def _do_seek(self, sec: float):
        t = max(0.0, min(sec, self._duration))
        self._position = t
        self.seekRequested.emit(t)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pps < (self.width() - PAD_LEFT * 2) / self._duration * 0.5:
            self._fit_pps()
        self.update()
