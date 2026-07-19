"""OCR 检测引擎 —— 纯逻辑。

整合 EasyOCR、图像预处理、关键词匹配。
"""

import json
import logging
import re
import threading
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
    source: str = "text"  # "text", "counter", "both"
    raw_start_sec: float = 0.0
    raw_end_sec: float = 0.0
    raw_text: str = ""
    confidence: float = 1.0
    match_strategy: str = "exact"


@dataclass
class SignalEvent:
    """推入事件栈的原子信号 —— 统一文本匹配和计数器跳变"""
    timestamp: float
    action: str = ""
    actor: str = ""
    pattern_id: str = ""
    raw_text: str = ""
    source: str = "text"  # "text" or "counter"
    confidence: float = 1.0
    match_strategy: str = "exact"


@dataclass
class EventStackConfig:
    """事件栈配置 —— 每种信号类型的 wait_after"""
    wait_after: dict[str, float] = field(default_factory=lambda: {
        "淘汰": 10.0, "击倒": 30.0, "counter": 10.0,
    })
    padding_before: float = 10.0
    padding_after: float = 10.0


@dataclass
class FrameLog:
    """单帧、单个ROI的一次OCR处理记录"""
    frame_number: int
    timestamp: float
    roi_id: int
    roi_label: str
    stage: str = "ocr"
    ocr_text: str = ""
    ocr_confidence: float = 0.0
    match_hit: bool = False
    match_pattern_id: str = ""
    match_action: str = ""
    match_actor: str = ""
    counter_value: int = -1
    counter_delta: int = 0
    dropped: bool = False
    drop_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "frame": self.frame_number, "ts": round(self.timestamp, 2),
            "roi_id": self.roi_id, "roi_label": self.roi_label,
            "stage": self.stage,
            "ocr_text": self.ocr_text, "ocr_confidence": self.ocr_confidence,
            "match_hit": self.match_hit, "match_pattern_id": self.match_pattern_id,
            "match_action": self.match_action, "match_actor": self.match_actor,
            "counter_value": self.counter_value, "counter_delta": self.counter_delta,
            "dropped": self.dropped, "drop_reason": self.drop_reason,
        }


@dataclass
class DroppedRecord:
    frame_number: int
    timestamp: float
    roi_label: str
    ocr_text: str
    ocr_confidence: float
    reason: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "frame": self.frame_number, "ts": round(self.timestamp, 2),
            "roi_label": self.roi_label,
            "ocr_text": self.ocr_text, "ocr_confidence": self.ocr_confidence,
            "reason": self.reason, "detail": self.detail,
        }


@dataclass
class RunSummary:
    total_frames: int = 0
    frames_with_ocr_hits: int = 0
    frames_with_matches: int = 0
    counter_total_events: int = 0
    counter_total_delta: int = 0
    dropped_total: int = 0
    dropped_by_reason: dict = field(default_factory=dict)
    detections_before_merge: int = 0
    detections_after_merge: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_frames": self.total_frames,
            "frames_with_ocr_hits": self.frames_with_ocr_hits,
            "frames_with_matches": self.frames_with_matches,
            "counter_total_events": self.counter_total_events,
            "counter_total_delta": self.counter_total_delta,
            "dropped_total": self.dropped_total,
            "dropped_by_reason": self.dropped_by_reason,
            "detections_before_merge": self.detections_before_merge,
            "detections_after_merge": self.detections_after_merge,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
        }


class FrameLogger:
    """共享帧日志 —— Pool / Legacy / 后处理共用。

    - 线程安全：Pool 模式多个 GPUWorker 并发写入。
    - populate(report) 将收集的数据写入 DetectionReport。
    """

    def __init__(self, video_fps: float = 30.0):
        import threading
        self._lock = threading.Lock()
        self._video_fps = video_fps
        self.frames: list[FrameLog] = []
        self.counter_events: list[dict] = []
        self.dropped: list[DroppedRecord] = []
        self._counter_value: int = -1

    def log_sample(self, timestamp: float, roi_label: str, roi_id: int,
                   ocr_text: str = "", ocr_confidence: float = 0.0,
                   match_hit: bool = False, match_pattern_id: str = "",
                   match_action: str = "", match_actor: str = "",
                   stage: str = "ocr"):
        fl = FrameLog(
            frame_number=int(timestamp * self._video_fps),
            timestamp=round(timestamp, 2), roi_id=roi_id, roi_label=roi_label,
            stage=stage, ocr_text=ocr_text, ocr_confidence=ocr_confidence,
            match_hit=match_hit, match_pattern_id=match_pattern_id,
            match_action=match_action, match_actor=match_actor,
        )
        with self._lock:
            self.frames.append(fl)

    def log_counter(self, timestamp: float, value: int, delta: int,
                    raw_text: str = "", drop_reason: str = ""):
        fl = FrameLog(
            frame_number=int(timestamp * self._video_fps),
            timestamp=round(timestamp, 2), roi_id=0, roi_label="淘汰计数",
            stage="counter_track", ocr_text=raw_text, ocr_confidence=0.0,
            counter_value=value, counter_delta=delta,
            dropped=bool(drop_reason), drop_reason=drop_reason,
        )
        with self._lock:
            self.frames.append(fl)
            if drop_reason:
                self.dropped.append(DroppedRecord(
                    frame_number=fl.frame_number, timestamp=timestamp,
                    roi_label="淘汰计数", ocr_text=raw_text, ocr_confidence=0.0,
                    reason=drop_reason, detail=f"raw_text='{raw_text}'",
                ))
            if delta > 0:
                self.counter_events.append({
                    "timestamp": round(timestamp, 2), "delta": delta,
                    "new_count": value,
                })
            self._counter_value = value

    def populate(self, report: "DetectionReport") -> None:
        with self._lock:
            report.frames_log = list(self.frames)
            report.dropped = list(self.dropped)
            report.counter_events = list(self.counter_events)
        s = report.summary
        s.counter_total_events = len(self.counter_events)
        s.counter_total_delta = sum(e.get("delta", 0) for e in self.counter_events)
        s.dropped_total = len(self.dropped)
        s.frames_with_ocr_hits = sum(1 for f in self.frames if f.match_hit)
        s.frames_with_matches = s.frames_with_ocr_hits


class DetectionReport:
    """一次检测运行的完整报告"""

    def __init__(self, video_path: str = "", video_width: int = 0,
                 video_height: int = 0, video_fps: float = 30.0,
                 video_duration_sec: float = 0.0, config: dict | None = None,
                 rois: list[dict] | None = None):
        self.video_path = video_path
        self.video_width = video_width
        self.video_height = video_height
        self.video_fps = video_fps
        self.video_duration_sec = video_duration_sec
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.config = config or {}
        self.rois = rois or []
        self.frames_log: list[FrameLog] = []
        self.dropped: list[DroppedRecord] = []
        self.counter_events: list[dict] = []
        self.results: list[dict] = []
        self.summary = RunSummary()

    def to_dict(self) -> dict:
        return {
            "video_path": self.video_path,
            "video_width": self.video_width,
            "video_height": self.video_height,
            "video_fps": self.video_fps,
            "video_duration_sec": self.video_duration_sec,
            "started_at": self.started_at,
            "config": self.config,
            "rois": self.rois,
            "frames_log": [f.to_dict() for f in self.frames_log],
            "dropped": [d.to_dict() for d in self.dropped],
            "counter_events": self.counter_events,
            "results": self.results,
            "summary": self.summary.to_dict(),
        }

    def save_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_json(cls, path: str) -> "DetectionReport":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        r = cls(
            video_path=raw.get("video_path", ""),
            video_width=raw.get("video_width", 0),
            video_height=raw.get("video_height", 0),
            video_fps=raw.get("video_fps", 30.0),
            video_duration_sec=raw.get("video_duration_sec", 0.0),
            config=raw.get("config", {}),
            rois=raw.get("rois", []),
        )
        r.started_at = raw.get("started_at", "")
        r.frames_log = [
            FrameLog(
                frame_number=fl.get("frame", 0), timestamp=fl.get("ts", 0.0),
                roi_id=fl.get("roi_id", 0), roi_label=fl.get("roi_label", ""),
                stage=fl.get("stage", "ocr"),
                ocr_text=fl.get("ocr_text", ""), ocr_confidence=fl.get("ocr_confidence", 0.0),
                match_hit=fl.get("match_hit", False),
                match_pattern_id=fl.get("match_pattern_id", ""),
                match_action=fl.get("match_action", ""),
                match_actor=fl.get("match_actor", ""),
                counter_value=fl.get("counter_value", -1),
                counter_delta=fl.get("counter_delta", 0),
                dropped=fl.get("dropped", False), drop_reason=fl.get("drop_reason", ""),
            )
            for fl in raw.get("frames_log", [])
        ]
        r.dropped = [
            DroppedRecord(
                frame_number=d.get("frame", 0), timestamp=d.get("ts", 0.0),
                roi_label=d.get("roi_label", ""),
                ocr_text=d.get("ocr_text", ""), ocr_confidence=d.get("ocr_confidence", 0.0),
                reason=d.get("reason", ""), detail=d.get("detail", ""),
            )
            for d in raw.get("dropped", [])
        ]
        r.counter_events = raw.get("counter_events", [])
        r.results = raw.get("results", [])
        s = raw.get("summary", {})
        r.summary = RunSummary(**s)
        return r


