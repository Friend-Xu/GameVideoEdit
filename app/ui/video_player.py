"""视频播放器 Widget —— 集成播放、标注、控制。"""

import json
import os

import cv2
import numpy as np
from PySide6.QtCore import Qt, QPoint, QRect, QSettings, QSize, QTimer, Signal
from PySide6.QtGui import (
    QColor, QDragEnterEvent, QDropEvent, QFont, QImage, QPixmap,
)
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSlider, QSplitter, QStyle, QStyleOptionSlider,
    QVBoxLayout, QWidget,
)

from app.core.player import VideoPlayer as CorePlayer
from app.ui.overlay import OverlayWidget


class VideoSlider(QSlider):
    sliderClicked = Signal(int)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            opt = QStyleOptionSlider(); self.initStyleOption(opt)
            rect = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
            pos = event.position().toPoint().x() - rect.x()
            value = round(self.minimum() + (self.maximum() - self.minimum()) * (pos / rect.width()))
            self.setValue(value); self.sliderClicked.emit(value)


class VideoPlayerWidget(QWidget):
    """视频播放器 + 标注工具"""
    frame_changed = Signal(int)
    DEFAULT_LABELS = ["击杀提示", "爆头提示", "武器类型", "淘汰播报"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(800, 500); self.setAcceptDrops(True)
        self._player = CorePlayer()
        self._video_path: str | None = None; self._rotation = 0
        self._current_frame_image: np.ndarray | None = None
        self._display_size = QSize(); self._display_offset = QPoint()
        self._original_size = QSize()
        self._regions: list[list] = []; self._region_counter = 0
        self._label_types = list(self.DEFAULT_LABELS)
        self._current_label = self._label_types[0]
        self._label_colors = self._gen_colors()
        self._selected_region_idx = -1
        self._drawing = False; self._start_point = QPoint()
        self._current_rect: list | None = None
        self._setup_ui(); self.setMouseTracking(True)
        self._timer = QTimer(self); self._timer.timeout.connect(self._on_timer); self._timer.setInterval(33)
        self._load_history()

    def _setup_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(5, 5, 5, 5); layout.setSpacing(5)
        splitter = QSplitter(Qt.Horizontal); layout.addWidget(splitter, 1)
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(5)
        self._video_container = QFrame(); self._video_container.setObjectName("videoContainer")
        self._video_container.setStyleSheet("background-color: black; border-radius: 6px;")
        vl = QVBoxLayout(self._video_container); vl.setContentsMargins(0, 0, 0, 0)
        self._video_label = QLabel(); self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.setMinimumSize(640, 480); self._video_label.setStyleSheet("background: black;")
        vl.addWidget(self._video_label)
        self._overlay = OverlayWidget(self._video_label)
        ll.addWidget(self._video_container, 1)
        self._slider = VideoSlider(Qt.Horizontal); self._slider.setRange(0, 100)
        self._slider.sliderMoved.connect(self._set_position); self._slider.sliderClicked.connect(self._set_position)
        ll.addWidget(self._slider)
        ctrl = QHBoxLayout(); ctrl.setSpacing(5)
        self._play_btn = QPushButton("播放"); self._play_btn.clicked.connect(self._toggle_play); ctrl.addWidget(self._play_btn)
        ctrl.addWidget(QPushButton("上一帧", clicked=self._prev_frame))
        ctrl.addWidget(QPushButton("下一帧", clicked=self._next_frame))
        self._mark_btn = QPushButton("标记区域"); self._mark_btn.setCheckable(True); ctrl.addWidget(self._mark_btn)
        ctrl.addWidget(QPushButton("旋转", clicked=self._rotate))
        ctrl.addWidget(QPushButton("清除标记", clicked=self._clear_regions))
        ll.addLayout(ctrl); splitter.addWidget(left)

        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(5, 5, 5, 5); rl.setSpacing(8)
        g1 = QGroupBox("标签管理"); gl = QVBoxLayout(g1); gl.setSpacing(5)
        tl = QHBoxLayout(); tl.addWidget(QLabel("标签类型:"))
        self._type_combo = QComboBox(); self._type_combo.addItems(self._label_types)
        self._type_combo.currentTextChanged.connect(lambda t: setattr(self, '_current_label', t))
        tl.addWidget(self._type_combo, 1)
        btn_add = QPushButton("+"); btn_add.setFixedSize(30, 30); btn_add.clicked.connect(self._add_label_type); tl.addWidget(btn_add)
        btn_del = QPushButton("-"); btn_del.setFixedSize(30, 30); btn_del.clicked.connect(self._del_label_type); tl.addWidget(btn_del)
        gl.addLayout(tl)
        self._tag_list = QListWidget(); self._tag_list.setMinimumHeight(150)
        self._tag_list.itemSelectionChanged.connect(self._on_tag_selected); gl.addWidget(self._tag_list)
        btns = QHBoxLayout()
        btns.addWidget(QPushButton("编辑", clicked=self._edit_tag))
        btns.addWidget(QPushButton("删除", clicked=self._delete_tag))
        gl.addLayout(btns); rl.addWidget(g1)

        g2 = QGroupBox("历史视频"); hl = QVBoxLayout(g2)
        self._history_list = QListWidget(); self._history_list.itemDoubleClicked.connect(self._open_history); hl.addWidget(self._history_list)
        hl.addWidget(QPushButton("清除历史", clicked=self._clear_history)); rl.addWidget(g2)
        splitter.addWidget(right); splitter.setSizes([700, 300])

    # ---- 视频操作 ----
    def open_video_file(self, path: str):
        self._player.close(); self._timer.stop()
        try: info = self._player.open(path)
        except Exception as e: QMessageBox.warning(self, "错误", f"无法打开视频:\n{e}"); return
        self._video_path = path; self._original_size = QSize(info.width, info.height)
        self._rotation = 0; self._regions.clear(); self._region_counter = 0; self._selected_region_idx = -1
        self._overlay.set_video_size(self._original_size); self._overlay.set_regions([])
        self._overlay.set_label_colors(self._label_colors)
        self._slider.setValue(0); self._play_btn.setText("播放")
        frame = self._player.seek(0)
        if frame is not None: self._display_frame(frame)
        self._update_tag_list(); self._add_to_history(path)

    def _toggle_play(self):
        if self._timer.isActive(): self._timer.stop(); self._play_btn.setText("播放")
        elif self._player.is_open: self._timer.start(); self._play_btn.setText("暂停")

    def _on_timer(self):
        frame = self._player.next_frame()
        if frame is None: self._timer.stop(); self._play_btn.setText("播放"); return
        self._display_frame(frame)
        if self._player.video_info:
            self._slider.setValue(int(self._player.current_frame / self._player.video_info.total_frames * 100))

    def _set_position(self, pos: int):
        if not self._player.is_open: return
        self._timer.stop(); self._play_btn.setText("播放")
        target = int(pos / 100.0 * self._player.video_info.total_frames)
        try: self._display_frame(self._player.seek(target))
        except Exception: pass

    def _prev_frame(self):
        self._timer.stop(); self._play_btn.setText("播放")
        if self._player.is_open:
            try: self._display_frame(self._player.seek(max(0, self._player.current_frame - 1)))
            except Exception: pass

    def _next_frame(self):
        self._timer.stop(); self._play_btn.setText("播放")
        f = self._player.next_frame()
        if f is not None: self._display_frame(f)

    def _rotate(self):
        self._rotation = (self._rotation + 90) % 360
        if self._current_frame_image is not None: self._display_frame(self._current_frame_image)

    def _display_frame(self, frame: np.ndarray):
        self._current_frame_image = frame.copy()
        pre_size = QSize(frame.shape[1], frame.shape[0])
        if self._rotation == 90: frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self._rotation == 180: frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif self._rotation == 270: frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB); h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        scaled = pixmap.scaled(self._video_label.width(), self._video_label.height(),
                               Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._display_size = scaled.size()
        self._display_offset = QPoint(
            (self._video_label.width() - scaled.width()) // 2,
            (self._video_label.height() - scaled.height()) // 2)
        self._video_label.setPixmap(scaled)
        self._overlay.set_display_rect(QRect(self._display_offset, self._display_size))
        self._overlay.set_video_size(pre_size); self._overlay.update()
        self.frame_changed.emit(self._player.current_frame)

    # ---- 标注 ----
    def _add_label_type(self):
        name, ok = QInputDialog.getText(self, "添加标签", "标签名称:")
        if ok and name and name not in self._label_types:
            self._label_types.append(name); self._type_combo.addItem(name)
            self._label_colors[name] = QColor(np.random.randint(50, 200), np.random.randint(50, 200), np.random.randint(50, 200), 200)

    def _del_label_type(self):
        t = self._type_combo.currentText()
        if t in self.DEFAULT_LABELS: QMessageBox.warning(self, "警告", "默认标签不能删除"); return
        self._label_types.remove(t); self._type_combo.removeItem(self._type_combo.currentIndex())
        self._label_colors.pop(t, None)
        self._regions = [r for r in self._regions if r[4] != t]; self._update_tag_list(); self._overlay.update()

    def _on_tag_selected(self):
        items = self._tag_list.selectedItems()
        self._selected_region_idx = self._tag_list.row(items[0]) if items else -1
        self._overlay.set_selected_region(self._selected_region_idx)

    def _edit_tag(self):
        items = self._tag_list.selectedItems()
        if not items: return
        idx = self._tag_list.row(items[0])
        if idx < 0 or idx >= len(self._regions): return
        r = self._regions[idx]; x, y, w, h, label, rid = r
        dlg = QDialog(self); dlg.setWindowTitle("编辑区域"); lo = QFormLayout(dlg)
        xe = QLineEdit(str(x)); ye = QLineEdit(str(y)); we = QLineEdit(str(w)); he = QLineEdit(str(h))
        lo.addRow("X:", xe); lo.addRow("Y:", ye); lo.addRow("宽:", we); lo.addRow("高:", he)
        cb = QComboBox(); cb.addItems(self._label_types); cb.setCurrentText(label); lo.addRow("标签:", cb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); lo.addRow(bb)
        if dlg.exec() == QDialog.Accepted:
            try: self._regions[idx] = [int(xe.text()), int(ye.text()), int(we.text()), int(he.text()), cb.currentText(), rid]; self._update_tag_list()
            except ValueError: QMessageBox.warning(self, "错误", "请输入有效整数")

    def _delete_tag(self):
        items = self._tag_list.selectedItems()
        if not items: return
        idx = self._tag_list.row(items[0])
        if 0 <= idx < len(self._regions): del self._regions[idx]; self._update_tag_list()

    def _clear_regions(self):
        self._regions.clear(); self._current_rect = None
        self._overlay.set_regions([]); self._overlay.set_current_region(None); self._update_tag_list()

    # ---- 鼠标 ----
    def mousePressEvent(self, event):
        if not self._mark_btn.isChecked() or event.button() != Qt.LeftButton:
            super().mousePressEvent(event); return
        pos = event.position().toPoint(); lp = self._video_label.mapFrom(self, pos)
        if (self._display_offset.x() <= lp.x() < self._display_offset.x() + self._display_size.width() and
                self._display_offset.y() <= lp.y() < self._display_offset.y() + self._display_size.height()):
            x = (lp.x() - self._display_offset.x()) * self._original_size.width() / self._display_size.width()
            y = (lp.y() - self._display_offset.y()) * self._original_size.height() / self._display_size.height()
            self._drawing = True; self._start_point = QPoint(int(x), int(y))
            self._current_rect = [int(x), int(y), 0, 0]; self._overlay.set_current_region(self._current_rect)

    def mouseMoveEvent(self, event):
        if not self._drawing: super().mouseMoveEvent(event); return
        pos = event.position().toPoint(); lp = self._video_label.mapFrom(self, pos)
        x = (lp.x() - self._display_offset.x()) * self._original_size.width() / self._display_size.width()
        y = (lp.y() - self._display_offset.y()) * self._original_size.height() / self._display_size.height()
        x = max(0, min(x, self._original_size.width())); y = max(0, min(y, self._original_size.height()))
        x1 = min(self._start_point.x(), x); y1 = min(self._start_point.y(), y)
        x2 = max(self._start_point.x(), x); y2 = max(self._start_point.y(), y)
        if x2 - x1 > 5 and y2 - y1 > 5:
            self._current_rect = [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]
            self._overlay.set_current_region(self._current_rect)

    def mouseReleaseEvent(self, event):
        if self._drawing and event.button() == Qt.LeftButton:
            self._drawing = False
            if self._current_rect and self._current_rect[2] > 5 and self._current_rect[3] > 5:
                self._region_counter += 1
                self._regions.append(self._current_rect + [self._current_label, self._region_counter])
                self._overlay.set_regions(self._regions); self._update_tag_list()
            self._current_rect = None; self._overlay.set_current_region(None)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith(('.mp4', '.avi', '.mov', '.mkv')): self.open_video_file(p); break

    # ---- 历史 ----
    def _add_to_history(self, path: str):
        for i in range(self._history_list.count()):
            if self._history_list.item(i).data(Qt.UserRole) == path: self._history_list.takeItem(i); break
        item = QListWidgetItem(os.path.basename(path)); item.setData(Qt.UserRole, path); item.setToolTip(path)
        self._history_list.insertItem(0, item)
        while self._history_list.count() > 10: self._history_list.takeItem(10); self._save_history()

    def _open_history(self, item):
        path = item.data(Qt.UserRole)
        if os.path.exists(path): self.open_video_file(path)
        else: self._history_list.takeItem(self._history_list.row(item)); self._save_history()

    def _clear_history(self): self._history_list.clear(); self._save_history()

    def _save_history(self):
        QSettings("GameVideoEdit", "PeaceEliteHighlights").setValue("video_history", json.dumps([
            self._history_list.item(i).data(Qt.UserRole) for i in range(self._history_list.count())]))

    def _load_history(self):
        data = QSettings("GameVideoEdit", "PeaceEliteHighlights").value("video_history", "")
        if data:
            try:
                for p in json.loads(data):
                    if os.path.exists(p):
                        item = QListWidgetItem(os.path.basename(p)); item.setData(Qt.UserRole, p); item.setToolTip(p)
                        self._history_list.addItem(item)
            except Exception: pass

    def _gen_colors(self) -> dict:
        base = [QColor(220, 50, 50, 200), QColor(50, 180, 50, 200), QColor(50, 120, 220, 200),
                QColor(220, 160, 50, 200), QColor(180, 50, 180, 200), QColor(50, 180, 180, 200),
                QColor(220, 120, 50, 200), QColor(150, 50, 220, 200)]
        return {l: base[i % len(base)] for i, l in enumerate(self._label_types)}

    def _update_tag_list(self):
        self._tag_list.clear()
        for r in self._regions:
            x, y, w, h, label, rid = r
            item = QListWidgetItem(f"{label} [{rid}] - ({int(x)},{int(y)},{int(w)},{int(h)})")
            item.setForeground(self._label_colors.get(label, Qt.black)); self._tag_list.addItem(item)

    @property
    def video_path(self) -> str | None: return self._video_path
    @property
    def regions(self) -> list: return list(self._regions)
    @property
    def rotation(self) -> int: return self._rotation

    def save_annotations(self) -> dict | None:
        if not self._regions or not self._player.is_open: return None
        info = self._player.video_info
        if info is None: return None
        yolo = []
        for r in self._regions:
            x, y, w, h, label, rid = r
            yolo.append({"id": rid, "label": label, "center_x": (x + w/2)/info.width,
                         "center_y": (y + h/2)/info.height, "width": w/info.width, "height": h/info.height})
        return {"video_path": self._video_path, "width": info.width, "height": info.height,
                "fps": info.fps, "total_frames": info.total_frames, "rotation": self._rotation, "regions": yolo}
