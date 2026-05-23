import os
import json
import re
import subprocess
import tempfile
import time

import cv2
import numpy as np
import EasyOCR_1_7_2.easyocr as easyocr
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QIcon, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QProgressBar, QLabel, QPushButton,
    QListWidget, QMessageBox, QFileDialog, QListWidgetItem,
    QHBoxLayout, QLineEdit, QGroupBox, QTextEdit, QSplitter, QSpinBox, QComboBox
)

# 导入日志窗口类
from log_window import LogWindow


class OCRProcessor(QThread):
    """OCR处理线程 - 增强日志分类"""

    # 日志类型常量
    LOG_INFO = 0
    LOG_WARNING = 1
    LOG_ERROR = 2
    LOG_RECOVERY = 3
    LOG_CHECK = 4
    LOG_ANALYSIS = 5

    # 更新信号以包含日志类型
    progress_updated = Signal(int, int, float)  # (线程ID, 当前帧, 总进度)
    result_detected = Signal(float, str)  # (时间戳, 识别文本)
    finished_segment = Signal(int, list)  # (线程ID, 本线程完成信号，发送检测到的时间段列表)
    ocr_text_detected = Signal(float, str)  # (时间戳, 识别文本)
    log_message = Signal(str, int)  # (日志消息, 日志类型)
    video_repair_progress = Signal(int)  # 视频修复进度信号
    h264_analysis_complete = Signal(dict)  # H.264分析结果信号

    def __init__(self, thread_id, video_path, regions, keywords, padding=10.0, skip_frames=1, rotation=0, start_frame=0, end_frame=None):
        super().__init__()
        self.thread_id = thread_id
        self.rotation = rotation
        self.original_video_path = video_path
        self.video_path = video_path
        self.regions = regions
        self.keywords = keywords
        self.padding = padding
        self.skip_frames = skip_frames
        self.start_frame = start_frame
        self.end_frame = end_frame
        OCRmodel_path = r"D:\Github\GameVideoEdit\EasyOCR_1_7_2\models"

        # 初始化EasyOCR读取器
        self.reader = easyocr.Reader(
            lang_list=['ch_sim', 'en'],
            model_storage_directory=OCRmodel_path,
            download_enabled=False,
            gpu=True
        )

        self.cancelled = False
        self.is_repaired_video = False
        self.temp_video_path = None
        self.repair_process = None
        self.analyze_process = None
        self.h264_analysis = {}
        # 添加新变量用于跟踪进度
        self.total_frames = 0
        self.thread_current_frames = {}  # 跟踪每个线程的当前帧

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

            # 1. 分析H.264码流
            self.log_message.emit(f"[H.264分析] 线程 {self.thread_id} 开始H.264码流分析...", self.LOG_ANALYSIS)
            self.analyze_h264_stream()

            # 2. 检查并修复视频
            if not self.check_and_repair_video():
                self.log_message.emit(f"[错误] 线程 {self.thread_id} 视频修复失败，无法继续处理", self.LOG_ERROR)
                self.finished_segment.emit(self.thread_id, [])
                return

            # 3. 打开视频
            cap = self.open_video_with_best_backend()
            if not cap or not cap.isOpened():
                raise Exception(f"[严重错误] 线程 {self.thread_id} 无法打开视频文件: {self.video_path}")

            # 获取视频信息
            fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.total_frames = total_frames

            # 设置起始帧
            if self.start_frame > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, self.start_frame)

            # 计算实际结束帧
            if self.end_frame is None or self.end_frame > total_frames:
                self.end_frame = total_frames - 1

            self.log_message.emit(
                f"[视频信息] 线程 {self.thread_id}: 处理范围 {self.start_frame}-{self.end_frame}, "
                f"总帧数: {total_frames}, FPS: {fps:.2f}",
                self.LOG_INFO
            )

            # 添加错误帧计数器
            error_frame_count = 0
            max_error_frames = 50  # 允许更多连续错误
            consecutive_error_threshold = 10  # 连续错误阈值
            skip_after_error = max(1, int(fps * 0.5))  # 错误后跳过的帧数

            # 存储检测结果
            detections = []

            # 记录处理统计
            processed_frames = 0
            skipped_frames = 0

            # 4. 处理每一帧
            frame_count = self.start_frame
            last_valid_frame = None
            last_valid_frame_count = self.start_frame
            max_frame_attempts = total_frames * 2  # 记录最后一个有效帧的编号

            # 添加帧位置安全保护
            max_frame_attempts = total_frames * 2  # 最大尝试帧数，防止死循环

            while cap.isOpened() and not self.cancelled and frame_count < max_frame_attempts:
                # 检查是否超出本线程处理范围
                if frame_count > self.end_frame:
                    self.log_message.emit(
                        f"[完成] 线程 {self.thread_id} 已完成处理范围 (帧 {self.end_frame}/{total_frames})",
                        self.LOG_INFO
                    )
                    break

                try:
                    # 尝试读取帧
                    ret, frame = cap.read()

                    if not ret:
                        # 检查是否已经到达视频末尾
                        current_pos = cap.get(cv2.CAP_PROP_POS_FRAMES)
                        if current_pos >= total_frames - 1:
                            self.log_message.emit(
                                f"[完成] 线程 {self.thread_id} 已到达视频末尾 (帧 {frame_count}/{total_frames})",
                                self.LOG_INFO
                            )
                            break

                        # 读取失败时的恢复策略
                        if last_valid_frame is not None:
                            frame = last_valid_frame.copy()
                            self.log_message.emit(
                                f"[恢复] 线程 {self.thread_id} 使用上一有效帧替代损坏帧 {frame_count} (源帧 {last_valid_frame_count})",
                                self.LOG_RECOVERY
                            )
                        else:
                            # 没有有效帧可恢复，跳过
                            raise Exception("无法读取帧且无有效帧恢复")

                    # 成功读取帧
                    error_frame_count = 0
                    processed_frames += 1
                    last_valid_frame = frame.copy()  # 保存当前帧用于恢复
                    last_valid_frame_count = frame_count  # 记录有效帧编号

                    # 应用旋转
                    if self.rotation in [90, 270]:
                        frame = apply_rotation(frame, self.rotation)
                        frame_width = frame.shape[1]
                        frame_height = frame.shape[0]
                    else:
                        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        if self.rotation == 180:
                            frame = apply_rotation(frame, self.rotation)

                    # 跳帧处理
                    if frame_count % self.skip_frames != 0:
                        frame_count += 1
                        continue

                    # 计算当前时间戳
                    timestamp = frame_count / fps

                    # 更新进度
                    segment_progress = min(100.0,
                                           (frame_count - self.start_frame) / (self.end_frame - self.start_frame) * 100)
                    total_progress = min(100.0, frame_count / total_frames * 100)
                    self.progress_updated.emit(self.thread_id, frame_count, total_progress)

                    # 处理所有标记区域
                    detected_in_frame = False  # 标记当前帧是否检测到击杀
                    # 处理所有标记区域
                    for region in self.regions:
                        # 转换为像素坐标（使用旋转后的尺寸）
                        cx, cy, w, h = self._denormalize_roi(
                            region, frame_width, frame_height
                        )

                        # 确保ROI在图像范围内
                        if cx < 0 or cy < 0 or w <= 0 or h <= 0 or cx + w > frame_width or cy + h > frame_height:
                            self.log_message.emit(
                                f"[警告] 线程 {self.thread_id} 无效ROI区域: {cx},{cy},{w},{h} - 跳过",
                                self.LOG_WARNING
                            )
                            continue

                        # 裁剪ROI区域
                        roi_img = frame[cy:cy + h, cx:cx + w]

                        # OCR识别
                        try:
                            results = self.reader.readtext(roi_img)
                            detected_text = " ".join([res[1] for res in results])
                        except Exception as e:
                            self.log_message.emit(
                                f"[OCR错误] 线程 {self.thread_id} 帧 {frame_count} OCR失败: {str(e)}",
                                self.LOG_ERROR
                            )
                            detected_text = ""

                        # 发射所有OCR识别结果
                        if detected_text.strip():
                            self.ocr_text_detected.emit(timestamp, detected_text)

                        # 改进的检测逻辑
                        if ("你使用" in detected_text) and \
                                any(kw in detected_text for kw in self.keywords):
                            self.result_detected.emit(timestamp, detected_text)
                            detections.append((timestamp, True))
                            detected_in_frame = True  # 标记检测到击杀

                        # 如果当前帧检测到击杀，则跳过一些帧避免重复
                    if detected_in_frame:
                        # 减少跳过的帧数，避免遗漏后续击杀
                        skip_count = max(1, int(fps * 0.3))  # 从0.5秒减少到0.3秒
                        next_frame = frame_count + skip_count

                        # 确保不会跳过视频末尾
                        if next_frame < total_frames:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, next_frame)
                            frame_count = next_frame
                            self.log_message.emit(
                                f"[跳过] 线程 {self.thread_id} 检测到关键词，跳过 {skip_count} 帧至 {next_frame}",
                                self.LOG_INFO
                            )
                        else:
                            frame_count = total_frames - 1  # 直接跳到最后一帧
                    else:
                        frame_count += 1

                except Exception as e:
                    # 捕获解码错误
                    error_frame_count += 1
                    skipped_frames += 1
                    # 注意：这里不重置last_valid_frame，保留最后一个有效帧

                    # 分类错误类型
                    error_msg = str(e)
                    if "NAL" in error_msg or "splitting" in error_msg:
                        error_type = "NAL单元错误"
                    elif "MB" in error_msg:
                        error_type = "宏块解码错误"
                    else:
                        error_type = "解码错误"

                    self.log_message.emit(
                        f"[{error_type}] 线程 {self.thread_id} 帧 {frame_count}: {error_msg}",
                        self.LOG_ERROR
                    )

                    # 尝试跳过当前帧并重置位置
                    try:
                        next_frame = frame_count + skip_after_error

                        # 检查是否超出本线程范围
                        if next_frame > self.end_frame:
                            self.log_message.emit(
                                f"[恢复] 线程 {self.thread_id} 已超出处理范围，终止处理",
                                self.LOG_RECOVERY
                            )
                            break

                        # 检查是否超出视频范围
                        if next_frame >= total_frames:
                            self.log_message.emit(
                                f"[恢复] 线程 {self.thread_id} 已到达视频末尾，终止处理 (帧 {frame_count}/{total_frames})",
                                self.LOG_RECOVERY
                            )
                            break

                        if next_frame < total_frames:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, next_frame)
                            frame_count = next_frame
                            self.log_message.emit(
                                f"[恢复] 线程 {self.thread_id} 跳过 {skip_after_error} 帧至 {next_frame}",
                                self.LOG_RECOVERY
                            )
                        else:
                            frame_count += 1
                    except Exception as e:
                        self.log_message.emit(
                            f"[恢复错误] 线程 {self.thread_id} 无法设置帧位置: {str(e)}",
                            self.LOG_ERROR
                        )
                        frame_count += 1

                    # 检查连续错误阈值
                    if error_frame_count > max_error_frames:
                        self.log_message.emit(
                            f"[严重错误] 线程 {self.thread_id} 连续解码错误超过{max_error_frames}次，终止处理",
                            self.LOG_ERROR
                        )
                        break
                    elif error_frame_count > consecutive_error_threshold:
                        # 尝试重新打开视频
                        try:
                            cap.release()
                            cap = self.open_video_with_best_backend()
                            if cap.isOpened():
                                # 确保不会超出视频范围
                                target_frame = min(frame_count, total_frames - 1)
                                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                                self.log_message.emit(
                                    f"[恢复] 线程 {self.thread_id} 重新打开视频并定位到帧 {target_frame}/{total_frames}",
                                    self.LOG_RECOVERY
                                )
                                error_frame_count = 0  # 重置错误计数
                        except Exception as e:
                            self.log_message.emit(
                                f"[恢复失败] 线程 {self.thread_id} 无法重新打开视频: {str(e)}",
                                self.LOG_ERROR
                            )

            # 确保在视频末尾退出
            if frame_count >= total_frames:
                self.log_message.emit(
                    f"[完成] 线程 {self.thread_id} 已处理到视频末尾 (帧 {frame_count}/{total_frames})",
                    self.LOG_INFO
                )

            # 合并时间段
            time_ranges = self.merge_time_ranges(detections)

            # 发送处理统计
            stats = f"线程 {self.thread_id} 处理完成: {processed_frames}帧处理, {skipped_frames}帧跳过"
            if self.is_repaired_video:
                stats += f" (使用修复后的视频: {os.path.basename(self.video_path)})"
            self.log_message.emit(f"[统计] {stats}", self.LOG_INFO)

            # 发送本线程完成信号
            self.finished_segment.emit(self.thread_id, time_ranges)

        except Exception as e:
            err_msg = f"[严重错误] 线程 {self.thread_id} OCR处理出错: {str(e)}\n视频路径: {self.video_path}"
            self.log_message.emit(err_msg, self.LOG_ERROR)
            self.finished_segment.emit(self.thread_id, [])
        finally:
            # 清理临时修复的视频文件
            if 'cap' in locals() and cap.isOpened():
                cap.release()

            # 显式释放OCR模型资源
            if hasattr(self, 'reader') and self.reader is not None:
                self.log_message.emit(f"[资源释放] 线程 {self.thread_id} 释放OCR模型资源", self.LOG_INFO)
                # 显式清除EasyOCR模型
                if hasattr(self.reader, 'detector'):
                    try:
                        self.reader.detector.destroy()
                    except Exception as e:
                        pass
                if hasattr(self.reader, 'recognizer'):
                    try:
                        self.reader.recognizer.destroy()
                    except Exception as e:
                        pass
                self.reader = None
    def open_video_with_best_backend(self):
        """尝试多种后端打开视频，添加超时保护"""
        backends = [
            cv2.CAP_FFMPEG,  # FFMPEG后端
            cv2.CAP_ANY,  # 自动选择
            cv2.CAP_MSMF,  # Windows媒体基础
            cv2.CAP_DSHOW,  # DirectShow
            cv2.CAP_GSTREAMER  # GStreamer
        ]

        best_cap = None
        for backend in backends:
            try:
                cap = cv2.VideoCapture(self.video_path, backend)
                if cap.isOpened():
                    # 测试读取第一帧（添加超时保护）
                    start_time = time.time()
                    timeout = 5.0  # 5秒超时

                    while True:
                        ret, frame = cap.read()
                        if ret or time.time() - start_time > timeout:
                            break
                        time.sleep(0.1)

                    if ret and frame is not None:
                        best_cap = cap
                        self.log_message.emit(
                            f"[视频] 成功使用后端 {backend} 打开视频",
                            self.LOG_INFO
                        )
                        break
                    else:
                        cap.release()
            except Exception as e:
                self.log_message.emit(
                    f"[视频] 后端 {backend} 失败: {str(e)}",
                    self.LOG_WARNING
                )

        if best_cap is None:
            # 最后尝试默认后端
            self.log_message.emit("[视频] 尝试默认后端", self.LOG_INFO)
            best_cap = cv2.VideoCapture(self.video_path)

        return best_cap

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
        """紧凑的时间区间合并算法 - 仅合并重叠区间"""
        trigger_times = [ts for ts, detected in detections if detected]

        if not trigger_times:
            return []

        # 按时间戳排序
        trigger_times.sort()

        # 创建基础时间范围（固定预留时间）
        time_ranges = []
        for ts in trigger_times:
            start_time = max(0, ts - self.padding)
            end_time = ts + self.padding
            time_ranges.append((start_time, end_time))

        # 合并重叠区间
        merged = []
        time_ranges.sort(key=lambda x: x[0])

        current_start, current_end = time_ranges[0]
        for start, end in time_ranges[1:]:
            if start <= current_end:
                # 合并重叠区间
                current_end = max(current_end, end)
            else:
                # 保存当前区间并开始新区间
                merged.append((current_start, current_end))
                current_start, current_end = start, end

        merged.append((current_start, current_end))

        return merged

    def check_and_repair_video(self):
        """检查并修复H.264视频流"""
        # 1. 检查视频完整性
        self.log_message.emit("[完整性检查] 检查视频完整性...", self.LOG_CHECK)
        if self.check_video_integrity():
            self.log_message.emit("[完整性检查] 视频完整性检查通过", self.LOG_CHECK)
            return True

        # 2. 尝试修复视频
        self.log_message.emit("[视频修复] 检测到视频损坏，尝试修复...", self.LOG_RECOVERY)
        repaired = self.repair_video()

        if repaired:
            self.log_message.emit(
                f"[视频修复] 视频修复成功，使用临时文件: {os.path.basename(self.video_path)}",
                self.LOG_RECOVERY
            )
            self.is_repaired_video = True
            return True

        # 3. 尝试使用替代解码方法
        self.log_message.emit("[解码] 尝试使用备用解码方法...", self.LOG_RECOVERY)
        return self.try_alternative_decoding()

    def analyze_h264_stream(self):
        """使用FFmpeg分析H.264码流"""
        if not self.has_ffmpeg():
            self.log_message.emit("[警告] FFmpeg不可用，跳过H.264分析", self.LOG_WARNING)
            return

        try:
            # 创建临时分析文件
            with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as log_file:
                log_path = log_file.name

            # 构建分析命令
            cmd = [
                'ffmpeg',
                '-v', 'debug',
                '-i', self.original_video_path,
                '-c:v', 'copy',
                '-an',
                '-f', 'null',
                '-',
                '2>', log_path
            ]

            # 在Windows上使用shell执行
            if os.name == 'nt':
                cmd_str = " ".join(cmd)
                self.analyze_process = subprocess.Popen(
                    cmd_str,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True
                )
            else:
                self.analyze_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True
                )

            # 等待分析完成
            self.analyze_process.wait()

            # 解析分析结果
            self.parse_h264_analysis(log_path)

            # 发送分析结果
            self.h264_analysis_complete.emit(self.h264_analysis)

            # 清理临时文件
            os.remove(log_path)

        except Exception as e:
            self.log_message.emit(f"[H.264分析] 分析失败: {str(e)}", self.LOG_ERROR)

    def parse_h264_analysis(self, log_path):
        """解析H.264分析日志 - 修复计算错误并增强分析"""
        self.h264_analysis = {
            "error_count": 0,
            "warnings": [],
            "nal_unit_errors": 0,
            "mb_errors": 0,
            "sps_pps_errors": 0,
            "resync_errors": 0,
            "frame_corruption": 0,
            "stream_errors": 0
        }

        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    # 1. 检测NAL单元错误
                    if "Invalid NAL unit size" in line or "Error splitting the input into NAL units" in line:
                        self.h264_analysis["nal_unit_errors"] += 1
                        self.h264_analysis["error_count"] += 1
                        self.h264_analysis["warnings"].append(line.strip())

                    # 2. 检测宏块错误
                    elif "error while decoding MB" in line or "concealing" in line:
                        self.h264_analysis["mb_errors"] += 1
                        self.h264_analysis["error_count"] += 1
                        self.h264_analysis["warnings"].append(line.strip())

                    # 3. 检测SPS/PPS错误
                    elif "no frame!" in line or "missing picture" in line or "sps_id" in line:
                        self.h264_analysis["sps_pps_errors"] += 1
                        self.h264_analysis["error_count"] += 1
                        self.h264_analysis["warnings"].append(line.strip())

                    # 4. 检测重新同步错误
                    elif "resync" in line.lower() or "synchronization" in line.lower():
                        self.h264_analysis["resync_errors"] += 1
                        self.h264_analysis["error_count"] += 1
                        self.h264_analysis["warnings"].append(line.strip())

                    # 5. 检测帧损坏错误
                    elif "corrupt" in line.lower() or "frame corruption" in line.lower():
                        self.h264_analysis["frame_corruption"] += 1
                        self.h264_analysis["error_count"] += 1
                        self.h264_analysis["warnings"].append(line.strip())

                    # 6. 检测流级错误
                    elif "stream error" in line.lower() or "bitstream" in line.lower():
                        self.h264_analysis["stream_errors"] += 1
                        self.h264_analysis["error_count"] += 1
                        self.h264_analysis["warnings"].append(line.strip())

                    # 7. 检测其他错误
                    elif "error" in line.lower() and "speed" not in line.lower():
                        self.h264_analysis["error_count"] += 1
                        self.h264_analysis["warnings"].append(line.strip())

            # 修复计算错误 - 只使用数值类型的字段进行计算
            classified_errors = (
                    self.h264_analysis["nal_unit_errors"] +
                    self.h264_analysis["mb_errors"] +
                    self.h264_analysis["sps_pps_errors"] +
                    self.h264_analysis["resync_errors"] +
                    self.h264_analysis["frame_corruption"] +
                    self.h264_analysis["stream_errors"]
            )

            other_errors = self.h264_analysis["error_count"] - classified_errors

            # 生成摘要报告
            summary = f"[H.264分析] 分析结果: {self.h264_analysis['error_count']}个错误\n"
            summary += f" - NAL单元错误: {self.h264_analysis['nal_unit_errors']}\n"
            summary += f" - 宏块错误: {self.h264_analysis['mb_errors']}\n"
            summary += f" - SPS/PPS错误: {self.h264_analysis['sps_pps_errors']}\n"
            summary += f" - 重新同步错误: {self.h264_analysis['resync_errors']}\n"
            summary += f" - 帧损坏: {self.h264_analysis['frame_corruption']}\n"
            summary += f" - 流错误: {self.h264_analysis['stream_errors']}\n"
            summary += f" - 其他错误: {other_errors}"

            self.h264_analysis["summary"] = summary
            self.log_message.emit(summary, self.LOG_ANALYSIS)

            # 记录前10个警告
            if self.h264_analysis["warnings"]:
                self.log_message.emit("[H.264分析] 前10个警告:", self.LOG_ANALYSIS)
                for warning in self.h264_analysis["warnings"][:10]:
                    self.log_message.emit(f" - {warning}", self.LOG_ANALYSIS)

        except Exception as e:
            self.log_message.emit(f"[H.264分析] 解析日志失败: {str(e)}", self.LOG_ERROR)

    def determine_repair_strategy(self):
        """根据H.264分析结果确定更精确的修复策略"""
        nal_errors = self.h264_analysis.get('nal_unit_errors', 0)
        sps_pps_errors = self.h264_analysis.get('sps_pps_errors', 0)
        resync_errors = self.h264_analysis.get('resync_errors', 0)
        frame_corruption = self.h264_analysis.get('frame_corruption', 0)
        total_errors = self.h264_analysis.get('error_count', 0)

        # 针对特定错误类型的修复策略
        if nal_errors > 50 or resync_errors > 30:
            return "nal_resync_repair"
        elif sps_pps_errors > 20:
            return "sps_pps_repair"
        elif frame_corruption > 40:
            return "frame_corruption_repair"
        elif total_errors > 100:
            return "aggressive"
        elif total_errors > 20:
            return "conservative"
        return "default"

    def repair_video(self):
        """使用FFmpeg修复视频文件 - 增强修复策略"""
        if not self.has_ffmpeg():
            self.log_message.emit("[错误] FFmpeg不可用，无法修复视频", self.LOG_ERROR)
            return False

        try:
            # 创建临时文件
            temp_dir = tempfile.mkdtemp()
            temp_file = os.path.join(temp_dir, f"repaired_{os.path.basename(self.original_video_path)}")
            self.temp_video_path = temp_file

            # 根据分析结果选择修复策略
            repair_strategy = self.determine_repair_strategy()
            self.log_message.emit(f"[视频修复] 使用修复策略: {repair_strategy}", self.LOG_RECOVERY)

            # 构建基础修复命令
            cmd = [
                'ffmpeg',
                '-y',
                '-i', self.original_video_path,
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-c:a', 'copy',
                '-map', '0',
                '-ignore_unknown',
                '-max_muxing_queue_size', '1024'
            ]

            # 添加特定修复选项
            if repair_strategy == "nal_resync_repair":
                # 针对NAL单元和重新同步错误的修复
                cmd.extend([
                    '-fflags', '+genpts+igndts+discardcorrupt',
                    '-err_detect', 'ignore_err',
                    '-vsync', '0',
                    '-x264-params', 'force-cfr=1:nal-hrd=cbr',
                    '-flags', '+ildct+ilme',
                    '-strict', '-2'
                ])
            elif repair_strategy == "sps_pps_repair":
                # 针对SPS/PPS错误的修复
                cmd.extend([
                    '-fflags', '+genpts',
                    '-err_detect', 'aggressive',
                    '-x264-params', 'repeat-headers=1:force-cfr=1',
                    '-strict', '-2'
                ])
            elif repair_strategy == "frame_corruption_repair":
                # 针对帧损坏的修复
                cmd.extend([
                    '-fflags', '+discardcorrupt',
                    '-err_detect', 'explode',
                    '-x264-params', 'deblock=-2,-2',
                    '-strict', '-2'
                ])
            elif repair_strategy == "aggressive":
                # 激进修复策略
                cmd.extend([
                    '-fflags', '+genpts+igndts+discardcorrupt',
                    '-err_detect', 'ignore_err',
                    '-vsync', '0',
                    '-strict', '-2'
                ])
            elif repair_strategy == "conservative":
                # 保守修复策略
                cmd.extend([
                    '-err_detect', 'explode',
                    '-strict', '-2'
                ])
            # "default" 使用基础命令

            cmd.append(temp_file)

            # 运行修复过程
            self.log_message.emit(f"[视频修复] 执行命令: {' '.join(cmd)}", self.LOG_RECOVERY)
            self.repair_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
                text=True
            )

            # 监控进度
            for line in self.repair_process.stdout:
                if "frame=" in line:
                    match = re.search(r'frame=\s*(\d+)', line)
                    if match:
                        frame_count = int(match.group(1))
                        self.video_repair_progress.emit(frame_count)

                # 捕获错误和警告
                if "error" in line.lower():
                    self.log_message.emit(f"[视频修复错误] {line.strip()}", self.LOG_ERROR)
                elif "warning" in line.lower():
                    self.log_message.emit(f"[视频修复警告] {line.strip()}", self.LOG_WARNING)
                else:
                    # 减少日志输出量
                    if "time=" in line:
                        self.log_message.emit(f"[视频修复] {line.strip()}", self.LOG_RECOVERY)

            self.repair_process.wait()

            if self.repair_process.returncode != 0:
                self.log_message.emit(
                    f"[错误] 视频修复失败，返回码: {self.repair_process.returncode}",
                    self.LOG_ERROR
                )
                return False

            # 使用修复后的视频
            self.video_path = temp_file

            # 验证修复结果
            if self.check_video_integrity():
                return True

            self.log_message.emit("[警告] 修复后的视频仍然存在问题", self.LOG_WARNING)
            return False
        except Exception as e:
            self.log_message.emit(f"[错误] 视频修复异常: {str(e)}", self.LOG_ERROR)
            return False
        finally:
            self.repair_process = None

    def check_video_integrity(self):
        """检查视频完整性"""
        try:
            # 方法1: 使用OpenCV检查
            cap = cv2.VideoCapture(self.video_path)
            if not cap.isOpened():
                self.log_message.emit("[完整性检查] OpenCV无法打开视频", self.LOG_CHECK)
                return False

            # 尝试读取多帧
            test_frames = 50
            success_count = 0
            frame_positions = np.linspace(0, cap.get(cv2.CAP_PROP_FRAME_COUNT) - 1, test_frames, dtype=int)

            for pos in frame_positions:
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                ret, _ = cap.read()
                if ret:
                    success_count += 1

            cap.release()

            success_rate = success_count / test_frames
            if success_rate < 0.8:
                self.log_message.emit(
                    f"[完整性检查] 视频帧读取失败率高: {success_count}/{test_frames} ({success_rate * 100:.1f}%)",
                    self.LOG_CHECK
                )
                return False

            # 方法2: 使用MediaInfo检查 (如果可用)
            if self.has_mediainfo():
                try:
                    cmd = ['mediainfo', '--Output=JSON', self.video_path]
                    result = subprocess.run(cmd, capture_output=True, text=True, check=True)

                    if result.returncode != 0:
                        self.log_message.emit(f"[完整性检查] MediaInfo检查失败: {result.stderr}", self.LOG_CHECK)
                        return False

                    media_info = json.loads(result.stdout)
                    if 'media' not in media_info or 'track' not in media_info['media']:
                        self.log_message.emit("[完整性检查] MediaInfo未检测到有效轨道", self.LOG_CHECK)
                        return False

                    # 检查视频轨道完整性
                    video_tracks = [t for t in media_info['media']['track'] if t.get('@type') == 'Video']
                    if not video_tracks:
                        self.log_message.emit("[完整性检查] MediaInfo未检测到视频轨道", self.LOG_CHECK)
                        return False

                    # 检查关键指标
                    video_track = video_tracks[0]
                    required_keys = ['Format', 'Width', 'Height', 'FrameRate', 'FrameCount']
                    missing_keys = [k for k in required_keys if k not in video_track]

                    if missing_keys:
                        self.log_message.emit(
                            f"[完整性检查] 视频轨道缺少关键信息: {', '.join(missing_keys)}",
                            self.LOG_CHECK
                        )
                        return False

                    self.log_message.emit("[完整性检查] MediaInfo检查通过", self.LOG_CHECK)
                except Exception as e:
                    self.log_message.emit(f"[完整性检查] MediaInfo检查异常: {str(e)}", self.LOG_CHECK)

            return True
        except Exception as e:
            self.log_message.emit(f"[完整性检查] 检查失败: {str(e)}", self.LOG_CHECK)
            return False

    def try_alternative_decoding(self):
        """尝试使用替代解码方法"""
        self.log_message.emit("[解码] 尝试使用OpenCV的FFMPEG后端...", self.LOG_RECOVERY)
        try:
            # 尝试使用FFMPEG后端
            cap = cv2.VideoCapture(self.original_video_path, cv2.CAP_FFMPEG)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    self.log_message.emit("[解码] FFMPEG后端成功打开视频", self.LOG_RECOVERY)
                    return True
        except Exception as e:
            self.log_message.emit(f"[解码错误] FFMPEG后端失败: {str(e)}", self.LOG_ERROR)

        self.log_message.emit("[解码] 尝试使用GPU加速解码...", self.LOG_RECOVERY)
        try:
            # 尝试使用GPU加速
            cap = cv2.VideoCapture(self.original_video_path, cv2.CAP_CUDA)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    self.log_message.emit("[解码] GPU加速解码成功", self.LOG_RECOVERY)
                    return True
        except Exception as e:
            self.log_message.emit(f"[解码错误] GPU加速失败: {str(e)}", self.LOG_ERROR)

        self.log_message.emit("[错误] 所有替代解码方法均失败", self.LOG_ERROR)
        return False

    def cleanup_temp_files(self):
        """清理临时文件"""
        if self.is_repaired_video and self.temp_video_path and os.path.exists(self.temp_video_path):
            try:
                # 删除临时文件及其目录
                temp_dir = os.path.dirname(self.temp_video_path)
                if os.path.isdir(temp_dir):
                    for file in os.listdir(temp_dir):
                        os.remove(os.path.join(temp_dir, file))
                    os.rmdir(temp_dir)
                self.log_message.emit(
                    f"[清理] 已清理临时视频: {os.path.basename(self.temp_video_path)}",
                    self.LOG_INFO
                )
            except Exception as e:
                self.log_message.emit(
                    f"[清理错误] 清理临时文件失败: {str(e)}",
                    self.LOG_ERROR
                )

    def has_ffmpeg(self):
        """检查系统是否安装了FFmpeg"""
        try:
            result = subprocess.run(['ffmpeg', '-version'],
                                    capture_output=True,
                                    text=True,
                                    timeout=5)
            return result.returncode == 0
        except:
            return False

    def has_mediainfo(self):
        """检查系统是否安装了MediaInfo"""
        try:
            result = subprocess.run(['mediainfo', '--version'],
                                    capture_output=True,
                                    text=True,
                                    timeout=5)
            return result.returncode == 0
        except:
            return False

    def cancel(self):
        """取消处理"""
        self.cancelled = True

        # 终止分析过程
        if self.analyze_process and self.analyze_process.poll() is None:
            try:
                self.analyze_process.terminate()
                self.log_message.emit("[取消] H.264分析已取消", self.LOG_INFO)
            except:
                pass

        # 终止修复过程
        if self.repair_process and self.repair_process.poll() is None:
            try:
                self.repair_process.terminate()
                self.log_message.emit("[取消] 视频修复已取消", self.LOG_INFO)
            except:
                pass


