"""OCR 工作线程 —— 后台多线程 OCR 处理。"""

import threading

from PySide6.QtCore import QThread, Signal

from app.core.annotator import AnnotationStore
from app.core.detector import DetectionEngine, FrameResult, OCRDetector
from app.core.keywords import KeywordMatcher
from app.core.player import VideoPlayer


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
                 padding_before: float = 10.0, padding_after: float = 10.0):
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
        self._cancel_event = threading.Event()

    def run(self):
        try:
            detector = OCRDetector(gpu=self._gpu)
            engine = DetectionEngine(
                self._matcher, detector,
                padding_before=self._padding_before,
                padding_after=self._padding_after,
                skip_frames=self._skip_frames,
            )
            player = VideoPlayer()
            info = player.open(self._video_path)
            fps, total = info.fps, info.total_frames
            end = self._end_frame or (total - 1)
            rois = self._annotation.to_pixel_rois(info.width, info.height)

            if not rois:
                self.finished.emit(self.thread_id, [])
                player.close()
                return

            detections: list[FrameResult] = []
            fn = self._start_frame

            while fn <= end and not self._cancel_event.is_set():
                try:
                    frame = player.seek(fn)
                except Exception:
                    fn += 1; continue

                result = engine.process_frame(frame, rois, fps, fn)

                if result.detected:
                    detections.append(result)
                    text = result.match_result.raw_text if result.match_result else ""
                    self.detected.emit(result.timestamp, text)
                    self.log.emit(
                        f"线程{self.thread_id}: [{result.timestamp:.1f}s] {text}", LOG_INFO)
                    fn += max(1, int(fps * 0.3))
                else:
                    fn += self._skip_frames

                pct = (fn - self._start_frame) / (end - self._start_frame) * 100
                self.progress.emit(self.thread_id, min(pct, 100.0))

            player.close()
            time_ranges = engine._merge_detections(detections)
            self.finished.emit(self.thread_id,
                               [(r.start_sec, r.end_sec) for r in time_ranges])
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit(self.thread_id, [])

    def cancel(self):
        self._cancel_event.set()
