"""视频播放器 Widget —— 播放、标注、控制。"""

import cv2
import numpy as np
from PySide6.QtCore import Qt, QEvent, QPoint, QRect, QSize, QTimer, QUrl, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
    QSlider, QStyle, QStyleOptionSlider, QVBoxLayout, QWidget,
)

from app.core.player import VideoPlayer as CorePlayer
from app.core.project import Project
from app.ui.overlay import OverlayWidget


class VideoSlider(QSlider):
    sliderClicked = Signal(int)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            rect = self.style().subControlRect(
                QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
            pos = event.position().toPoint().x() - rect.x()
            value = round(self.minimum() + (self.maximum() - self.minimum())
                          * (pos / rect.width()))
            self.setValue(value)
            self.sliderClicked.emit(value)


class VideoPlayerWidget(QWidget):
    """视频播放器 + 标注工具"""
    frame_changed = Signal(int)
    regions_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._player = CorePlayer()
        self._audio_output = QAudioOutput()
        self._audio_player = QMediaPlayer()
        self._audio_player.setAudioOutput(self._audio_output)
        self._video_path: str | None = None
        self._rotation = 0
        self._current_frame_image: np.ndarray | None = None
        self._display_size = QSize()
        self._display_offset = QPoint()
        self._original_size = QSize()
        self._regions: list[list] = []
        self._region_counter = 0
        self._drawing = False
        self._start_point = QPoint()
        self._current_rect: list | None = None
        self._project: Project | None = None
        self._current_label: str = ""
        self._label_colors: dict = {}
        self._setup_ui()
        self.setMouseTracking(True)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self._video_container = QFrame()
        self._video_container.setObjectName("videoContainer")
        self._video_container.setStyleSheet(
            "background-color: black; border-radius: 6px;")
        vl = QVBoxLayout(self._video_container)
        vl.setContentsMargins(0, 0, 0, 0)
        self._video_label = QLabel()
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._video_label.setStyleSheet("background: black;")
        self._video_label.installEventFilter(self)
        vl.addWidget(self._video_label)
        self._overlay = OverlayWidget(self._video_label)
        layout.addWidget(self._video_container, 1)

        self._slider = VideoSlider(Qt.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.sliderMoved.connect(self._set_position)
        self._slider.sliderClicked.connect(self._set_position)
        layout.addWidget(self._slider)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(5)

        # 播放控制  [⏮][▶][⏭]
        ctrl.addWidget(QPushButton("上一帧", clicked=self._prev_frame))
        self._play_btn = QPushButton("播放")
        self._play_btn.clicked.connect(self._toggle_play)
        ctrl.addWidget(self._play_btn)
        ctrl.addWidget(QPushButton("下一帧", clicked=self._next_frame))
        ctrl.addSpacing(10)

        # 变换  [↻]
        ctrl.addWidget(QPushButton("旋转", clicked=self._rotate))
        ctrl.addSpacing(10)

        # 标注操作  [✚][✕]
        self._mark_btn = QPushButton("标记区域")
        self._mark_btn.setCheckable(True)
        ctrl.addWidget(self._mark_btn)
        ctrl.addWidget(QPushButton("清除标记", clicked=self._clear_regions))

        ctrl.addStretch()
        layout.addLayout(ctrl)

    def set_project(self, project: Project):
        self._project = project

    def set_label_colors(self, colors: dict):
        self._label_colors = colors
        self._overlay.set_label_colors(colors)

    def set_current_label(self, label: str):
        self._current_label = label

    # ---- 视频操作 ----

    def open_video_file(self, path: str):
        self._player.close()
        self._timer.stop()
        try:
            info = self._player.open(path)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开视频:\n{e}")
            return
        self._video_path = path
        self._original_size = QSize(info.width, info.height)
        self._rotation = 0
        self._regions.clear()
        self._region_counter = 0
        self._overlay.set_video_size(self._original_size)
        self._overlay.set_regions([])
        self._slider.setValue(0)
        self._play_btn.setText("播放")
        self._timer_interval = max(16, int(1000 / info.fps))
        self._timer.setInterval(self._timer_interval)
        frame = self._player.seek_rgb(0)
        if frame is not None:
            self._current_frame_image = frame
            QTimer.singleShot(0, lambda: self._display_frame(
                self._current_frame_image))
        if self._project:
            self._project.set_video(
                path, info.width, info.height, info.fps, info.total_frames)
            self.refresh_regions()
        # 加载音频 — QMediaPlayer 直接解码视频中的音频流，无需提取
        self._audio_player.setSource(QUrl.fromLocalFile(path))

    def _toggle_play(self):
        if not self._player.is_open:
            return
        if self._timer.isActive():
            self._timer.stop()
            self._audio_player.pause()
            self._play_btn.setText("播放")
        else:
            self._timer.start()
            self._audio_player.play()
            self._play_btn.setText("暂停")

    def _on_timer(self):
        frame = self._player.next_frame_rgb()
        if frame is None:
            self._timer.stop()
            self._audio_player.pause()
            self._play_btn.setText("播放")
            return
        self._display_frame(frame)
        if self._player.video_info:
            self._slider.setValue(int(
                self._player.current_frame /
                self._player.video_info.total_frames * 100))

    def seek_to_second(self, second: float):
        if not self._player.is_open or not self._player.video_info:
            return
        self._timer.stop()
        self._audio_player.pause()
        self._audio_player.setPosition(int(second * 1000))
        self._play_btn.setText("播放")
        target = int(second * self._player.video_info.fps)
        try:
            self._display_frame(self._player.seek_rgb(target))
        except Exception:
            pass

    def _set_position(self, pos: int):
        if not self._player.is_open or not self._player.video_info:
            return
        self._timer.stop()
        self._audio_player.pause()
        self._play_btn.setText("播放")
        target = int(pos / 100.0 * self._player.video_info.total_frames)
        second = target / self._player.video_info.fps
        self._audio_player.setPosition(int(second * 1000))
        try:
            self._display_frame(self._player.seek_rgb(target))
        except Exception:
            pass

    def _prev_frame(self):
        self._timer.stop()
        self._audio_player.pause()
        self._play_btn.setText("播放")
        if self._player.is_open:
            try:
                self._display_frame(
                    self._player.seek_rgb(max(0, self._player.current_frame - 1)))
            except Exception:
                pass

    def _next_frame(self):
        self._timer.stop()
        self._audio_player.pause()
        self._play_btn.setText("播放")
        f = self._player.next_frame_rgb()
        if f is not None:
            self._display_frame(f)

    def _rotate(self):
        self._rotation = (self._rotation + 90) % 360
        if self._project:
            self._project.detection.rotation = self._rotation
        if self._current_frame_image is not None:
            self._display_frame(self._current_frame_image)

    def eventFilter(self, obj, event):
        if obj is self._video_label and event.type() == QEvent.Resize:
            if self._current_frame_image is not None:
                self._display_frame(self._current_frame_image)
        return super().eventFilter(obj, event)

    def _display_frame(self, frame: np.ndarray):
        self._current_frame_image = frame
        pre_size = QSize(frame.shape[1], frame.shape[0])
        if self._rotation == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self._rotation == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif self._rotation == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        h, w, ch = frame.shape
        qimg = QImage(frame.data, w, h, ch * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        scaled = pixmap.scaled(
            self._video_label.width(), self._video_label.height(),
            Qt.KeepAspectRatio, Qt.FastTransformation)
        self._display_size = scaled.size()
        self._display_offset = QPoint(
            (self._video_label.width() - scaled.width()) // 2,
            (self._video_label.height() - scaled.height()) // 2)
        self._video_label.setPixmap(scaled)
        self._overlay.set_display_rect(
            QRect(self._display_offset, self._display_size))
        self._overlay.set_video_size(pre_size)
        self._overlay.update()
        self.frame_changed.emit(self._player.current_frame)

    # ---- 区域刷新 ----

    def refresh_regions(self):
        """从 Project 读取区域并刷新 overlay"""
        if not self._project or self._original_size.isEmpty():
            return
        w, h = self._original_size.width(), self._original_size.height()
        self._regions.clear()
        for r, roi in zip(
            self._project.annotations.regions,
            self._project.annotations.to_pixel_rois(w, h),
        ):
            self._regions.append(
                [roi.x, roi.y, roi.w, roi.h, r.label, r.id])
        if self._regions:
            self._region_counter = max(r[5] for r in self._regions)
        self._overlay.set_regions(self._regions)

    def set_selected_region(self, idx: int):
        self._overlay.set_selected_region(idx)

    # ---- 标注（鼠标拖拽） ----

    def _clear_regions(self):
        if self._project:
            self._project.annotations.clear_regions()
            self._project.auto_save_roi()
        self._regions.clear()
        self._current_rect = None
        self._overlay.set_regions([])
        self._overlay.set_current_region(None)
        self.regions_changed.emit()

    def mousePressEvent(self, event):
        if not self._mark_btn.isChecked() or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint()
        lp = self._video_label.mapFrom(self, pos)
        if (self._display_offset.x() <= lp.x() <
                self._display_offset.x() + self._display_size.width() and
                self._display_offset.y() <= lp.y() <
                self._display_offset.y() + self._display_size.height()):
            x = ((lp.x() - self._display_offset.x()) *
                 self._original_size.width() / self._display_size.width())
            y = ((lp.y() - self._display_offset.y()) *
                 self._original_size.height() / self._display_size.height())
            self._drawing = True
            self._start_point = QPoint(int(x), int(y))
            self._current_rect = [int(x), int(y), 0, 0]
            self._overlay.set_current_region(self._current_rect)

    def mouseMoveEvent(self, event):
        if not self._drawing:
            super().mouseMoveEvent(event)
            return
        pos = event.position().toPoint()
        lp = self._video_label.mapFrom(self, pos)
        x = ((lp.x() - self._display_offset.x()) *
             self._original_size.width() / self._display_size.width())
        y = ((lp.y() - self._display_offset.y()) *
             self._original_size.height() / self._display_size.height())
        x = max(0, min(x, self._original_size.width()))
        y = max(0, min(y, self._original_size.height()))
        x1 = min(self._start_point.x(), x)
        y1 = min(self._start_point.y(), y)
        x2 = max(self._start_point.x(), x)
        y2 = max(self._start_point.y(), y)
        if x2 - x1 > 5 and y2 - y1 > 5:
            self._current_rect = [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]
            self._overlay.set_current_region(self._current_rect)

    def mouseReleaseEvent(self, event):
        if self._drawing and event.button() == Qt.LeftButton:
            self._drawing = False
            if (self._current_rect and self._current_rect[2] > 5
                    and self._current_rect[3] > 5):
                if self._project:
                    x, y, w, h = self._current_rect
                    self._project.annotations.add_region(
                        self._current_label, x, y, w, h,
                        self._original_size.width(),
                        self._original_size.height())
                    self._project.auto_save_roi()
                self.refresh_regions()
                self.regions_changed.emit()
            self._current_rect = None
            self._overlay.set_current_region(None)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith(('.mp4', '.avi', '.mov', '.mkv')):
                self.open_video_file(p)
                break

    @property
    def regions(self) -> list:
        return list(self._regions)

    @property
    def rotation(self) -> int:
        if self._project:
            return self._project.detection.rotation
        return self._rotation

    def save_annotations(self) -> dict | None:
        if not self._regions or not self._player.is_open:
            return None
        info = self._player.video_info
        if info is None:
            return None
        yolo = []
        for r in self._regions:
            x, y, w, h, label, rid = r
            yolo.append({
                "id": rid, "label": label,
                "center_x": (x + w / 2) / info.width,
                "center_y": (y + h / 2) / info.height,
                "width": w / info.width, "height": h / info.height,
            })
        return {
            "video_path": self._video_path,
            "width": info.width, "height": info.height,
            "fps": info.fps, "total_frames": info.total_frames,
            "rotation": self._rotation, "regions": yolo,
        }