class CounterTracker:
    """淘汰计数器追踪：数字递增 = 淘汰事件，跳变说明漏检"""

    def __init__(self):
        self._count = 0
        self._started = False
        self.events: list[tuple[float, int]] = []  # (timestamp, delta)
        self.on_event: Callable[[SignalEvent], None] | None = None

    def feed(self, timestamp: float, text: str, log_cb=None) -> int:
        """返回当前读数；log_cb(report, stage, value, delta) 可空"""
        num = self._extract_number(text)
        if num is None:
            if log_cb:
                log_cb(timestamp, "counter_track", -1, 0,
                       text, "parse_number_failed", False)
            return self._count
        if not self._started:
            self._count = num
            self._started = True
            if log_cb:
                log_cb(timestamp, "counter_track", num, 0, text, "", False)
            return self._count
        delta = num - self._count
        if delta > 0:
            self.events.append((timestamp, delta))
            if self.on_event:
                for i in range(delta):
                    self.on_event(SignalEvent(
                        timestamp=timestamp + i * 0.001,
                        action="淘汰", actor="自己",
                        pattern_id="counter", source="counter",
                    ))
        elif delta < -3 and self._count >= 7 and num <= 3:
            if log_cb:
                log_cb(timestamp, "counter_reset", num, delta, text, "", False)
            self._count = num
            return self._count
        self._count = max(self._count, num)
        if log_cb:
            log_cb(timestamp, "counter_track", num, max(delta, 0), text, "", delta > 0)
        return self._count

    @staticmethod
    def _extract_number(text: str) -> int | None:
        m = re.search(r"(\d+)", text)
        return int(m.group(1)) if m else None

    def reset(self):
        self._count = 0
        self._started = False
        self.events.clear()


class EventStackEngine:
    """事件栈：按 (action, actor) 分栈，同类型事件合并，不同类型独立。"""

    def __init__(self, config: EventStackConfig):
        self._cfg = config
        self._stacks: dict[tuple[str, str], list[SignalEvent]] = {}
        self._deadlines: dict[tuple[str, str], float] = {}
        self._results: list[DetectionResult] = []
        self.total_pushed: int = 0

    def push(self, event: SignalEvent) -> None:
        self.total_pushed += 1
        key = (event.action, event.actor) if event.actor else (event.action, "")
        # 先检查所有其他栈是否过期
        for k in list(self._stacks.keys()):
            if k != key and self._stacks[k] and event.timestamp > self._deadlines.get(k, 0):
                self._pop_key(k)
        # 当前栈：超时则先出栈
        if self._stacks.get(key) and event.timestamp > self._deadlines.get(key, 0):
            self._pop_key(key)
        self._stacks.setdefault(key, []).append(event)
        actor_key = f"{event.action}:{event.actor}" if event.actor else event.action
        wait = self._cfg.wait_after.get(actor_key) or self._cfg.wait_after.get(event.action, 15.0)
        self._deadlines[key] = max(self._deadlines.get(key, 0), event.timestamp + wait)

    def flush(self) -> list[DetectionResult]:
        for k in list(self._stacks.keys()):
            self._pop_key(k)
        return self._results

    def _pop_key(self, key: tuple[str, str]) -> None:
        events = self._stacks.pop(key, [])
        self._deadlines.pop(key, None)
        if not events:
            return
        events.sort(key=lambda e: e.timestamp)
        has_text = any(e.source == "text" for e in events)
        has_counter = any(e.source == "counter" for e in events)
        if has_text and has_counter:
            src = "both"
        elif has_counter:
            src = "counter"
        else:
            src = "text"
        min_conf = min(e.confidence for e in events)
        is_fuzzy = any(e.match_strategy == "fuzzy" for e in events)
        self._results.append(DetectionResult(
            start_sec=max(0.0, events[0].timestamp - self._cfg.padding_before),
            end_sec=events[-1].timestamp + self._cfg.padding_after,
            raw_start_sec=events[0].timestamp,
            raw_end_sec=events[-1].timestamp,
            action=key[0], actor=key[1], pattern_id=events[0].pattern_id,
            match_count=len(events), source=src,
            confidence=min_conf,
            match_strategy="fuzzy" if is_fuzzy else "exact",
            raw_text=events[0].raw_text,
        ))


class TextFusionBuffer:
    """多帧 OCR 文本融合缓冲区 —— 借鉴 video-subtitle-extractor / GameSentenceMiner。

    滑动窗口内收集 OCR 结果，按 Levenshtein 相似度聚类。
    当同类文本被多次确认后，返回融合后的最佳文本。
    """

    def __init__(self, window_sec: float = 3.0, min_confirmations: int = 2,
                 sim_threshold: float = 0.6):
        self._window = window_sec
        self._min_confirm = min_confirmations
        self._sim_threshold = sim_threshold
        self._buffer: list[tuple[float, str, float]] = []  # (timestamp, text, confidence)

    def feed(self, timestamp: float, text: str, confidence: float) -> str | None:
        """输入一次 OCR 结果。返回融合文本或 None（等待更多帧）。"""
        if not text or not text.strip():
            return None
        # 清理窗口外旧数据
        self._buffer = [(ts, t, c) for ts, t, c in self._buffer
                        if timestamp - ts <= self._window]
        self._buffer.append((timestamp, text, confidence))
        # 聚类
        clusters = self._cluster()
        # 查找已确认的聚类
        for cluster in clusters:
            if len(cluster) >= self._min_confirm:
                return self._fuse(cluster)
        return None

    def _cluster(self) -> list[list[tuple[float, str, float]]]:
        """按 Levenshtein 相似度聚类"""
        if not self._buffer:
            return []
        clusters: list[list[tuple[float, str, float]]] = []
        for entry in self._buffer:
            placed = False
            for cluster in clusters:
                if self._levenshtein_ratio(entry[1], cluster[0][1]) >= self._sim_threshold:
                    cluster.append(entry)
                    placed = True
                    break
            if not placed:
                clusters.append([entry])
        return clusters

    def _fuse(self, cluster: list[tuple[float, str, float]]) -> str:
        """从聚类中选出最佳文本：最长 + 最高置信度"""
        best = max(cluster, key=lambda e: (len(e[1]), e[2]))
        return best[1]

    @staticmethod
    def _levenshtein_ratio(a: str, b: str) -> float:
        """Levenshtein 相似度 (0.0-1.0)"""
        if not a or not b:
            return 0.0
        n, m = len(a), len(b)
        if n == 0 or m == 0:
            return 0.0
        # 使用两行 DP 减少内存
        prev = list(range(m + 1))
        curr = [0] * (m + 1)
        for i in range(1, n + 1):
            curr[0] = i
            for j in range(1, m + 1):
                cost = 0 if a[i - 1] == b[j - 1] else 1
                curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
            prev, curr = curr, prev
        distance = prev[m]
        return 1.0 - (distance / max(n, m))


