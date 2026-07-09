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

    def _merge_detections(self, detections: list[FrameResult]) -> list[TimeRange]:
        if not detections:
            return []
        ranges = [TimeRange(
            max(0.0, d.timestamp - self.padding_before),
            d.timestamp + self.padding_after,
        ) for d in detections]
        ranges.sort(key=lambda r: r.start_sec)
        merged = [ranges[0]]
        for r in ranges[1:]:
            last = merged[-1]
            if r.start_sec - last.end_sec <= self.merge_gap:
                merged[-1] = TimeRange(last.start_sec, max(last.end_sec, r.end_sec))
            else:
                merged.append(r)
        return merged

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
