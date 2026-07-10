"""OCR 检测引擎 —— 纯逻辑。

整合 EasyOCR、图像预处理、关键词匹配。
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import cv2
import numpy as np

from app.core.keywords import KeywordMatcher, MatchResult
from app.core.model_loader import ModelManager


@dataclass
class OCRResult:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]


@dataclass
class FrameResult:
    frame_number: int
    timestamp: float
    detected: bool = False
    ocr_results: list[OCRResult] = field(default_factory=list)
    match_result: MatchResult | None = None


@dataclass
class TimeRange:
    start_sec: float
    end_sec: float

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec

    @property
    def center(self) -> float:
        return (self.start_sec + self.end_sec) / 2


@dataclass
class DetectionResult(TimeRange):
    """带元数据的检测结果"""
    action: str = ""
    actor: str = ""
    pattern_id: str = ""
    match_count: int = 1


@dataclass
class DetectionReport:
    video_path: str
    total_frames: int
    processed_frames: int
    detection_count: int
    time_ranges: list[TimeRange]
    all_detections: list[FrameResult] = field(default_factory=list)


class OCRDetector:
    """OCR 检测器"""

    def __init__(self, gpu: bool = True, languages: list[str] | None = None):
        if languages is None:
            languages = ["ch_sim", "en"]
        self._reader = ModelManager().get_easyocr_reader(gpu=gpu, languages=languages)

    def detect(self, roi_image: np.ndarray) -> list[OCRResult]:
        if roi_image is None or roi_image.size == 0:
            return []
        processed = self._preprocess(roi_image)
        try:
            raw = self._reader.readtext(processed)
        except Exception:
            return []
        results: list[OCRResult] = []
        for bbox, text, conf in raw:
            if conf < 0.3:
                continue
            x1, y1 = int(bbox[0][0]), int(bbox[0][1])
            x2, y2 = int(bbox[2][0]), int(bbox[2][1])
            results.append(OCRResult(text.strip(), round(float(conf), 4), (x1, y1, x2 - x1, y2 - y1)))
        return results

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        denoised = cv2.fastNlMeansDenoising(enhanced, h=10)
        return cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)


class DetectionEngine:
    """完整检测流水线: 逐帧OCR → 关键词匹配 → 合并时间段"""

    def __init__(self, matcher: KeywordMatcher, detector: OCRDetector,
                 padding_before: float = 10.0, padding_after: float = 10.0,
                 skip_frames: int = 3, merge_gap: float = 30.0,
                 mode: str = "frame", interval_sec: float = 1.0,
                 post_detect_skip_sec: float = 0.3,
                 allowed_actors: set | None = None):
        self._matcher = matcher
        self._detector = detector
        self.padding_before = padding_before
        self.padding_after = padding_after
        self.skip_frames = skip_frames
        self.merge_gap = merge_gap
        self.mode = mode
        self.interval_sec = interval_sec
        self.post_detect_skip_sec = post_detect_skip_sec
        self.allowed_actors = allowed_actors

    def process_frame(self, frame: np.ndarray, rois: list,
                      fps: float, frame_number: int) -> FrameResult:
        frame_matched: MatchResult | None = None
        frame_hits: list[OCRResult] = []
        for roi in rois:
            if hasattr(roi, 'x'):
                roi_img = frame[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]
            else:
                roi_img = frame[roi['y']:roi['y'] + roi['h'], roi['x']:roi['x'] + roi['w']]
            for ocr_r in self._detector.detect(roi_img):
                match = self._matcher.match(ocr_r.text)
                if match:
                    if self.allowed_actors is not None and match.actor not in self.allowed_actors:
                        continue
                    frame_hits.append(ocr_r)
                    if frame_matched is None:
                        frame_matched = match
        return FrameResult(
            frame_number=frame_number, timestamp=frame_number / fps,
            detected=frame_matched is not None,
            ocr_results=frame_hits, match_result=frame_matched,
        )

    def run_full(self, video_path: str, annotations, start_frame: int = 0,
                 end_frame: int | None = None,
                 progress_cb=None, detected_cb=None, cancel_check=None
                 ) -> list[TimeRange]:
        """完整检测管线 — GUI 线程和 CLI 共用。

        Args:
            video_path: 视频文件路径
            annotations: AnnotationStore 实例
            start_frame: 起始帧号
            end_frame: 结束帧号，None=视频末尾
            progress_cb: 进度回调 (pct: float) -> None
            detected_cb: 检测回调 (timestamp: float, text: str) -> None
            cancel_check: 取消检查 () -> bool

        Returns:
            list[TimeRange] 合并后的时间段
        """
        from app.core.player import VideoPlayer

        player = VideoPlayer()
        info = player.open(video_path)
        fps = info.fps
        end = end_frame if end_frame is not None else (info.total_frames - 1)
        rois = annotations.to_pixel_rois(info.width, info.height)

        if not rois:
            player.close()
            return []

        detections: list[FrameResult] = []

        if self.mode == "time":
            start_sec = start_frame / fps
            end_sec = end / fps
            current_time = start_sec
            while current_time <= end_sec and not (cancel_check and cancel_check()):
                fn = int(current_time * fps)
                if fn > end:
                    break
                try:
                    frame = player.seek(fn)
                except Exception:
                    current_time += self.interval_sec
                    continue

                result = self.process_frame(frame, rois, fps, fn)

                if result.detected:
                    detections.append(result)
                    if detected_cb:
                        detected_cb(result.timestamp,
                                    result.match_result.raw_text if result.match_result else "")
                    current_time += self.post_detect_skip_sec
                else:
                    current_time += self.interval_sec

                if progress_cb:
                    pct = (current_time - start_sec) / (end_sec - start_sec) * 100
                    progress_cb(min(pct, 100.0))
        else:
            fn = start_frame
            while fn <= end and not (cancel_check and cancel_check()):
                try:
                    frame = player.seek(fn)
                except Exception:
                    fn += 1
                    continue

                result = self.process_frame(frame, rois, fps, fn)

                if result.detected:
                    detections.append(result)
                    if detected_cb:
                        detected_cb(result.timestamp,
                                    result.match_result.raw_text if result.match_result else "")
                    fn += max(1, int(fps * self.post_detect_skip_sec))
                else:
                    fn += self.skip_frames

                if progress_cb:
                    pct = (fn - start_frame) / (end - start_frame) * 100
                    progress_cb(min(pct, 100.0))

        player.close()
        return self._merge_detections(detections)

    @staticmethod
    def _best_meta(detections: list[FrameResult]) -> tuple[str, str, str]:
        """从一组检测帧中选出最佳元数据：优先淘汰 > 击倒，取最高频次"""
        score: dict[str, tuple[int, str, str, str]] = {}  # key → (count, action, actor, id)
        for d in detections:
            if d.match_result is None:
                continue
            action = d.match_result.action
            key = f"{d.match_result.actor}|{action}"
            if key not in score:
                score[key] = (0, action, d.match_result.actor, d.match_result.pattern_id)
            score[key] = (score[key][0] + 1, score[key][1], score[key][2], score[key][3])
        priority_order = ["淘汰", "击倒"]
        best = sorted(score.values(), key=lambda x: (
            0 if x[1] in priority_order else 99 - priority_order.index(x[1]) if x[1] in priority_order else 0,
            x[0]
        ), reverse=True)
        if best:
            return best[0][1], best[0][2], best[0][3]  # action, actor, pattern_id
        return "", "", ""

    def _merge_detections(self, detections: list[FrameResult]) -> list[DetectionResult]:
        if not detections:
            return []
        # 每帧分组合并
        groups: list[list[FrameResult]] = []
        sorted_d = sorted(detections, key=lambda d: d.timestamp)
        groups.append([sorted_d[0]])
        for d in sorted_d[1:]:
            last = groups[-1][-1]
            if d.timestamp - last.timestamp <= self.padding_before + self.padding_after + self.merge_gap:
                groups[-1].append(d)
            else:
                groups.append([d])
        results: list[DetectionResult] = []
        for g in groups:
            action, actor, pid = self._best_meta(g)
            results.append(DetectionResult(
                start_sec=max(0.0, g[0].timestamp - self.padding_before),
                end_sec=g[-1].timestamp + self.padding_after,
                action=action, actor=actor, pattern_id=pid,
                match_count=len(g),
            ))
        return results

    @staticmethod
    def merge_time_ranges(ranges: list[TimeRange], max_gap: float = 30.0) -> list[TimeRange]:
        if not ranges:
            return []
        sorted_r = sorted(ranges, key=lambda r: r.start_sec)
        merged = [sorted_r[0]]
        for r in sorted_r[1:]:
            last = merged[-1]
            if r.start_sec - last.end_sec <= max_gap:
                merged[-1] = TimeRange(last.start_sec, max(last.end_sec, r.end_sec))
            else:
                merged.append(r)
        return merged