class OCRDetector:
    """OCR 检测器 —— 支持 EasyOCR / RapidOCR 双引擎。

    GPU 调用通过类级锁保证线程安全（EasyOCR），RapidOCR 的 ONNX Runtime 本身线程安全。
    """

    _gpu_lock = threading.Lock()

    def __init__(self, gpu: bool = True, languages: list[str] | None = None,
                 engine: str = "rapidocr"):
        if languages is None:
            languages = ["ch_sim", "en"]
        self._engine_type = engine
        self._gpu = gpu
        self._languages = languages
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    @property
    def _engine(self):
        """每次访问通过 ModelManager 获取线程本地引擎实例。"""
        return ModelManager().get_engine(self._engine_type, gpu=self._gpu,
                                         languages=self._languages)

    # ── 公共接口 ──

    def detect(self, roi_image: np.ndarray) -> list[OCRResult]:
        """对 ROI 图像做 OCR，返回识别结果列表。"""
        if roi_image is None or roi_image.size == 0:
            return []
        if self._engine_type == "rapidocr":
            return self._detect_rapidocr(roi_image)
        return self._detect_easyocr(roi_image)

    def detect_raw(self, preprocessed: np.ndarray) -> list[OCRResult]:
        """对已预处理的图像做 OCR（EasyOCR 路径保留 CLAHE，RapidOCR 路径等同 detect）。"""
        if preprocessed is None or preprocessed.size == 0:
            return []
        if self._engine_type == "rapidocr":
            return self._detect_rapidocr(preprocessed)
        return self._detect_easyocr(preprocessed)

    def has_text(self, roi_image: np.ndarray) -> bool:
        """快速判断 ROI 内是否有文字（神经门控）。

        RapidOCR: 仅检测 → 返回 bool。
        EasyOCR: CRAFT 文本区域检测（不做识别）
        """
        if roi_image is None or roi_image.size == 0:
            return False
        if self._engine_type == "rapidocr":
            result = self._engine(roi_image, use_det=True, use_cls=False, use_rec=False)
            return result.boxes is not None and len(result.boxes) > 0
        h_list, f_list = self._engine.detect(roi_image)
        h_list = h_list[0] if h_list else []
        f_list = f_list[0] if f_list else []
        return (len(h_list) + len(f_list)) > 0

    # ── RapidOCR ──

    def _detect_rapidocr(self, roi_image: np.ndarray) -> list[OCRResult]:
        """RapidOCR 推理 —— 每线程独立引擎，无需全局锁。"""
        result = self._engine(roi_image, use_det=True, use_cls=True, use_rec=True)
        if result.boxes is None or len(result.boxes) == 0:
            return []
        results: list[OCRResult] = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            if score < 0.3:
                continue
            x1 = int(round(box[0][0].item() if hasattr(box[0][0], 'item') else box[0][0]))
            y1 = int(round(box[0][1].item() if hasattr(box[0][1], 'item') else box[0][1]))
            x2 = int(round(box[2][0].item() if hasattr(box[2][0], 'item') else box[2][0]))
            y2 = int(round(box[2][1].item() if hasattr(box[2][1], 'item') else box[2][1]))
            results.append(OCRResult(text.strip(), round(float(score), 4),
                                     (x1, y1, x2 - x1, y2 - y1)))
        return results

    # ── EasyOCR ──

    def _detect_easyocr(self, roi_image: np.ndarray) -> list[OCRResult]:
        """EasyOCR 推理 —— CLAHE 预处理 → readtext。"""
        prepped = self._preprocess(roi_image)
        with self._gpu_lock:
            try:
                raw = self._engine.readtext(prepped)
            except Exception:
                return []
        results: list[OCRResult] = []
        for bbox, text, conf in raw:
            if conf < 0.3:
                continue
            x1, y1 = int(bbox[0][0]), int(bbox[0][1])
            x2, y2 = int(bbox[2][0]), int(bbox[2][1])
            results.append(OCRResult(text.strip(), round(float(conf), 4),
                                     (x1, y1, x2 - x1, y2 - y1)))
        return results

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()
        enhanced = self._clahe.apply(gray)
        return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


class TextPresenceGate:
    """文字存在性门控 —— 灰度 + 阈值 + 白像素计数，<1ms 判断 ROI 内是否有文字。

    FPS 游戏中击杀信息为白色文字，半透明深色底条。
    对 ROI 做高阈值二值化，白像素数 > min_white 即认为有文字。
    """

    def __init__(self, threshold: int = 230, min_white: int = 200,
                 change_ratio: float = 0.15):
        self._threshold = threshold
        self._min_white = min_white
        self._change_ratio = change_ratio
        self._last_white: int = 0
        self._last_has_text: bool = False

    def check(self, roi_image: np.ndarray) -> tuple[bool, bool]:
        """返回 (has_text, is_new) —— 是否有文字，是否是新出现的/变化的文字"""
        gray = cv2.cvtColor(roi_image, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, self._threshold, 255, cv2.THRESH_BINARY)
        white = cv2.countNonZero(mask)
        has_text = white >= self._min_white
        if not has_text:
            self._last_white = white
            self._last_has_text = False
            return False, False
        if not self._last_has_text:
            # 文字刚出现
            self._last_white = white
            self._last_has_text = True
            return True, True
        # 文字持续存在 — 检查是否有明显变化（新击杀信息）
        if white > 0:
            ratio = abs(white - self._last_white) / white
            is_new = ratio > self._change_ratio
        else:
            is_new = True
        self._last_white = white
        self._last_has_text = True
        return True, is_new

    def reset(self):
        self._last_white = 0
        self._last_has_text = False


class FramePrefetcher:
    """CPU 并行预取器 —— 每个 worker 独立 VideoPlayer，seek + 文字门控 + 预处理一体化。

    用法:
        prefetcher = FramePrefetcher(video_path, detector, rois, fps, num_workers=4)
        prefetcher.start()
        for fn in frame_list:
            prefetcher.submit(fn)
        while (result := prefetcher.next_result()) is not None:
            fn, prepped, timestamp, has_text = result
            if has_text: process(prepped)
        prefetcher.stop()
    """

    def __init__(self, video_path: str, detector: OCRDetector, rois: list,
                 fps: float, num_workers: int = 4):
        import queue
        self._video_path = video_path
        self._detector = detector
        self._rois = rois
        self._fps = fps
        self._num_workers = num_workers
        self._input_queue: queue.Queue = queue.Queue(maxsize=num_workers * 3)
        self._output_queue: queue.Queue = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._running = False
        self._submitted = 0
        self._collected = 0
        # 找出击杀信息 ROI
        self._kill_roi_index = 0
        for i, roi in enumerate(rois):
            lbl = getattr(roi, 'label', '') if hasattr(roi, 'label') else roi.get('label', '')
            if lbl == '击杀信息':
                self._kill_roi_index = i
                break
        self._text_gate = TextPresenceGate()

    def start(self):
        self._running = True
        for _ in range(self._num_workers):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self._workers.append(t)

    def stop(self):
        for _ in self._workers:
            try:
                self._input_queue.put_nowait(None)
            except Exception:
                pass
        self._running = False

    def submit(self, frame_number: int):
        """提交一帧到预处理队列（非阻塞，队列满时等待）"""
        self._input_queue.put(frame_number)
        self._submitted += 1

    def next_result(self):
        """获取下一个预处理完成的 ROI（阻塞直到有结果或结束）"""
        if self._collected >= self._submitted:
            return None
        result = self._output_queue.get()
        if result is None:
            return None
        self._collected += 1
        return result

    def next_result_nowait(self):
        """获取已完成的结果（非阻塞，无结果时抛出 queue.Empty）"""
        if self._collected >= self._submitted:
            return None
        result = self._output_queue.get_nowait()
        if result is None:
            return None
        self._collected += 1
        return result

    @property
    def pending(self) -> int:
        return self._submitted - self._collected

    def _worker(self):
        """CPU 工作线程：独立 VideoPlayer → seek → 文字门控 + 裁剪 + 预处理 → 输出队列"""
        from app.core.player import VideoPlayer
        player = VideoPlayer()
        try:
            player.open(self._video_path)
        except Exception:
            return
        try:
            while self._running:
                try:
                    fn = self._input_queue.get(timeout=0.5)
                except Exception:
                    continue
                if fn is None:
                    break
                try:
                    frame = player.seek(fn)
                    if frame is None:
                        self._output_queue.put((fn, [], 0.0, False, False))
                        continue
                    timestamp = fn / max(self._fps, 1)
                    # 文字门控：快速检查击杀信息 ROI
                    roi = self._rois[self._kill_roi_index]
                    if hasattr(roi, 'x'):
                        kill_img = frame[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]
                    else:
                        kill_img = frame[roi['y']:roi['y'] + roi['h'],
                                         roi['x']:roi['x'] + roi['w']]
                    has_text, is_new = self._text_gate.check(kill_img)
                    if not has_text:
                        self._output_queue.put((fn, [], timestamp, False, False))
                        continue
                    # 有文字 → 预处理所有 ROI
                    prepped = []
                    for roi in self._rois:
                        if hasattr(roi, 'x'):
                            roi_img = frame[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]
                        else:
                            roi_img = frame[roi['y']:roi['y'] + roi['h'],
                                             roi['x']:roi['x'] + roi['w']]
                        prepped.append((self._detector._preprocess(roi_img), roi))
                    self._output_queue.put((fn, prepped, timestamp, True, is_new))
                except Exception:
                    self._output_queue.put((fn, [], 0.0, False, False))
        finally:
            try:
                player.close()
            except Exception:
                pass


