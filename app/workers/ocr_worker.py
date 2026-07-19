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
    raw_text = Signal(float, str, str)  # timestamp, text, roi_label
    log = Signal(str, int)
    finished = Signal(int, list, object)
    error = Signal(str)

    def __init__(self, thread_id: int, video_path: str,
                 annotation: AnnotationStore, matcher: KeywordMatcher,
                 gpu: bool = True, skip_frames: int = 3,
                 start_frame: int = 0, end_frame: int | None = None,
                 padding_before: float = 10.0, padding_after: float = 10.0,
                 mode: str = "frame", interval_sec: float = 1.0,
                 post_detect_skip_sec: float = 0.3,
                 allowed_actors: set | None = None,
                 ocr_engine: str = "rapidocr"):
        super().__init__()
        self.thread_id = thread_id
        self._video_path = video_path
        self._annotation = annotation
        self._matcher = matcher
        self._gpu = gpu
        self._ocr_engine = ocr_engine
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
            detector = OCRDetector(gpu=self._gpu, engine=self._ocr_engine)
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

            def on_raw_ocr(timestamp: float, text: str, label: str):
                self.raw_text.emit(timestamp, text, label)

            time_ranges, report = engine.run_full(
                video_path=self._video_path,
                annotations=self._annotation,
                start_frame=self._start_frame,
                end_frame=self._end_frame,
                progress_cb=on_progress,
                detected_cb=on_detected,
                raw_ocr_cb=on_raw_ocr,
                cancel_check=self._cancel_event.is_set,
            )
            results = [(r.start_sec, r.end_sec, r.action, r.actor, r.pattern_id, r.source,
                        r.raw_start_sec, r.raw_end_sec,
                        r.raw_text, r.confidence, r.match_strategy)
                       for r in time_ranges]
            self.finished.emit(tid, results, report)
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit(self.thread_id, [], None)

    def cancel(self):
        self._cancel_event.set()

class PoolOCRWorker(QThread):
    """Pool 模式 OCR 线程: 使用 DetectionPipeline (CPU/GPU 解耦) 。"""

    progress = Signal(int, float)
    detected = Signal(float, str)
    raw_text = Signal(float, str, str)
    log = Signal(str, int)
    finished = Signal(int, list, object)
    error = Signal(str)

    def __init__(self, thread_id: int, video_path: str,
                 annotation, matcher,
                 gpu: bool = True,
                 start_frame: int = 0, end_frame: int | None = None,
                 padding_before: float = 10.0, padding_after: float = 10.0,
                 mode: str = "frame", interval_sec: float = 1.0,
                 post_detect_skip_sec: float = 0.3,
                 allowed_actors: set | None = None,
                 cpu_workers: int = 3, gpu_workers: int = 2,
                 skip_frames: int = 60,
                 gate_mode: str = "pixel"):
        super().__init__()
        self.thread_id = thread_id
        self._video_path = video_path
        self._annotation = annotation
        self._matcher = matcher
        self._gpu = gpu
        self._start_frame = start_frame
        self._end_frame = end_frame
        self._padding_before = padding_before
        self._padding_after = padding_after
        self._mode = mode
        self._interval_sec = interval_sec
        self._post_detect_skip_sec = post_detect_skip_sec
        self._allowed_actors = allowed_actors
        self._cpu_workers = cpu_workers
        self._gpu_workers = gpu_workers
        self._skip_frames = skip_frames
        self._gate_mode = gate_mode
        self._cancel_event = threading.Event()

    def run(self):
        try:
            from app.core.detector import OCRDetector, DetectionPipeline
            from app.core.player import VideoPlayer
            import queue as qmod

            player = VideoPlayer()
            info = player.open(self._video_path)
            total_frames = info.total_frames
            player.close()

            end = self._end_frame or (total_frames - 1)
            segments = max(1, getattr(self, '_segments', 1))
            frames_per = (end - self._start_frame + 1) // segments

            all_results = []
            all_reports = []
            rq = qmod.Queue()
            threads = []

            def run_segment(sid, sf, ef):
                try:
                    det = OCRDetector(gpu=self._gpu)
                    pl = DetectionPipeline(
                        self._matcher, det,
                        cpu_workers=self._cpu_workers,
                        gpu_workers=self._gpu_workers,
                        padding_before=self._padding_before,
                        padding_after=self._padding_after,
                        allowed_actors=self._allowed_actors,
                        skip_frames=self._skip_frames,
                        mode=self._mode,
                        interval_sec=self._interval_sec,
                        gate_mode=self._gate_mode,
                    )
                    tr, rp = pl.run_full(
                        video_path=self._video_path,
                        annotations=self._annotation,
                        start_frame=sf, end_frame=ef,
                        progress_cb=lambda p: self.progress.emit(sid, p),
                        detected_cb=lambda ts, t: self.detected.emit(ts, t),
                        raw_ocr_cb=lambda ts, t, l: self.raw_text.emit(ts, t, l),
                        cancel_check=self._cancel_event.is_set,
                    )
                    rq.put((sid, tr, rp))
                except Exception as e:
                    import traceback
                    self.error.emit(f"Segment {sid}: {e}\n{traceback.format_exc()}")
                    rq.put((sid, [], None))

            for i in range(segments):
                sf = self._start_frame + i * frames_per
                ef_end = self._start_frame + (i + 1) * frames_per - 1
                ef = ef_end if i < segments - 1 else end
                t = threading.Thread(target=run_segment, args=(i, sf, ef), daemon=True)
                t.start()
                threads.append(t)

            for t in threads:
                t.join()

            while not rq.empty():
                _, tr, rp = rq.get_nowait()
                all_results.extend(tr)
                if rp:
                    all_reports.append(rp)

            all_results.sort(key=lambda r: r.start_sec)
            merged = []
            for r in all_results:
                rt = (r.start_sec, r.end_sec, r.action, r.actor, r.pattern_id, r.source,
                      r.raw_start_sec, r.raw_end_sec,
                      r.raw_text, r.confidence, r.match_strategy)
                if not merged or merged[-1][:6] != rt[:6]:
                    merged.append(rt)

            self.finished.emit(self.thread_id, merged,
                               all_reports[0] if all_reports else None)
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit(self.thread_id, [], None)

    def cancel(self):
        self._cancel_event.set()
