"""标注覆盖层 —— 在视频上绘制虚线标注框。"""

from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


class OverlayWidget(QWidget):
    """透明覆盖层，在视频上绘制标注区域"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._regions: list = []
        self._current_region: list | None = None
        self._selected_idx: int = -1
        self._label_colors: dict[str, QColor] = {}
        self._video_size = QSize()
        self._display_rect = QRect()
        self._default_colors = [
            QColor(220, 50, 50, 200), QColor(50, 180, 50, 200),
            QColor(50, 120, 220, 200), QColor(220, 160, 50, 200),
            QColor(180, 50, 180, 200), QColor(50, 180, 180, 200),
            QColor(220, 120, 50, 200), QColor(150, 50, 220, 200),
        ]

    def set_regions(self, regions): self._regions = regions; self.update()
    def set_label_colors(self, c): self._label_colors = c; self.update()
    def set_current_region(self, r): self._current_region = r; self.update()
    def set_selected_region(self, i): self._selected_idx = i; self.update()
    def set_video_size(self, s): self._video_size = s; self.update()
    def set_display_rect(self, r): self._display_rect = r; self.setGeometry(r); self.update()

    def resizeEvent(self, event):
        self.setGeometry(0, 0, self.parent().width(), self.parent().height())
        super().resizeEvent(event)

    def paintEvent(self, event):
        if not self._video_size.isValid() or self._display_rect.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        sx = self._display_rect.width() / self._video_size.width()
        sy = self._display_rect.height() / self._video_size.height()

        for i, region in enumerate(self._regions):
            if isinstance(region, dict):
                x = int((region["center_x"] - region["width"] / 2) * self._video_size.width())
                y = int((region["center_y"] - region["height"] / 2) * self._video_size.height())
                w = int(region["width"] * self._video_size.width())
                h = int(region["height"] * self._video_size.height())
                label = region.get("label", "")
            else:
                x, y, w, h = region[0], region[1], region[2], region[3]
                label = region[4] if len(region) > 4 else ""

            dx = self._display_rect.x() + x * sx; dy = self._display_rect.y() + y * sy
            dw, dh = w * sx, h * sy
            color = self._label_colors.get(label, self._default_colors[i % len(self._default_colors)])
            pen = QPen(color, 2); pen.setStyle(Qt.DashLine); painter.setPen(pen)
            painter.drawRect(int(dx), int(dy), int(dw), int(dh))
            painter.fillRect(int(dx), int(dy - 20), max(int(dw), 40), 20, color)
            painter.setPen(Qt.white); painter.setFont(QFont("Microsoft YaHei", 9))
            painter.drawText(int(dx) + 4, int(dy) - 5, label)
            if i == self._selected_idx:
                painter.setPen(QPen(QColor(255, 255, 0), 3))
                painter.drawRect(int(dx), int(dy), int(dw), int(dh))

        if self._current_region:
            x, y, w, h = self._current_region[:4]
            dx = self._display_rect.x() + x * sx; dy = self._display_rect.y() + y * sy
            dw, dh = w * sx, h * sy
            pen = QPen(QColor(0, 200, 255), 2); pen.setStyle(Qt.DashLine); painter.setPen(pen)
            painter.drawRect(int(dx), int(dy), int(dw), int(dh))
            painter.fillRect(int(dx), int(dy - 20), max(int(dw), 50), 20, QColor(0, 200, 255, 200))
            painter.setPen(Qt.white); painter.setFont(QFont("Microsoft YaHei", 9))
            painter.drawText(int(dx) + 4, int(dy) - 5, "绘制中")
