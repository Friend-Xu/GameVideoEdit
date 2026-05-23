import os
import json
import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import EasyOCR_1_7_2.easyocr as easyocr
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QProgressBar, QLabel, QPushButton,
    QListWidget, QMessageBox, QFileDialog, QListWidgetItem,
    QHBoxLayout, QLineEdit, QFileDialog, QGroupBox
)


class OCRProcessor(QThread):
    """OCR处理线程"""
    progress_updated = Signal(int, float)  # (当前帧, 总进度)
    result_detected = Signal(float, str)  # (时间戳, 识别文本)
    finished_processing = Signal(list)  # 最终检测到的时间段列表

    def __init__(self, video_path, regions, keywords, padding=10.0, skip_frames=1,rotation=0):
        super().__init__()
        self.rotation = rotation  # 添加旋转参数
        self.video_path = video_path
        self.regions = regions
        self.keywords = keywords  # 现在只包含动作词 ["淘汰了", "击倒了"]
        self.padding = padding
        self.skip_frames = skip_frames
        OCRmodel_path = "D:\Github\GameVideoEdit\EasyOCR_1_7_2\models"
        self.reader = easyocr.Reader(lang_list=['ch_sim', 'en'],
                                     model_storage_directory=OCRmodel_path,
                                     download_enabled=False,
                                     gpu=True)  # 这只需要运行一次即可将模型加载到内存中
        self.cancelled = False



    def run(self):
        def apply_rotation(image, rotation):
            """根据旋转角度旋转图像"""
            if rotation == 90:
                return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
            elif rotation == 180:
                return cv2.rotate(image, cv2.ROTATE_180)
            elif rotation == 270:
                return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
            else:
                return image.copy()
        try:
            self.cancelled = False
            cap = cv2.VideoCapture(self.video_path)
            if not cap.isOpened():
                raise Exception("无法打开视频文件")

            # 获取视频信息
            fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            # 从标注文件获取旋转角度（假设已传入）
            rotation = self.rotation  # 需要添加到OCRProcessor的__init__参数

            # 存储检测结果
            detections = []

            # 处理每一帧
            frame_count = 0
            while cap.isOpened() and not self.cancelled:
                ret, frame = cap.read()
                if not ret:
                    break

                # 应用旋转（关键修改）==================================
                if rotation in [90, 270]:
                    # 旋转后宽高互换
                    frame = apply_rotation(frame, rotation)
                    frame_width = frame.shape[1]
                    frame_height = frame.shape[0]
                else:
                    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    if rotation == 180:
                        frame = apply_rotation(frame, rotation)
                # ====================================================

                # 跳帧处理
                if frame_count % self.skip_frames != 0:
                    frame_count += 1
                    continue

                # 计算当前时间戳
                timestamp = frame_count / fps

                # 更新进度
                progress = frame_count / total_frames * 100
                self.progress_updated.emit(frame_count, progress)

                # 处理所有标记区域
                for region in self.regions:
                    # 转换为像素坐标（使用旋转后的尺寸）
                    cx, cy, w, h = self._denormalize_roi(
                        region, frame_width, frame_height
                    )

                    # 裁剪ROI区域
                    roi_img = frame[cy:cy + h, cx:cx + w]

                    # OCR识别
                    results = self.reader.readtext(roi_img)
                    detected_text = " ".join([res[1] for res in results])
                    # print(detected_text)
                    # 改进的检测逻辑：要求同时包含"你使用"和动作词
                    if ("你使用" in detected_text) and \
                            any(kw in detected_text for kw in self.keywords):
                        self.result_detected.emit(timestamp, detected_text)
                        detections.append((timestamp, True))

                        # 检测到关键词后跳过一些帧避免重复
                        skip_count = int(fps * 0.5)  # 跳过0.5秒
                        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count + skip_count)
                        frame_count += skip_count
                        break

                frame_count += 1

            # 合并时间段
            time_ranges = self.merge_time_ranges(detections)
            self.finished_processing.emit(time_ranges)

        except Exception as e:
            print(f"OCR处理出错: {str(e)}")
        finally:
            if 'cap' in locals() and cap.isOpened():
                cap.release()

    def _denormalize_roi(self, region, img_w, img_h):
        """将YOLO格式ROI转换为像素坐标"""
        cx = region['center_x'] * img_w
        cy = region['center_y'] * img_h
        w = region['width'] * img_w
        h = region['height'] * img_h

        # 计算左上角坐标
        x = int(cx - w / 2)
        y = int(cy - h / 2)
        w = int(w)
        h = int(h)

        # 确保在图像范围内
        x = max(0, min(x, img_w - 1))
        y = max(0, min(y, img_h - 1))
        w = min(w, img_w - x)
        h = min(h, img_h - y)

        return x, y, w, h

    def merge_time_ranges(self, detections):
        """合并重叠的时间区间"""
        # 提取所有触发点的时间戳
        trigger_times = [ts for ts, detected in detections if detected]

        if not trigger_times:
            return []

        # 创建初始时间区间
        time_ranges = [(max(0, t - self.padding), t + self.padding)
                       for t in trigger_times]
        time_ranges.sort(key=lambda x: x[0])

        # 合并重叠区间
        merged = []
        current_start, current_end = time_ranges[0]

        for start, end in time_ranges[1:]:
            if start <= current_end:  # 重叠区间
                current_end = max(current_end, end)
            else:
                merged.append((current_start, current_end))
                current_start, current_end = start, end

        merged.append((current_start, current_end))
        return merged

    def cancel(self):
        """取消处理"""
        self.cancelled = True


