import sys
import cv2
import os
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QLabel, QFileDialog,
    QHBoxLayout, QListWidget, QListWidgetItem, QLineEdit, QSlider, QMessageBox, QComboBox
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QEvent
from PySide6.QtGui import QPixmap, QImage, QMouseEvent, QPainter, QColor


class VideoPlayer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频标注工具")
        self.video_path = None
        self.capture = None
        self.current_frame = None
        self.timer = QTimer(self)
        self.frame_index = 0
        self.fps = 30
        self.playing = False
        self.playback_speed = 1.0
        self.rectangles = []  # [(x, y, w, h, label)] in original frame coords
        self.labels = ["kill_area", "headshot_area"]
        self.current_label = self.labels[0]
        self.mouse_pressed = False
        self.start_pos = None
        self.temp_end_pos = None
        self.frame_size = (1, 1)  # (w, h)
        self.video_pixmap = None

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        self.video_label = QLabel("视频预览区")
        self.video_label.setFixedSize(960, 540)
        self.video_label.setStyleSheet("background-color: #333")
        self.video_label.mousePressEvent = self.on_mouse_press
        self.video_label.mouseReleaseEvent = self.on_mouse_release
        self.video_label.mouseMoveEvent = self.on_mouse_move
        layout.addWidget(self.video_label)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.valueChanged.connect(self.on_slider_changed)
        self.slider.mousePressEvent = self.on_slider_clicked
        layout.addWidget(self.slider)

        btn_layout = QHBoxLayout()
        self.open_btn = QPushButton("打开视频")
        self.open_btn.clicked.connect(self.open_video)
        btn_layout.addWidget(self.open_btn)

        self.play_btn = QPushButton("播放/暂停")
        self.play_btn.clicked.connect(self.toggle_play)
        btn_layout.addWidget(self.play_btn)

        self.speed_box = QComboBox()
        self.speed_box.addItems(["0.25x", "0.5x", "1x", "1.5x", "2x"])
        self.speed_box.setCurrentText("1x")
        self.speed_box.currentTextChanged.connect(self.change_speed)
        btn_layout.addWidget(QLabel("播放速度:"))
        btn_layout.addWidget(self.speed_box)

        layout.addLayout(btn_layout)

        label_layout = QHBoxLayout()
        self.label_list = QListWidget()
        for label in self.labels:
            self.label_list.addItem(label)
        self.label_list.itemClicked.connect(self.select_label)
        label_layout.addWidget(self.label_list)

        form_layout = QVBoxLayout()
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("输入新标签名")
        form_layout.addWidget(self.label_input)

        add_btn = QPushButton("添加标签")
        add_btn.clicked.connect(self.add_label)
        form_layout.addWidget(add_btn)

        del_btn = QPushButton("删除所选标签")
        del_btn.clicked.connect(self.remove_label)
        form_layout.addWidget(del_btn)

        label_layout.addLayout(form_layout)
        layout.addLayout(label_layout)

        self.setLayout(layout)
        self.timer.timeout.connect(self.next_frame)
        self.setAcceptDrops(True)

    def change_speed(self, text):
        self.playback_speed = float(text.replace("x", ""))
        if self.playing:
            self.timer.stop()
            self.timer.start(int(1000 / (self.fps * self.playback_speed)))

    def on_slider_clicked(self, event):
        if self.capture and self.slider.maximum() > 0:
            pos = event.position().x() if hasattr(event, 'position') else event.x()
            ratio = pos / self.slider.width()
            frame_number = int(ratio * self.slider.maximum())
            self.slider.setValue(frame_number)
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ret, frame = self.capture.read()
            if ret and frame is not None:
                self.current_frame = frame
                self.update_frame()

    def open_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Video Files (*.mp4 *.avi)")
        if path:
            self.load_video(path)

    def load_video(self, path):
        self.video_path = path
        self.capture = cv2.VideoCapture(path)
        if not self.capture.isOpened():
            QMessageBox.critical(self, "错误", "无法打开视频文件")
            return
        self.fps = self.capture.get(cv2.CAP_PROP_FPS)
        self.slider.setMaximum(int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.playing = False
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = self.capture.read()
        if ret:
            self.current_frame = frame
            self.frame_size = (frame.shape[1], frame.shape[0])
            self.update_frame()

    def toggle_play(self):
        if not self.capture:
            return
        if self.playing:
            self.timer.stop()
        else:
            self.timer.start(int(1000 / (self.fps * self.playback_speed)))
        self.playing = not self.playing

    def next_frame(self):
        if not self.capture:
            return
        ret, frame = self.capture.read()
        if not ret or frame is None:
            self.timer.stop()
            return
        self.current_frame = frame
        self.frame_index = int(self.capture.get(cv2.CAP_PROP_POS_FRAMES))
        self.slider.setValue(self.frame_index)
        self.update_frame()

    def update_frame(self):
        if self.current_frame is None:
            return

        frame = self.current_frame.copy()
        h, w = frame.shape[:2]
        for rect in self.rectangles:
            x, y, rw, rh, label = rect
            x1, y1 = int(x), int(y)
            x2, y2 = int(x + rw), int(y + rh)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.putText(frame, label, (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_frame.shape
        bytes_per_line = ch * w
        image = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
        self.video_pixmap = QPixmap.fromImage(image)

        if self.temp_end_pos and self.start_pos:
            temp_pixmap = self.video_pixmap.copy()
            painter = QPainter(temp_pixmap)
            painter.setPen(QColor(0, 255, 0))
            sp = self.map_to_frame(self.start_pos)
            ep = self.map_to_frame(self.temp_end_pos)
            rect = QRectF(QPointF(*sp), QPointF(*ep)).normalized()
            painter.drawRect(rect)
            painter.end()
            self.video_label.setPixmap(temp_pixmap.scaled(self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.video_label.setPixmap(self.video_pixmap.scaled(self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def map_to_frame(self, widget_point):
        label_size = self.video_label.size()
        pixmap_size = self.video_pixmap.size()
        scale = min(label_size.width() / pixmap_size.width(), label_size.height() / pixmap_size.height())
        x_offset = (label_size.width() - pixmap_size.width() * scale) / 2
        y_offset = (label_size.height() - pixmap_size.height() * scale) / 2
        x = (widget_point.x() - x_offset) / scale
        y = (widget_point.y() - y_offset) / scale
        return int(x), int(y)

    def on_slider_changed(self, value):
        if self.capture:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, value)
            ret, frame = self.capture.read()
            if ret and frame is not None:
                self.current_frame = frame
                self.update_frame()

    def select_label(self, item):
        self.current_label = item.text()

    def add_label(self):
        text = self.label_input.text()
        if text and text not in self.labels:
            self.labels.append(text)
            self.label_list.addItem(text)
            self.label_input.clear()

    def remove_label(self):
        selected = self.label_list.currentItem()
        if selected:
            label = selected.text()
            self.labels.remove(label)
            self.label_list.takeItem(self.label_list.currentRow())
            if self.current_label == label and self.labels:
                self.current_label = self.labels[0]

    def on_mouse_press(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self.video_pixmap:
            self.mouse_pressed = True
            self.start_pos = event.position().toPoint()
            self.temp_end_pos = None

    def on_mouse_release(self, event: QMouseEvent):
        if self.mouse_pressed and self.video_pixmap:
            self.temp_end_pos = event.position().toPoint()
            sp = self.map_to_frame(self.start_pos)
            ep = self.map_to_frame(self.temp_end_pos)
            x1, y1 = sp
            x2, y2 = ep
            x, y = min(x1, x2), min(y1, y2)
            w, h = abs(x1 - x2), abs(y1 - y2)
            if w > 5 and h > 5:
                self.rectangles.append((x, y, w, h, self.current_label))
            self.mouse_pressed = False
            self.temp_end_pos = None
            self.update_frame()

    def on_mouse_move(self, event: QMouseEvent):
        if self.mouse_pressed:
            self.temp_end_pos = event.position().toPoint()
            self.update_frame()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.lower().endswith((".mp4", ".avi")):
                self.load_video(file_path)
                break


if __name__ == "__main__":
    app = QApplication(sys.argv)
    player = VideoPlayer()
    player.resize(1024, 720)
    player.show()
    sys.exit(app.exec())
