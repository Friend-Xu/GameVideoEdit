import sys
import os
import json
import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, QSize, QRect, QPoint, QSettings, Signal
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QFont, QBrush, QDragEnterEvent, QDropEvent, QIcon, \
    QTransform
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QSlider, QPushButton, QFileDialog, QGroupBox,
                               QListWidget, QListWidgetItem, QLineEdit, QComboBox,
                               QMessageBox, QSizePolicy, QSplitter, QToolBar, QStatusBar,
                               QDialog, QDialogButtonBox, QFormLayout, QInputDialog,
                                QFrame, QStyle)
from PySide6.QtGui import QAction

class VideoSlider(QSlider):
    """自定义视频滑块，支持点击跳转"""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 计算点击位置对应的值
            value = QStyle.sliderValueFromPosition(
                self.minimum(),
                self.maximum(),
                event.position().x(),
                self.width()
            )
            self.setValue(value)
            self.sliderMoved.emit(value)
        super().mousePressEvent(event)


class VideoPlayer(QWidget):
    frame_changed = Signal(int)  # 帧变化信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(800, 500)
        self.setAcceptDrops(True)  # 启用拖放功能

        # 视频相关变量
        self.video_path = ""
        self.cap = None
        self.total_frames = 0
        self.fps = 0
        self.current_frame = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.play_next_frame)

        # 标记相关变量
        self.regions = []  # 存储标记区域 [x, y, w, h, label_type]
        self.current_region = None
        self.drawing = False
        self.start_point = QPoint()
        self.label_types = ["击杀提示", "爆头提示", "武器类型", "伤害类型"]
        self.current_label_type = self.label_types[0]

        # 视频显示相关
        self.original_size = QSize()  # 原始视频尺寸
        self.display_rect = QRect()  # 实际显示区域
        self.scale_factor = 1.0  # 缩放比例
        self.rotation = 0  # 旋转角度

        # 标签颜色映射
        self.label_colors = {
            "击杀提示": QColor(255, 0, 0),  # 红色
            "爆头提示": QColor(0, 255, 0),  # 绿色
            "武器类型": QColor(0, 0, 255),  # 蓝色
            "伤害类型": QColor(255, 255, 0)  # 黄色
        }

        # UI设置
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)

        # 创建分割器：左侧视频，右侧标签
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # 左侧视频区域
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 视频显示区域容器（用于正确计算坐标）
        video_container = QFrame()
        video_layout = QVBoxLayout(video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)

        self.video_label = QLabel("拖放视频文件到此处或点击打开按钮")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("""
            background-color: #2D2D30;
            color: #CCCCCC;
            font-size: 16px;
            border: 2px dashed #555555;
            padding: 20px;
        """)
        self.video_label.setMinimumHeight(400)
        video_layout.addWidget(self.video_label, 0, Qt.AlignCenter)

        left_layout.addWidget(video_container)

        # 进度条
        self.slider = VideoSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.sliderMoved.connect(self.set_position)
        left_layout.addWidget(self.slider)

        # 控制按钮
        control_layout = QHBoxLayout()

        self.open_btn = QPushButton("打开视频")
        self.open_btn.setIcon(QIcon.fromTheme("document-open"))
        self.open_btn.clicked.connect(self.open_video)
        control_layout.addWidget(self.open_btn)

        self.play_btn = QPushButton("播放")
        self.play_btn.setIcon(QIcon.fromTheme("media-playback-start"))
        self.play_btn.clicked.connect(self.toggle_play)
        control_layout.addWidget(self.play_btn)

        self.prev_btn = QPushButton("上一帧")
        self.prev_btn.setIcon(QIcon.fromTheme("media-skip-backward"))
        self.prev_btn.clicked.connect(self.prev_frame)
        control_layout.addWidget(self.prev_btn)

        self.next_btn = QPushButton("下一帧")
        self.next_btn.setIcon(QIcon.fromTheme("media-skip-forward"))
        self.next_btn.clicked.connect(self.next_frame)
        control_layout.addWidget(self.next_btn)

        self.mark_btn = QPushButton("标记区域")
        self.mark_btn.setIcon(QIcon.fromTheme("draw-rectangle"))
        self.mark_btn.clicked.connect(self.toggle_marking)
        self.mark_btn.setCheckable(True)
        control_layout.addWidget(self.mark_btn)

        self.clear_btn = QPushButton("清除标记")
        self.clear_btn.setIcon(QIcon.fromTheme("edit-clear"))
        self.clear_btn.clicked.connect(self.clear_regions)
        control_layout.addWidget(self.clear_btn)

        left_layout.addLayout(control_layout)

        # 右侧标签区域
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 10, 10, 10)

        # 标签管理
        label_group = QGroupBox("标签管理")
        label_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #555555;
                border-radius: 5px;
                margin-top: 1ex;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
        """)
        label_layout = QVBoxLayout(label_group)

        # 标签类型选择
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("标签类型:"))

        self.type_combo = QComboBox()
        self.type_combo.addItems(self.label_types)
        self.type_combo.currentTextChanged.connect(self.set_label_type)
        type_layout.addWidget(self.type_combo)

        self.add_type_btn = QPushButton("+")
        self.add_type_btn.setFixedSize(30, 30)
        self.add_type_btn.clicked.connect(self.add_label_type)
        type_layout.addWidget(self.add_type_btn)

        self.rotate_btn = QPushButton("旋转")
        self.rotate_btn.setIcon(QIcon.fromTheme("object-rotate-right"))
        self.rotate_btn.clicked.connect(self.rotate_video)
        type_layout.addWidget(self.rotate_btn)

        label_layout.addLayout(type_layout)

        # 标签列表
        self.tag_list = QListWidget()
        self.tag_list.setStyleSheet("""
            QListWidget {
                background-color: #252526;
                color: #D4D4D4;
                border: 1px solid #3C3C3C;
                border-radius: 3px;
            }
            QListWidget::item {
                padding: 5px;
            }
            QListWidget::item:selected {
                background-color: #094771;
                color: white;
            }
        """)
        self.tag_list.setMinimumHeight(200)
        self.tag_list.itemSelectionChanged.connect(self.on_tag_selected)
        label_layout.addWidget(self.tag_list)

        # 标签操作按钮
        btn_layout = QHBoxLayout()

        self.edit_tag_btn = QPushButton("编辑")
        self.edit_tag_btn.clicked.connect(self.edit_selected_tag)
        btn_layout.addWidget(self.edit_tag_btn)

        self.delete_tag_btn = QPushButton("删除")
        self.delete_tag_btn.clicked.connect(self.delete_selected_tag)
        btn_layout.addWidget(self.delete_tag_btn)

        label_layout.addLayout(btn_layout)

        right_layout.addWidget(label_group)

        # 历史记录
        history_group = QGroupBox("历史视频")
        history_layout = QVBoxLayout(history_group)

        self.history_list = QListWidget()
        self.history_list.setStyleSheet(self.tag_list.styleSheet())
        self.history_list.setMinimumHeight(100)
        self.history_list.itemDoubleClicked.connect(self.open_history_video)
        history_layout.addWidget(self.history_list)

        self.clear_history_btn = QPushButton("清除历史记录")
        self.clear_history_btn.clicked.connect(self.clear_history)
        history_layout.addWidget(self.clear_history_btn)

        right_layout.addWidget(history_group)

        # 添加到分割器
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([700, 300])

        # 状态标签
        self.status_label = QLabel("准备就绪")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("background-color: #1E1E1E; color: #CCCCCC; padding: 5px;")
        main_layout.addWidget(self.status_label)

        # 加载历史记录
        self.load_history()

    def dragEnterEvent(self, event: QDragEnterEvent):
        """处理拖入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        """处理拖放事件"""
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv')):
                self.open_video_file(file_path)
                break

    def open_video(self):
        """打开视频文件对话框"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "打开视频文件", "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*.*)"
        )

        if file_path:
            self.open_video_file(file_path)

    def open_video_file(self, file_path):
        """打开视频文件"""
        self.video_path = file_path
        self.cap = cv2.VideoCapture(file_path)

        if not self.cap.isOpened():
            QMessageBox.critical(self, "错误", "无法打开视频文件")
            return

        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.slider.setRange(0, self.total_frames)
        self.current_frame = 0

        # 获取第一帧以确定原始尺寸
        ret, frame = self.cap.read()
        if ret:
            self.original_size = QSize(frame.shape[1], frame.shape[0])
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        # 更新状态
        self.update_frame()
        self.status_label.setText(f"已打开: {os.path.basename(file_path)}")

        # 添加到历史记录
        self.add_to_history(file_path)

    def add_to_history(self, file_path):
        """添加到历史记录列表"""
        # 检查是否已存在
        for i in range(self.history_list.count()):
            if self.history_list.item(i).data(Qt.UserRole) == file_path:
                return

        # 添加到列表
        item = QListWidgetItem(os.path.basename(file_path))
        item.setData(Qt.UserRole, file_path)
        item.setToolTip(file_path)
        self.history_list.insertItem(0, item)

        # 保存历史记录
        self.save_history()

    def open_history_video(self, item):
        """打开历史记录中的视频"""
        file_path = item.data(Qt.UserRole)
        if os.path.exists(file_path):
            self.open_video_file(file_path)
        else:
            QMessageBox.warning(self, "文件不存在", "视频文件已移动或删除")
            self.history_list.takeItem(self.history_list.row(item))
            self.save_history()

    def clear_history(self):
        """清除历史记录"""
        self.history_list.clear()
        self.save_history()

    def save_history(self):
        """保存历史记录到设置"""
        settings = QSettings("GameVideoEdit", "PeaceEliteHighlights")
        history = []

        for i in range(self.history_list.count()):
            item = self.history_list.item(i)
            history.append(item.data(Qt.UserRole))

        settings.setValue("video_history", json.dumps(history))

    def load_history(self):
        """从设置加载历史记录"""
        settings = QSettings("GameVideoEdit", "PeaceEliteHighlights")
        history_json = settings.value("video_history", "")

        if history_json:
            try:
                history = json.loads(history_json)
                for file_path in history:
                    if os.path.exists(file_path):
                        item = QListWidgetItem(os.path.basename(file_path))
                        item.setData(Qt.UserRole, file_path)
                        item.setToolTip(file_path)
                        self.history_list.addItem(item)
            except:
                pass

    def toggle_play(self):
        """切换播放/暂停"""
        if self.timer.isActive():
            self.timer.stop()
            self.play_btn.setIcon(QIcon.fromTheme("media-playback-start"))
            self.play_btn.setText("播放")
        else:
            if self.cap is not None:
                self.timer.start(int(1000 / self.fps))
                self.play_btn.setIcon(QIcon.fromTheme("media-playback-pause"))
                self.play_btn.setText("暂停")

    def prev_frame(self):
        """跳转到上一帧"""
        if self.cap is not None:
            self.current_frame = max(0, self.current_frame - 1)
            self.update_frame()

    def next_frame(self):
        """跳转到下一帧"""
        if self.cap is not None:
            self.current_frame = min(self.total_frames - 1, self.current_frame + 1)
            self.update_frame()

    def play_next_frame(self):
        """播放下一帧"""
        if self.cap is not None:
            self.current_frame += 1
            if self.current_frame >= self.total_frames:
                self.current_frame = 0
                self.timer.stop()
                self.play_btn.setIcon(QIcon.fromTheme("media-playback-start"))
                self.play_btn.setText("播放")
            self.update_frame()

    def toggle_marking(self):
        """切换标记模式"""
        if self.mark_btn.isChecked():
            self.status_label.setText("标记模式: 按住鼠标左键拖拽标记区域")
            # 进入标记模式时暂停播放
            if self.timer.isActive():
                self.toggle_play()
        else:
            self.status_label.setText("标记已禁用")

    def set_label_type(self, label_type):
        """设置当前标签类型"""
        self.current_label_type = label_type

    def add_label_type(self):
        """添加新标签类型"""
        text, ok = QInputDialog.getText(self, "添加标签类型", "请输入新标签名称:")
        if ok and text:
            if text not in self.label_types:
                self.label_types.append(text)
                self.type_combo.addItem(text)
                self.label_colors[text] = QColor(np.random.randint(50, 200),
                                                 np.random.randint(50, 200),
                                                 np.random.randint(50, 200))

    def rotate_video(self):
        """旋转视频显示方向"""
        self.rotation = (self.rotation + 90) % 360
        self.update_frame()

    def on_tag_selected(self):
        """标签选择事件"""
        selected_items = self.tag_list.selectedItems()
        if selected_items:
            index = self.tag_list.row(selected_items[0])
            if 0 <= index < len(self.regions):
                # 高亮显示选中的区域
                self.update_frame()

    def edit_selected_tag(self):
        """编辑选中的标签"""
        selected_items = self.tag_list.selectedItems()
        if not selected_items:
            return

        item = selected_items[0]
        region_idx = self.tag_list.row(item)

        if 0 <= region_idx < len(self.regions):
            # 获取当前区域信息
            x, y, w, h, label_type = self.regions[region_idx]

            # 创建编辑对话框
            dialog = QDialog(self)
            dialog.setWindowTitle("编辑标签")
            layout = QVBoxLayout(dialog)

            # 位置信息
            pos_layout = QFormLayout()
            x_edit = QLineEdit(str(x))
            y_edit = QLineEdit(str(y))
            w_edit = QLineEdit(str(w))
            h_edit = QLineEdit(str(h))

            pos_layout.addRow("X:", x_edit)
            pos_layout.addRow("Y:", y_edit)
            pos_layout.addRow("宽度:", w_edit)
            pos_layout.addRow("高度:", h_edit)

            layout.addLayout(pos_layout)

            # 标签类型
            type_combo = QComboBox()
            type_combo.addItems(self.label_types)
            type_combo.setCurrentText(label_type)
            layout.addWidget(QLabel("标签类型:"))
            layout.addWidget(type_combo)

            # 按钮
            btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            btn_box.accepted.connect(dialog.accept)
            btn_box.rejected.connect(dialog.reject)
            layout.addWidget(btn_box)

            if dialog.exec() == QDialog.Accepted:
                try:
                    # 更新区域
                    self.regions[region_idx] = [
                        int(x_edit.text()),
                        int(y_edit.text()),
                        int(w_edit.text()),
                        int(h_edit.text()),
                        type_combo.currentText()
                    ]

                    # 更新标签列表
                    self.update_tag_list()
                    self.update_frame()
                except ValueError:
                    QMessageBox.warning(self, "输入错误", "请输入有效的整数值")

    def delete_selected_tag(self):
        """删除选中的标签"""
        selected_items = self.tag_list.selectedItems()
        if not selected_items:
            return

        item = selected_items[0]
        region_idx = self.tag_list.row(item)

        if 0 <= region_idx < len(self.regions):
            # 从区域列表中删除
            del self.regions[region_idx]

            # 更新标签列表
            self.update_tag_list()
            self.update_frame()

    def update_frame(self):
        """更新视频帧显示"""
        if self.cap is not None:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame)
            ret, frame = self.cap.read()

            if ret:
                # 转换为RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = frame_rgb.shape
                bytes_per_line = ch * w
                q_img = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)

                # 创建QPixmap
                pixmap = QPixmap.fromImage(q_img)

                # 应用旋转
                if self.rotation != 0:
                    transform = QTransform().rotate(self.rotation)
                    pixmap = pixmap.transformed(transform, Qt.SmoothTransformation)

                # 绘制标记
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.Antialiasing)

                # 绘制所有标记区域
                for i, region in enumerate(self.regions):
                    x, y, w, h, label_type = region
                    color = self.label_colors.get(label_type, Qt.yellow)

                    # 设置不同线宽区分选中状态
                    line_width = 3 if i == self.tag_list.currentRow() else 2

                    pen = QPen(color, line_width, Qt.DashLine)
                    painter.setPen(pen)
                    painter.drawRect(x, y, w, h)

                    # 绘制区域名称
                    painter.setFont(QFont("Arial", 12, QFont.Bold))
                    painter.setBrush(QBrush(color))
                    painter.drawText(x + 5, y + 20, label_type)

                # 绘制当前正在标记的区域
                if self.current_region:
                    x, y, w, h = self.current_region
                    pen = QPen(Qt.yellow, 3, Qt.SolidLine)
                    painter.setPen(pen)
                    painter.drawRect(x, y, w, h)

                painter.end()

                # 显示图像
                self.display_pixmap = pixmap
                self.update_video_display()

                self.slider.setValue(self.current_frame)
                self.frame_changed.emit(self.current_frame)

    def update_video_display(self):
        """更新视频显示，保持宽高比并记录显示区域"""
        if hasattr(self, 'display_pixmap') and not self.display_pixmap.isNull():
            pixmap = self.display_pixmap

            # 获取标签尺寸
            label_width = self.video_label.width()
            label_height = self.video_label.height()

            # 计算缩放比例
            pix_ratio = pixmap.width() / pixmap.height()
            label_ratio = label_width / label_height

            if pix_ratio > label_ratio:
                # 宽度是限制因素
                scaled_width = label_width
                scaled_height = int(label_width / pix_ratio)
            else:
                # 高度是限制因素
                scaled_height = label_height
                scaled_width = int(label_height * pix_ratio)

            # 计算显示区域（居中）
            x_offset = (label_width - scaled_width) // 2
            y_offset = (label_height - scaled_height) // 2
            self.display_rect = QRect(x_offset, y_offset, scaled_width, scaled_height)

            # 计算缩放比例
            self.scale_factor = scaled_width / pixmap.width()

            # 缩放图像
            scaled_pixmap = pixmap.scaled(
                scaled_width,
                scaled_height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )

            self.video_label.setPixmap(scaled_pixmap)

    def resizeEvent(self, event):
        """窗口大小变化时更新视频显示"""
        super().resizeEvent(event)
        self.update_video_display()

    def set_position(self, position):
        """设置视频位置"""
        if self.cap is not None:
            self.current_frame = position
            self.update_frame()

    def map_to_image(self, pos):
        """将鼠标位置映射到原始图像坐标"""
        if not self.display_rect.isValid():
            return None

        # 转换为相对于显示区域的坐标
        relative_x = pos.x() - self.display_rect.x()
        relative_y = pos.y() - self.display_rect.y()

        # 检查是否在图像范围内
        if (0 <= relative_x < self.display_rect.width() and
                0 <= relative_y < self.display_rect.height()):
            # 转换为原始图像坐标
            img_x = int(relative_x / self.scale_factor)
            img_y = int(relative_y / self.scale_factor)

            return QPoint(img_x, img_y)

        return None

    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if self.mark_btn.isChecked() and event.button() == Qt.LeftButton:
            # 获取相对于视频标签的坐标
            label_pos = self.video_label.mapFromParent(event.pos())

            # 映射到原始图像坐标
            img_point = self.map_to_image(label_pos)
            if img_point:
                self.drawing = True
                self.start_point = img_point
                self.current_region = [img_point.x(), img_point.y(), 0, 0]
                self.update_frame()

    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if self.drawing:
            # 获取相对于视频标签的坐标
            label_pos = self.video_label.mapFromParent(event.pos())

            # 映射到原始图像坐标
            img_point = self.map_to_image(label_pos)
            if img_point:
                # 更新当前区域
                x1 = min(self.start_point.x(), img_point.x())
                y1 = min(self.start_point.y(), img_point.y())
                x2 = max(self.start_point.x(), img_point.x())
                y2 = max(self.start_point.y(), img_point.y())

                self.current_region = [x1, y1, x2 - x1, y2 - y1]
                self.update_frame()

    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if self.drawing and event.button() == Qt.LeftButton:
            self.drawing = False

            if self.current_region and self.current_region[2] > 10 and self.current_region[3] > 10:
                # 添加到区域列表
                self.regions.append(self.current_region + [self.current_label_type])
                self.status_label.setText(f"已标记区域: {self.current_label_type}")

                # 更新标签列表
                self.update_tag_list()

            self.current_region = None
            self.update_frame()

    def update_tag_list(self):
        """更新标签列表"""
        self.tag_list.clear()
        for i, region in enumerate(self.regions):
            x, y, w, h, label_type = region
            item = QListWidgetItem(f"{label_type} - [{x}, {y}, {w}, {h}]")
            item.setForeground(self.label_colors.get(label_type, Qt.white))
            self.tag_list.addItem(item)

    def clear_regions(self):
        """清除所有标记区域"""
        self.regions = []
        self.current_region = None
        self.update_tag_list()
        self.update_frame()
        self.status_label.setText("已清除所有标记区域")

    def save_regions(self):
        """保存标记区域到文件"""
        if not self.regions:
            return []

        # 获取原始视频尺寸
        if self.cap is not None:
            width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            # 转换为YOLO格式 (归一化中心坐标和宽高)
            yolo_regions = []
            for region in self.regions:
                x, y, w, h, label_type = region

                # 计算归一化值
                cx = (x + w / 2) / width
                cy = (y + h / 2) / height
                nw = w / width
                nh = h / height

                yolo_regions.append({
                    "label": label_type,
                    "center_x": cx,
                    "center_y": cy,
                    "width": nw,
                    "height": nh
                })

            self.status_label.setText(f"已保存{len(yolo_regions)}个标记区域(YOLO格式)")
            return yolo_regions
        return []


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("和平精英击杀剪辑工具 - 标注模块")
        self.setGeometry(100, 100, 1200, 800)

        # 设置应用样式
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1E1E1E;
            }
            QWidget {
                color: #D4D4D4;
                background-color: #252526;
                font: 10pt "Segoe UI";
            }
            QPushButton {
                background-color: #3C3C3C;
                border: 1px solid #3C3C3C;
                border-radius: 4px;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #4F4F4F;
                border: 1px solid #6A6A6A;
            }
            QPushButton:pressed {
                background-color: #2D2D30;
                border: 1px solid #3C3C3C;
            }
            QPushButton:checked {
                background-color: #094771;
                border: 1px solid #007ACC;
            }
            QSlider::groove:horizontal {
                background: #3C3C3C;
                height: 8px;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #007ACC;
                border: 1px solid #007ACC;
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }
            QSlider::sub-page:horizontal {
                background: #007ACC;
                border-radius: 4px;
            }
        """)

        # 创建主窗口
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        # 创建工具栏
        toolbar = QToolBar("主工具栏")
        toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(toolbar)

        # 添加工具栏按钮 - 使用QIcon.fromTheme替代
        open_action = toolbar.addAction("打开视频")
        open_action.setIcon(QIcon.fromTheme("document-open"))
        open_action.triggered.connect(self.open_video)

        save_action = toolbar.addAction("保存标注")
        save_action.setIcon(QIcon.fromTheme("document-save"))
        save_action.triggered.connect(self.save_annotations)

        toolbar.addSeparator()

        export_action = toolbar.addAction("导出剪辑")
        export_action.setIcon(QIcon.fromTheme("media-playback-start"))
        export_action.triggered.connect(self.export_clips)

        # 创建状态栏
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)

        # 创建视频播放器
        self.video_player = VideoPlayer()

        # 设置主布局
        layout = QVBoxLayout(main_widget)
        layout.addWidget(self.video_player)

    def open_video(self):
        self.video_player.open_video()

    def save_annotations(self):
        """保存标注信息"""
        regions = self.video_player.save_regions()
        if not regions:
            QMessageBox.warning(self, "警告", "没有要保存的标注信息")
            return

        # 获取视频路径
        video_path = self.video_player.video_path
        if not video_path:
            QMessageBox.warning(self, "警告", "请先打开视频文件")
            return

        # 创建标注文件名
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        default_file = f"{base_name}_annotations.json"

        # 选择保存位置
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存标注文件", default_file, "JSON文件 (*.json)"
        )

        if file_path:
            # 添加视频信息
            annotation_data = {
                "video_path": video_path,
                "frame_width": int(self.video_player.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "frame_height": int(self.video_player.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "annotations": regions
            }

            # 保存到文件
            try:
                with open(file_path, 'w') as f:
                    json.dump(annotation_data, f, indent=2)
                QMessageBox.information(self, "成功", f"标注信息已保存到:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存标注失败:\n{str(e)}")

    def export_clips(self):
        """导出剪辑"""
        QMessageBox.information(self, "功能提示", "剪辑功能将在下一部分实现")


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 设置应用样式
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())