class OCRProcessDialog(QDialog):
    """OCR处理对话框"""

    def __init__(self, video_path=None, regions=None, annotation_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OCR处理")
        self.setMinimumSize(600, 500)  # 增加窗口大小以容纳新控件

        # 设置窗口图标
        if os.path.exists("icon.png"):
            self.setWindowIcon(QIcon("icon.png"))

        # 布局
        layout = QVBoxLayout()
        self.setLayout(layout)

        # =================== 标注文件选择区域 ===================
        annotation_group = QGroupBox("标注文件选择")
        annotation_layout = QVBoxLayout(annotation_group)

        # 标注文件路径显示
        annotation_path_layout = QHBoxLayout()
        annotation_path_layout.addWidget(QLabel("标注文件路径:"))

        self.annotation_path_edit = QLineEdit()
        self.annotation_path_edit.setReadOnly(True)
        annotation_path_layout.addWidget(self.annotation_path_edit, 1)

        self.browse_btn = QPushButton("浏览...")
        self.browse_btn.clicked.connect(self.select_annotation_file)
        annotation_path_layout.addWidget(self.browse_btn)

        annotation_layout.addLayout(annotation_path_layout)

        # 加载按钮
        self.load_btn = QPushButton("加载标注文件")
        self.load_btn.clicked.connect(self.load_annotation)
        annotation_layout.addWidget(self.load_btn)

        layout.addWidget(annotation_group)
        # =====================================================

        # 状态标签
        self.status_label = QLabel("准备开始OCR处理...")
        layout.addWidget(self.status_label)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        # 处理信息
        self.info_label = QLabel("")
        layout.addWidget(self.info_label)

        # 结果列表
        self.result_list = QListWidget()
        layout.addWidget(self.result_list, 1)

        # 按钮布局
        button_layout = QHBoxLayout()

        self.start_btn = QPushButton("开始处理")
        self.start_btn.clicked.connect(self.start_processing)
        self.start_btn.setEnabled(False)  # 初始不可用，直到加载标注文件
        button_layout.addWidget(self.start_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_processing)
        button_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("保存结果")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_results)
        button_layout.addWidget(self.save_btn)

        layout.addLayout(button_layout)

        # 初始化OCR处理器
        self.video_path = video_path
        self.regions = regions
        self.time_ranges = []
        self.annotation_path = annotation_path

        # 设置OCR关键词 - 只保留动作词
        self.keywords = ["淘汰了", "击倒了"]

        # 如果提供了标注文件路径，直接加载
        if annotation_path:
            self.annotation_path_edit.setText(annotation_path)
            self.load_annotation()
        elif video_path and regions:
            # 使用传入的视频路径和区域
            self.update_info()
        else:
            self.status_label.setText("请选择标注文件或提供视频路径和区域")

    def select_annotation_file(self):
        """选择标注文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择标注文件", "", "JSON文件 (*.json)"
        )

        if file_path:
            self.annotation_path = file_path
            self.annotation_path_edit.setText(file_path)
            self.load_annotation()

    def load_annotation(self):
        """加载标注文件"""
        if not self.annotation_path:
            QMessageBox.warning(self, "警告", "请先选择标注文件")
            return

        try:
            with open(self.annotation_path, 'r', encoding='utf-8') as f:
                annotation_data = json.load(f)

            # 提取必要信息
            self.video_path = annotation_data.get("video_path", "")
            self.regions = annotation_data.get("regions", [])
            self.rotation = annotation_data.get("rotation",int)
            if not self.video_path or not os.path.exists(self.video_path):
                QMessageBox.warning(self, "警告", "视频文件路径无效或不存在")
                return

            if not self.regions:
                QMessageBox.warning(self, "警告", "标注文件中未找到检测区域")
                return

            # 更新信息并启用开始按钮
            self.update_info()
            self.start_btn.setEnabled(True)
            self.status_label.setText("标注文件加载成功，可以开始处理")

        except Exception as e:
            QMessageBox.critical(
                self, "加载失败", f"加载标注文件时出错:\n{str(e)}"
            )
            self.status_label.setText(f"加载失败: {str(e)}")

    def update_info(self):
        """更新对话框信息"""
        if not self.video_path or not self.regions:
            self.info_label.setText("未加载有效视频或区域信息")
            return

        info = f"视频文件: {os.path.basename(self.video_path)}\n"
        info += f"检测区域数: {len(self.regions)}\n"

        # 显示前3个区域的位置信息
        for i, region in enumerate(self.regions[:3]):
            info += (f"区域 {i + 1}: "
                     f"({region['center_x']:.2f}, {region['center_y']:.2f}) "
                     f"宽{region['width']:.2f} 高{region['height']:.2f}\n")

        if len(self.regions) > 3:
            info += f"...共 {len(self.regions)} 个区域\n"

        info += f"关键词: 你使用 + ({', '.join(self.keywords)})"
        self.info_label.setText(info)

    def start_processing(self):
        """开始处理视频"""
        if not self.video_path or not os.path.exists(self.video_path):
            QMessageBox.warning(self, "警告", "视频文件路径无效或不存在")
            return

        if not self.regions:
            QMessageBox.warning(self, "警告", "未设置检测区域")
            return

        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.result_list.clear()
        self.status_label.setText("OCR处理中...")

        # 创建OCR处理器
        self.ocr_processor = OCRProcessor(
            self.video_path,
            self.regions,
            self.keywords,
            padding=10.0,
            skip_frames=3,
            rotation=self.rotation  # 传递旋转参数
        )

        # 连接信号
        self.ocr_processor.progress_updated.connect(self.update_progress)
        self.ocr_processor.result_detected.connect(self.add_result)
        self.ocr_processor.finished_processing.connect(self.finish_processing)

        # 启动处理线程
        self.ocr_processor.start()

    def cancel_processing(self):
        """取消处理"""
        if hasattr(self, 'ocr_processor'):
            self.ocr_processor.cancel()
        self.status_label.setText("处理已取消")
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    def update_progress(self, frame, progress):
        """更新进度"""
        self.progress_bar.setValue(int(progress))
        self.status_label.setText(f"处理中: 帧 {frame}, 进度 {progress:.1f}%")

    def add_result(self, timestamp, text):
        """添加识别结果 - 只显示包含'你使用'的结果"""
        # 过滤非目标文本
        if "你使用" not in text:
            return

        minutes = int(timestamp // 60)
        seconds = int(timestamp % 60)
        item = QListWidgetItem(f"[{minutes:02d}:{seconds:02d}] {text}")
        self.result_list.addItem(item)

    def finish_processing(self, time_ranges):
        """处理完成"""
        self.time_ranges = time_ranges
        self.status_label.setText(f"处理完成! 找到 {len(time_ranges)} 个击杀片段")
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.save_btn.setEnabled(True)

        # 显示剪辑时间段
        if time_ranges:
            self.result_list.addItem("\n--- 生成的剪辑时间段 ---")
            for i, (start, end) in enumerate(time_ranges):
                start_min = int(start // 60)
                start_sec = int(start % 60)
                end_min = int(end // 60)
                end_sec = int(end % 60)
                duration = end - start
                self.result_list.addItem(
                    f"片段 {i + 1}: [{start_min:02d}:{start_sec:02d} - {end_min:02d}:{end_sec:02d}] "
                    f"时长: {duration:.1f}秒"
                )

    def save_results(self):
        """保存结果到文件"""
        if not self.time_ranges:
            QMessageBox.warning(self, "警告", "没有可保存的结果")
            return

        # 获取视频文件名
        video_name = os.path.splitext(os.path.basename(self.video_path))[0]
        default_path = f"{video_name}_clips.json"

        # 选择保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存剪辑时间段", default_path, "JSON文件 (*.json)"
        )

        if file_path:
            try:
                # 确保文件扩展名
                if not file_path.lower().endswith('.json'):
                    file_path += '.json'

                # 创建结果数据结构
                result_data = {
                    "video_path": self.video_path,
                    "clip_ranges": self.time_ranges,
                    "keywords": ["你使用"] + self.keywords,  # 添加"你使用"到关键词列表
                    "annotation_file": self.annotation_path if self.annotation_path else ""
                }

                # 保存到文件
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(result_data, f, ensure_ascii=False, indent=2)

                QMessageBox.information(
                    self, "保存成功",
                    f"已保存 {len(self.time_ranges)} 个剪辑时间段到:\n{file_path}"
                )
            except Exception as e:
                QMessageBox.critical(
                    self, "保存失败", f"保存结果时出错:\n{str(e)}"
                )