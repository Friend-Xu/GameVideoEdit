from PySide6.QtWidgets import (QMainWindow, QWidget, QPushButton, QLineEdit,
                               QFileDialog, QLabel, QVBoxLayout, QHBoxLayout, QGraphicsView, QGraphicsScene,
                               QProgressBar)
import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton,QWidget
from GUI.ui_main import Ui_Form

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PUBG 自动剪辑工具")
        self.resize(800, 600)

        # 中央窗口及布局
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(15)  # 设置布局间距
        layout.setContentsMargins(20, 20, 20, 20)  # 设置布局边距

        # 导入视频
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)  # 设置按钮布局间距
        self.importButton = QPushButton("导入视频")
        self.importButton.clicked.connect()
        self.videoPathEdit = QLineEdit()
        self.videoPathEdit.setPlaceholderText("未选择视频")
        btn_layout.addWidget(self.importButton)
        btn_layout.addWidget(self.videoPathEdit)

        # 关键词输入
        key_layout = QHBoxLayout()
        key_layout.setSpacing(10)  # 设置关键词布局间距
        self.keywordEdit = QLineEdit()
        self.keywordEdit.setPlaceholderText("输入 OCR 关键词（如 'You killed'）")
        key_layout.addWidget(QLabel("关键词："))
        key_layout.addWidget(self.keywordEdit)

        # 区域选择视图
        self.graphicsView = QGraphicsView()
        self.graphicsScene = QGraphicsScene()
        self.graphicsView.setScene(self.graphicsScene)
        # 这里可以初始化一个空的 QGraphicsPixmapItem，用于显示视频帧

        # 播放/分析/导出 按钮
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(10)  # 设置控制按钮布局间距
        self.playButton = QPushButton("播放/暂停")
        self.analyzeButton = QPushButton("开始分析")
        self.exportButton = QPushButton("导出视频")
        self.progressBar = QProgressBar()
        ctrl_layout.addWidget(self.playButton)
        ctrl_layout.addWidget(self.analyzeButton)
        ctrl_layout.addWidget(self.exportButton)
        ctrl_layout.addWidget(self.progressBar)

        # 将所有布局添加到主布局
        layout.addLayout(btn_layout)
        layout.addStretch(1)  # 添加伸展项
        layout.addLayout(key_layout)
        layout.addStretch(1)  # 添加伸展项
        layout.addWidget(self.graphicsView)
        layout.addStretch(2)  # 添加伸展项
        layout.addLayout(ctrl_layout)

        # 初始化属性
        self.video_path = None
        self.roi = None  # 用户选中的区域 (x, y, w, h)

        # 信号槽连接
        self.analyzeButton.clicked.connect(self.start_analysis)
        self.exportButton.clicked.connect(self.export_highlight)
        # 播放按钮可以与 QMediaPlayer 连接（此处略）

if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()