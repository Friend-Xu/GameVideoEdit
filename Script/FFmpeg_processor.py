import os
import subprocess
import tempfile
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, Signal, Qt, QThread, QTimer
from PySide6.QtGui import QIcon, QColor, QTextCharFormat, QFont, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton,
    QHBoxLayout, QFileDialog, QGroupBox, QLineEdit, QMessageBox,
    QApplication, QTextEdit, QSplitter
)

# 尝试导入OpenCV，如果可用则作为备用剪辑后端
try:
    import cv2

    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False
    logging.warning("OpenCV不可用，将仅使用FFmpeg")

# 尝试导入moviepy，如果可用则作为备用剪辑后端
try:
    from moviepy.video.io import VideoFileClip

    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False
    logging.warning("MoviePy不可用，将仅使用FFmpeg")


class VideoEditor(QObject):
    progress_updated = Signal(int, str)  # (进度百分比, 状态消息)
    finished = Signal(bool, str)  # (是否成功, 消息)
    error_occurred = Signal(str)  # 错误消息
    log_message = Signal(str, str)  # (日志内容, 日志类型: info/warning/error/debug/backend)

    # 日志类型常量
    LOG_INFO = "info"
    LOG_WARNING = "warning"
    LOG_ERROR = "error"
    LOG_DEBUG = "debug"
    LOG_BACKEND = "backend"

    def __init__(self):
        super().__init__()
        self.temp_files = []
        self.is_cancelled = False
        self.current_backend = "ffmpeg"  # 当前使用的剪辑后端
        self.repair_cache = {}  # 缓存修复后的视频片段路径
        self.frame_repair_threshold = 5  # 连续损坏帧阈值
        self.gpu_options = None  # GPU加速选项

    def log(self, message, log_type=LOG_INFO):
        """发送日志消息"""
        self.log_message.emit(message, log_type)

    def get_gpu_acceleration_options(self):
        """获取适合当前系统的GPU加速选项"""
        try:
            # 检查NVIDIA GPU
            try:
                result = subprocess.run(
                    ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                if result.returncode == 0 and result.stdout.strip():
                    self.log("检测到NVIDIA GPU，启用CUDA加速", self.LOG_INFO)
                    return {
                        'decoder': 'cuda',
                        'encoder': 'h264_nvenc',
                        'hwaccel': 'cuda',
                        'extra': ['-hwaccel_output_format', 'cuda']
                    }
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass

            # 检查AMD GPU (Linux)
            if os.name == 'posix':
                try:
                    result = subprocess.run(
                        ['rocminfo'],  # AMD ROCm工具
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                    )
                    if result.returncode == 0:
                        self.log("检测到AMD GPU，启用AMF加速", self.LOG_INFO)
                        return {
                            'decoder': 'h264_amf',
                            'encoder': 'h264_amf',
                            'hwaccel': 'auto',
                            'extra': []
                        }
                except (FileNotFoundError, subprocess.CalledProcessError):
                    pass

            # 检查Intel Quick Sync
            try:
                result = subprocess.run(
                    ['ffmpeg', '-hide_banner', '-encoders'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                if 'h264_qsv' in result.stdout:
                    self.log("检测到Intel Quick Sync，启用QSV加速", self.LOG_INFO)
                    return {
                        'decoder': 'h264_qsv',
                        'encoder': 'h264_qsv',
                        'hwaccel': 'qsv',
                        'extra': ['-load_plugin', 'hevc_hw']
                    }
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass
        except Exception as e:
            self.log(f"GPU检测失败: {str(e)}", self.LOG_WARNING)

        self.log("未检测到GPU加速支持，使用CPU编码", self.LOG_INFO)
        return None

    def combine_clips(self, video_path, clip_ranges, output_path, ffmpeg_path="ffmpeg"):
        # 获取GPU加速选项
        if not hasattr(self, 'gpu_options') or self.gpu_options is None:
            self.gpu_options = self.get_gpu_acceleration_options()

        preprocessed_path = None
        try:
            self.is_cancelled = False
            self.log("开始剪辑视频...", self.LOG_INFO)
            self.progress_updated.emit(0, "开始剪辑视频...")

            # 确保至少有一个后端可用
            if not self.check_ffmpeg(ffmpeg_path) and not OPENCV_AVAILABLE and not MOVIEPY_AVAILABLE:
                error_msg = "没有可用的视频处理后端"
                self.log(error_msg, self.LOG_ERROR)
                self.error_occurred.emit(error_msg)
                return False

            # 预处理视频 - 修复已知问题
            self.log("预处理视频文件...", self.LOG_INFO)
            self.progress_updated.emit(1, "预处理视频文件...")
            preprocessed_path = self.preprocess_video(video_path, ffmpeg_path)
            if not preprocessed_path or not os.path.exists(preprocessed_path):
                error_msg = "视频预处理失败"
                self.log(error_msg, self.LOG_ERROR)
                self.error_occurred.emit(error_msg)
                return False

            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                self.temp_files = []
                total_clips = len(clip_ranges)

                # 生成视频片段
                self.log(f"准备剪辑 {total_clips} 个片段...", self.LOG_INFO)
                self.progress_updated.emit(5, f"准备剪辑 {total_clips} 个片段...")
                futures = []
                with ThreadPoolExecutor(max_workers=min(4, total_clips)) as executor:
                    for i, (start, end) in enumerate(clip_ranges):
                        if self.is_cancelled:
                            self._cleanup_temp_files()
                            return False

                        clip_path = os.path.join(temp_dir, f"clip_{i}.mp4")
                        self.temp_files.append(clip_path)
                        self.log(f"开始剪辑片段 {i + 1}: [{start:.2f}-{end:.2f}]秒", self.LOG_INFO)

                        futures.append(executor.submit(
                            self.cut_clip,
                            preprocessed_path,
                            start,
                            end,
                            clip_path,
                            ffmpeg_path
                        ))

                # 检查片段生成结果
                failed_clips = []
                for i, future in enumerate(futures):
                    if self.is_cancelled:
                        self._cleanup_temp_files()
                        return False

                    success, message = future.result()
                    progress = 5 + int((i + 1) / total_clips * 75)

                    if not success:
                        error_msg = f"片段 {i + 1}/{total_clips} 剪辑失败: {message}"
                        self.log(error_msg, self.LOG_ERROR)
                        self.progress_updated.emit(progress, error_msg)
                        failed_clips.append(i)
                    else:
                        success_msg = f"片段 {i + 1}/{total_clips} 剪辑成功"
                        self.log(success_msg, self.LOG_INFO)
                        self.progress_updated.emit(progress, success_msg)

                # 如果有失败片段，尝试使用替代后端
                if failed_clips:
                    self.log(f"尝试修复 {len(failed_clips)} 个失败片段...", self.LOG_WARNING)
                    self.progress_updated.emit(80, f"尝试修复 {len(failed_clips)} 个失败片段...")
                    repair_futures = []
                    with ThreadPoolExecutor(max_workers=min(4, len(failed_clips))) as executor:
                        for i in failed_clips:
                            start, end = clip_ranges[i]
                            clip_path = os.path.join(temp_dir, f"clip_{i}_repaired.mp4")
                            self.temp_files[i] = clip_path  # 替换为修复后的路径

                            self.log(f"使用备选后端修复片段 {i + 1}: [{start:.2f}-{end:.2f}]秒", self.LOG_WARNING)

                            repair_futures.append(executor.submit(
                                self.cut_with_alternative_backend,
                                video_path,
                                start,
                                end,
                                clip_path,
                                ffmpeg_path
                            ))

                    # 检查修复结果
                    for idx, future in enumerate(repair_futures):
                        i = failed_clips[idx]
                        success, message = future.result()
                        progress = 80 + int((idx + 1) * 15 / len(failed_clips))

                        if not success:
                            error_msg = f"片段 {i + 1} 修复失败: {message}"
                            self.log(error_msg, self.LOG_ERROR)
                            self.error_occurred.emit(error_msg)
                            return False
                        else:
                            success_msg = f"片段 {i + 1} 修复成功: {message}"
                            self.log(success_msg, self.LOG_INFO)
                            self.progress_updated.emit(progress, success_msg)

                # 合并片段
                if self.is_cancelled:
                    self._cleanup_temp_files()
                    return False

                self.log("合并视频片段...", self.LOG_INFO)
                self.progress_updated.emit(95, "合并视频片段...")
                list_file = os.path.join(temp_dir, "clips.txt")
                self._create_clip_list(list_file)
                success, message = self.merge_clips(list_file, output_path, ffmpeg_path)

                if not success:
                    error_msg = f"合并失败: {message}"
                    self.log(error_msg, self.LOG_ERROR)
                    self.error_occurred.emit(error_msg)
                    self._cleanup_temp_files()
                    return False

                success_msg = f"剪辑完成! 输出文件: {output_path}"
                self.log(success_msg, self.LOG_INFO)
                self.progress_updated.emit(100, "剪辑完成!")
                self.finished.emit(True, success_msg)
                return True

        except Exception as e:
            error_msg = f"剪辑过程中发生错误: {str(e)}"
            self.log(error_msg, self.LOG_ERROR)
            self.error_occurred.emit(error_msg)
            logging.exception("视频剪辑错误")
            return False
        finally:
            self._cleanup_temp_files()
            # 清理预处理文件
            if preprocessed_path and preprocessed_path != video_path:
                try:
                    os.remove(preprocessed_path)
                    self.log(f"清理预处理文件: {os.path.basename(preprocessed_path)}", self.LOG_DEBUG)
                except Exception:
                    pass

    def get_video_duration(self, video_path, ffmpeg_path):
        """获取视频时长（秒）"""
        try:
            cmd = [
                ffmpeg_path,
                '-i', video_path
            ]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            output, _ = process.communicate()

            # 解析时长
            duration_match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", output)
            if duration_match:
                hours, minutes, seconds = map(float, duration_match.groups())
                return hours * 3600 + minutes * 60 + seconds
            return None
        except Exception as e:
            self.log(f"获取视频时长失败: {str(e)}", self.LOG_ERROR)
            return None

    def preprocess_video(self, video_path, ffmpeg_path):
        """预处理视频文件 - 修复已知问题"""
        try:
            # 确保GPU选项已初始化
            if not hasattr(self, 'gpu_options') or self.gpu_options is None:
                self.gpu_options = self.get_gpu_acceleration_options()
            # 检查视频是否需要修复
            if not self.detect_video_issues(video_path, ffmpeg_path):
                self.log("视频无需预处理", self.LOG_INFO)
                return video_path  # 不需要修复

            self.log("检测到视频问题，正在修复...", self.LOG_WARNING)
            self.progress_updated.emit(2, "检测到视频问题，正在修复...")

            # 获取视频时长用于进度计算
            duration = self.get_video_duration(video_path, ffmpeg_path)
            if duration is None or duration <= 0:
                duration = 0  # 防止除零错误
                self.log("无法获取视频时长，进度报告将受限", self.LOG_WARNING)

            # 创建临时修复文件
            temp_dir = tempfile.mkdtemp()
            temp_file = os.path.join(temp_dir, f"repaired_{os.path.basename(video_path)}")
            self.log(f"创建临时修复文件: {temp_file}", self.LOG_DEBUG)

            # 使用高级修复命令
            cmd = [
                ffmpeg_path,
                '-y',
                '-i', video_path,
                '-map', '0',  # 确保映射所有流
                '-fflags', '+genpts+discardcorrupt',  # 生成新时间戳并丢弃损坏数据
                '-err_detect', 'explode',  # 严格错误检测
                '-max_muxing_queue_size', '9999',
                '-movflags', '+faststart',  # 优化网络播放
                '-vsync', '0',  # 禁用时间戳同步
                '-progress', '-',  # 输出进度信息
            ]

            # 添加GPU加速选项
            if self.gpu_options:
                cmd.extend([
                    '-hwaccel', self.gpu_options['hwaccel'],
                    *self.gpu_options['extra'],
                    '-c:v', self.gpu_options['encoder'],
                ])
            else:
                cmd.extend([
                    '-c:v', 'libx264',  # 重新编码视频
                    '-preset', 'fast',
                    '-crf', '20',  # 高质量
                ])

            # 音频设置
            cmd.extend([
                '-c:a', 'aac',  # 重新编码音频
                '-b:a', '192k',
                temp_file
            ])

            # 记录命令
            self.log(f"执行视频修复命令: {' '.join(cmd)}", self.LOG_DEBUG)

            # 执行修复命令
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                # 添加 encoding 参数
                encoding='utf-8',
                errors='replace',  # 替换无法解码的字符
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            # 捕获输出并记录 - 使用非阻塞方式避免CPU 100%
            output_lines = []
            last_progress = 0
            last_log_time = 0
            log_buffer = []
            MAX_LOG_RATE = 0.05  # 最大日志发送频率(秒)

            while True:
                if self.is_cancelled:
                    self.log("预处理被取消", self.LOG_WARNING)
                    process.terminate()
                    break

                # 检查进程是否结束
                returncode = process.poll()
                if returncode is not None:
                    break

                # 非阻塞读取输出
                line = process.stdout.readline()
                if not line:
                    time.sleep(0.1)  # 避免忙等待
                    continue

                line = line.strip()
                output_lines.append(line)

                # 解析进度信息
                if "out_time=" in line:
                    try:
                        # 解析格式: out_time=00:01:23.456789
                        time_str = line.split('=')[1]
                        parts = time_str.split(':')
                        hours = int(parts[0])
                        minutes = int(parts[1])
                        seconds = float(parts[2])
                        current_time = hours * 3600 + minutes * 60 + seconds

                        # 计算进度百分比
                        if duration > 0:
                            progress = min(99, int(current_time / duration * 95))  # 预留5%给后续处理
                            if progress > last_progress:
                                self.progress_updated.emit(2 + progress, f"修复视频中... ({progress}%)")
                                last_progress = progress
                    except Exception as e:
                        pass  # 忽略进度解析错误

                # 日志处理 - 优化发送频率
                current_time = time.time()
                if "error" in line.lower():
                    log_buffer.append((f"[FFmpeg] {line}", self.LOG_ERROR))
                elif "warning" in line.lower():
                    log_buffer.append((f"[FFmpeg] {line}", self.LOG_WARNING))
                else:
                    # 普通日志只保留在缓冲区，不发送到UI
                    pass

                # 定期刷新日志缓冲区到UI
                if current_time - last_log_time > MAX_LOG_RATE and log_buffer:
                    for log_msg, log_type in log_buffer:
                        self.log(log_msg, log_type)
                    log_buffer = []
                    last_log_time = current_time

            # 刷新剩余的日志
            for log_msg, log_type in log_buffer:
                self.log(log_msg, log_type)

            # 等待进程完全结束
            if returncode is None:
                process.wait()
                returncode = process.returncode

            # 检查结果
            if returncode != 0:
                self.log(f"视频修复失败，返回码: {returncode}", self.LOG_ERROR)
                # 记录最后10行错误输出
                for line in output_lines[-10:]:
                    self.log(f"[FFmpeg] {line}", self.LOG_ERROR)
                return video_path  # 返回原始文件

            # 验证修复结果
            if os.path.exists(temp_file) and os.path.getsize(temp_file) > 1024:
                self.log("视频修复完成", self.LOG_INFO)
                self.progress_updated.emit(100, "视频修复完成")
                return temp_file

            return video_path
        except Exception as e:
            self.log(f"视频预处理失败: {str(e)}", self.LOG_ERROR)
            return video_path

    def detect_video_issues(self, video_path, ffmpeg_path):
        """检测视频是否存在已知问题"""
        try:
            self.log("检测视频问题...", self.LOG_DEBUG)
            # 使用FFmpeg分析视频
            cmd = [ffmpeg_path]

            # 添加GPU加速选项
            if self.gpu_options:
                cmd.extend([
                    '-hwaccel', self.gpu_options['hwaccel'],
                    *self.gpu_options['extra']
                ])

            cmd.extend([
                '-v', 'error',
                '-i', video_path,
                '-f', 'null', '-'
            ])

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # 添加 encoding 参数
                encoding='utf-8',
                errors='replace',  # 替换无法解码的字符
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            # 捕获错误输出
            output, _ = process.communicate()
            process.wait()

            # 检查是否存在关键错误
            critical_errors = [
                "Invalid NAL unit size",
                "missing picture in access unit",
                "error while decoding MB",
                "corrupt decoded frame"
            ]

            has_issues = False
            for error in critical_errors:
                if error in output:
                    self.log(f"检测到视频问题: {error}", self.LOG_WARNING)
                    has_issues = True

            return has_issues
        except Exception as e:
            self.log(f"视频问题检测失败: {str(e)}", self.LOG_ERROR)
            return False

    def cut_clip(self, video_path, start_time, end_time, output_path, ffmpeg_path):
        """使用FFmpeg切割视频片段 - 增强错误处理"""
        try:
            duration = end_time - start_time

            # 高级修复命令
            command = [
                ffmpeg_path,
                '-y',
                '-ss', str(start_time),
                '-i', video_path,
                '-t', str(duration),
                '-fflags', '+discardcorrupt+genpts',  # 丢弃损坏数据并生成新时间戳
                '-err_detect', 'ignore_err',  # 忽略错误
                '-max_muxing_queue_size', '9999',  # 防止缓冲区溢出
                '-bsf:v', 'h264_mp4toannexb',  # 转换为H.264 Annex B格式
                '-c', 'copy',  # 尝试复制流
                '-avoid_negative_ts', 'make_zero',  # 处理负时间戳
                output_path
            ]

            # 记录命令
            self.log(f"执行剪辑命令: {' '.join(command)}", self.LOG_DEBUG)

            # 处理Windows和Linux的差异
            creation_flags = 0
            if os.name == 'nt':
                creation_flags = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                # 添加 encoding 参数
                encoding='utf-8',
                errors='replace',  # 替换无法解码的字符
                universal_newlines=True,
                creationflags=creation_flags
            )

            # 实时处理输出以检测错误
            error_lines = []
            for line in process.stdout:
                line = line.strip()
                if "error" in line.lower() or "invalid" in line.lower():
                    self.log(f"[FFmpeg] {line}", self.LOG_ERROR)
                    error_lines.append(line)
                elif "warning" in line.lower():
                    self.log(f"[FFmpeg] {line}", self.LOG_WARNING)
                else:
                    self.log(f"[FFmpeg] {line}", self.LOG_DEBUG)

            # 等待进程完成
            process.wait()

            # 检查结果
            if process.returncode != 0:
                error_msg = "\n".join(error_lines[-5:]) or f"未知错误 (返回码: {process.returncode})"
                return False, error_msg

            # 验证输出文件
            if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
                return False, "输出文件大小异常"

            return True, "FFmpeg剪辑成功"
        except Exception as e:
            return False, str(e)

    def cut_with_alternative_backend(self, video_path, start_time, end_time, output_path, ffmpeg_path):
        """使用备选后端切割视频片段"""
        # 尝试OpenCV后端
        if OPENCV_AVAILABLE:
            self.log("切换到OpenCV后端", self.LOG_BACKEND)
            success, message = self.cut_with_opencv(video_path, start_time, end_time, output_path)
            if success:
                return True, message

        # 尝试MoviePy后端
        if MOVIEPY_AVAILABLE:
            self.log("切换到MoviePy后端", self.LOG_BACKEND)
            success, message = self.cut_with_moviepy(video_path, start_time, end_time, output_path)
            if success:
                return True, message

        # 尝试FFmpeg重编码
        self.log("切换到FFmpeg重编码模式", self.LOG_BACKEND)
        return self.reencode_clip(video_path, start_time, end_time, output_path, ffmpeg_path)

    def cut_with_opencv(self, video_path, start_time, end_time, output_path):
        """使用OpenCV切割视频片段 - 帧级精确处理"""
        try:
            self.log(f"使用OpenCV剪辑片段: {start_time:.2f}-{end_time:.2f}秒", self.LOG_INFO)

            # 打开视频
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return False, "OpenCV无法打开视频文件"

            # 获取视频属性
            fps = cap.get(cv2.CAP_PROP_FPS)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            if fps <= 0:
                return False, "无效的帧率"

            # 计算帧范围
            start_frame = int(start_time * fps)
            end_frame = int(end_time * fps)

            # 设置起始帧
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

            # 创建输出视频
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

            if not out.isOpened():
                return False, "无法创建输出文件"

            # 逐帧处理
            current_frame = start_frame
            error_count = 0
            last_valid_frame = None

            self.log(f"开始处理帧: {start_frame} 到 {end_frame} (共 {end_frame - start_frame} 帧)", self.LOG_INFO)

            while current_frame <= end_frame and current_frame < total_frames:
                if self.is_cancelled:
                    self.log("剪辑被取消", self.LOG_WARNING)
                    break

                ret, frame = cap.read()
                if not ret:
                    error_count += 1
                    self.log(f"帧 {current_frame} 读取失败 (连续错误 {error_count}/{self.frame_repair_threshold})",
                             self.LOG_ERROR)

                    # 如果连续错误超过阈值，放弃处理
                    if error_count > self.frame_repair_threshold:
                        return False, f"在帧 {current_frame} 处连续解码错误"

                    # 尝试使用上一有效帧
                    if last_valid_frame is not None:
                        frame = last_valid_frame.copy()
                        self.log(f"使用上一有效帧替代损坏帧 {current_frame}", self.LOG_WARNING)
                    else:
                        current_frame += 1
                        continue
                else:
                    last_valid_frame = frame.copy()
                    error_count = 0

                # 写入帧
                out.write(frame)
                current_frame += 1

                # 每50帧记录一次进度
                if current_frame % 50 == 0:
                    progress = (current_frame - start_frame) / (end_frame - start_frame) * 100
                    self.log(f"处理进度: {progress:.1f}% ({current_frame}/{end_frame} 帧)", self.LOG_INFO)

            # 释放资源
            cap.release()
            out.release()

            # 验证输出
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True, "OpenCV剪辑成功"
            return False, "OpenCV剪辑后文件为空"
        except Exception as e:
            return False, f"OpenCV错误: {str(e)}"

    def cut_with_moviepy(self, video_path, start_time, end_time, output_path):
        """使用MoviePy切割视频片段"""
        try:
            self.log(f"使用MoviePy剪辑片段: {start_time:.2f}-{end_time:.2f}秒", self.LOG_INFO)

            # 加载视频
            clip = VideoFileClip(video_path)

            # 切割片段
            subclip = clip.subclip(start_time, end_time)

            # 写入输出
            self.log("正在写入输出文件...", self.LOG_INFO)
            subclip.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                preset='fast',
                threads=4,
                logger=None  # 禁用详细日志
            )

            # 关闭剪辑
            clip.close()
            subclip.close()

            # 验证输出
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True, "MoviePy剪辑成功"
            return False, "MoviePy剪辑后文件为空"
        except Exception as e:
            return False, f"MoviePy错误: {str(e)}"

    def reencode_clip(self, video_path, start_time, end_time, output_path, ffmpeg_path):
        """完全重新编码视频片段"""
        try:
            duration = end_time - start_time

            command = [
                ffmpeg_path,
                '-y',
                '-ss', str(start_time),
                '-i', video_path,
                '-t', str(duration),
                '-fflags', '+discardcorrupt+genpts',  # 丢弃损坏数据并生成新时间戳
                '-err_detect', 'ignore_err',  # 忽略错误
                '-vsync', '0',  # 禁用时间戳同步
                '-map', '0',  # 确保映射所有流
                '-max_muxing_queue_size', '9999',
            ]

            # 添加GPU加速选项
            if self.gpu_options:
                command.extend([
                    '-hwaccel', self.gpu_options['hwaccel'],
                    *self.gpu_options['extra'],
                    '-c:v', self.gpu_options['encoder'],
                ])
            else:
                command.extend([
                    '-c:v', 'libx264',  # 重新编码视频
                    '-preset', 'fast',
                    '-crf', '20',  # 高质量
                ])

            # 音频设置
            command.extend([
                '-c:a', 'aac',  # 重新编码音频
                '-b:a', '192k',
                output_path
            ])

            # 记录命令
            self.log(f"执行重编码命令: {' '.join(command)}", self.LOG_DEBUG)

            # 执行命令
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # 添加 encoding 参数
                encoding='utf-8',
                errors='replace',  # 替换无法解码的字符
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            # 捕获输出并记录
            output_lines = []
            for line in process.stdout:
                line = line.strip()
                if "error" in line.lower():
                    self.log(f"[FFmpeg] {line}", self.LOG_ERROR)
                    output_lines.append(line)
                elif "warning" in line.lower():
                    self.log(f"[FFmpeg] {line}", self.LOG_WARNING)
                    output_lines.append(line)
                else:
                    self.log(f"[FFmpeg] {line}", self.LOG_DEBUG)

            # 等待完成
            process.communicate()

            if process.returncode != 0:
                error_msg = "\n".join(output_lines[-5:]) or f"重编码失败 (返回码: {process.returncode})"
                return False, error_msg

            # 验证输出
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True, "重编码成功"
            return False, "重编码后文件为空"
        except Exception as e:
            return False, str(e)

    def merge_clips(self, list_file, output_path, ffmpeg_path):
        """合并视频片段 - 增强错误处理"""
        try:
            command = [
                ffmpeg_path,
                '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', list_file,
                '-fflags', '+genpts',  # 重新生成时间戳
                '-c', 'copy',
                '-max_muxing_queue_size', '9999',  # 增加缓冲区大小
                output_path
            ]

            # 记录命令
            self.log(f"执行合并命令: {' '.join(command)}", self.LOG_DEBUG)

            # 处理Windows和Linux的差异
            creation_flags = 0
            if os.name == 'nt':
                creation_flags = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                # 添加 encoding 参数
                encoding='utf-8',
                errors='replace',  # 替换无法解码的字符
                universal_newlines=True,
                creationflags=creation_flags
            )

            # 捕获输出并记录
            output_lines = []
            for line in process.stdout:
                line = line.strip()
                if "error" in line.lower():
                    self.log(f"[FFmpeg] {line}", self.LOG_ERROR)
                    output_lines.append(line)
                elif "warning" in line.lower():
                    self.log(f"[FFmpeg] {line}", self.LOG_WARNING)
                    output_lines.append(line)
                else:
                    self.log(f"[FFmpeg] {line}", self.LOG_DEBUG)

            # 等待进程完成
            process.wait()

            if process.returncode != 0:
                error_msg = "\n".join(output_lines[-5:]) or f"合并失败 (返回码: {process.returncode})"
                return False, error_msg

            # 验证输出文件
            if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
                return False, "合并后的文件大小异常"

            return True, "FFmpeg合并成功"
        except Exception as e:
            return False, str(e)

    def _create_clip_list(self, list_file_path):
        """创建剪辑列表文件"""
        with open(list_file_path, 'w', encoding='utf-8') as f:
            for clip_path in self.temp_files:
                # 转义路径中的特殊字符
                clip_path = clip_path.replace("'", "'\\''")
                f.write(f"file '{clip_path}'\n")

    def _cleanup_temp_files(self):
        """清理临时文件"""
        for file_path in self.temp_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    self.log(f"清理临时文件: {os.path.basename(file_path)}", self.LOG_DEBUG)
            except Exception as e:
                logging.warning(f"无法删除临时文件 {file_path}: {str(e)}")
        self.temp_files = []

    def check_ffmpeg(self, ffmpeg_path="ffmpeg"):
        """检查FFmpeg是否可用"""
        try:
            result = subprocess.run(
                [ffmpeg_path, '-version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            return "ffmpeg version" in result.stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def cancel(self):
        """取消剪辑操作"""
        self.is_cancelled = True
        self.log("操作已取消", self.LOG_WARNING)
        self.progress_updated.emit(0, "操作已取消")


class ExportDialog(QDialog):
    def __init__(self, video_path, clip_data=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出剪辑")
        self.setMinimumSize(800, 700)  # 增大窗口尺寸以容纳日志显示

        # 设置窗口图标
        icon_path = "edit.png"
        if icon_path and os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # 主布局
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        self.setLayout(main_layout)

        # 创建分割器
        splitter = QSplitter(Qt.Vertical)

        # =================== 上半部分：控制面板 ===================
        control_group = QGroupBox("剪辑控制")
        control_layout = QVBoxLayout(control_group)
        control_layout.setSpacing(10)

        # 源视频信息
        source_group = QGroupBox("源视频信息")
        source_layout = QVBoxLayout(source_group)
        source_layout.setSpacing(10)

        # 视频文件信息
        video_info_layout = QHBoxLayout()
        video_info_layout.addWidget(QLabel("视频文件:"))
        self.video_label = QLabel("未打开视频文件" if not video_path else os.path.basename(video_path))
        self.video_label.setStyleSheet("font-weight: bold;")
        video_info_layout.addWidget(self.video_label, 1)
        source_layout.addLayout(video_info_layout)

        # 剪辑片段信息
        clips_info_layout = QHBoxLayout()
        clips_info_layout.addWidget(QLabel("剪辑片段:"))
        clip_ranges = clip_data.get("clip_ranges", []) if clip_data else []
        self.clips_label = QLabel("未加载剪辑数据" if not clip_ranges else f"{len(clip_ranges)} 个")
        self.clips_label.setStyleSheet("font-weight: bold;")
        clips_info_layout.addWidget(self.clips_label, 1)
        source_layout.addLayout(clips_info_layout)

        # 加载JSON文件按钮
        self.load_json_btn = QPushButton("加载剪辑数据(JSON)")
        self.load_json_btn.clicked.connect(self.load_json_file)
        source_layout.addWidget(self.load_json_btn)

        control_layout.addWidget(source_group)

        # 输出设置
        output_group = QGroupBox("输出设置")
        output_layout = QVBoxLayout(output_group)
        output_layout.setSpacing(10)

        # 输出路径设置
        output_path_layout = QHBoxLayout()
        output_path_layout.addWidget(QLabel("输出路径:"))
        self.output_path_edit = QLineEdit()
        self.output_path_edit.setReadOnly(True)
        output_path_layout.addWidget(self.output_path_edit, 1)
        self.browse_btn = QPushButton("浏览...")
        self.browse_btn.clicked.connect(self.select_output_path)
        output_path_layout.addWidget(self.browse_btn)
        output_layout.addLayout(output_path_layout)

        # FFmpeg路径设置
        ffmpeg_layout = QHBoxLayout()
        ffmpeg_layout.addWidget(QLabel("FFmpeg路径:"))
        self.ffmpeg_path_edit = QLineEdit("ffmpeg")
        ffmpeg_layout.addWidget(self.ffmpeg_path_edit, 1)
        self.check_ffmpeg_btn = QPushButton("检查")
        self.check_ffmpeg_btn.clicked.connect(self.check_ffmpeg)
        ffmpeg_layout.addWidget(self.check_ffmpeg_btn)
        self.ffmpeg_status = QLabel("未检查")
        self.ffmpeg_status.setStyleSheet("font-weight: bold;")
        ffmpeg_layout.addWidget(self.ffmpeg_status)
        output_layout.addLayout(ffmpeg_layout)

        control_layout.addWidget(output_group)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        control_layout.addWidget(self.progress_bar)

        # 状态标签
        self.status_label = QLabel("准备导出...")
        self.status_label.setStyleSheet("font-weight: bold;")
        control_layout.addWidget(self.status_label)

        # 按钮
        button_layout = QHBoxLayout()
        self.export_btn = QPushButton("开始导出")
        self.export_btn.setStyleSheet("font-weight: bold;")
        self.export_btn.clicked.connect(self.start_export)
        button_layout.addWidget(self.export_btn)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.cancel_export)
        button_layout.addWidget(self.cancel_btn)
        control_layout.addLayout(button_layout)

        # =================== 下半部分：日志显示 ===================
        log_group = QGroupBox("剪辑日志")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #F8F8F8;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 10pt;
            }
        """)

        # 设置日志格式
        self.log_format = {
            "info": QTextCharFormat(),
            "warning": QTextCharFormat(),
            "error": QTextCharFormat(),
            "debug": QTextCharFormat(),
            "backend": QTextCharFormat()
        }

        # 信息 - 黑色
        self.log_format["info"].setForeground(QColor("#000000"))

        # 警告 - 橙色
        warn_format = self.log_format["warning"]
        warn_format.setForeground(QColor("#FF8C00"))  # 深橙色
        warn_format.setFontWeight(QFont.Bold)

        # 错误 - 红色
        error_format = self.log_format["error"]
        error_format.setForeground(QColor("#FF0000"))  # 红色
        error_format.setFontWeight(QFont.Bold)

        # 调试 - 灰色
        debug_format = self.log_format["debug"]
        debug_format.setForeground(QColor("#808080"))  # 灰色

        # 后端 - 蓝色
        backend_format = self.log_format["backend"]
        backend_format.setForeground(QColor("#0000FF"))  # 蓝色
        backend_format.setFontWeight(QFont.Bold)

        log_layout.addWidget(self.log_text)

        # 添加控件到分割器
        splitter.addWidget(control_group)
        splitter.addWidget(log_group)
        splitter.setSizes([300, 400])  # 控制面板30%，日志显示70%

        main_layout.addWidget(splitter)

        # 初始化日志缓冲 - 必须先于任何可能调用append_log的操作
        self.log_buffer = []
        self.log_timer = QTimer()
        self.log_timer.timeout.connect(self.flush_log_buffer)
        self.log_timer.start(100)  # 每100毫秒刷新一次日志

        # 初始化变量
        self.video_path = video_path
        self.clip_data = clip_data or {}
        self.clip_ranges = clip_data.get("clip_ranges", []) if clip_data else []
        self.output_path = ""
        self.json_file_path = ""

        self.editor = VideoEditor()

        # 如果提供了视频路径，自动设置默认输出路径
        if self.video_path:
            self.set_default_output_path()

        # 自动检测并加载剪辑数据文件
        self.auto_detect_clip_data()

    def flush_log_buffer(self):
        """刷新日志缓冲区"""
        if not self.log_buffer:
            return

        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)

        for message, log_type in self.log_buffer:
            cursor.insertText(f"{message}\n", self.log_format.get(log_type, self.log_format["info"]))

        self.log_buffer = []
        self.log_text.ensureCursorVisible()

    def append_log(self, message, log_type):
        """添加日志到日志框，使用不同的格式"""
        # 使用缓冲机制减少UI更新频率
        self.log_buffer.append((message, log_type))

    def auto_detect_clip_data(self):
        """自动检测并加载同级目录下的剪辑数据文件"""
        if not self.video_path:
            return

        try:
            # 构建预期的剪辑数据文件路径
            video_dir = os.path.dirname(self.video_path)
            video_name = os.path.splitext(os.path.basename(self.video_path))[0]
            auto_json_path = os.path.join(video_dir, f"{video_name}_clips.json")

            # 检查文件是否存在
            if os.path.exists(auto_json_path):
                self.load_clip_data(auto_json_path, auto_detected=True)
                self.append_log(f"自动加载剪辑数据文件: {auto_json_path}", "info")
        except Exception as e:
            self.append_log(f"自动加载剪辑数据失败: {str(e)}", "error")

    def set_default_output_path(self):
        """设置默认输出路径（与原视频同一目录）"""
        if not self.video_path:
            return

        video_dir = os.path.dirname(self.video_path)
        video_name = os.path.splitext(os.path.basename(self.video_path))[0]
        default_filename = f"{video_name}_highlights.mp4"
        default_path = os.path.join(video_dir, default_filename)

        self.output_path = default_path
        self.output_path_edit.setText(default_path)

    def load_json_file(self):
        """手动加载剪辑数据JSON文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择剪辑数据文件", "", "JSON文件 (*.json)",
        )

        if not file_path:
            return

        self.load_clip_data(file_path)

    def load_clip_data(self, file_path, auto_detected=False):
        """加载剪辑数据文件（内部方法）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                clip_data = json.load(f)

            # 更新视频路径和剪辑范围
            self.json_file_path = file_path
            self.video_path = clip_data.get("video_path", self.video_path)  # 保留原路径如果新路径无效
            self.clip_ranges = clip_data.get("clip_ranges", [])

            # 更新UI显示
            if self.video_path:
                self.video_label.setText(os.path.basename(self.video_path))
                self.set_default_output_path()
            else:
                self.video_label.setText("未找到视频路径")

            self.clips_label.setText(f"{len(self.clip_ranges)} 个")

            if not auto_detected:
                QMessageBox.information(self, "成功", f"已加载 {len(self.clip_ranges)} 个剪辑片段")
                self.append_log(f"已加载剪辑数据: {len(self.clip_ranges)} 个片段", "info")
            else:
                self.status_label.setText(f"已自动加载 {len(self.clip_ranges)} 个剪辑片段")
                self.append_log(f"自动加载剪辑数据: {len(self.clip_ranges)} 个片段", "info")

        except Exception as e:
            if not auto_detected:
                QMessageBox.warning(self, "错误", f"加载JSON文件失败: {str(e)}")
                self.append_log(f"加载JSON文件失败: {str(e)}", "error")
            else:
                self.append_log(f"自动加载剪辑数据失败: {str(e)}", "error")

    def select_output_path(self):
        """选择输出路径"""
        if self.video_path:
            video_name = os.path.splitext(os.path.basename(self.video_path))[0]
            default_file = f"{video_name}_highlights.mp4"
            default_dir = os.path.dirname(self.video_path)
        else:
            default_file = "highlights.mp4"
            default_dir = ""

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存剪辑视频", os.path.join(default_dir, default_file), "MP4视频 (*.mp4)"
        )

        if file_path:
            if not file_path.lower().endswith('.mp4'):
                file_path += '.mp4'

            self.output_path = file_path
            self.output_path_edit.setText(file_path)
            self.append_log(f"设置输出路径: {file_path}", "info")

    def check_ffmpeg(self):
        """检查FFmpeg是否可用"""
        ffmpeg_path = self.ffmpeg_path_edit.text().strip()
        if not ffmpeg_path:
            self.ffmpeg_status.setText("请输入FFmpeg路径")
            self.append_log("未输入FFmpeg路径", "warning")
            return False

        if self.editor.check_ffmpeg(ffmpeg_path):
            self.ffmpeg_status.setText("FFmpeg可用")
            self.ffmpeg_status.setStyleSheet("color: green;")
            self.append_log("FFmpeg检查通过", "info")
            return True
        else:
            self.ffmpeg_status.setText("未找到FFmpeg")
            self.ffmpeg_status.setStyleSheet("color: red;")
            self.append_log("FFmpeg不可用，请检查路径", "error")
            return False

    def start_export(self):
        """开始导出视频"""
        if not self.output_path:
            QMessageBox.warning(self, "错误", "请选择输出路径")
            self.append_log("错误: 未选择输出路径", "error")
            return

        if not self.check_ffmpeg():
            QMessageBox.warning(self, "错误", "FFmpeg不可用，请检查路径")
            self.append_log("错误: FFmpeg不可用", "error")
            return

        if not self.clip_ranges:
            QMessageBox.warning(self, "错误", "没有可导出的剪辑片段")
            self.append_log("错误: 没有可导出的剪辑片段", "error")
            return

        # 禁用按钮
        self.export_btn.setEnabled(False)
        self.browse_btn.setEnabled(False)
        self.check_ffmpeg_btn.setEnabled(False)
        self.load_json_btn.setEnabled(False)
        self.status_label.setText("正在导出...")
        self.append_log("开始导出视频...", "info")

        # 创建并启动处理线程
        ffmpeg_path = self.ffmpeg_path_edit.text().strip()
        self.processing_thread = VideoProcessingThread(
            self.editor,
            self.video_path,
            self.clip_ranges,
            self.output_path,
            ffmpeg_path
        )

        # 连接线程信号
        self.processing_thread.finished.connect(self.export_finished)
        self.processing_thread.progress.connect(self.update_progress)
        self.processing_thread.log.connect(self.append_log)

        # 启动线程
        self.processing_thread.start()

    def update_progress(self, progress, message):
        """更新进度"""
        self.progress_bar.setValue(progress)
        self.status_label.setText(message)
        self.append_log(f"进度: {progress}% - {message}", "info")

    def export_finished(self, success, message):
        """导出完成处理"""
        # 重新启用按钮
        self.export_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self.check_ffmpeg_btn.setEnabled(True)
        self.load_json_btn.setEnabled(True)

        if success:
            QMessageBox.information(self, "完成", message)
            self.append_log(f"导出成功: {message}", "info")
        else:
            QMessageBox.warning(self, "警告", message)
            self.append_log(f"导出失败: {message}", "error")

    def cancel_export(self):
        """取消导出操作"""
        if hasattr(self, 'processing_thread') and self.processing_thread.isRunning():
            self.processing_thread.cancel()
            self.append_log("导出操作已取消", "warning")
            self.status_label.setText("操作已取消")

        # 重新启用按钮
        self.export_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self.check_ffmpeg_btn.setEnabled(True)
        self.load_json_btn.setEnabled(True)


class VideoProcessingThread(QThread):
    finished = Signal(bool, str)
    progress = Signal(int, str)
    log = Signal(str, str)

    def __init__(self, editor, video_path, clip_ranges, output_path, ffmpeg_path):
        super().__init__()
        self.editor = editor
        self.video_path = video_path
        self.clip_ranges = clip_ranges
        self.output_path = output_path
        self.ffmpeg_path = ffmpeg_path

    def run(self):
        try:
            # 连接编辑器的信号
            self.editor.progress_updated.connect(self.handle_progress)
            self.editor.log_message.connect(self.handle_log)

            # 执行视频编辑
            success = self.editor.combine_clips(
                self.video_path,
                self.clip_ranges,
                self.output_path,
                self.ffmpeg_path
            )

            self.finished.emit(success, "操作完成" if success else "操作失败")
        except Exception as e:
            self.log.emit(f"线程错误: {str(e)}", "error")
            self.finished.emit(False, f"线程错误: {str(e)}")

    def handle_progress(self, progress, message):
        self.progress.emit(progress, message)

    def handle_log(self, message, log_type):
        self.log.emit(message, log_type)

    def cancel(self):
        """取消处理"""
        self.editor.cancel()
        if self.isRunning():
            self.terminate()