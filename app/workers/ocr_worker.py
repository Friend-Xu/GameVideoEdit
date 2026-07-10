"""OCR 工作线程 —— 后台多线程 OCR 处理。"""

import threading

from PySide6.QtCore import QThread, Signal

from app.core.annotator import AnnotationStore
from app.core.detector import DetectionEngine, OCRDetector
from app.core.keywords import KeywordMatcher


LOG_INFO = 0; LOG_WARNING = 1; LOG_ERROR = 2
LOG_RECOVERY = 3; LOG_CHECK = 4; LOG_ANALYSIS = 5


class OCRWorker(QThread):
    """单个 OCR 处理线程"""

    progress = Signal(int, float)
    detected = Signal(float, str)
    log = Signal(str, int)
    finished = Signal(int, list)
    error = Signal(str)

    def __init__(self, thread_id: int, video_path: str,
                 annotation: AnnotationStore, matcher: KeywordMatcher,
                 gpu: bool = True, skip_frames: int = 3,
                 start_frame: int = 0, end_frame: int | None = None,
                 padding_before: float = 10.0, padding_after: float = 10.0,
                 mode: str = "frame", interval_sec: float = 1.0,
                 post_detect_skip_sec: float = 0.3,
                 allowed_actors: set | None = None):
        super().__init__()
        self.thread_id = thread_id
        self._video_path = video_path
        self._annotation = annotation
        self._matcher = matcher
        self._gpu = gpu
        self._skip_frames = skip_frames
        self._start_frame = start_frame
        self._end_frame = end_frame
        self._padding_before = padding_before
        self._padding_after = padding_after
        self._mode = mode
        self._interval_sec = interval_sec
        self._post_detect_skip_sec = post_detect_skip_sec
        self._allowed_actors = allowed_actors
        self._cancel_event = threading.Event()

    def run(self):
        try:
            detector = OCRDetector(gpu=self._gpu)
            engine = DetectionEngine(
                self._matcher, detector,
                padding_before=self._padding_before,
                padding_after=self._padding_after,
                skip_frames=self._skip_frames,
                mode=self._mode,
                interval_sec=self._interval_sec,
                post_detect_skip_sec=self._post_detect_skip_sec,
                allowed_actors=self._allowed_actors,
            )
            tid = self.thread_id

            def on_progress(pct: float):
                self.progress.emit(tid, pct)

            def on_detected(timestamp: float, text: str):
                self.detected.emit(timestamp, text)
                self.log.emit(
                    f"线程{tid}: [{timestamp:.1f}s] {text}", LOG_INFO)

            time_ranges = engine.run_full(
                video_path=self._video_path,
                annotations=self._annotation,
                start_frame=self._start_frame,
                end_frame=self._end_frame,
                progress_cb=on_progress,
                detected_cb=on_detected,
                cancel_check=self._cancel_event.is_set,
            )
            self.finished.emit(tid, [
                (r.start_sec, r.end_sec, r.action, r.actor, r.pattern_id)
                for r in time_ranges
            ])
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit(self.thread_id, [])

    def cancel(self):
        self._cancel_event.set()