class DetectionEngine:
    """完整检测流水线: 逐帧OCR → 关键词匹配 → 合并时间段"""

    def __init__(self, matcher: KeywordMatcher, detector: OCRDetector,
                 padding_before: float = 10.0, padding_after: float = 10.0,
                 skip_frames: int = 3, merge_gap: float = 30.0,
                 mode: str = "frame", interval_sec: float = 1.0,
                 post_detect_skip_sec: float = 0.3,
                 allowed_actors: set | None = None,
                 wait_after: dict[str, float] | None = None,
                 burst_interval_sec: float = 0.25,
                 burst_window_sec: float = 3.0):
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
        self._wait_after = wait_after
        self.burst_interval_sec = burst_interval_sec
        self.burst_window_sec = burst_window_sec
        self._counter = CounterTracker()
        self._frame_logger = FrameLogger()
        self._report: DetectionReport | None = None
        self._config_meta: dict = {}
        self._fusion_buffer = TextFusionBuffer()
        self._roi_snapshot: dict[str, np.ndarray] = {}
        self._roi_frame_gap: dict[str, int] = {}

    def _ensure_report(self, video_info, annotations) -> DetectionReport:
        if self._report is None:
            roi_dicts = [{"id": r.id, "label": r.label} for r in annotations.regions]
            duration = video_info.total_frames / max(video_info.fps, 1)
            self._report = DetectionReport(
                video_path=video_info.path if hasattr(video_info, 'path') else "",
                video_width=video_info.width, video_height=video_info.height,
                video_fps=video_info.fps, video_duration_sec=duration,
                config=self._config_meta, rois=roi_dicts,
            )
            self._frame_logger = FrameLogger(video_fps=video_info.fps)
        return self._report

    def _log_counter(self, timestamp: float, stage: str, value: int, delta: int,
                     raw_text: str, drop_reason: str, is_event: bool):
        if self._report is None:
            return
        fl = FrameLog(
            frame_number=int(timestamp * (self._report.video_fps or 30)),
            timestamp=timestamp, roi_id=0, roi_label="淘汰计数",
            stage=stage, ocr_text=raw_text, ocr_confidence=0.0,
            counter_value=value, counter_delta=delta,
            dropped=bool(drop_reason), drop_reason=drop_reason,
        )
        self._report.frames_log.append(fl)
        if drop_reason:
            self._report.dropped.append(DroppedRecord(
                frame_number=fl.frame_number, timestamp=timestamp,
                roi_label="淘汰计数", ocr_text=raw_text, ocr_confidence=0.0,
                reason=drop_reason, detail=f"raw_text='{raw_text}'",
            ))
        if is_event:
            self._report.counter_events.append({
                "timestamp": round(timestamp, 2), "delta": delta,
                "new_count": value,
            })

    def process_frame(self, frame: np.ndarray, rois: list,
                      fps: float, frame_number: int,
                      raw_ocr_cb=None) -> FrameResult:
        frame_matched: MatchResult | None = None
        frame_hits: list[OCRResult] = []
        timestamp = frame_number / fps
        for i, roi in enumerate(rois):
            if hasattr(roi, 'x'):
                roi_img = frame[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]
                label = getattr(roi, 'label', '击杀信息')
            else:
                roi_img = frame[roi['y']:roi['y'] + roi['h'], roi['x']:roi['x'] + roi['w']]
                label = roi.get('label', '击杀信息')
            self._matcher.set_roi(label)
            roi_id = self._roi_id(roi, i)
            if label == '淘汰计数':
                for ocr_r in self._detector.detect(roi_img):
                    old = self._counter._count
                    new = self._counter.feed(timestamp, ocr_r.text)
                    self._frame_logger.log_counter(
                        timestamp, new, new - old, ocr_r.text)
                    if raw_ocr_cb:
                        raw_ocr_cb(timestamp, ocr_r.text, label)
            else:
                ocr_results = self._detector.detect(roi_img)
                if not ocr_results:
                    self._log_keyword(timestamp, frame_number, roi_id, label,
                                      ocr_text="", ocr_conf=0.0, match=None,
                                      prefix_ok=False, drop_reason="ocr_no_text")
                # 拼接同帧同 ROI 的碎片文本（按 x 坐标排序）
                if len(ocr_results) > 1:
                    sorted_results = sorted(ocr_results, key=lambda r: r.bbox[0])
                    joined_text = "".join(r.text for r in sorted_results)
                    joined_conf = min(r.confidence for r in ocr_results)
                    joined_match = self._matcher.match(joined_text)
                    if joined_match and (self.allowed_actors is None or joined_match.actor in self.allowed_actors):
                        self._fusion_buffer.feed(timestamp, joined_text, joined_conf)
                        for ocr_r in ocr_results:
                            if raw_ocr_cb:
                                raw_ocr_cb(timestamp, ocr_r.text, label)
                        frame_hits.extend(ocr_results)
                        if frame_matched is None:
                            frame_matched = joined_match
                        self._log_keyword(timestamp, frame_number, roi_id, label,
                                          joined_text, joined_conf, joined_match,
                                          True, "joined_fragments")
                        continue
                for ocr_r in ocr_results:
                    if raw_ocr_cb:
                        raw_ocr_cb(timestamp, ocr_r.text, label)
                    has_prefix = self._matcher.has_trigger_prefix(ocr_r.text)
                    match = self._matcher.match(ocr_r.text)
                    if match:
                        # 直接匹配成功 — 同时喂入融合缓冲区供后续纠错
                        self._fusion_buffer.feed(timestamp, ocr_r.text, ocr_r.confidence)
                        if self.allowed_actors is not None and match.actor not in self.allowed_actors:
                            continue
                        frame_hits.append(ocr_r)
                        if frame_matched is None:
                            frame_matched = match
                        self._log_keyword(timestamp, frame_number, roi_id, label,
                                          ocr_r.text, ocr_r.confidence, match,
                                          has_prefix, "")
                    else:
                        # 直接匹配失败 — 尝试融合缓冲区纠错
                        fused_text = self._fusion_buffer.feed(
                            timestamp, ocr_r.text, ocr_r.confidence)
                        if fused_text:
                            fused_match = self._matcher.match(fused_text)
                            if fused_match:
                                if self.allowed_actors is not None and fused_match.actor not in self.allowed_actors:
                                    continue
                                frame_hits.append(ocr_r)
                                if frame_matched is None:
                                    frame_matched = fused_match
                                self._log_keyword(timestamp, frame_number, roi_id, label,
                                                  fused_text, ocr_r.confidence, fused_match,
                                                  True, "fused_correction")
                                continue
                        # 无法匹配 — 记录丢弃原因
                        reason = ""
                        if not has_prefix:
                            reason = "no_trigger_prefix"
                        elif ocr_r.confidence < 0.3:
                            reason = "low_confidence"
                        else:
                            reason = "regex_no_match"
                        self._log_keyword(timestamp, frame_number, roi_id, label,
                                          ocr_r.text, ocr_r.confidence, None,
                                          has_prefix, reason)
        return FrameResult(
            frame_number=frame_number, timestamp=timestamp,
            detected=frame_matched is not None,
            ocr_results=frame_hits, match_result=frame_matched,
        )

    def _match_prepped(self, prepped_rois: list, rois: list,
                       timestamp: float, fn: int, raw_ocr_cb=None,
                       is_new: bool = False, process_counter: bool = True):
        """对预处理器输出的 ROI 图像做 GPU OCR + 关键词匹配。

        is_new=False 时跳过击杀信息 ROI 的 OCR（文字未变化）。
        process_counter=False 时跳过淘汰计数 ROI（burst 帧不需要）。"""
        from app.core.keywords import MatchResult
        frame_matched: MatchResult | None = None
        for roi_img, roi in prepped_rois:
            if hasattr(roi, 'x'):
                label = getattr(roi, 'label', '击杀信息')
            else:
                label = roi.get('label', '击杀信息')
            self._matcher.set_roi(label)
            roi_id = self._roi_id(roi, 0)
            if label == '淘汰计数':
                if not process_counter:
                    continue
                for ocr_r in self._detector.detect_raw(roi_img):
                    old = self._counter._count
                    new = self._counter.feed(timestamp, ocr_r.text)
                    self._frame_logger.log_counter(
                        timestamp, new, new - old, ocr_r.text)
                    if raw_ocr_cb:
                        raw_ocr_cb(timestamp, ocr_r.text, label)
            else:
                ocr_results = self._detector.detect_raw(roi_img)
                if not ocr_results:
                    self._log_keyword(timestamp, fn, roi_id, label,
                                      ocr_text="", ocr_conf=0.0, match=None,
                                      prefix_ok=False, drop_reason="ocr_no_text")
                # 拼接同帧同 ROI 的碎片文本（按 x 坐标排序）
                if len(ocr_results) > 1:
                    sorted_results = sorted(ocr_results, key=lambda r: r.bbox[0])
                    joined_text = "".join(r.text for r in sorted_results)
                    joined_conf = min(r.confidence for r in ocr_results)
                    joined_match = self._matcher.match(joined_text)
                    if joined_match and (self.allowed_actors is None or joined_match.actor in self.allowed_actors):
                        self._fusion_buffer.feed(timestamp, joined_text, joined_conf)
                        for ocr_r in ocr_results:
                            if raw_ocr_cb:
                                raw_ocr_cb(timestamp, ocr_r.text, label)
                        if frame_matched is None:
                            frame_matched = joined_match
                        self._log_keyword(timestamp, fn, roi_id, label,
                                          joined_text, joined_conf, joined_match,
                                          True, "joined_fragments")
                        continue
                for ocr_r in ocr_results:
                    if raw_ocr_cb:
                        raw_ocr_cb(timestamp, ocr_r.text, label)
                    has_prefix = self._matcher.has_trigger_prefix(ocr_r.text)
                    match = self._matcher.match(ocr_r.text)
                    if match:
                        self._fusion_buffer.feed(timestamp, ocr_r.text, ocr_r.confidence)
                        if self.allowed_actors is not None and match.actor not in self.allowed_actors:
                            continue
                        if frame_matched is None:
                            frame_matched = match
                        self._log_keyword(timestamp, fn, roi_id, label,
                                          ocr_r.text, ocr_r.confidence, match,
                                          has_prefix, "")
                    else:
                        fused_text = self._fusion_buffer.feed(
                            timestamp, ocr_r.text, ocr_r.confidence)
                        if fused_text:
                            fused_match = self._matcher.match(fused_text)
                            if fused_match:
                                if self.allowed_actors is not None and fused_match.actor not in self.allowed_actors:
                                    continue
                                if frame_matched is None:
                                    frame_matched = fused_match
                                self._log_keyword(timestamp, fn, roi_id, label,
                                                  fused_text, ocr_r.confidence, fused_match,
                                                  True, "fused_correction")
                                continue
                        reason = ""
                        if not has_prefix:
                            reason = "no_trigger_prefix"
                        elif ocr_r.confidence < 0.3:
                            reason = "low_confidence"
                        else:
                            reason = "regex_no_match"
                        self._log_keyword(timestamp, fn, roi_id, label,
                                          ocr_r.text, ocr_r.confidence, None,
                                          has_prefix, reason)
        return frame_matched

    @staticmethod
    def _roi_id(roi, index: int) -> int:
        if hasattr(roi, 'label') and hasattr(roi, 'x'):
            return index  # PixelROI has no id; use index
        return roi.get('id', index) if isinstance(roi, dict) else index

    def _log_keyword(self, timestamp: float, fn: int, roi_id: int, label: str,
                     ocr_text: str, ocr_conf: float, match,
                     prefix_ok: bool, drop_reason: str):
        self._frame_logger.log_sample(
            timestamp, label, roi_id, ocr_text, round(ocr_conf, 4),
            match_hit=match is not None,
            match_pattern_id=match.pattern_id if match else "",
            match_action=match.action if match else "",
            match_actor=match.actor if match else "",
            stage="keyword_match" if match else "ocr")

    def run_full(self, video_path: str, annotations, start_frame: int = 0,
                 end_frame: int | None = None,
                 progress_cb=None, detected_cb=None, cancel_check=None,
                 raw_ocr_cb=None,
                 ) -> list[TimeRange]:
        """完整检测管线 — GUI 线程和 CLI 共用。

        Args:
            video_path: 视频文件路径
            annotations: AnnotationStore 实例
            start_frame: 起始帧号
            end_frame: 结束帧号，None=视频末尾
            progress_cb: 进度回调 (pct: float) -> None
            detected_cb: 匹配成功回调 (timestamp: float, text: str) -> None
            cancel_check: 取消检查 () -> bool
            raw_ocr_cb: 原始 OCR 回调 (timestamp: float, text: str, roi_label: str) -> None

        Returns:
            (list[DetectionResult], DetectionReport) 合并结果和完整报告
        """
        from app.core.player import VideoPlayer

        _run_start = time.time()
        player = VideoPlayer()
        info = player.open(video_path)
        fps = info.fps
        end = end_frame if end_frame is not None else (info.total_frames - 1)
        rois = annotations.to_pixel_rois(info.width, info.height)

        self._config_meta = {
            "mode": self.mode, "interval_sec": self.interval_sec,
            "skip_frames": self.skip_frames,
            "padding_before": self.padding_before, "padding_after": self.padding_after,
            "merge_gap": self.merge_gap,
            "post_detect_skip_sec": self.post_detect_skip_sec,
            "burst_interval_sec": self.burst_interval_sec,
            "burst_window_sec": self.burst_window_sec,
        }
        self._report = self._ensure_report(info, annotations)
        self._report.video_path = video_path

        if not rois:
            player.close()
            return [], self._report

        self._counter.reset()
        self._counter.on_event = None
        stack = EventStackEngine(EventStackConfig(
            wait_after=self._wait_after or {},
            padding_before=self.padding_before,
            padding_after=self.padding_after,
        ))
        self._counter.on_event = stack.push

        if self.mode == "time":
            start_sec = start_frame / fps
            end_sec = end / fps
            frame_list = []
            current_time = start_sec
            while current_time <= end_sec:
                fn = int(current_time * fps)
                if fn > end:
                    break
                frame_list.append(fn)
                current_time += self.interval_sec

            if frame_list:
                prefetcher = FramePrefetcher(video_path, self._detector, rois, fps,
                                             num_workers=max(2, self.skip_frames))
                prefetcher.start()
                for fn in frame_list:
                    prefetcher.submit(fn)

                while True:
                    item = prefetcher.next_result()
                    if item is None:
                        break
                    fn, prepped_rois, timestamp, has_text, is_new = item
                    if not has_text or not prepped_rois:
                        continue
                    if cancel_check and cancel_check():
                        break
                    frame_matched = self._match_prepped(prepped_rois, rois, timestamp, fn, raw_ocr_cb, is_new)
                    if frame_matched:
                        stack.push(SignalEvent(
                            timestamp=timestamp,
                            action=frame_matched.action,
                            actor=frame_matched.actor,
                            pattern_id=frame_matched.pattern_id,
                            raw_text=frame_matched.raw_text,
                            source="text",
                            confidence=frame_matched.confidence,
                            match_strategy=frame_matched.strategy,
                        ))
                        if detected_cb:
                            detected_cb(timestamp, frame_matched.raw_text)

                    if progress_cb:
                        pct = (fn / fps - start_sec) / (end_sec - start_sec) * 100
                        progress_cb(min(pct, 100.0))

                prefetcher.stop()
                del prefetcher
        else:
            frame_list = []
            fn = start_frame
            while fn <= end and not (cancel_check and cancel_check()):
                frame_list.append(fn)
                fn += self.skip_frames
                if progress_cb:
                    pct = (fn - start_frame) / (end - start_frame) * 100
                    progress_cb(min(pct, 100.0))

            if frame_list:
                prefetcher = FramePrefetcher(video_path, self._detector, rois, fps,
                                             num_workers=max(2, self.skip_frames))
                prefetcher.start()
                for fn in frame_list:
                    prefetcher.submit(fn)

                while True:
                    item = prefetcher.next_result()
                    if item is None:
                        break
                    fn, prepped_rois, timestamp, has_text, is_new = item
                    if not has_text or not prepped_rois:
                        continue
                    if cancel_check and cancel_check():
                        break
                    frame_matched = self._match_prepped(prepped_rois, rois, timestamp, fn, raw_ocr_cb, is_new)
                    if frame_matched:
                        stack.push(SignalEvent(
                            timestamp=timestamp,
                            action=frame_matched.action,
                            actor=frame_matched.actor,
                            pattern_id=frame_matched.pattern_id,
                            raw_text=frame_matched.raw_text,
                            source="text",
                            confidence=frame_matched.confidence,
                            match_strategy=frame_matched.strategy,
                        ))
                        if detected_cb:
                            detected_cb(timestamp, frame_matched.raw_text)

                prefetcher.stop()
                del prefetcher

        player.close()
        results = stack.flush()
        results = self._merge_overlapping(results)
        self._build_summary(stack, results, time.time() - _run_start)
        return results, self._report

    def _build_summary(self, stack: EventStackEngine, merged: list, elapsed: float):
        if self._report is None:
            return
        self._frame_logger.populate(self._report)
        s = self._report.summary
        s.elapsed_seconds = elapsed
        s.detections_before_merge = stack.total_pushed
        s.detections_after_merge = len(merged)
        s.counter_total_events = len(self._counter.events)
        s.counter_total_delta = sum(d for _, d in self._counter.events)
        hit_frames: set[int] = set()
        match_frames: set[int] = set()
        drop_counts: dict[str, int] = {}
        for fl in self._report.frames_log:
            s.total_frames = max(s.total_frames, fl.frame_number + 1)
            if fl.ocr_text:
                hit_frames.add(fl.frame_number)
            if fl.match_hit:
                match_frames.add(fl.frame_number)
            if fl.dropped and fl.drop_reason:
                drop_counts[fl.drop_reason] = drop_counts.get(fl.drop_reason, 0) + 1
        s.frames_with_ocr_hits = len(hit_frames)
        s.frames_with_matches = len(match_frames)
        s.dropped_total = sum(drop_counts.values())
        s.dropped_by_reason = drop_counts
        self._report.results = [
            {"start_sec": r.start_sec, "end_sec": r.end_sec,
             "action": r.action, "actor": r.actor,
             "pattern_id": r.pattern_id, "source": r.source}
            for r in merged
        ]

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

    @staticmethod
    def _merge_overlapping(results: list[DetectionResult]) -> list[DetectionResult]:
        """合并时间重叠的片段，消除导出时的重复画面。"""
        if len(results) <= 1:
            return results
        sorted_r = sorted(results, key=lambda r: r.start_sec)
        merged: list[DetectionResult] = [sorted_r[0]]
        for r in sorted_r[1:]:
            last = merged[-1]
            same_key = (r.action == last.action and r.actor == last.actor)
            if r.start_sec <= last.end_sec and same_key:
                merged[-1] = DetectionResult(
                    start_sec=last.start_sec,
                    end_sec=max(last.end_sec, r.end_sec),
                    raw_start_sec=min(last.raw_start_sec, r.raw_start_sec),
                    raw_end_sec=max(last.raw_end_sec, r.raw_end_sec),
                    action=last.action,
                    actor=last.actor,
                    pattern_id=last.pattern_id,
                    match_count=last.match_count + r.match_count,
                    source=last.source,
                    confidence=min(last.confidence, r.confidence),
                    match_strategy="fuzzy" if last.match_strategy == "fuzzy" or r.match_strategy == "fuzzy" else "exact",
                )
            else:
                merged.append(r)
        return merged



