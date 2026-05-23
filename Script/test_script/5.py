import sys
import os
import json
import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, QSize, QRect, QPoint, QSettings, Signal, QEvent, QUrl
from PySide6.QtGui import (QImage, QPixmap, QPainter, QPen, QColor, QFont, QBrush,
                           QIcon, QTransform, QAction, QCursor, QKeySequence, QPalette, QDragEnterEvent, QDropEvent)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QSlider, QPushButton, QFileDialog, QGroupBox,
                               QListWidget, QListWidgetItem, QLineEdit, QComboBox,
                               QMessageBox, QSizePolicy, QSplitter, QStatusBar,
                               QDialog, QDialogButtonBox, QFormLayout, QInputDialog,
                               QFrame, QStyle, QMenu, QStyleOptionSlider, QCheckBox, QProgressBar)

from Script.ocr_processor import OCRProcessDialog
# 删除OCRProcessDialog导入

class VideoSlider(QSlider):
    """自定义视频滑块，支持点击跳转"""
    sliderClicked = Signal(int)

    def mousePressEvent(self, event):
        """鼠标按下事件"""
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            # 计算点击位置对应的值
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            rect = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
            pos = event.position().toPoint().x() - rect.x()
            value = round(self.minimum() + (self.maximum() - self.minimum()) * (pos / rect.width()))
            self.setValue(value)
            self.sliderClicked.emit(value)