class MultiThreadOCRProcessor:
    """多线程OCR处理器"""

    def __init__(self, video_path, regions, keywords, padding=10.0, skip_frames=1, rotation=0, num_threads=4):
        self.video_path = video_path
        self.regions = regions
        self.keywords = keywords
        self.padding = padding
        self.skip_frames = skip_frames
        self.rotation = rotation
        self.num_threads = num_threads
        self.processors = []
        self.total_frames = 0
        self.time_ranges = []
        self.is_prepared = False

    def prepare_video(self):
        """准备视频：分析并修复"""
        try:
            # 使用第一个线程进行视频准备
            prep_processor = OCRProcessor(
                0,
                self.video_path,
                self.regions,
                self.keywords,
                self.padding,
                self.skip_frames,
                self.rotation
            )

            # 1. 分析H.264码流
            prep_processor.analyze_h264_stream()

            # 2. 检查并修复视频
            if not prep_processor.check_and_repair_video():
                return False

            # 3. 获取视频信息
            cap = prep_processor.open_video_with_best_backend()
            if not cap or not cap.isOpened():
                return False

            self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()

            self.video_path = prep_processor.video_path  # 更新为修复后的路径
            self.is_repaired = prep_processor.is_repaired_video
            self.temp_video_path = prep_processor.temp_video_path

            self.is_prepared = True
            return True

        except Exception as e:
            print(f"视频准备失败: {str(e)}")
            return False

    def split_video_segments(self):
        """分割视频为多个片段"""
        segments = []
        frames_per_segment = self.total_frames // self.num_threads

        for i in range(self.num_threads):
            start_frame = i * frames_per_segment
            end_frame = (i + 1) * frames_per_segment - 1 if i < self.num_threads - 1 else self.total_frames - 1
            segments.append((start_frame, end_frame))

        return segments

    def create_processors(self):
        """创建处理器线程"""
        if not self.is_prepared:
            return False

        segments = self.split_video_segments()
        self.processors = []

        for i, (start, end) in enumerate(segments):
            processor = OCRProcessor(
                i,
                self.video_path,
                self.regions,
                self.keywords,
                self.padding,
                self.skip_frames,
                self.rotation,
                start,
                end
            )
            self.processors.append(processor)

        return True

    def merge_results(self, all_results):
        """合并所有线程的结果"""
        # 合并所有检测到的时间戳
        all_timestamps = []
        for time_ranges in all_results:
            for start, end in time_ranges:
                # 取中间点作为检测时间戳
                timestamp = (start + end) / 2
                all_timestamps.append(timestamp)

        # 合并时间段
        if not all_timestamps:
            return []

        all_timestamps.sort()
        time_ranges = [(max(0, t - self.padding), t + self.padding) for t in all_timestamps]
        time_ranges.sort(key=lambda x: x[0])

        # 合并重叠区间
        merged = []
        current_start, current_end = time_ranges[0]

        for start, end in time_ranges[1:]:
            if start <= current_end:
                current_end = max(current_end, end)
            else:
                merged.append((current_start, current_end))
                current_start, current_end = start, end

        merged.append((current_start, current_end))
        return merged

    def cleanup(self):
        """清理资源"""
        if self.is_repaired and self.temp_video_path and os.path.exists(self.temp_video_path):
            try:
                temp_dir = os.path.dirname(self.temp_video_path)
                if os.path.isdir(temp_dir):
                    for file in os.listdir(temp_dir):
                        os.remove(os.path.join(temp_dir, file))
                    os.rmdir(temp_dir)
            except:
                pass