# ═══════════════════════════════════════════════════════════════
# Pool 模式: 解耦 CPU/GPU Worker (config pipeline_mode: pool)
# ═══════════════════════════════════════════════════════════════

class CPUWorker(threading.Thread):
    """CPU 工作线程: 视频解码 + 文字门控 + CLAHE 预处理。

    frame_req_queue -> VideoPlayer.seek -> TextPresenceGate
    -> 无文字: push to skip_queue
    -> 有文字: CLAHE all ROIs -> push to prepped_queue"""

    def __init__(self, video_path: str, rois: list, fps: float,
                 detector: "OCRDetector", kill_roi_index: int,
                 frame_req_queue, prepped_queue, result_queue,
                 text_gate: "TextPresenceGate" = None,
                 gate_mode: str = "pixel"):
        super().__init__(daemon=True)
        self._video_path = video_path
        self._rois = rois
        self._fps = fps
        self._detector = detector
        self._kill_roi_index = kill_roi_index
        self._req = frame_req_queue
        self._prepped = prepped_queue
        self._result = result_queue
        self._gate = text_gate or TextPresenceGate()
        self._gate_mode = gate_mode
        self._running = False

    def run(self):
        from app.core.player import VideoPlayer
        player = VideoPlayer()
        try:
            player.open(self._video_path)
        except Exception:
            return
        try:
            self._running = True
            while self._running:
                try:
                    fn = self._req.get(timeout=0.5)
                except Exception:
                    continue
                if fn is None:
                    break
                try:
                    frame = player.seek(fn)
                    ts = fn / max(self._fps, 1)
                    if frame is None:
                        self._result.put((fn, ts, None))
                        continue
                    if self._gate_mode == "neural":
                        prepped = []
                        for r in self._rois:
                            if hasattr(r, 'x'):
                                ri = frame[r.y:r.y + r.h, r.x:r.x + r.w]
                            else:
                                ri = frame[r['y']:r['y'] + r['h'],
                                          r['x']:r['x'] + r['w']]
                            prepped.append((self._detector._preprocess(ri), r))
                        self._prepped.put((fn, prepped, ts, True))
                    else:
                        roi = self._rois[self._kill_roi_index]
                        if hasattr(roi, 'x'):
                            ki = frame[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]
                        else:
                            ki = frame[roi['y']:roi['y'] + roi['h'],
                                       roi['x']:roi['x'] + roi['w']]
                        has_text, is_new = self._gate.check(ki)
                        if not has_text:
                            self._result.put((fn, ts, None))
                            continue
                        prepped = []
                        for r in self._rois:
                            if hasattr(r, 'x'):
                                ri = frame[r.y:r.y + r.h, r.x:r.x + r.w]
                            else:
                                ri = frame[r['y']:r['y'] + r['h'],
                                          r['x']:r['x'] + r['w']]
                            prepped.append((self._detector._preprocess(ri), r))
                        self._prepped.put((fn, prepped, ts, is_new))
                except Exception:
                    self._result.put((fn, 0.0, None))
        finally:
            try:
                player.close()
            except Exception:
                pass

    def stop(self):
        self._running = False
        try:
            self._req.put_nowait(None)
        except Exception:
            pass