class VideoPlayer(QWidget):
    frame_changed = Signal(int)  # 帧变化信号

    def __init__(self, parent=None):
        super().__init__(parent)
        # OpenCV视频相关
        self.cap = None
        self.current_frame_pos = 0
        self.total_frames = 0
        self.fps = 30
        self.rotation = 0  # 旋转角度
        self.current_frame_image = None
        self.video_path = None
        self.original_size = QSize()  # 原始视频尺寸
        self.display_size = QSize()  # 实际显示尺寸
        self.display_offset = QPoint()  # 显示偏移量

        self.setMinimumSize(800, 500)
        self.setAcceptDrops(True)  # 启用拖放功能

        # 标记相关变量
        self.regions = []  # 存储标记区域 [x, y, w, h, label_type, id]
        self.current_region = None
        self.drawing = False
        self.start_point = QPoint()
        self.label_types = ["击杀提示", "爆头提示", "武器类型", "淘汰播报"]
        self.current_label_type = self.label_types[0]
        self.selected_region_idx = -1
        self.region_counter = 0  # 用于生成唯一ID

        # 标签颜色映射
        self.label_colors = self.generate_label_colors()

        # UI设置
        self.setup_ui()
        self.setMouseTracking(True)

    def generate_label_colors(self):
        """生成随机但一致的颜色"""
        colors = {}
        base_colors = [
            QColor(220, 50, 50, 200),  # 红色
            QColor(50, 180, 50, 200),  # 绿色
            QColor(50, 120, 220, 200),  # 蓝色
            QColor(220, 160, 50, 200),  # 黄色
            QColor(180, 50, 180, 200),  # 紫色
            QColor(50, 180, 180, 200),  # 青色
            QColor(220, 120, 50, 200),  # 橙色
            QColor(150, 50, 220, 200)  # 紫罗兰
        ]

        for i, label in enumerate(self.label_types):
            if i < len(base_colors):
                colors[label] = base_colors[i]
            else:
                # 随机生成新颜色
                colors[label] = QColor(
                    np.random.randint(50, 200),
                    np.random.randint(50, 200),
                    np.random.randint(50, 200),
                    200
                )
        return colors

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # 创建分割器：左侧视频，右侧标签
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #D0D0D0;
            }
        """)
        main_layout.addWidget(splitter, 1)

        # 左侧视频区域
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)

        # 视频显示区域容器
        self.video_container = QFrame()
        self.video_container.setFrameShape(QFrame.Box)
        self.video_container.setStyleSheet("""
            background-color: #F8F8F8; 
            border: 1px solid #CCCCCC; 
            border-radius: 4px;
        """)
        video_layout = QVBoxLayout(self.video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)

        # 添加视频标签（替代QVideoWidget）
        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(640, 480)
        self.video_label.setStyleSheet("background-color: black;")
        video_layout.addWidget(self.video_label)

        # 创建覆盖层用于绘制标注
        self.overlay = OverlayWidget(self.video_label)  # 直接使用视频标签作为父组件
        self.overlay.set_regions(self.regions)
        self.overlay.set_label_colors(self.label_colors)

        left_layout.addWidget(self.video_container, 1)

        # 进度条
        self.slider = VideoSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.sliderMoved.connect(self.set_position)
        self.slider.sliderClicked.connect(self.set_position)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: #E0E0E0;
                height: 8px;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #3a7ca5;
                border: 1px solid #2c5d83;
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }
            QSlider::sub-page:horizontal {
                background: #3a7ca5;
                border-radius: 4px;
            }
        """)
        left_layout.addWidget(self.slider)

        # 控制按钮
        control_layout = QHBoxLayout()
        control_layout.setSpacing(5)

        self.play_btn = QPushButton("播放")
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.clicked.connect(self.toggle_play)
        self.play_btn.setMinimumHeight(30)
        self.play_btn.setStyleSheet(self.get_button_style())
        control_layout.addWidget(self.play_btn)

        self.prev_btn = QPushButton("上一帧")
        self.prev_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipBackward))
        self.prev_btn.clicked.connect(self.prev_frame)
        self.prev_btn.setMinimumHeight(30)
        self.prev_btn.setStyleSheet(self.get_button_style())
        control_layout.addWidget(self.prev_btn)

        self.next_btn = QPushButton("下一帧")
        self.next_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipForward))
        self.next_btn.clicked.connect(self.next_frame)
        self.next_btn.setMinimumHeight(30)
        self.next_btn.setStyleSheet(self.get_button_style())
        control_layout.addWidget(self.next_btn)

        self.mark_btn = QPushButton("标记区域")
        self.mark_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self.mark_btn.clicked.connect(self.toggle_marking)
        self.mark_btn.setCheckable(True)
        self.mark_btn.setMinimumHeight(30)
        self.mark_btn.setStyleSheet(self.get_button_style())
        control_layout.addWidget(self.mark_btn)

        self.rotate_btn = QPushButton("旋转")
        self.rotate_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.rotate_btn.clicked.connect(self.rotate_video)
        self.rotate_btn.setMinimumHeight(30)
        self.rotate_btn.setStyleSheet(self.get_button_style())
        control_layout.addWidget(self.rotate_btn)

        self.clear_btn = QPushButton("清除标记")
        self.clear_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogResetButton))
        self.clear_btn.clicked.connect(self.clear_regions)
        self.clear_btn.setMinimumHeight(30)
        self.clear_btn.setStyleSheet(self.get_button_style())
        control_layout.addWidget(self.clear_btn)

        left_layout.addLayout(control_layout)

        # 右侧标签区域
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(10)

        # 标签管理
        label_group = QGroupBox("标签管理")
        label_group.setStyleSheet(self.get_groupbox_style())
        label_layout = QVBoxLayout(label_group)
        label_layout.setSpacing(8)

        # 标签类型选择
        type_layout = QHBoxLayout()
        type_layout.setSpacing(5)
        type_layout.addWidget(QLabel("标签类型:"))

        self.type_combo = QComboBox()
        self.type_combo.addItems(self.label_types)
        self.type_combo.currentTextChanged.connect(self.set_label_type)
        self.type_combo.setMinimumHeight(30)
        self.type_combo.setStyleSheet(self.get_combobox_style())
        type_layout.addWidget(self.type_combo, 1)

        self.add_type_btn = QPushButton("+")
        self.add_type_btn.setFixedSize(30, 30)
        self.add_type_btn.setToolTip("添加新标签类型")
        self.add_type_btn.clicked.connect(self.add_label_type)
        self.add_type_btn.setStyleSheet(self.get_button_style())
        type_layout.addWidget(self.add_type_btn)

        self.del_type_btn = QPushButton("-")
        self.del_type_btn.setFixedSize(30, 30)
        self.del_type_btn.setToolTip("删除当前标签类型")
        self.del_type_btn.clicked.connect(self.delete_label_type)
        self.del_type_btn.setStyleSheet(self.get_button_style())
        type_layout.addWidget(self.del_type_btn)

        label_layout.addLayout(type_layout)

        # 标签列表
        self.tag_list = QListWidget()
        self.tag_list.setStyleSheet(self.get_list_style())
        self.tag_list.setMinimumHeight(200)
        self.tag_list.itemSelectionChanged.connect(self.on_tag_selected)
        label_layout.addWidget(self.tag_list, 1)

        # 标签操作按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(5)

        self.edit_tag_btn = QPushButton("编辑")
        self.edit_tag_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogContentsView))
        self.edit_tag_btn.clicked.connect(self.edit_selected_tag)
        self.edit_tag_btn.setMinimumHeight(30)
        self.edit_tag_btn.setStyleSheet(self.get_button_style())
        btn_layout.addWidget(self.edit_tag_btn)

        self.delete_tag_btn = QPushButton("删除")
        self.delete_tag_btn.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.delete_tag_btn.clicked.connect(self.delete_selected_tag)
        self.delete_tag_btn.setMinimumHeight(30)
        self.delete_tag_btn.setStyleSheet(self.get_button_style())
        btn_layout.addWidget(self.delete_tag_btn)

        label_layout.addLayout(btn_layout)

        right_layout.addWidget(label_group, 1)

        # 历史记录
        history_group = QGroupBox("历史视频")
        history_group.setStyleSheet(self.get_groupbox_style())
        history_layout = QVBoxLayout(history_group)

        self.history_list = QListWidget()
        self.history_list.setStyleSheet(self.get_list_style())
        self.history_list.setMinimumHeight(100)
        self.history_list.itemDoubleClicked.connect(self.open_history_video)
        history_layout.addWidget(self.history_list, 1)

        history_btn_layout = QHBoxLayout()
        self.clear_history_btn = QPushButton("清除历史记录")
        self.clear_history_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogResetButton))
        self.clear_history_btn.clicked.connect(self.clear_history)
        self.clear_history_btn.setMinimumHeight(30)
        self.clear_history_btn.setStyleSheet(self.get_button_style())
        history_btn_layout.addWidget(self.clear_history_btn)
        history_layout.addLayout(history_btn_layout)

        right_layout.addWidget(history_group, 1)

        # 添加到分割器
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([700, 300])

        # 底部状态栏
        self.status_label = QLabel("准备就绪")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("""
            background-color: #F0F0F0; 
            color: #333333; 
            padding: 8px;
            border: 1px solid #CCCCCC;
            border-radius: 4px;
        """)
        main_layout.addWidget(self.status_label)

        # 加载历史记录
        self.load_history()

        # 设置视频更新定时器
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_frame)
        self.timer.setInterval(33)  # 约30fps

    def get_button_style(self):
        return """
            QPushButton {
                background-color: #F0F0F0;
                color: #333333;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #E0E0E0;
            }
            QPushButton:pressed {
                background-color: #3a7ca5;
                color: white;
            }
            QPushButton:checked {
                background-color: #3a7ca5;
                color: white;
            }
        """

    def get_groupbox_style(self):
        return """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                margin-top: 1ex;
                background: #FFFFFF;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
                color: #333333;
            }
        """

    def get_list_style(self):
        return """
            QListWidget {
                background-color: #FFFFFF;
                color: #333333;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #EEEEEE;
            }
            QListWidget::item:selected {
                background-color: #3a7ca5;
                color: white;
                border-radius: 4px;
            }
        """

    def get_combobox_style(self):
        return """
            QComboBox {
                background-color: #FFFFFF;
                color: #333333;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 5px;
            }
        """

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
        # 关闭当前视频
        if self.cap:
            self.cap.release()
            self.cap = None

        # 使用OpenCV打开视频
        self.cap = cv2.VideoCapture(file_path)
        if not self.cap.isOpened():
            QMessageBox.warning(self, "错误", "无法打开视频文件")
            return

        # 获取视频信息
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.original_size = QSize(self.width, self.height)

        # 设置覆盖层的视频原始尺寸
        self.overlay.set_video_size(self.original_size)

        # 重置状态
        self.video_path = file_path
        self.slider.setRange(0, 100)
        self.current_frame_pos = 0
        self.rotation = 0
        self.regions = []
        self.region_counter = 0
        self.overlay.set_regions(self.regions)

        # 读取第一帧
        self.current_frame_pos = 0
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = self.cap.read()
        if ret:
            self.display_frame(frame)

        # 更新状态
        self.update_tag_list()
        self.status_label.setText(f"已打开: {os.path.basename(file_path)}")

        # 添加到历史记录
        self.add_to_history(file_path)

    def add_to_history(self, file_path):
        """添加到历史记录列表"""
        # 检查是否已存在
        for i in range(self.history_list.count()):
            if self.history_list.item(i).data(Qt.UserRole) == file_path:
                self.history_list.takeItem(i)
                break

        # 添加到列表顶部
        item = QListWidgetItem(os.path.basename(file_path))
        item.setData(Qt.UserRole, file_path)
        item.setToolTip(file_path)
        self.history_list.insertItem(0, item)

        # 限制历史记录数量
        if self.history_list.count() > 10:
            self.history_list.takeItem(10)

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
        self.status_label.setText("历史记录已清除")

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
            self.play_btn.setText("播放")
            self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
            self.status_label.setText("已暂停")
        else:
            if self.cap:
                self.timer.start()
                self.play_btn.setText("暂停")
                self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
                self.status_label.setText("播放中...")

    def update_frame(self):
        """更新视频帧"""
        if not self.cap:
            return

        ret, frame = self.cap.read()
        if not ret:
            # 视频结束，停止播放
            self.timer.stop()
            self.play_btn.setText("播放")
            self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
            self.status_label.setText("视频播放结束")
            return

        self.current_frame_pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        self.display_frame(frame)

        # 更新进度条
        if self.total_frames > 0:
            progress = int(self.current_frame_pos / self.total_frames * 100)
            self.slider.setValue(progress)

    def display_frame(self, frame):
        """显示帧并处理旋转"""
        # 保存当前帧图像
        self.current_frame_image = frame.copy()

        # 保存旋转前的尺寸（用于坐标转换）
        self.pre_rotation_size = QSize(self.width, self.height)

        # 应用旋转
        if self.rotation == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self.rotation == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif self.rotation == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        # 转换为Qt图像
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        q_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
        # 设置到标签
        pixmap = QPixmap.fromImage(q_img)

        # 计算缩放后的尺寸（保持宽高比）
        scaled_pixmap = pixmap.scaled(
            self.video_label.width(),
            self.video_label.height(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        # 保存实际显示尺寸和偏移量
        self.display_size = scaled_pixmap.size()
        self.display_offset = QPoint(
            (self.video_label.width() - scaled_pixmap.width()) // 2,
            (self.video_label.height() - scaled_pixmap.height()) // 2
        )

        self.video_label.setPixmap(scaled_pixmap)

        # 更新覆盖层 - 保持原始坐标系
        self.overlay.set_display_rect(QRect(
            self.display_offset.x(),
            self.display_offset.y(),
            scaled_pixmap.width(),
            scaled_pixmap.height()
        ))
        self.overlay.set_video_size(self.pre_rotation_size)
        self.overlay.update()

    def set_position(self, position):
        """设置视频位置"""
        if not self.cap:
            return

        # 计算目标帧位置
        target_frame = int(position / 100.0 * self.total_frames)

        # 设置帧位置
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = self.cap.read()
        if ret:
            self.current_frame_pos = target_frame
            self.display_frame(frame)
            self.status_label.setText(f"跳转到: {position}%")

    def prev_frame(self):
        """跳转到上一帧"""
        if not self.cap:
            return

        # 停止播放
        if self.timer.isActive():
            self.timer.stop()
            self.play_btn.setText("播放")
            self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

        # 跳转前一帧
        target_frame = max(0, self.current_frame_pos - 1)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = self.cap.read()
        if ret:
            self.current_frame_pos = target_frame
            self.display_frame(frame)
            self.status_label.setText(f"上一帧: {target_frame}/{self.total_frames}")

    def next_frame(self):
        """跳转到下一帧"""
        if not self.cap:
            return

        # 停止播放
        if self.timer.isActive():
            self.timer.stop()
            self.play_btn.setText("播放")
            self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

        # 跳转后一帧
        target_frame = min(self.total_frames - 1, self.current_frame_pos + 1)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = self.cap.read()
        if ret:
            self.current_frame_pos = target_frame
            self.display_frame(frame)
            self.status_label.setText(f"下一帧: {target_frame}/{self.total_frames}")

    def toggle_marking(self):
        """切换标记模式"""
        if self.mark_btn.isChecked():
            self.status_label.setText("标记模式: 按住鼠标左键拖拽标记区域")
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
                # 生成随机颜色
                self.label_colors[text] = QColor(
                    np.random.randint(50, 200),
                    np.random.randint(50, 200),
                    np.random.randint(50, 200),
                    200
                )
                self.overlay.set_label_colors(self.label_colors)
                self.status_label.setText(f"已添加标签类型: {text}")

    def delete_label_type(self):
        """删除当前标签类型"""
        current_label = self.type_combo.currentText()
        if current_label in ["击杀提示", "爆头提示"]:
            QMessageBox.warning(self, "警告", "系统默认标签不能删除")
            return

        # 确认删除
        reply = QMessageBox.question(
            self, "删除标签类型",
            f"确定要删除标签类型 '{current_label}' 吗?\n所有使用此标签的区域将被删除。",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            # 删除标签类型
            index = self.type_combo.currentIndex()
            self.type_combo.removeItem(index)
            self.label_types.remove(current_label)
            del self.label_colors[current_label]
            self.overlay.set_label_colors(self.label_colors)

            # 删除所有使用此标签的区域
            self.regions = [r for r in self.regions if r[4] != current_label]
            self.overlay.set_regions(self.regions)

            # 更新UI
            self.update_tag_list()
            self.status_label.setText(f"已删除标签类型: {current_label}")

    def rotate_video(self):
        """旋转视频显示方向，但不影响标注框坐标系"""
        self.rotation = (self.rotation + 90) % 360
        # 重新显示当前帧以应用旋转
        if self.current_frame_image is not None:
            self.display_frame(self.current_frame_image)
        self.status_label.setText(f"已旋转: {self.rotation}度")

    def on_tag_selected(self):
        """标签选择事件"""
        selected_items = self.tag_list.selectedItems()
        if selected_items:
            index = self.tag_list.row(selected_items[0])
            self.selected_region_idx = index
            self.overlay.set_selected_region(index)
            self.overlay.update()

    def edit_selected_tag(self):
        """编辑选中的标签"""
        selected_items = self.tag_list.selectedItems()
        if not selected_items:
            return

        item = selected_items[0]
        region_idx = self.tag_list.row(item)

        if 0 <= region_idx < len(self.regions):
            # 获取当前区域信息
            x, y, w, h, label_type, _ = self.regions[region_idx]

            # 创建编辑对话框
            dialog = QDialog(self)
            dialog.setWindowTitle("编辑区域")
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
                        type_combo.currentText(),
                        self.regions[region_idx][5]  # 保持ID不变
                    ]

                    self.overlay.set_regions(self.regions)

                    # 更新标签列表
                    self.update_tag_list()
                    self.status_label.setText("区域信息已更新")
                    self.overlay.update()
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
            self.overlay.set_regions(self.regions)

            # 更新标签列表
            self.update_tag_list()
            self.status_label.setText("已删除选中区域")
            self.overlay.update()

    def mousePressEvent(self, event):
        """鼠标按下事件 - 使用原始坐标系"""
        # 标注模式
        if self.mark_btn.isChecked() and event.button() == Qt.LeftButton:
            # 获取鼠标在视频标签上的位置
            mouse_pos = event.position().toPoint()

            # 转换到视频标签坐标系
            label_pos = self.video_label.mapFrom(self, mouse_pos)

            # 检查是否在显示区域内
            if (self.display_offset.x() <= label_pos.x() < self.display_offset.x() + self.display_size.width() and
                    self.display_offset.y() <= label_pos.y() < self.display_offset.y() + self.display_size.height()):
                # 转换为原始视频坐标（不随旋转变化）
                x = (label_pos.x() - self.display_offset.x()) * self.original_size.width() / self.display_size.width()
                y = (label_pos.y() - self.display_offset.y()) * self.original_size.height() / self.display_size.height()

                # 确保坐标在视频范围内
                x = max(0, min(x, self.original_size.width()))
                y = max(0, min(y, self.original_size.height()))

                self.drawing = True
                self.start_point = QPoint(int(x), int(y))
                self.current_region = [int(x), int(y), 0, 0]
                self.overlay.set_current_region(self.current_region)
                self.overlay.update()

    def mouseMoveEvent(self, event):
        """鼠标移动事件 - 使用原始坐标系"""
        if self.drawing:
            # 获取鼠标位置
            mouse_pos = event.position().toPoint()

            # 转换到视频标签坐标系
            label_pos = self.video_label.mapFrom(self, mouse_pos)

            # 检查是否在显示区域内
            if (self.display_offset.x() <= label_pos.x() < self.display_offset.x() + self.display_size.width() and
                    self.display_offset.y() <= label_pos.y() < self.display_offset.y() + self.display_size.height()):

                # 转换为原始视频坐标（不随旋转变化）
                x = (label_pos.x() - self.display_offset.x()) * self.original_size.width() / self.display_size.width()
                y = (label_pos.y() - self.display_offset.y()) * self.original_size.height() / self.display_size.height()

                # 确保坐标在视频范围内
                x = max(0, min(x, self.original_size.width()))
                y = max(0, min(y, self.original_size.height()))

                # 计算矩形区域
                x1 = min(self.start_point.x(), x)
                y1 = min(self.start_point.y(), y)
                x2 = max(self.start_point.x(), x)
                y2 = max(self.start_point.y(), y)

                w = x2 - x1
                h = y2 - y1

                # 确保最小尺寸
                if w > 5 and h > 5:
                    self.current_region = [int(x1), int(y1), int(w), int(h)]
                    self.overlay.set_current_region(self.current_region)
                    self.overlay.update()

    def mouseReleaseEvent(self, event):
        """鼠标释放事件 - 使用原始坐标系"""
        # 完成标注
        if self.drawing and event.button() == Qt.LeftButton:
            self.drawing = False

            if self.current_region and self.current_region[2] > 5 and self.current_region[3] > 5:
                # 添加到区域列表
                self.region_counter += 1
                self.regions.append(self.current_region + [self.current_label_type, self.region_counter])
                self.overlay.set_regions(self.regions)
                self.status_label.setText(f"已标记区域: {self.current_label_type}")

                # 更新标签列表
                self.update_tag_list()

            self.current_region = None
            self.overlay.set_current_region(None)
            self.overlay.update()

    def update_tag_list(self):
        """更新标签列表"""
        self.tag_list.clear()
        for i, region in enumerate(self.regions):
            x, y, w, h, label_type, region_id = region
            item = QListWidgetItem(f"{label_type} [{region_id}] - ({int(x)}, {int(y)}, {int(w)}, {int(h)})")
            item.setForeground(self.label_colors.get(label_type, Qt.black))
            self.tag_list.addItem(item)

    def clear_regions(self):
        """清除所有标记区域"""
        self.regions = []
        self.current_region = None
        self.overlay.set_regions([])
        self.overlay.set_current_region(None)
        self.update_tag_list()
        self.status_label.setText("已清除所有标记区域")
        self.overlay.update()

    def save_regions(self):
        """保存标记区域到文件 - 使用原始坐标系"""
        if not self.regions:
            return None

        # 获取原始视频尺寸
        video_width = self.original_size.width()
        video_height = self.original_size.height()

        # 转换为YOLO格式 (归一化中心坐标和宽高)
        yolo_regions = []
        for region in self.regions:
            x, y, w, h, label_type, region_id = region

            # 计算归一化值 - 使用原始视频尺寸
            cx = (x + w / 2) / video_width
            cy = (y + h / 2) / video_height
            nw = w / video_width
            nh = h / video_height

            yolo_regions.append({
                "id": region_id,
                "label": label_type,
                "center_x": cx,
                "center_y": cy,
                "width": nw,
                "height": nh
            })

        # 创建包含视频信息的完整数据结构
        video_data = {
            "video_path": self.video_path,
            "width": video_width,
            "height": video_height,
            "fps": self.fps,
            "total_frames": self.total_frames,
            "rotation": self.rotation,  # 添加旋转信息
            "regions": yolo_regions
        }

        self.status_label.setText(f"已保存{len(yolo_regions)}个标记区域(YOLO格式)")
        return video_data

    def start_ocr(self):
        """开始OCR识别"""
        # 显示OCR处理对话框
        self.ocr_dialog = OCRProcessDialog(
            self.video_path,
        )
        self.ocr_dialog.exec()


class OverlayWidget(QWidget):
    """用于在视频上绘制标注框的覆盖层 - 保持原始坐标系"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.regions = []
        self.current_region = None
        self.selected_region_idx = -1
        self.label_colors = {}
        self.video_size = QSize()  # 视频原始尺寸
        self.video_display_rect = QRect()  # 视频实际显示区域

        # 初始大小
        self.setGeometry(0, 0, parent.width(), parent.height())

    def set_display_rect(self, rect):
        """设置视频实际显示区域"""
        self.video_display_rect = rect
        self.setGeometry(rect)  # 调整覆盖层位置和大小
        self.update()

    def resizeEvent(self, event):
        """随父组件调整大小"""
        self.setGeometry(0, 0, self.parent().width(), self.parent().height())
        super().resizeEvent(event)

    def set_regions(self, regions):
        self.regions = regions
        self.update()

    def set_video_size(self, size):
        self.video_size = size
        self.update()

    def set_current_region(self, region):
        self.current_region = region
        self.update()

    def set_label_colors(self, label_colors):
        self.label_colors = label_colors
        self.update()

    def set_selected_region(self, index):
        self.selected_region_idx = index
        self.update()

    def paintEvent(self, event):
        """绘制覆盖层"""
        if not self.video_size.isValid() or self.video_display_rect.isEmpty():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 计算缩放比例
        scale_x = self.video_display_rect.width() / self.video_size.width()
        scale_y = self.video_display_rect.height() / self.video_size.height()

        # 绘制所有标记区域
        for i, region in enumerate(self.regions):
            if len(region) < 5:
                continue

            x, y, w, h, label_type = region[:5]

            # 转换为显示坐标（相对于显示区域）
            disp_x = self.video_display_rect.x() + x * scale_x
            disp_y = self.video_display_rect.y() + y * scale_y
            disp_w = w * scale_x
            disp_h = h * scale_y

            # 获取颜色
            color = self.label_colors.get(label_type, QColor(0, 255, 255, 200))

            # 绘制矩形
            pen = QPen(color, 2)
            painter.setPen(pen)
            painter.drawRect(disp_x, disp_y, disp_w, disp_h)

            # 绘制标签背景
            painter.fillRect(disp_x, disp_y - 20, disp_w, 20, color)

            # 绘制标签文本
            painter.setPen(Qt.white)
            painter.setFont(QFont("Arial", 9))
            painter.drawText(disp_x + 5, disp_y - 5, label_type)

            # 如果被选中，绘制高亮边框
            if i == self.selected_region_idx:
                pen = QPen(QColor(255, 255, 0), 3)  # 黄色
                painter.setPen(pen)
                painter.drawRect(disp_x, disp_y, disp_w, disp_h)

        # 绘制当前正在标记的区域
        if self.current_region:
            x, y, w, h = self.current_region
            disp_x = self.video_display_rect.x() + x * scale_x
            disp_y = self.video_display_rect.y() + y * scale_y
            disp_w = w * scale_x
            disp_h = h * scale_y

            # 绘制虚线矩形
            pen = QPen(QColor(0, 200, 255), 2)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(disp_x, disp_y, disp_w, disp_h)

            # 绘制"新区域"标签
            painter.fillRect(disp_x, disp_y - 20, disp_w, 20, QColor(0, 200, 255, 200))
            painter.setPen(Qt.white)
            painter.setFont(QFont("Arial", 9))
            painter.drawText(disp_x + 5, disp_y - 5, "新区域")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("和平精英击杀剪辑工具")
        self.setGeometry(100, 100, 1200, 800)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #F8F8F8;
            }
            QWidget {
                background-color: #F8F8F8;
                color: #333333;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                margin-top: 1ex;
                background: #FFFFFF;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
                color: #333333;
            }
            QListWidget {
                background-color: #FFFFFF;
                color: #333333;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #EEEEEE;
            }
            QListWidget::item:selected {
                background-color: #3a7ca5;
                color: white;
                border-radius: 4px;
            }
            QSlider::groove:horizontal {
                background: #E0E0E0;
                height: 8px;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #3a7ca5;
                border: 1px solid #2c5d83;
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }
            QSlider::sub-page:horizontal {
                background: #3a7ca5;
                border-radius: 4px;
            }
            QPushButton {
                background-color: #F0F0F0;
                color: #333333;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #E0E0E0;
            }
            QPushButton:pressed {
                background-color: #3a7ca5;
                color: white;
            }
            QPushButton:checked {
                background-color: #3a7ca5;
                color: white;
            }
            QComboBox {
                background-color: #FFFFFF;
                color: #333333;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 5px;
            }
            QLineEdit {
                background-color: #FFFFFF;
                color: #333333;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 5px;
            }
            QMenu {
                background-color: white;
                border: 1px solid #CCCCCC;
                color: #333333;
            }
            QMenu::item {
                padding: 5px 25px 5px 20px;
            }
            QMenu::item:selected {
                background-color: #3a7ca5;
                color: white;
            }
        """)

        # 创建主窗口
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        # 创建主布局
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # 标题栏
        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(5, 5, 5, 15)

        title_label = QLabel("和平精英击杀剪辑工具")
        title_label.setStyleSheet("""
            font-size: 24px;
            font-weight: bold;
            color: #3a7ca5;
        """)
        title_layout.addWidget(title_label)

        title_layout.addStretch()

        # 主题切换按钮
        self.theme_btn = QPushButton("深色模式")
        self.theme_btn.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        self.theme_btn.clicked.connect(self.toggle_theme)
        self.theme_btn.setMinimumHeight(40)
        self.theme_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a7ca5;
                color: white;
                font-weight: bold;
                border-radius: 5px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #2c5d83;
            }
        """)
        title_layout.addWidget(self.theme_btn)

        # 创建视频播放器
        self.video_player = VideoPlayer()

        # 添加功能按钮
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        self.open_btn = QPushButton("打开视频")
        self.open_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.open_btn.clicked.connect(self.open_video)
        self.open_btn.setMinimumHeight(40)
        self.open_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a7ca5;
                color: white;
                font-weight: bold;
                border-radius: 5px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #2c5d83;
            }
        """)
        button_layout.addWidget(self.open_btn)
        self.save_btn = QPushButton("保存标注")
        self.save_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.save_btn.clicked.connect(self.save_regions)
        self.save_btn.setMinimumHeight(40)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                border-radius: 5px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #3d8b40;
            }
        """)
        button_layout.addWidget(self.save_btn)

        self.ocr_btn = QPushButton("OCR识别")
        self.ocr_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self.ocr_btn.clicked.connect(self.start_ocr)
        self.ocr_btn.setMinimumHeight(40)
        self.ocr_btn.setStyleSheet("""
            QPushButton {
                background-color: #9C27B0;
                color: white;
                font-weight: bold;
                border-radius: 5px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
        """)
        button_layout.addWidget(self.ocr_btn)

        self.export_btn = QPushButton("导出剪辑")
        self.export_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.export_btn.clicked.connect(self.export_clips)
        self.export_btn.setMinimumHeight(40)
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF5722;
                color: white;
                font-weight: bold;
                border-radius: 5px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #E64A19;
            }
        """)
        button_layout.addWidget(self.export_btn)

        # 添加布局
        main_layout.addLayout(title_layout)
        main_layout.addWidget(self.video_player, 1)
        main_layout.addLayout(button_layout)

        # 创建状态栏
        self.statusBar().setStyleSheet("""
            QStatusBar {
                background-color: #F0F0F0;
                color: #333333;
                border-top: 1px solid #CCCCCC;
            }
        """)
        self.statusBar().showMessage("就绪")

        # 主题状态
        self.dark_theme = False

    def toggle_theme(self):
        """切换深色/浅色主题"""
        self.dark_theme = not self.dark_theme

        if self.dark_theme:
            self.theme_btn.setText("浅色模式")
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #1E1E1E;
                }
                QWidget {
                    background-color: #252526;
                    color: #D4D4D4;
                }
                QGroupBox {
                    font-weight: bold;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    margin-top: 1ex;
                    background: #252526;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 10px;
                    padding: 0 3px 0 3px;
                    color: #CCCCCC;
                }
                QListWidget {
                    background-color: #252526;
                    color: #D4D4D4;
                    border: 1px solid #3C3C3C;
                    border-radius: 4px;
                }
                QListWidget::item {
                    padding: 8px;
                    border-bottom: 1px solid #3C3C3C;
                }
                QListWidget::item:selected {
                    background-color: #094771;
                    color: white;
                    border-radius: 4px;
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
                QPushButton {
                    background-color: #3C3C3C;
                    color: #D4D4D4;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 5px;
                }
                QPushButton:hover {
                    background-color: #555555;
                }
                QPushButton:pressed {
                    background-color: #007ACC;
                    color: white;
                }
                QPushButton:checked {
                    background-color: #007ACC;
                    color: white;
                }
                QComboBox {
                    background-color: #3C3C3C;
                    color: #D4D4D4;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 5px;
                }
                QLineEdit {
                    background-color: #3C3C3C;
                    color: #D4D4D4;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 5px;
                }
                QMenu {
                    background-color: #252526;
                    border: 1px solid #555555;
                    color: #D4D4D4;
                }
                QMenu::item {
                    padding: 5px 25px 5px 20px;
                }
                QMenu::item:selected {
                    background-color: #094771;
                    color: white;
                }
            """)
        else:
            self.theme_btn.setText("深色模式")
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #F8F8F8;
                }
                QWidget {
                    background-color: #F8F8F8;
                    color: #333333;
                }
                QGroupBox {
                    font-weight: bold;
                    border: 1px solid #CCCCCC;
                    border-radius: 4px;
                    margin-top: 1ex;
                    background: #FFFFFF;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 10px;
                    padding: 0 3px 0 3px;
                    color: #333333;
                }
                QListWidget {
                    background-color: #FFFFFF;
                    color: #333333;
                    border: 1px solid #CCCCCC;
                    border-radius: 4px;
                }
                QListWidget::item {
                    padding: 8px;
                    border-bottom: 1px solid #EEEEEE;
                }
                QListWidget::item:selected {
                    background-color: #3a7ca5;
                    color: white;
                    border-radius: 4px;
                }
                QSlider::groove:horizontal {
                    background: #E0E0E0;
                    height: 8px;
                    border-radius: 4px;
                }
                QSlider::handle:horizontal {
                    background: #3a7ca5;
                    border: 1px solid #2c5d83;
                    width: 18px;
                    margin: -5px 0;
                    border-radius: 9px;
                }
                QSlider::sub-page:horizontal {
                    background: #3a7ca5;
                    border-radius: 4px;
                }
                QPushButton {
                    background-color: #F0F0F0;
                    color: #333333;
                    border: 1px solid #CCCCCC;
                    border-radius: 4px;
                    padding: 5px;
                }
                QPushButton:hover {
                    background-color: #E0E0E0;
                }
                QPushButton:pressed {
                    background-color: #3a7ca5;
                    color: white;
                }
                QPushButton:checked {
                    background-color: #3a7ca5;
                    color: white;
                }
                QComboBox {
                    background-color: #FFFFFF;
                    color: #333333;
                    border: 1px solid #CCCCCC;
                    border-radius: 4px;
                    padding: 5px;
                }
                QLineEdit {
                    background-color: #FFFFFF;
                    color: #333333;
                    border: 1px solid #CCCCCC;
                    border-radius: 4px;
                    padding: 5px;
                }
                QMenu {
                    background-color: white;
                    border: 1px solid #CCCCCC;
                    color: #333333;
                }
                QMenu::item {
                    padding: 5px 25px 5px 20px;
                }
                QMenu::item:selected {
                    background-color: #3a7ca5;
                    color: white;
                }
            """)

    def open_video(self):
        self.video_player.open_video()

    def save_regions(self):
        """保存标记区域到文件"""
        video_data = self.video_player.save_regions()
        if not video_data:
            QMessageBox.warning(self, "警告", "没有可保存的标注区域")
            return

        # 设置默认保存路径和文件名
        video_path = video_data["video_path"]
        video_dir = os.path.dirname(video_path)
        video_name = os.path.splitext(os.path.basename(video_path))[0]  # 去掉扩展名
        default_file = os.path.join(video_dir, f"{video_name}_labels.json")

        # 选择保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存标注",
            default_file,  # 设置默认路径
            "JSON文件 (*.json);;所有文件 (*.*)"
        )

        if file_path:
            try:
                # 确保文件扩展名是.json
                if not file_path.lower().endswith('.json'):
                    file_path += '.json'

                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(video_data, f, ensure_ascii=False, indent=2)
                self.statusBar().showMessage(f"标注已保存到: {file_path}")

                # 显示保存的信息摘要
                info = f"视频路径: {os.path.basename(video_data['video_path'])}\n"
                info += f"视频尺寸: {video_data['width']}x{video_data['height']}\n"
                info += f"总帧数: {video_data['total_frames']}\n"
                info += f"帧率: {video_data['fps']:.2f}\n"
                info += f"旋转角度: {video_data['rotation']}度\n"
                info += f"标注区域数量: {len(video_data['regions'])}"

                QMessageBox.information(self, "保存成功", f"标注信息已保存:\n\n{info}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", f"保存标注时出错:\n{str(e)}")

    def export_clips(self):
        """导出剪辑"""
        if not self.video_player.video_path:
            QMessageBox.warning(self, "警告", "请先打开视频文件")
            return

        # 模拟剪辑过程
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存剪辑片段", "", "视频文件 (*.mp4)"
        )

        if save_path:
            self.statusBar().showMessage(f"剪辑完成: {os.path.basename(save_path)}")
            QMessageBox.information(self, "成功", f"剪辑片段已保存到:\n{save_path}")

    def start_ocr(self):
        """开始OCR识别 - 直接调用播放器的OCR功能"""
        self.video_player.start_ocr()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # 设置应用样式
    app.setStyle("Fusion")

    # 设置应用图标
    if os.path.exists("icon.png"):
        app.setWindowIcon(QIcon("icon.png"))

    window = MainWindow()
    window.show()

    sys.exit(app.exec())