class OCRProcessDialog(QDialog):
    """OCR处理对话框 - 集成日志窗口"""

    def __init__(self, initial_dir="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("OCR处理")
        self.setMinimumSize(1000, 800)  # 增大窗口尺寸以适应新控件

        # 设置窗口图标
        if os.path.exists("OCR.png"):
            self.setWindowIcon(QIcon("OCR.png"))

        # 初始化变量
        self.video_path = None
        self.regions = []
        self.time_ranges = []
        self.annotation_path = None
        self.keywords = ["淘汰了", "击倒了"]
        self.rotation = 0
        self.padding = 5.0  # 默认预留时间为5秒
        self.processing = False
        self.total_frames = 0
        self.processed_frames = 0
        self.thread_current_frames = {}

        # 保存初始目录
        self.initial_dir = initial_dir
        self.video_dir = ""
        self.video_name = None

        # 如果是文件路径，则提取目录和视频名称
        if initial_dir and os.path.isfile(initial_dir):
            self.video_dir = os.path.dirname(initial_dir)
            self.video_name = os.path.splitext(os.path.basename(initial_dir))[0]

        # 主布局
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

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
        self.browse_btn.clicked.connect(lambda: self.select_annotation_file())
        annotation_path_layout.addWidget(self.browse_btn)

        annotation_layout.addLayout(annotation_path_layout)
        main_layout.addWidget(annotation_group)
        # =====================================================

        # =================== 参数设置区域 ===================
        params_group = QGroupBox("参数设置")
        params_layout = QHBoxLayout(params_group)

        # 击杀前后预留时间设置
        params_layout.addWidget(QLabel("击杀前后预留时间(秒):"))
        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(1, 30)  # 设置范围1-30秒
        self.padding_spin.setValue(5)  # 默认值5秒
        self.padding_spin.setSingleStep(1)  # 步长为1秒
        self.padding_spin.valueChanged.connect(self.update_padding)
        params_layout.addWidget(self.padding_spin)

        # 旋转角度设置
        params_layout.addWidget(QLabel("视频旋转角度:"))
        self.rotation_combo = QComboBox()
        self.rotation_combo.addItem("0度", 0)
        self.rotation_combo.addItem("90度", 90)
        self.rotation_combo.addItem("180度", 180)
        self.rotation_combo.addItem("270度", 270)
        params_layout.addWidget(self.rotation_combo)

        # 线程数设置
        params_layout.addWidget(QLabel("处理线程数:"))
        self.thread_count_spin = QSpinBox()
        self.thread_count_spin.setRange(1, 16)
        self.thread_count_spin.setValue(12)
        self.thread_count_spin.setToolTip("设置处理线程数量 (根据CPU核心数调整)")
        params_layout.addWidget(self.thread_count_spin)

        # 填充空白
        params_layout.addStretch(1)
        main_layout.addWidget(params_group)
        # =================================================

        # 状态标签
        self.status_label = QLabel("准备开始OCR处理...")
        main_layout.addWidget(self.status_label)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        main_layout.addWidget(self.progress_bar)

        # 处理信息
        self.info_label = QLabel("")
        main_layout.addWidget(self.info_label)

        # ================ 日志显示区域 (使用QSplitter) ================
        # 创建水平分割器
        log_splitter = QSplitter(Qt.Horizontal)

        # OCR识别文本窗口
        ocr_log_group = QGroupBox("OCR识别文本 (实时)")
        ocr_log_layout = QVBoxLayout(ocr_log_group)

        self.ocr_log_text = QTextEdit()
        self.ocr_log_text.setReadOnly(True)
        self.ocr_log_text.setMinimumHeight(200)
        self.ocr_log_text.setStyleSheet("""
            QTextEdit {
                background-color: #F8F8F8;
                border: 1px solid #CCCCCC;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 10pt;
            }
        """)
        ocr_log_layout.addWidget(self.ocr_log_text)
        ocr_log_group.setLayout(ocr_log_layout)
        log_splitter.addWidget(ocr_log_group)

        # 系统日志窗口
        system_log_group = QGroupBox("系统日志")
        system_log_layout = QVBoxLayout(system_log_group)

        self.system_log_text = QTextEdit()
        self.system_log_text.setReadOnly(True)
        self.system_log_text.setMinimumHeight(200)
        self.system_log_text.setStyleSheet("""
            QTextEdit {
                background-color: #F0F0F0;
                border: 1px solid #CCCCCC;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 10pt;
            }
        """)
        system_log_layout.addWidget(self.system_log_text)
        system_log_group.setLayout(system_log_layout)
        log_splitter.addWidget(system_log_group)

        # 设置初始大小比例
        log_splitter.setSizes([400, 400])
        main_layout.addWidget(log_splitter)
        # =====================================================

        # 结果列表
        self.result_list = QListWidget()
        main_layout.addWidget(self.result_list, 1)

        # 按钮布局
        button_layout = QHBoxLayout()

        self.start_btn = QPushButton("开始处理")
        self.start_btn.clicked.connect(self.start_processing)
        self.start_btn.setEnabled(False)  # 初始不可用
        button_layout.addWidget(self.start_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_processing)
        button_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("保存结果")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_results)
        button_layout.addWidget(self.save_btn)

        main_layout.addLayout(button_layout)

        # UI初始化完成后进行自动检测
        if self.video_dir and self.video_name:
            self.auto_detect_annotation_file(self.video_dir, self.video_name)

    def update_padding(self, value):
        """更新预留时间值"""
        self.padding = value
        self.append_to_system_log(f"预留时间已设置为: {value}秒")

    def auto_detect_annotation_file(self, video_dir, video_name):
        """自动检测标注文件"""
        # 检查与视频同名的标注文件
        video_based_name = f"{video_name}_labels.json"
        video_based_path = os.path.join(video_dir, video_based_name)

        # 优先使用固定名称的文件
        if os.path.exists(video_based_path):
            self.annotation_path = video_based_path
            self.annotation_path_edit.setText(video_based_path)
            self.load_annotation()
            self.append_to_system_log(f"[自动检测] 找到标注文件: {video_based_name}")
        else:
            self.append_to_system_log(f"[自动检测] 未找到标注文件: {video_based_name}")

    # 重写关闭事件
    def closeEvent(self, event):
        """处理窗口关闭事件"""
        if self.processing:
            reply = QMessageBox.warning(
                self,
                "处理正在进行中",
                "OCR处理正在进行中，关闭窗口将取消处理。\n是否要取消处理并关闭窗口？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                self.cancel_processing()
                # 等待所有线程安全退出
                self.wait_for_threads()
                event.accept()
            else:
                event.ignore()
        else:
            # 确保释放所有资源
            self.cleanup_resources()
            event.accept()

    def wait_for_threads(self):
        """等待所有线程安全退出"""
        if hasattr(self, 'ocr_processors'):
            self.append_to_system_log("[资源释放] 等待线程结束...")
            for processor in self.ocr_processors:
                if processor.isRunning():
                    processor.wait(2000)  # 最多等待2秒
            self.append_to_system_log("[资源释放] 所有线程已结束")

    def cleanup_resources(self):
        """清理所有资源"""
        # 显式释放OCR模型资源
        if hasattr(self, 'ocr_processors'):
            for processor in self.ocr_processors:
                if hasattr(processor, 'reader') and processor.reader is not None:
                    try:
                        if hasattr(processor.reader, 'detector'):
                            processor.reader.detector.destroy()
                        if hasattr(processor.reader, 'recognizer'):
                            processor.reader.recognizer.destroy()
                        processor.reader = None
                    except Exception as e:
                        pass
            self.ocr_processors = []
            self.append_to_system_log("[资源释放] OCR模型资源已释放")
    def select_annotation_file(self):
        """选择标注文件并自动加载"""
        # 设置默认目录为视频所在目录（如果存在）
        default_dir = self.video_dir if self.video_dir else ""

        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择标注文件", default_dir, "JSON文件 (*.json)"
        )

        if file_path:
            self.annotation_path = file_path
            self.annotation_path_edit.setText(file_path)
            self.load_annotation()  # 自动加载标注文件

    def load_annotation(self):
        """加载标注文件"""
        if not self.annotation_path:
            QMessageBox.warning(self, "警告", "请先选择标注文件")
            return

        try:
            with open(self.annotation_path, 'r', encoding='utf-8') as f:
                annotation_data = json.load(f)

            # 提取信息
            self.video_path = annotation_data.get("video_path", "")
            self.regions = annotation_data.get("regions", [])
            self.rotation = annotation_data.get("rotation", 0)
            self.padding = annotation_data.get("padding", 5.0)  # 默认5秒

            # 更新UI控件
            self.padding_spin.setValue(int(self.padding))
            self.rotation_combo.setCurrentIndex(self.rotation_combo.findData(self.rotation))

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
            self.ocr_log_text.clear()  # 清空之前的OCR日志
            self.system_log_text.clear()  # 清空之前的系统日志

        except Exception as e:
            QMessageBox.critical(
                self, "加载失败", f"加载标注文件时出错:\n{str(e)}"
            )
            self.status_label.setText(f"加载失败: {str(e)}")
            self.append_to_system_log(f"错误: {str(e)}")

    def update_info(self):
        """更新对话框信息"""
        if not self.video_path or not self.regions:
            self.info_label.setText("未加载有效视频或区域信息")
            return

        info = f"视频文件: {os.path.basename(self.video_path)}\n"
        info += f"检测区域数: {len(self.regions)}\n"
        info += f"预留时间: {self.padding}秒\n"
        info += f"旋转角度: {self.rotation}度\n"

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
        # 设置处理中标志
        self.processing = True
        # 获取当前旋转角度
        self.rotation = self.rotation_combo.currentData()
        # 获取线程数
        num_threads = self.thread_count_spin.value()

        # 创建OCR处理器线程列表
        self.ocr_processors = []
        self.thread_results = {}
        self.completed_threads = 0

        # 打开视频以获取总帧数
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            QMessageBox.critical(self, "错误", "无法打开视频文件")
            self.processing = False
            return

        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        # 重置进度跟踪
        self.thread_current_frames = {}
        self.progress_bar.setValue(0)

        # 计算每个线程处理的帧范围
        frames_per_thread = self.total_frames // num_threads
        segments = []
        for i in range(num_threads):
            start_frame = i * frames_per_thread
            end_frame = (i + 1) * frames_per_thread - 1 if i < num_threads - 1 else self.total_frames - 1
            segments.append((start_frame, end_frame))

            # 初始化每个线程的当前帧位置为起始帧
            self.thread_current_frames[i] = start_frame

        # 创建处理线程
        for i, (start_frame, end_frame) in enumerate(segments):
            processor = OCRProcessor(
                i,
                self.video_path,
                self.regions,
                self.keywords,
                padding=self.padding,
                skip_frames=3,
                rotation=self.rotation,
                start_frame=start_frame,
                end_frame=end_frame
            )
            self.ocr_processors.append(processor)

            # 连接信号
            processor.progress_updated.connect(self.update_progress)
            processor.result_detected.connect(self.add_result)
            processor.finished_segment.connect(self.handle_thread_finished)
            processor.ocr_text_detected.connect(self.show_ocr_text)
            processor.log_message.connect(self.handle_log_message)

        # 启动所有线程
        for processor in self.ocr_processors:
            processor.start()

        # 更新UI
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.save_btn.setEnabled(False)
        self.status_label.setText(f"已启动 {num_threads} 个处理线程...")
        self.result_list.clear()
        self.ocr_log_text.clear()
        self.system_log_text.clear()

    def handle_log_message(self, message, log_type):
        """处理日志消息并显示在系统日志框中"""
        # 在文本框中显示带颜色的日志
        if log_type == OCRProcessor.LOG_ERROR:
            formatted_msg = f"<font color='red'>{message}</font>"
        elif log_type == OCRProcessor.LOG_WARNING:
            formatted_msg = f"<font color='orange'>{message}</font>"
        elif log_type == OCRProcessor.LOG_RECOVERY:
            formatted_msg = f"<font color='blue'>{message}</font>"
        elif log_type == OCRProcessor.LOG_CHECK:
            formatted_msg = f"<font color='green'>{message}</font>"
        elif log_type == OCRProcessor.LOG_ANALYSIS:
            formatted_msg = f"<font color='purple'>{message}</font>"
        else:
            formatted_msg = message

        # 使用线程安全的方式更新系统日志框
        QTimer.singleShot(0, lambda: self.append_to_system_log(formatted_msg))

    def show_ocr_text(self, timestamp, text):
        """在OCR日志框中显示OCR识别结果"""
        # 过滤空文本
        if not text.strip():
            return

        # 格式化时间戳
        minutes = int(timestamp // 60)
        seconds = int(timestamp % 60)

        # 创建日志条目
        log_entry = f"[{minutes:02d}:{seconds:02d}] {text}"

        # 使用线程安全的方式更新UI
        QTimer.singleShot(0, lambda: self.append_to_ocr_log(log_entry))

    def append_to_ocr_log(self, text):
        """将文本追加到OCR日志框并自动滚动到底部"""
        self.ocr_log_text.append(text)
        # 自动滚动到底部
        cursor = self.ocr_log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.ocr_log_text.setTextCursor(cursor)

    def append_to_system_log(self, text):
        """将文本追加到系统日志框并自动滚动到底部"""
        self.system_log_text.append(text)
        # 自动滚动到底部
        cursor = self.system_log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.system_log_text.setTextCursor(cursor)

    def cancel_processing(self):
        """取消处理"""
        if hasattr(self, 'ocr_processors'):
            for processor in self.ocr_processors:
                processor.cancel()
        self.status_label.setText("处理已取消")
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

        # 重置处理中标志
        self.processing = False

    def update_progress(self, thread_id, frame, progress):
        """更新进度"""
        # 更新当前线程的帧位置
        self.thread_current_frames[thread_id] = frame

        # 计算所有线程已处理的总帧数
        total_processed = 0
        for tid, current_frame in self.thread_current_frames.items():
            # 获取该线程的起始帧
            start_frame = self.ocr_processors[tid].start_frame

            # 计算该线程已处理的帧数
            thread_processed = max(0, current_frame - start_frame)
            total_processed += thread_processed

        # 计算整体进度
        overall_progress = min(100.0, (total_processed / self.total_frames) * 100)

        # 更新进度条
        self.progress_bar.setValue(int(overall_progress))

        # 更新状态标签
        self.status_label.setText(
            f"处理中: 总进度 {overall_progress:.1f}% (已处理 {total_processed}/{self.total_frames} 帧)")

    def add_result(self, timestamp, text):
        """添加识别结果并更新日志"""
        # 过滤非目标文本
        if "你使用" not in text:
            return

        # 添加到结果列表
        minutes = int(timestamp // 60)
        seconds = int(timestamp % 60)
        item = QListWidgetItem(f"[{minutes:02d}:{seconds:02d}] {text}")
        self.result_list.addItem(item)

        # 添加到OCR日志 (线程安全)
        log_entry = f"[{minutes:02d}:{seconds:02d}] {text}"
        QTimer.singleShot(0, lambda: self.append_to_ocr_log(log_entry))

    def handle_thread_finished(self, thread_id, time_ranges):
        """处理单个线程完成 - 简化结果合并"""
        self.thread_results[thread_id] = time_ranges
        self.completed_threads += 1

        self.append_to_system_log(f"线程 {thread_id} 完成, 检测到 {len(time_ranges)} 个时间段")

        # 检查是否所有线程都完成
        if self.completed_threads == len(self.ocr_processors):
            self.append_to_system_log("所有线程已完成, 合并结果...")
            self.progress_bar.setValue(100)

            # 收集所有时间段
            all_ranges = []
            for tid, ranges in self.thread_results.items():
                all_ranges.extend(ranges)

            # 按开始时间排序
            all_ranges.sort(key=lambda x: x[0])

            # 合并重叠区间
            merged = []
            if all_ranges:
                current_start, current_end = all_ranges[0]

                for start, end in all_ranges[1:]:
                    if start <= current_end:
                        # 合并重叠区间
                        current_end = max(current_end, end)
                    else:
                        # 保存当前区间并开始新区间
                        merged.append((current_start, current_end))
                        current_start, current_end = start, end

                # 添加最后一个区间
                merged.append((current_start, current_end))

            # 完成处理
            self.finish_processing(merged)

    def finish_processing(self, time_ranges):
        """处理完成，显示精简结果"""
        self.time_ranges = time_ranges
        self.status_label.setText(f"处理完成! 找到 {len(time_ranges)} 个片段")
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.save_btn.setEnabled(True)
        self.processing = False

        # 计算总击杀数
        total_kills = 0
        for processor in self.ocr_processors:
            total_kills += len(processor.detections) if hasattr(processor, 'detections') else 0

        # 清空结果列表
        self.result_list.clear()

        # 添加标题
        self.result_list.addItem(f"检测到总击杀数: {total_kills}")
        self.result_list.addItem(f"生成 {len(time_ranges)} 个剪辑片段")

        if time_ranges:
            # 显示每个时间段
            for i, (start, end) in enumerate(time_ranges):
                start_min = int(start // 60)
                start_sec = int(start % 60)
                end_min = int(end // 60)
                end_sec = int(end % 60)
                duration = end - start

                # 计算此时间段内的击杀数
                kills_in_range = 0
                for processor in self.ocr_processors:
                    if hasattr(processor, 'detections'):
                        for ts, detected in processor.detections:
                            if start <= ts <= end:
                                kills_in_range += 1

                self.result_list.addItem(
                    f"片段 {i + 1}: [{start_min:02d}:{start_sec:02d} - {end_min:02d}:{end_sec:02d}] "
                    f"时长: {duration:.1f}秒 | 击杀: {kills_in_range}"
                )

            # 添加统计信息
            self.result_list.addItem("\n--- 合并统计 ---")
            self.result_list.addItem(f"团战识别: 30秒内多次击杀自动合并")
            self.result_list.addItem(f"基础预留时间: {self.padding}秒")
            self.result_list.addItem(f"大规模团战额外增加时间")
        else:
            self.result_list.addItem("\n--- 未检测到击杀 ---")
            self.result_list.addItem(f"检测到总击杀数: {total_kills}")

            # 显示所有击杀时间戳
            if total_kills > 0:
                self.result_list.addItem("\n所有击杀时间点:")
                all_kills = []
                for processor in self.ocr_processors:
                    if hasattr(processor, 'detections'):
                        for ts, detected in processor.detections:
                            all_kills.append(ts)

                all_kills.sort()
                for ts in all_kills:
                    minutes = int(ts // 60)
                    seconds = int(ts % 60)
                    self.result_list.addItem(f"  - [{minutes:02d}:{seconds:02d}]")


    def save_results(self):
        """保存结果到文件"""
        if not self.time_ranges:
            QMessageBox.warning(self, "警告", "没有可保存的结果")
            return

        # 获取视频文件名
        video_name = os.path.splitext(os.path.basename(self.video_path))[0]

        # 设置默认保存路径为视频所在目录
        video_dir = os.path.dirname(self.video_path)
        default_path = os.path.join(video_dir, f"{video_name}_clips.json")

        # 选择保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存剪辑时间段",
            default_path,  # 设置默认路径
            "JSON file (*.json)"
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
                    "annotation_file": self.annotation_path if self.annotation_path else "",
                    "padding": self.padding  # 保存预留时间设置
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