class GPUWorker(threading.Thread):
    """GPU 工作线程: OCR 识别 + 关键词匹配。

    prepped_queue -> GPU OCR -> KeywordMatch -> CounterTrack -> TextFusion
    -> FrameResult to result_queue"""

    def __init__(self, detector: "OCRDetector", matcher: "KeywordMatcher",
                 prepped_queue, result_queue,
                 counter: "CounterTracker" = None,
                 counter_event_cb=None,
                 allowed_actors: set = None,
                 raw_ocr_cb=None, logger: "FrameLogger" = None,
                 gate_mode: str = "pixel"):
        super().__init__(daemon=True)
        self._detector = detector
        self._matcher = matcher
        self._prepped_queue = prepped_queue
        self._result_queue = result_queue
        self._counter = counter or CounterTracker()
        if counter_event_cb:
            self._counter.on_event = counter_event_cb
        self.allowed_actors = allowed_actors
        self._raw_ocr_cb = raw_ocr_cb
        self._logger = logger
        self._gate_mode = gate_mode
        self._fusion = TextFusionBuffer()
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            try:
                item = self._prepped_queue.get(timeout=0.5)
            except Exception:
                continue
            if item is None:
                break
            fn, prepped, ts, _is_new = item
            try:
                matched = self._match(prepped, ts, fn)
                self._result_queue.put((fn, ts, matched))
            except Exception:
                self._result_queue.put((fn, ts, None))

    def _match(self, prepped_rois, timestamp, fn):
        if self._gate_mode == "neural":
            for roi_idx, (roi_img, roi) in enumerate(prepped_rois):
                label = (getattr(roi, 'label', '') if hasattr(roi, 'label')
                         else roi.get('label', ''))
                self._matcher.set_roi(label)
                if label == '击杀信息':
                    if not self._detector.has_text(roi_img):
                        if self._logger:
                            self._logger.log_sample(
                                timestamp, label, roi_idx,
                                ocr_text="", stage="gate_neural")
                        return None
                    if self._logger:
                        self._logger.log_sample(
                            timestamp, label, roi_idx,
                            ocr_text="text detected",
                            stage="gate_neural")
                    break
        frame_matched = None
        for roi_idx, (roi_img, roi) in enumerate(prepped_rois):
            label = (getattr(roi, 'label', '') if hasattr(roi, 'label')
                     else roi.get('label', ''))
            self._matcher.set_roi(label)
            if label == '淘汰计数':
                for ocr_r in self._detector.detect_raw(roi_img):
                    old_count = self._counter._count
                    new_count = self._counter.feed(timestamp, ocr_r.text)
                    if self._logger:
                        self._logger.log_counter(
                            timestamp, new_count, new_count - old_count, ocr_r.text)
                    if self._raw_ocr_cb:
                        self._raw_ocr_cb(timestamp, ocr_r.text, label)
            else:
                ocr_results = self._detector.detect_raw(roi_img)
                # 记录所有 OCR 文本
                if self._logger:
                    for ocr_r in ocr_results:
                        self._logger.log_sample(
                            timestamp, label, roi_idx, ocr_r.text, ocr_r.confidence)
                if len(ocr_results) > 1:
                    sorted_r = sorted(ocr_results, key=lambda r: r.bbox[0])
                    joined = "".join(r.text for r in sorted_r)
                    jm = self._matcher.match(joined)
                    if jm and (self.allowed_actors is None or jm.actor in self.allowed_actors):
                        self._fusion.feed(timestamp, joined,
                                          min(r.confidence for r in ocr_results))
                        if self._logger:
                            self._logger.log_sample(
                                timestamp, label, roi_idx, joined,
                                min(r.confidence for r in ocr_results),
                                match_hit=True, match_pattern_id=jm.pattern_id,
                                match_action=jm.action, match_actor=jm.actor,
                                stage="match")
                        if self._raw_ocr_cb:
                            for ocr_r in ocr_results:
                                self._raw_ocr_cb(timestamp, ocr_r.text, label)
                        if frame_matched is None:
                            frame_matched = jm
                        continue
                for ocr_r in ocr_results:
                    if self._raw_ocr_cb:
                        self._raw_ocr_cb(timestamp, ocr_r.text, label)
                    match = self._matcher.match(ocr_r.text)
                    if match:
                        self._fusion.feed(timestamp, ocr_r.text, ocr_r.confidence)
                        if (self.allowed_actors is not None
                                and match.actor not in self.allowed_actors):
                            continue
                        if self._logger:
                            self._logger.log_sample(
                                timestamp, label, roi_idx, ocr_r.text, ocr_r.confidence,
                                match_hit=True, match_pattern_id=match.pattern_id,
                                match_action=match.action, match_actor=match.actor,
                                stage="match")
                        if frame_matched is None:
                            frame_matched = match
                    else:
                        fused = self._fusion.feed(timestamp, ocr_r.text,
                                                  ocr_r.confidence)
                        if fused:
                            fm = self._matcher.match(fused)
                            if fm:
                                if (self.allowed_actors is not None
                                        and fm.actor not in self.allowed_actors):
                                    continue
                                if self._logger:
                                    self._logger.log_sample(
                                        timestamp, label, roi_idx, fused, 0.0,
                                        match_hit=True, match_pattern_id=fm.pattern_id,
                                        match_action=fm.action, match_actor=fm.actor,
                                        stage="fusion")
                                if frame_matched is None:
                                    frame_matched = fm
        return frame_matched

    def stop(self):
        self._running = False
        try:
            self._prepped_queue.put_nowait(None)
        except Exception:
            pass


