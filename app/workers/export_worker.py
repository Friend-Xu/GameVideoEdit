"""导出工作线程 —— 后台视频导出。"""

import threading

from PySide6.QtCore import QThread, Signal

from app.core.detector import TimeRange
from app.core.exporter import ExportConfig, VideoExporter


class ExportWorker(QThread):
    """视频导出工作线程"""

    progress = Signal(int, str)
    log = Signal(str, str)
    finished = Signal(bool, str)
    error = Signal(str)

    def __init__(self, video_path: str, clip_ranges: list[TimeRange],
                 config: ExportConfig):
        super().__init__()
        self._video_path = video_path
        self._clip_ranges = clip_ranges
        self._config = config
        self._cancel_event = threading.Event()
        self._exporter = VideoExporter()

    def run(self):
        try:
            def on_progress(pct: int, msg: str):
                if not self._cancel_event.is_set():
                    self.progress.emit(pct, msg)

            result = self._exporter.combine_clips(
                self._video_path, self._clip_ranges, self._config,
                progress_callback=on_progress,
                cancel_check=self._cancel_event.is_set,
            )
            self.finished.emit(result.success, result.message)
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit(False, str(e))

    def cancel(self):
        self._cancel_event.set()
