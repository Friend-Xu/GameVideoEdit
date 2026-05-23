import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QVBoxLayout, QWidget,
    QSlider, QPushButton, QHBoxLayout, QLabel, QSizePolicy
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import QUrl, Qt, QTime
from PySide6.QtGui import QAction, QKeySequence, QIcon, QFont


class VideoPlayer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PySide6 高级视频播放器")
        self.resize(1000, 700)

        # 创建核心组件
        self.media_player = QMediaPlayer()

        # 创建音频输出
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(640, 480)

        # 创建UI组件
        self.create_controls()

        # 设置布局
        central_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.addWidget(self.video_widget)
        main_layout.addLayout(self.create_progress_bar())
        main_layout.addLayout(self.create_control_bar())
        main_layout.addLayout(self.create_volume_bar())
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        # 连接媒体输出
        self.media_player.setVideoOutput(self.video_widget)

        # 创建菜单
        self._create_menu()

        # 错误处理
        self.media_player.errorOccurred.connect(self._handle_error)

        # 初始化标志
        self.user_is_dragging = False

    def create_controls(self):
        """创建控制按钮"""
        # 播放按钮
        self.play_btn = QPushButton()
        self.play_btn.setIcon(QIcon.fromTheme("media-playback-start"))
        self.play_btn.setToolTip("播放")
        self.play_btn.setFixedSize(40, 40)
        self.play_btn.clicked.connect(self.play_video)

        # 暂停按钮
        self.pause_btn = QPushButton()
        self.pause_btn.setIcon(QIcon.fromTheme("media-playback-pause"))
        self.pause_btn.setToolTip("暂停")
        self.pause_btn.setFixedSize(40, 40)
        self.pause_btn.clicked.connect(self.pause_video)

        # 停止按钮
        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(QIcon.fromTheme("media-playback-stop"))
        self.stop_btn.setToolTip("停止")
        self.stop_btn.setFixedSize(40, 40)
        self.stop_btn.clicked.connect(self.stop_video)

        # 全屏按钮
        self.fullscreen_btn = QPushButton()
        self.fullscreen_btn.setIcon(QIcon.fromTheme("view-fullscreen"))
        self.fullscreen_btn.setToolTip("全屏")
        self.fullscreen_btn.setFixedSize(40, 40)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)

    def create_progress_bar(self):
        """创建进度条和时间显示"""
        layout = QHBoxLayout()

        # 当前时间标签
        self.current_time_label = QLabel("00:00:00")
        self.current_time_label.setFont(QFont("Arial", 10))
        self.current_time_label.setMinimumWidth(70)

        # 进度条
        self.progress_slider = QSlider(Qt.Horizontal)
        self.progress_slider.setRange(0, 100)
        self.progress_slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # 进度条事件处理
        self.progress_slider.sliderPressed.connect(self.slider_pressed)
        self.progress_slider.sliderMoved.connect(self.slider_moved)
        self.progress_slider.sliderReleased.connect(self.slider_released)

        # 总时间标签
        self.total_time_label = QLabel("00:00:00")
        self.total_time_label.setFont(QFont("Arial", 10))
        self.total_time_label.setMinimumWidth(70)

        # 添加组件到布局
        layout.addWidget(self.current_time_label)
        layout.addWidget(self.progress_slider)
        layout.addWidget(self.total_time_label)

        # 连接媒体播放器信号
        self.media_player.positionChanged.connect(self.update_position)
        self.media_player.durationChanged.connect(self.update_duration)

        return layout

    def create_control_bar(self):
        """创建控制按钮栏"""
        layout = QHBoxLayout()
        layout.addStretch()
        layout.addWidget(self.play_btn)
        layout.addWidget(self.pause_btn)
        layout.addWidget(self.stop_btn)
        layout.addWidget(self.fullscreen_btn)
        layout.addStretch()
        return layout

    def create_volume_bar(self):
        """创建音量控制栏"""
        layout = QHBoxLayout()

        # 音量图标
        self.volume_icon = QLabel()
        self.volume_icon.setPixmap(QIcon.fromTheme("audio-volume-high").pixmap(24, 24))

        # 音量滑块
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(50)
        self.volume_slider.setMaximumWidth(150)
        self.volume_slider.valueChanged.connect(self.set_volume)

        # 音量值标签
        self.volume_label = QLabel("50%")
        self.volume_label.setFont(QFont("Arial", 10))
        self.volume_label.setMinimumWidth(40)

        # 添加组件
        layout.addWidget(self.volume_icon)
        layout.addWidget(self.volume_slider)
        layout.addWidget(self.volume_label)
        layout.addStretch()

        # 初始化音量 - 使用新的音频输出对象
        self.audio_output.setVolume(0.5)  # 0.5 对应 50%

        return layout

    def _create_menu(self):
        """创建菜单"""
        menu = self.menuBar().addMenu("&文件")

        # 打开动作
        open_action = QAction("打开视频", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._open_file)
        menu.addAction(open_action)

        # 退出动作
        exit_action = QAction("退出", self)
        exit_action.setShortcut(QKeySequence.Quit)
        exit_action.triggered.connect(self.close)
        menu.addAction(exit_action)

    def _open_file(self):
        """打开视频文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv);;所有文件 (*)"
        )

        if file_path:
            # 加载并播放
            self.media_player.setSource(QUrl.fromLocalFile(file_path))
            self.media_player.play()
            self.play_btn.setEnabled(False)
            self.pause_btn.setEnabled(True)

            # 启用硬件加速
            self.media_player.setPlaybackRate(1.0)

            # 降低CPU占用
            self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)

    def _handle_error(self, error, error_string):
        """处理播放错误"""
        print(f"[播放错误] {error_string} (Code: {error})")
        self.statusBar().showMessage(f"错误: {error_string}", 5000)

    def play_video(self):
        """播放视频"""
        if self.media_player.playbackState() == QMediaPlayer.PausedState:
            self.media_player.play()
        elif self.media_player.playbackState() == QMediaPlayer.StoppedState:
            if self.media_player.source().isValid():
                self.media_player.play()

        self.play_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)

    def pause_video(self):
        """暂停视频"""
        self.media_player.pause()
        self.play_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)

    def stop_video(self):
        """停止视频"""
        self.media_player.stop()
        self.play_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.progress_slider.setValue(0)
        self.current_time_label.setText("00:00:00")

    def toggle_fullscreen(self):
        """切换全屏模式"""
        if self.video_widget.isFullScreen():
            self.video_widget.setFullScreen(False)
            self.menuBar().show()
            self.statusBar().show()
        else:
            self.video_widget.setFullScreen(True)
            self.menuBar().hide()
            self.statusBar().hide()

    def set_volume(self, value):
        """设置音量 - 使用新的音频输出对象"""
        # 将0-100的滑块值转换为0.0-1.0的浮点数
        volume_level = value / 100.0
        self.audio_output.setVolume(volume_level)
        self.volume_label.setText(f"{value}%")

        # 更新音量图标
        if value == 0:
            icon_name = "audio-volume-muted"
        elif value < 33:
            icon_name = "audio-volume-low"
        elif value < 66:
            icon_name = "audio-volume-medium"
        else:
            icon_name = "audio-volume-high"

        self.volume_icon.setPixmap(QIcon.fromTheme(icon_name).pixmap(24, 24))

    def update_position(self, position):
        """更新播放位置"""
        if not self.user_is_dragging:
            # 更新进度条位置
            if self.media_player.duration() > 0:
                progress = max(0, min(100, int((position / self.media_player.duration()) * 100)))
                self.progress_slider.setValue(progress)

            # 更新时间标签
            current_time = QTime(0, 0, 0).addMSecs(position)
            self.current_time_label.setText(current_time.toString("HH:mm:ss"))

    def update_duration(self, duration):
        """更新视频总时长"""
        total_time = QTime(0, 0, 0).addMSecs(duration)
        self.total_time_label.setText(total_time.toString("HH:mm:ss"))

    def slider_pressed(self):
        """进度条被按下"""
        self.user_is_dragging = True

    def slider_moved(self, position):
        """进度条被拖动"""
        # 在拖动过程中实时更新播放位置
        if self.media_player.duration() > 0:
            new_position = int((position / 100) * self.media_player.duration())
            self.media_player.setPosition(new_position)

            # 更新时间标签
            current_time = QTime(0, 0, 0).addMSecs(new_position)
            self.current_time_label.setText(current_time.toString("HH:mm:ss"))

    def slider_released(self):
        """进度条被释放"""
        # 设置最终位置
        new_position = int((self.progress_slider.value() / 100) * self.media_player.duration())
        self.media_player.setPosition(new_position)
        self.user_is_dragging = False

        # 如果视频已停止，重新播放
        if self.media_player.playbackState() == QMediaPlayer.StoppedState:
            self.media_player.play()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 设置支持高清渲染
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    player = VideoPlayer()
    player.show()
    sys.exit(app.exec())