class DetectionPipeline:
    """解耦流水线: CPU Pool -> GPU Pool -> 按 fn 排序 -> EventStack。

    替代 DetectionEngine.run_full() 的调度逻辑。"""

    def __init__(self, matcher, detector,
                 cpu_workers: int = 3, gpu_workers: int = 2,
                 padding_before: float = 10.0, padding_after: float = 10.0,
                 merge_gap: float = 30.0,
                 allowed_actors: set = None,
                 wait_after: dict = None,
                 skip_frames: int = 60,
                 mode: str = "time", interval_sec: float = 1.0,
                 gate_mode: str = "pixel",
                 refine_boundaries: bool = False,
                 refine_search_window: float = 2.0,
                 cell_divide: bool = False,
                 cell_min_gap: float = 2.0):
        self._matcher = matcher
        self._detector = detector
        self._cpu_n = cpu_workers
        self._gpu_n = gpu_workers
        self.padding_before = padding_before
        self.padding_after = padding_after
        self.merge_gap = merge_gap
        self.allowed_actors = allowed_actors
        self._wait_after = wait_after
        self._skip_frames = skip_frames
        self._mode = mode
        self._interval_sec = interval_sec
        self._gate_mode = gate_mode
        self._refine_boundaries = refine_boundaries
        self._refine_search_window = refine_search_window
        self._cell_divide = cell_divide
        self._cell_min_gap = cell_min_gap
        self._config_meta: dict = {}

    def run_full(self, video_path: str, annotations,
                 start_frame: int = 0, end_frame: int = None,
                 progress_cb=None, detected_cb=None,
                 raw_ocr_cb=None, cancel_check=None,
                 gap_progress_cb=None):
        import queue
        from app.core.player import VideoPlayer

        _log = logging.getLogger("app.core.detector.pipeline")
        _t0 = time.time()
        _log.debug("run_full 入口: video=%s, start=%d, end=%s, cpu=%d, gpu=%d",
                   video_path, start_frame, end_frame, self._cpu_n, self._gpu_n)
        player = VideoPlayer()
        info = player.open(video_path)
        fps = info.fps
        end = end_frame or (info.total_frames - 1)
        rois = annotations.to_pixel_rois(info.width, info.height)
        player.close()
        _log.debug("视频信息: %dx%d, %.2ffps, %d frames, %d rois",
                   info.width, info.height, fps, info.total_frames, len(rois))

        self._config_meta = {
            "pipeline": "pool", "cpu_workers": self._cpu_n,
            "gpu_workers": self._gpu_n,
            "padding_before": self.padding_before,
            "padding_after": self.padding_after,
            "merge_gap": self.merge_gap,
            "mode": self._mode, "interval_sec": self._interval_sec,
            "skip_frames": self._skip_frames,
            "gate_mode": self._gate_mode,
            "refine_boundaries": self._refine_boundaries,
            "refine_search_window": self._refine_search_window,
            "cell_divide": self._cell_divide,
            "cell_min_gap": self._cell_min_gap,
        }

        if not rois:
            _log.warning("run_full: 无 ROI，返回空")
            return [], None

        kill_idx = 0
        for i, r in enumerate(rois):
            label = (getattr(r, 'label', '') if hasattr(r, 'label')
                     else r.get('label', ''))
            if label == '击杀信息':
                kill_idx = i
                break

        # 单队列: CPU no-text → result_q, GPU OCR → result_q, 阻塞 get
        frame_req = queue.Queue()
        prepped_q = queue.Queue()
        result_q = queue.Queue()

        cpu_pool = [CPUWorker(video_path, rois, fps, self._detector,
                              kill_idx, frame_req, prepped_q, result_q,
                              gate_mode=self._gate_mode)
                    for _ in range(self._cpu_n)]
        for w in cpu_pool:
            w.start()

        stack = EventStackEngine(EventStackConfig(
            wait_after=self._wait_after or {},
            padding_before=self.padding_before,
            padding_after=self.padding_after,
        ))

        frame_logger = FrameLogger(video_fps=fps)
        gpu_pool = [GPUWorker(self._detector, self._matcher,
                              prepped_q, result_q,
                              CounterTracker() if i == 0 else None,
                              counter_event_cb=stack.push if i == 0 else None,
                              allowed_actors=self.allowed_actors,
                              raw_ocr_cb=raw_ocr_cb,
                              logger=frame_logger,
                              gate_mode=self._gate_mode)
                    for i in range(self._gpu_n)]
        for w in gpu_pool:
            w.start()

        # 帧列表: 按 skip_frames 步进 (匹配 legacy frame-mode)
        frame_nos = []
        fn = start_frame
        step = int(fps * self._interval_sec) if self._mode == "time" else self._skip_frames
        while fn <= end:
            if cancel_check and cancel_check():
                break
            frame_nos.append(fn)
            fn += step

        total = len(frame_nos)
        pending: dict[int, tuple] = {}
        next_idx = 0

        # 一次性提交
        for fn in frame_nos:
            frame_req.put(fn)
            if cancel_check and cancel_check():
                break

        if progress_cb:
            progress_cb(0)

        # 单队列阻塞收集 (像 legacy next_result())
        collected = 0
        while collected < total:
            if cancel_check and cancel_check():
                break
            fn_r, ts, matched = result_q.get()  # blocking
            pending[fn_r] = (fn_r, ts, matched)
            collected += 1

            while next_idx < total:
                fn = frame_nos[next_idx]
                if fn not in pending:
                    break
                _, ts, matched = pending.pop(fn)
                if matched:
                    stack.push(SignalEvent(
                        timestamp=ts, action=matched.action,
                        actor=matched.actor,
                        pattern_id=matched.pattern_id,
                        raw_text=matched.raw_text, source="text",
                        confidence=matched.confidence,
                        match_strategy=matched.strategy))
                    if detected_cb:
                        detected_cb(ts, matched.raw_text)
                next_idx += 1

            if progress_cb:
                pct = (next_idx / total) * 100
                progress_cb(min(pct, 85.0))

        for w in cpu_pool:
            w.stop()
        for w in cpu_pool:
            w.stop()
        for w in gpu_pool:
            w.stop()

        results = stack.flush()
        results = DetectionEngine._merge_overlapping(results)

        # ── 后处理: gap_binary_search 精确边界 (85-95%) ──
        if self._cell_divide and results:
            from app.core.coarse_to_fine import gap_binary_search, _extract_coarse_hits
            _log.info("细胞分裂(gap): 从 FrameLogger 提取粗扫命中...")
            coarse_hits = _extract_coarse_hits(frame_logger)
            _log.info("细胞分裂(gap): 提取到 %d 个粗扫命中点", len(coarse_hits))
            if coarse_hits:
                precise = gap_binary_search(
                    video_path, rois, kill_idx, fps,
                    coarse_hits,
                    search_window=self._refine_search_window,
                    cancel_check=cancel_check,
                    raw_ocr_cb=raw_ocr_cb,
                    progress_cb=gap_progress_cb,
                )
                _log.info("细胞分裂(gap): 完成 (%d 个精确事件)", len(precise))
                # 添加 padding
                for r in precise:
                    r.raw_start_sec = r.start_sec
                    r.raw_end_sec = r.end_sec
                    r.start_sec = max(0.0, r.start_sec - self.padding_before)
                    r.end_sec = r.end_sec + self.padding_after
                # 合并重叠或相近的事件
                precise.sort(key=lambda r: r.start_sec)
                results = []
                for r in precise:
                    merged = False
                    for i in range(len(results) - 1, -1, -1):
                        if results[i].action == r.action and results[i].actor == r.actor:
                            if r.start_sec - results[i].end_sec <= self.merge_gap:
                                results[i].end_sec = max(results[i].end_sec, r.end_sec)
                                merged = True
                            break
                    if not merged:
                        results.append(r)
                _log.info("细胞分裂(gap): padding+合并后 %d 个片段", len(results))
                if detected_cb:
                    for r in precise:
                        detected_cb(r.start_sec, f"{r.actor}:{r.action}")
            else:
                _log.warning("细胞分裂(gap): 无粗扫命中点，跳过")

        # ── 边界精化 (仅当 cell_divide 关闭但 refine 开启时) ──
        if self._refine_boundaries and results and not self._cell_divide:
            from app.core.coarse_to_fine import BoundaryRefiner
            _log.info("边界精化: 开始 (%d 个片段)", len(results))
            refiner = BoundaryRefiner(video_path, rois, kill_idx)
            n = len(results)
            refined = []
            for i, r in enumerate(results):
                if cancel_check and cancel_check():
                    refined.append(r); continue
                refined.append(refiner.refine(r, fps,
                    search_window=self._refine_search_window))
                if progress_cb:
                    progress_cb(85 + (i + 1) / max(n, 1) * 10)
            results = refined
            _log.info("边界精化: 完成 (%d 个片段)", len(results))

        elapsed = time.time() - _t0
        report = DetectionReport(
            video_path=video_path,
            video_width=info.width, video_height=info.height,
            video_fps=fps,
            video_duration_sec=info.total_frames / max(fps, 1),
            config=self._config_meta,
            rois=[{"id": i, "label": getattr(r, 'label', str(r))}
                  for i, r in enumerate(rois)],
        )
        frame_logger.populate(report)
        s = report.summary
        s.total_frames = total
        s.elapsed_seconds = elapsed
        s.detections_before_merge = stack.total_pushed
        s.detections_after_merge = len(results)
        report.results = [
            {"start_sec": r.start_sec, "end_sec": r.end_sec,
             "action": r.action, "actor": r.actor,
             "pattern_id": r.pattern_id, "source": r.source}
            for r in results
        ]
        if progress_cb:
            progress_cb(100)
        _log.info("run_full 完成: %d results, %.1fs elapsed, pushed=%d",
                  len(results), elapsed, stack.total_pushed)
        return results, report
