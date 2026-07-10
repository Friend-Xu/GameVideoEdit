"""视频播放引擎 —— 纯逻辑，不含UI。

使用 decord 作为主引擎(O(1)随机访问)，OpenCV 作为后备。
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

try:
    import decord as _decord
    _HAS_DECORD = True
except ImportError:
    _HAS_DECORD = False


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    total_frames: int
    rotation: int = 0


class VideoPlayer:
    """视频播放引擎"""

    def __init__(self):
        self._cap: cv2.VideoCapture | None = None
        self._decord_reader = None
        self._info: VideoInfo | None = None
        self._current_frame: int = 0
        self._use_decord: bool = False
        self._is_open: bool = False

    def open(self, video_path: str) -> VideoInfo:
        self.close()
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        if _HAS_DECORD:
            try:
                reader = _decord.VideoReader(str(path))
                self._decord_reader = reader
                self._use_decord = True
                self._info = VideoInfo(
                    path=str(path),
                    width=int(reader[0].shape[1]),
                    height=int(reader[0].shape[0]),
                    fps=float(reader.get_avg_fps()),
                    total_frames=len(reader),
                )
                self._is_open = True
                self._current_frame = 0
                return self._info
            except Exception:
                self._decord_reader = None
                self._use_decord = False

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频: {video_path}")

        self._cap = cap
        self._info = VideoInfo(
            path=str(path),
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(cap.get(cv2.CAP_PROP_FPS)),
            total_frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        )
        self._is_open = True
        self._current_frame = 0
        return self._info

    def seek(self, frame: int) -> np.ndarray:
        if not self._is_open:
            raise RuntimeError("未打开视频")
        frame = max(0, min(frame, self._info.total_frames - 1))

        if self._use_decord:
            img = self._decord_reader[frame].asnumpy()
            self._current_frame = frame
            return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
        ret, img = self._cap.read()
        if not ret:
            raise RuntimeError(f"跳转到帧 {frame} 失败")
        self._current_frame = frame
        return img

    def seek_rgb(self, frame: int) -> np.ndarray:
        """跳转到指定帧，返回 RGB 图像（播放专用，免去 BGR 转换）"""
        if not self._is_open:
            raise RuntimeError("未打开视频")
        frame = max(0, min(frame, self._info.total_frames - 1))

        if self._use_decord:
            img = self._decord_reader[frame].asnumpy()
            self._current_frame = frame
            return img  # decord 原生返回 RGB

        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
        ret, img = self._cap.read()
        if not ret:
            raise RuntimeError(f"跳转到帧 {frame} 失败")
        self._current_frame = frame
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    def seek_time(self, seconds: float) -> np.ndarray:
        if not self._is_open:
            raise RuntimeError("未打开视频")
        frame = int(seconds * self._info.fps)
        return self.seek(frame)

    def next_frame(self) -> np.ndarray | None:
        if not self._is_open:
            raise RuntimeError("未打开视频")
        nf = self._current_frame + 1
        if nf >= self._info.total_frames:
            return None
        if self._use_decord:
            return self.seek(nf)
        ret, img = self._cap.read()
        if not ret:
            return None
        self._current_frame = nf
        return img

    def next_frame_rgb(self) -> np.ndarray | None:
        """下一帧 RGB（播放专用）"""
        if not self._is_open:
            raise RuntimeError("未打开视频")
        nf = self._current_frame + 1
        if nf >= self._info.total_frames:
            return None
        if self._use_decord:
            return self.seek_rgb(nf)
        ret, img = self._cap.read()
        if not ret:
            return None
        self._current_frame = nf
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    def close(self):
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._decord_reader = None
        self._info = None
        self._is_open = False
        self._current_frame = 0

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def video_info(self) -> VideoInfo | None:
        return self._info

    @property
    def current_frame(self) -> int:
        return self._current_frame

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
