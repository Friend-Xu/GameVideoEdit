"""视频导出引擎 —— 纯逻辑。

FFmpeg 切割/合并，GPU 加速 (NVIDIA/AMD/Intel)。
"""

import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.core.detector import TimeRange

_log = logging.getLogger("app.core.exporter")


@dataclass
class GPUPreference:
    encoder: str = "libx264"
    available: bool = False
    vendor: str = "cpu"
    extra_args: list[str] = field(default_factory=list)


@dataclass
class ExportConfig:
    output_path: str = ""
    ffmpeg_path: str = "ffmpeg"
    quality: int = 23
    preset: str = "fast"
    use_gpu: bool = True


@dataclass
class ExportResult:
    success: bool
    message: str
    output_path: str = ""
    elapsed_seconds: float = 0.0


class VideoExporter:
    """视频导出引擎"""

    _NVENC_PRESETS = {"fast": "p2", "medium": "p4", "slow": "p7"}
    _QSV_PRESETS = {"fast": "faster", "medium": "medium", "slow": "slower"}
    _AMF_PRESETS = {"fast": "speed", "medium": "balanced", "slow": "quality"}

    def __init__(self):
        self._gpu: GPUPreference | None = None
        self._temp_files: list[Path] = []
        self._temp_dir: str = ""

    @staticmethod
    def detect_gpu() -> GPUPreference:
        if VideoExporter._check_cmd("nvidia-smi"):
            return GPUPreference(encoder="h264_nvenc", available=True, vendor="nvidia")
        if VideoExporter._check_qsv():
            return GPUPreference(encoder="h264_qsv", available=True, vendor="intel")
        if VideoExporter._check_amf():
            return GPUPreference(encoder="h264_amf", available=True, vendor="amd")
        return GPUPreference()

    @staticmethod
    def _check_cmd(cmd: str) -> bool:
        try:
            subprocess.run([cmd], capture_output=True, timeout=5)
            return True
        except Exception:
            return False

    @staticmethod
    def _check_qsv() -> bool:
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                               capture_output=True, text=True, timeout=10)
            return "h264_qsv" in r.stdout
        except Exception:
            return False

    @staticmethod
    def _check_amf() -> bool:
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                               capture_output=True, text=True, timeout=10)
            return "h264_amf" in r.stdout
        except Exception:
            return False

    def combine_clips(
        self, video_path: str, clip_ranges: list[TimeRange],
        config: ExportConfig,
        progress_callback: Callable[[int, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ExportResult:
        if not clip_ranges:
            return ExportResult(success=False, message="没有可导出的片段")
        if not self._has_ffmpeg(config.ffmpeg_path):
            return ExportResult(success=False, message="FFmpeg 不可用")
        if self._gpu is None:
            self._gpu = self.detect_gpu() if config.use_gpu else GPUPreference()

        start_t = time.time()
        try:
            self._temp_dir = tempfile.mkdtemp()
            self._temp_files = []

            if progress_callback:
                progress_callback(5, f"切割 {len(clip_ranges)} 个片段...")

            clip_paths: list[Path] = []
            total = len(clip_ranges)
            for i, tr in enumerate(clip_ranges):
                if cancel_check and cancel_check():
                    return ExportResult(success=False, message="已取消")
                cp = Path(self._temp_dir) / f"clip_{i:03d}.mp4"
                _log.debug("切割片段 %d/%d: [%.1f-%.1f] dur=%.1f", i+1, total,
                           tr.start_sec, tr.end_sec, tr.end_sec - tr.start_sec)
                ok, err = self._cut_reencode(video_path, tr, cp, config)
                if not ok:
                    _log.warning("片段 %d reencode 失败: %s, 尝试 stream copy", i+1, err[:100])
                    ok, err = self._cut_clip(video_path, tr, cp, config)
                if ok:
                    clip_paths.append(cp)
                    self._temp_files.append(cp)
                    _log.debug("片段 %d 切割成功", i+1)
                else:
                    _log.error("片段 %d 切割完全失败 (跳过): %s", i+1, err[:100])
                if progress_callback:
                    progress_callback(5 + int((i + 1) / total * 75), f"片段 {i + 1}/{total}")

            if not clip_paths:
                return ExportResult(success=False, message="所有片段切割失败")
            _log.info("切割完成: %d/%d 个片段成功", len(clip_paths), total)

            if progress_callback:
                progress_callback(85, "合并片段...")

            list_file = Path(self._temp_dir) / "list.txt"
            with open(list_file, "w", encoding="utf-8") as f:
                for cp in clip_paths:
                    f.write(f"file '{str(cp).replace(chr(92), '/')}'\n")

            ok, msg = self._merge(list_file, Path(config.output_path), config)
            if not ok:
                return ExportResult(success=False, message=f"合并失败: {msg}")

            elapsed = time.time() - start_t
            if progress_callback:
                progress_callback(100, "完成!")
            return ExportResult(success=True, message="导出成功",
                                output_path=config.output_path, elapsed_seconds=round(elapsed, 1))
        except Exception as e:
            return ExportResult(success=False, message=str(e))
        finally:
            self._cleanup()

    def _cut_clip(self, video: str, tr: TimeRange, out: Path, cfg: ExportConfig) -> tuple[bool, str]:
        dur = tr.end_sec - tr.start_sec
        cmd = [cfg.ffmpeg_path, "-y", "-ss", str(tr.start_sec), "-i", video,
               "-t", str(dur), "-c", "copy", "-avoid_negative_ts", "make_zero",
               "-max_muxing_queue_size", "9999", str(out)]
        return self._run(cmd)

    def _cut_reencode(self, video: str, tr: TimeRange, out: Path, cfg: ExportConfig) -> tuple[bool, str]:
        dur = tr.end_sec - tr.start_sec
        cmd = [cfg.ffmpeg_path, "-y", "-ss", str(tr.start_sec), "-i", video, "-t", str(dur)]
        self._add_gpu_args(cmd, cfg)
        cmd += ["-c:a", "aac", "-b:a", "192k", str(out)]
        return self._run(cmd)

    def _merge(self, list_file: Path, out: Path, cfg: ExportConfig) -> tuple[bool, str]:
        cmd = [cfg.ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
               "-i", str(list_file), "-c", "copy",
               "-max_muxing_queue_size", "9999", str(out)]
        return self._run(cmd)

    def _add_gpu_args(self, cmd: list[str], cfg: ExportConfig) -> None:
        g = self._gpu
        if not g or not g.available:
            cmd += ["-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.quality)]
            return
        cmd += ["-c:v", g.encoder]
        if g.vendor == "nvidia":
            cmd += ["-preset", self._NVENC_PRESETS.get(cfg.preset, "p4"), "-cq", str(cfg.quality)]
        elif g.vendor == "intel":
            cmd += ["-preset", self._QSV_PRESETS.get(cfg.preset, "medium"),
                    "-global_quality", str(cfg.quality)]
        elif g.vendor == "amd":
            cmd += ["-preset", self._AMF_PRESETS.get(cfg.preset, "balanced"),
                    "-qp_i", str(cfg.quality), "-qp_p", str(cfg.quality)]

    def _run(self, cmd: list[str], timeout: int = 300) -> tuple[bool, str]:
        try:
            kw = {"capture_output": True, "text": True, "timeout": timeout}
            if os.name == "nt":
                kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            p = subprocess.run(cmd, **kw)
            if p.returncode != 0:
                return False, (p.stderr or p.stdout or "")[-300:]
            return True, ""
        except subprocess.TimeoutExpired:
            return False, "超时"
        except FileNotFoundError:
            return False, "FFmpeg未安装"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _has_ffmpeg(path: str) -> bool:
        try:
            subprocess.run([path, "-version"], capture_output=True, timeout=5, check=True)
            return True
        except Exception:
            return False

    def _cleanup(self):
        for fp in self._temp_files:
            try:
                if fp.exists(): fp.unlink()
            except OSError: pass
        self._temp_files.clear()
        if self._temp_dir and os.path.isdir(self._temp_dir):
            try:
                for f in os.listdir(self._temp_dir):
                    os.remove(os.path.join(self._temp_dir, f))
                os.rmdir(self._temp_dir)
            except OSError: pass
        self._temp_dir = ""

    @property
    def gpu_preference(self) -> GPUPreference:
        if self._gpu is None:
            self._gpu = self.detect_gpu()
        return self._gpu
