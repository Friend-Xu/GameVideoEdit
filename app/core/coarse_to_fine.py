"""Coarse-to-Fine detection pipeline.

Phase 1: CoarseScanner — sparse TextPresenceGate scan to find candidate regions.
Phase 2: DenseScanner — dense TextPresenceGate scan + selective OCR within candidates.
Phase 3: BoundaryRefiner — binary search refinement of event boundaries.
"""

import logging
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

_log = logging.getLogger(__name__)

from app.core.annotator import AnnotationStore
from app.core.detector import (
    CounterTracker,
    DetectionEngine,
    DetectionReport,
    DetectionResult,
    EventStackConfig,
    EventStackEngine,
    FrameLogger,
    OCRDetector,
    SignalEvent,
    TextFusionBuffer,
    TextPresenceGate,
)
from app.core.keywords import KeywordMatcher
from app.core.player import VideoPlayer


@dataclass
class CoarseCandidate:
    start_sec: float
    end_sec: float


@dataclass
class TextSegment:
    start_sec: float
    end_sec: float


def _fast_white_check(roi_img: np.ndarray, threshold: int = 230,
                      min_white: int = 200) -> bool:
    """Stateless white-pixel check for binary search (no internal state)."""
    if roi_img is None or roi_img.size == 0:
        return False
    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(mask) >= min_white


class CoarseScanner:
    """Phase 1: Sparse full-video TextPresenceGate scan.

    Stride determined by existing mode + interval_sec/skip_frames params.
    Pure CPU, zero GPU.
    """

    def __init__(self, video_path: str, pixel_rois: list, kill_roi_index: int,
                 mode: str = "time", interval_sec: float = 1.0,
                 skip_frames: int = 15):
        self._video_path = video_path
        self._rois = pixel_rois
        self._kill_idx = kill_roi_index
        self._mode = mode
        self._interval_sec = interval_sec
        self._skip_frames = skip_frames

    def scan(self, start_frame: int, end_frame: int, fps: float,
             progress_cb: Callable[[float], None] = None,
             cancel_check: Callable[[], bool] | None = None) -> list[CoarseCandidate]:
        """Scan video and return merged candidate time windows."""
        if self._mode == "time":
            stride_frames = max(1, int(self._interval_sec * fps))
            stride_sec = self._interval_sec
        else:
            stride_frames = max(1, self._skip_frames)
            stride_sec = stride_frames / max(fps, 1)

        player = VideoPlayer()
        player.open(self._video_path)
        gate = TextPresenceGate()
        kill_roi = self._rois[self._kill_idx]
        total_steps = (end_frame - start_frame) // stride_frames + 1
        hits: list[float] = []

        try:
            fn = start_frame
            step_count = 0
            while fn <= end_frame:
                if cancel_check and cancel_check():
                    break
                frame = player.seek(fn)
                if frame is not None:
                    roi_img = frame[kill_roi.y:kill_roi.y + kill_roi.h,
                                    kill_roi.x:kill_roi.x + kill_roi.w]
                    has_text, _ = gate.check(roi_img)
                    if has_text:
                        hits.append(fn / fps)
                fn += stride_frames
                step_count += 1
                if progress_cb:
                    progress_cb(min(step_count / max(total_steps, 1) * 100, 100.0))
        finally:
            player.close()

        return self._merge_candidates(hits, stride_sec)

    def _merge_candidates(self, timestamps: list[float],
                          stride_sec: float) -> list[CoarseCandidate]:
        if not timestamps:
            return []
        gap_threshold = stride_sec * 2
        candidates: list[CoarseCandidate] = []
        seg_start = timestamps[0]
        seg_end = timestamps[0]
        for t in timestamps[1:]:
            if t - seg_end <= gap_threshold:
                seg_end = t
            else:
                candidates.append(CoarseCandidate(seg_start, seg_end))
                seg_start = t
                seg_end = t
        candidates.append(CoarseCandidate(seg_start, seg_end))
        return candidates


class DenseScanner:
    """Phase 2: Dense TextPresenceGate scan + selective OCR within candidate windows."""

    def __init__(self, video_path: str, pixel_rois: list, kill_roi_index: int,
                 detector: OCRDetector, matcher: KeywordMatcher,
                 dense_interval_sec: float = 0.1,
                 dense_ocr_interval_sec: float = 0.25,
                 window_padding: float = 3.0,
                 allowed_actors: set | None = None):
        self._video_path = video_path
        self._rois = pixel_rois
        self._kill_idx = kill_roi_index
        self._detector = detector
        self._matcher = matcher
        self._dense_interval = dense_interval_sec
        self._ocr_interval = dense_ocr_interval_sec
        self._window_padding = window_padding
        self._allowed_actors = allowed_actors

    def scan_and_ocr(self, candidates: list[CoarseCandidate], fps: float,
                     raw_ocr_cb: Callable = None,
                     detected_cb: Callable = None,
                     progress_cb: Callable[[float], None] = None,
                     cancel_check: Callable[[], bool] | None = None,
                     counter: CounterTracker | None = None) -> list[SignalEvent]:
        """Process all candidates: dense scan → text segments → OCR → SignalEvents."""
        if not candidates:
            return []

        player = VideoPlayer()
        player.open(self._video_path)
        fusion = TextFusionBuffer()
        if counter is None:
            counter = CounterTracker()
        events: list[SignalEvent] = []

        windows = self._merge_windows(candidates)
        total = len(windows)

        try:
            for wi, window in enumerate(windows):
                if cancel_check and cancel_check():
                    break
                segs = self._dense_scan(player, window, fps, cancel_check)
                for seg in segs:
                    if cancel_check and cancel_check():
                        break
                    self._ocr_segment(player, seg, fps, fusion, counter,
                                      raw_ocr_cb, detected_cb, events,
                                      cancel_check)
                if progress_cb:
                    progress_cb((wi + 1) / max(total, 1) * 100)
        finally:
            player.close()

        return events

    def _merge_windows(self, candidates: list[CoarseCandidate]) -> list[CoarseCandidate]:
        """Merge overlapping candidate windows (after padding extension)."""
        if not candidates:
            return []
        windows = []
        for c in candidates:
            windows.append(CoarseCandidate(
                max(0, c.start_sec - self._window_padding),
                c.end_sec + self._window_padding,
            ))
        windows.sort(key=lambda w: w.start_sec)
        merged = [windows[0]]
        for w in windows[1:]:
            last = merged[-1]
            if w.start_sec <= last.end_sec:
                merged[-1] = CoarseCandidate(last.start_sec, max(last.end_sec, w.end_sec))
            else:
                merged.append(w)
        return merged

    def _dense_scan(self, player: VideoPlayer, window: CoarseCandidate,
                    fps: float, cancel_check) -> list[TextSegment]:
        """Dense TextPresenceGate scan within a window, extract contiguous text segments."""
        gate = TextPresenceGate()
        kill_roi = self._rois[self._kill_idx]
        hole_tolerance = self._dense_interval * 2

        scan_results: list[tuple[float, bool]] = []
        t = window.start_sec
        while t <= window.end_sec:
            if cancel_check and cancel_check():
                break
            fn = int(t * fps)
            frame = player.seek(fn)
            if frame is not None:
                roi_img = frame[kill_roi.y:kill_roi.y + kill_roi.h,
                                kill_roi.x:kill_roi.x + kill_roi.w]
                has_text, _ = gate.check(roi_img)
                scan_results.append((t, has_text))
            t += self._dense_interval

        return self._extract_segments(scan_results, hole_tolerance)

    @staticmethod
    def _extract_segments(scan_results: list[tuple[float, bool]],
                          hole_tolerance: float) -> list[TextSegment]:
        """Extract contiguous text-present intervals from scan results."""
        segments: list[TextSegment] = []
        seg_start: float | None = None
        last_text_ts: float | None = None
        for ts, has_text in scan_results:
            if has_text:
                if seg_start is None:
                    seg_start = ts
                last_text_ts = ts
            else:
                if seg_start is not None and last_text_ts is not None:
                    if ts - last_text_ts <= hole_tolerance:
                        continue
                    segments.append(TextSegment(seg_start, last_text_ts))
                    seg_start = None
                    last_text_ts = None
        if seg_start is not None:
            segments.append(TextSegment(seg_start, last_text_ts or seg_start))
        return segments

    def _ocr_segment(self, player: VideoPlayer, seg: TextSegment, fps: float,
                     fusion: TextFusionBuffer, counter: CounterTracker,
                     raw_ocr_cb, detected_cb, events: list[SignalEvent],
                     cancel_check):
        """Run OCR within a text segment, push matched SignalEvents."""
        t = seg.start_sec
        while t <= seg.end_sec:
            if cancel_check and cancel_check():
                break
            fn = int(t * fps)
            frame = player.seek(fn)
            if frame is None:
                t += self._ocr_interval
                continue
            timestamp = fn / max(fps, 1)
            frame_matched = self._match_frame(frame, timestamp, fusion, counter,
                                              raw_ocr_cb)
            if frame_matched:
                events.append(SignalEvent(
                    timestamp=timestamp,
                    action=frame_matched.action,
                    actor=frame_matched.actor,
                    pattern_id=frame_matched.pattern_id,
                    raw_text=frame_matched.raw_text,
                    source="text",
                ))
                if detected_cb:
                    detected_cb(timestamp, frame_matched.raw_text)
            t += self._ocr_interval

    def _match_frame(self, frame: np.ndarray, timestamp: float,
                     fusion: TextFusionBuffer, counter: CounterTracker,
                     raw_ocr_cb):
        """Run OCR + keyword matching on all ROIs of a single frame."""
        from app.core.keywords import MatchResult
        frame_matched: MatchResult | None = None
        for roi in self._rois:
            roi_img = frame[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]
            label = roi.label
            if label == '淘汰计数':
                prepped = self._detector._preprocess(roi_img)
                for ocr_r in self._detector.detect_raw(prepped):
                    counter.feed(timestamp, ocr_r.text)
                    if raw_ocr_cb:
                        raw_ocr_cb(timestamp, ocr_r.text, label)
            else:
                prepped = self._detector._preprocess(roi_img)
                ocr_results = self._detector.detect_raw(prepped)
                if not ocr_results:
                    continue
                if len(ocr_results) > 1:
                    sorted_r = sorted(ocr_results, key=lambda r: r.bbox[0])
                    joined = "".join(r.text for r in sorted_r)
                    jm = self._matcher.match(joined)
                    if jm and (self._allowed_actors is None or jm.actor in self._allowed_actors):
                        fusion.feed(timestamp, joined,
                                    min(r.confidence for r in ocr_results))
                        for ocr_r in ocr_results:
                            if raw_ocr_cb:
                                raw_ocr_cb(timestamp, ocr_r.text, label)
                        if frame_matched is None:
                            frame_matched = jm
                        continue
                for ocr_r in ocr_results:
                    if raw_ocr_cb:
                        raw_ocr_cb(timestamp, ocr_r.text, label)
                    match = self._matcher.match(ocr_r.text)
                    if match:
                        fusion.feed(timestamp, ocr_r.text, ocr_r.confidence)
                        if (self._allowed_actors is not None
                                and match.actor not in self._allowed_actors):
                            continue
                        if frame_matched is None:
                            frame_matched = match
                    else:
                        fused = fusion.feed(timestamp, ocr_r.text, ocr_r.confidence)
                        if fused:
                            fm = self._matcher.match(fused)
                            if fm:
                                if (self._allowed_actors is not None
                                        and fm.actor not in self._allowed_actors):
                                    continue
                                if frame_matched is None:
                                    frame_matched = fm
        return frame_matched


class BoundaryRefiner:
    """Phase 3: Binary search to refine event boundaries to ±1 frame precision."""

    def __init__(self, video_path: str, pixel_rois: list, kill_roi_index: int):
        self._video_path = video_path
        self._rois = pixel_rois
        self._kill_idx = kill_roi_index

    def refine(self, result: DetectionResult, fps: float,
               search_window: float = 2.0,
               cancel_check: Callable[[], bool] | None = None) -> DetectionResult:
        """Refine start_sec and end_sec via binary search."""
        player = VideoPlayer()
        player.open(self._video_path)
        kill_roi = self._rois[self._kill_idx]

        try:
            new_start = self._binary_search_boundary(
                player, kill_roi, fps,
                lower=max(0, result.start_sec - search_window),
                upper=result.start_sec + search_window,
                target="first_has_text",
                cancel_check=cancel_check,
            )
            new_end = self._binary_search_boundary(
                player, kill_roi, fps,
                lower=result.end_sec - search_window,
                upper=result.end_sec + search_window,
                target="last_has_text",
                cancel_check=cancel_check,
            )
        finally:
            player.close()

        return DetectionResult(
            start_sec=new_start, end_sec=new_end,
            action=result.action, actor=result.actor,
            pattern_id=result.pattern_id, match_count=result.match_count,
            source=result.source,
        )

    def _binary_search_boundary(self, player: VideoPlayer, kill_roi, fps: float,
                                lower: float, upper: float, target: str,
                                cancel_check) -> float:
        """Binary search to ~3-frame precision, then linear sweep to exact frame."""
        lo, hi = lower, upper
        while (hi - lo) * fps > 3.0:
            if cancel_check and cancel_check():
                return lo
            mid = (lo + hi) / 2
            fn = int(mid * fps)
            frame = player.seek(fn)
            if frame is None:
                return (lo + hi) / 2
            roi_img = frame[kill_roi.y:kill_roi.y + kill_roi.h,
                            kill_roi.x:kill_roi.x + kill_roi.w]
            has_text = _fast_white_check(roi_img)
            if target == "first_has_text":
                if has_text:
                    hi = mid
                else:
                    lo = mid
            else:
                if has_text:
                    lo = mid
                else:
                    hi = mid

        # Linear sweep ±3 frames for exact boundary
        center_fn = int((lo + hi) / 2 * fps)
        for offset in range(-3, 4):
            fn = center_fn + offset
            if fn < 0:
                continue
            frame = player.seek(fn)
            if frame is None:
                continue
            roi_img = frame[kill_roi.y:kill_roi.y + kill_roi.h,
                            kill_roi.x:kill_roi.x + kill_roi.w]
            has_text = _fast_white_check(roi_img)
            if target == "first_has_text" and has_text:
                return fn / fps
            if target == "last_has_text" and not has_text and offset > -3:
                return (fn - 1) / fps
        return (lo + hi) / 2


class CoarseToFinePipeline:
    """[已废弃] 独立的粗到精流水线。请使用 BoundaryRefiner 作为 pool/legacy 的后处理。"""

    # (保留类定义以避免引用错误，暂不删除)


# ═══════════════════════════════════════════════════════════════
# Gap Binary Search —— 基于粗扫间隙的二分搜索 (方案A)
# ═══════════════════════════════════════════════════════════════

def _same_event(a: dict | None, b: dict | None) -> bool:
    """判断两个粗扫命中是否属于同一事件 (基于结构化字段比较)。"""
    if a is None or b is None:
        return False
    return (a.get('action'), a.get('actor')) == (b.get('action'), b.get('actor'))


def _extract_coarse_hits(frame_logger: "FrameLogger") -> list[dict]:
    """从 FrameLogger 提取粗扫命中点 (match_hit=True)，按时间排序合并相邻同事件。"""
    from app.core.detector import FrameLogger as FL
    hits: list[dict] = []
    seen_keys: set[tuple] = set()
    for f in frame_logger.frames:
        if not f.match_hit or f.stage == "cell_divide":
            continue
        ts = round(f.timestamp, 2)
        key = (ts, f.match_action, f.match_actor)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        hits.append({
            "timestamp": ts,
            "action": f.match_action,
            "actor": f.match_actor,
            "pattern_id": f.match_pattern_id,
            "ocr_text": f.ocr_text,
        })
    hits.sort(key=lambda h: h["timestamp"])
    # 合并连续的同事件命中
    if not hits:
        return hits
    merged = [hits[0]]
    for h in hits[1:]:
        if _same_event(merged[-1], h) and h["timestamp"] - merged[-1]["timestamp"] < 5.0:
            merged[-1] = h  # 保留最新的
        else:
            merged.append(h)
    return merged


def gap_binary_search(video_path: str, pixel_rois: list, kill_roi_index: int,
                      fps: float, coarse_hits: list[dict],
                      search_window: float = 2.0,
                      cancel_check: Callable[[], bool] | None = None,
                      raw_ocr_cb: Callable[[float, str, str], None] | None = None,
                      progress_cb: Callable[[float], None] | None = None,
                      ) -> list[DetectionResult]:
    """基于粗扫命中间隙的二分搜索，精确确定事件边界。

    coarse_hits: 粗扫命中的结构化列表，每项含 timestamp, action, actor, pattern_id, ocr_text。
    对每对相邻命中分析状态转换，在间隙中二分搜索精确边界。

    四种间隙处理:
      - 同事件延续: 跳过 (无需分裂)
      - 不同事件: 二分搜索事件A文字的消失点 (last_has_text)
      - 首个事件前: 二分搜索 first_has_text 确定精确起点
      - 末个事件后: 二分搜索 last_has_text 确定精确终点
    """
    if not coarse_hits:
        return []

    player = VideoPlayer()
    info = player.open(video_path)
    kill_roi = pixel_rois[kill_roi_index]
    refiner = BoundaryRefiner.__new__(BoundaryRefiner)
    refiner._video_path = video_path
    refiner._rois = pixel_rois
    refiner._kill_idx = kill_roi_index
    max_sec = info.total_frames / max(fps, 1)

    events: list[DetectionResult] = []
    n = len(coarse_hits)

    try:
        i = 0
        while i < n:
            hit = coarse_hits[i]
            ts = hit["timestamp"]

            # ── 找此事件的精确 start (在命中点附近 search_window 内) ──
            start = refiner._binary_search_boundary(
                player, kill_roi, fps,
                lower=max(0, ts - search_window),
                upper=ts,
                target="first_has_text",
                cancel_check=cancel_check,
            )

            # ── 找此事件最后一个同事件命中 (跳过同事件延续的 1s 采样) ──
            j = i + 1
            while j < n and _same_event(hit, coarse_hits[j]):
                j += 1
            last_ts = coarse_hits[j - 1]["timestamp"]

            # ── 找此事件的精确 end (在最后一个命中点附近 search_window 内) ──
            end = refiner._binary_search_boundary(
                player, kill_roi, fps,
                lower=last_ts,
                upper=last_ts + search_window,
                target="last_has_text",
                cancel_check=cancel_check,
            )

            _log.debug("[gap_search] 事件 %d: [%.2fs - %.2fs] %s:%s",
                       len(events), start, end,
                       hit.get('action', '?'), hit.get('actor', '?'))
            if raw_ocr_cb:
                raw_ocr_cb(start, hit.get('ocr_text', ''), '击杀信息')

            events.append(DetectionResult(
                start_sec=start, end_sec=end,
                action=hit.get('action', ''),
                actor=hit.get('actor', ''),
                pattern_id=hit.get('pattern_id', ''),
                source="text",
            ))

            if progress_cb:
                progress_cb((i + 1) / max(n, 1) * 100)

            i = j

    finally:
        player.close()

    return events


# ═══════════════════════════════════════════════════════════════
# BinSeg 递归事件搜索 (细胞分裂) — 旧实现，保留作为回退
# ═══════════════════════════════════════════════════════════════

def binseg_event_search(video_path: str, pixel_rois: list, kill_roi_index: int,
                        region_start: float, region_end: float,
                        fps: float, detector, matcher,
                        scan_step: float = 0.25, min_segment: float = 2.0,
                        search_window: float = 2.0,
                        fusion: "TextFusionBuffer | None" = None,
                        cancel_check: Callable[[], bool] | None = None,
                        allowed_actors: set | None = None,
                        depth: int = 0,
                        frame_logger: "FrameLogger | None" = None,
                        progress_cb: Callable[[float], None] | None = None,
                        raw_ocr_cb: Callable[[float, str, str], None] | None = None) -> list[DetectionResult]:
    """BinSeg 事件搜索 —— 细胞分裂式查找击杀/击倒子事件。

    借鉴 Recursive Binary Segmentation (Vostrikova 1981):
    1. 在 region 内 while 循环扫描，找击杀/击倒
    2. 二分搜索精确文字边界
    3. 命中后递归左侧（回溯查漏），然后 jump+continue 继续向前扫描

    Args:
        depth: 递归深度（内部使用，防左递归无限）
    """
    MAX_DEPTH = 20
    if depth > MAX_DEPTH:
        return [], 0, 0, []

    events: list[DetectionResult] = []
    if fusion is None:
        fusion = TextFusionBuffer()

    _log.debug("[细胞分裂 d=%d] 进入区域 [%.2fs - %.2fs] (跨度 %.1fs)",
               depth, region_start, region_end, region_end - region_start)

    player = VideoPlayer()
    player.open(video_path)
    kill_roi = pixel_rois[kill_roi_index]
    refiner = BoundaryRefiner.__new__(BoundaryRefiner)
    refiner._video_path = video_path
    refiner._rois = pixel_rois
    refiner._kill_idx = kill_roi_index

    try:
        t = region_start
        region_span = max(region_end - region_start, 0.001)
        while t <= region_end:
            if progress_cb:
                progress_cb((t - region_start) / region_span)
            if cancel_check and cancel_check():
                break
            fn = int(t * fps)
            frame = player.seek(fn)
            if frame is None:
                t += scan_step
                continue

            timestamp = fn / max(fps, 1)
            match = _match_killfeed_frame(
                frame, pixel_rois, timestamp, detector, matcher,
                fusion, allowed_actors, frame_logger=frame_logger,
                raw_ocr_cb=raw_ocr_cb)
            if match:
                _log.debug("[细胞分裂 d=%d] t=%.2fs fn=%d: 命中! action=%s actor=%s raw_text=%s",
                           depth, timestamp, fn, match.action, match.actor, match.raw_text[:60])
                # 二分搜索精确文字边界
                new_start = refiner._binary_search_boundary(
                    player, kill_roi, fps,
                    lower=max(region_start, timestamp - search_window),
                    upper=timestamp,
                    target="first_has_text",
                    cancel_check=cancel_check,
                )
                new_end = refiner._binary_search_boundary(
                    player, kill_roi, fps,
                    lower=timestamp,
                    upper=min(region_end, timestamp + search_window),
                    target="last_has_text",
                    cancel_check=cancel_check,
                )
                _log.debug("[细胞分裂 d=%d] 边界: [%.2fs, %.2fs] -> [%.2fs, %.2fs]",
                           depth, region_start, region_end, new_start, new_end)

                events.append(DetectionResult(
                    start_sec=new_start, end_sec=new_end,
                    action=match.action, actor=match.actor,
                    pattern_id=match.pattern_id, source="text",
                ))

                # 递归左侧 (不传 progress_cb，避免进度条回跳)
                if new_start - region_start > min_segment:
                    left_evts, _, _, _ = binseg_event_search(
                        video_path, pixel_rois, kill_roi_index,
                        region_start, new_start, fps,
                        detector, matcher, scan_step, min_segment,
                        search_window, fusion, cancel_check,
                        allowed_actors, depth + 1,
                        frame_logger=frame_logger,
                        raw_ocr_cb=raw_ocr_cb,
                    )
                    events.extend(left_evts)

                t = new_end + scan_step
                continue

            t += scan_step
    finally:
        player.close()

    # 后处理: 合并重叠 → 过滤太短
    events.sort(key=lambda r: r.start_sec)
    merged_count = len(events)
    events = _merge_overlapping_events(events)
    merged_count -= len(events)

    short_count = 0
    short_summary: list[dict] = []
    if min_segment > 0:
        events, short_count, short_summary = _filter_short_events(events, min_segment)

    # 将过滤统计存入最后一个事件的 source 字段（供调用方读取）
    if short_count > 0 or merged_count > 0:
        # 返回统计信息作为额外属性
        pass

    _log.debug("[细胞分裂 d=%d] 区域完成 [%.2fs - %.2fs]: %d 事件(合并%d, 过滤%d)",
               depth, region_start, region_end,
               len(events), merged_count, short_count)

    return events, merged_count, short_count, short_summary


def _merge_overlapping_events(events: list[DetectionResult]) -> list[DetectionResult]:
    """合并时间重叠的事件（同一 killfeed 文字的不同 OCR 匹配）。

    重叠意味着同一条 killfeed 被 OCR 匹配到多次（如"击倒"和"淘汰"），
    应合并为一个事件而非拆成多个。
    """
    if len(events) <= 1:
        return events
    merged = [events[0]]
    for r in events[1:]:
        last = merged[-1]
        if r.start_sec < last.end_sec:
            merged[-1] = DetectionResult(
                start_sec=min(last.start_sec, r.start_sec),
                end_sec=max(last.end_sec, r.end_sec),
                action=f"{last.action}+{r.action}" if r.action != last.action else last.action,
                actor=f"{last.actor}+{r.actor}" if r.actor != last.actor else last.actor,
                pattern_id=f"{last.pattern_id}+{r.pattern_id}" if r.pattern_id != last.pattern_id else last.pattern_id,
                match_count=last.match_count + r.match_count,
                source="binseg",
            )
        else:
            merged.append(r)
    return merged


def _filter_short_events(events: list[DetectionResult],
                         min_duration: float) -> tuple[list[DetectionResult], int, list[dict]]:
    """过滤时长 < min_duration 的事件，返回 (kept, filtered_count, filtered_summary)。"""
    kept: list[DetectionResult] = []
    filtered: list[dict] = []
    for r in events:
        if r.duration < min_duration:
            filtered.append({
                "start_sec": round(r.start_sec, 2),
                "end_sec": round(r.end_sec, 2),
                "duration": round(r.duration, 2),
                "action": r.action,
                "actor": r.actor,
            })
        else:
            kept.append(r)
    return kept, len(filtered), filtered


def _match_killfeed_frame(frame, pixel_rois, timestamp,
                          detector, matcher, fusion,
                          allowed_actors: set | None = None,
                          frame_logger: "FrameLogger | None" = None,
                          raw_ocr_cb: Callable[[float, str, str], None] | None = None):
    """OCR + 关键词匹配单帧的击杀信息 ROI。返回 MatchResult 或 None。"""
    from app.core.keywords import MatchResult
    frame_matched: MatchResult | None = None
    for roi_idx, roi in enumerate(pixel_rois):
        label = roi.label
        if label == '淘汰计数':
            continue  # 细胞分裂时跳过计数器，只关注击杀信息
        roi_img = frame[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]
        prepped = detector._preprocess(roi_img)
        ocr_results = detector.detect_raw(prepped)
        if not ocr_results:
            continue
        # 记录 OCR 文本
        if frame_logger:
            for ocr_r in ocr_results:
                frame_logger.log_sample(
                    timestamp, label, roi_idx, ocr_r.text, ocr_r.confidence,
                    stage="cell_divide")
        if raw_ocr_cb:
            for ocr_r in ocr_results:
                raw_ocr_cb(timestamp, ocr_r.text, label)
        if len(ocr_results) > 1:
            sorted_r = sorted(ocr_results, key=lambda r: r.bbox[0])
            joined = "".join(r.text for r in sorted_r)
            jm = matcher.match(joined)
            if jm and (allowed_actors is None or jm.actor in allowed_actors):
                fusion.feed(timestamp, joined,
                            min(r.confidence for r in ocr_results))
                if frame_logger:
                    frame_logger.log_sample(
                        timestamp, label, roi_idx, joined,
                        min(r.confidence for r in ocr_results),
                        match_hit=True, match_pattern_id=jm.pattern_id,
                        match_action=jm.action, match_actor=jm.actor,
                        stage="cell_divide_match")
                if frame_matched is None:
                    frame_matched = jm
                continue
        for ocr_r in ocr_results:
            match = matcher.match(ocr_r.text)
            if match:
                fusion.feed(timestamp, ocr_r.text, ocr_r.confidence)
                if allowed_actors is not None and match.actor not in allowed_actors:
                    continue
                if frame_logger:
                    frame_logger.log_sample(
                        timestamp, label, roi_idx, ocr_r.text, ocr_r.confidence,
                        match_hit=True, match_pattern_id=match.pattern_id,
                        match_action=match.action, match_actor=match.actor,
                        stage="cell_divide_match")
                if frame_matched is None:
                    frame_matched = match
            else:
                fused = fusion.feed(timestamp, ocr_r.text, ocr_r.confidence)
                if fused:
                    fm = matcher.match(fused)
                    if fm:
                        if allowed_actors is not None and fm.actor not in allowed_actors:
                            continue
                        if frame_matched is None:
                            frame_matched = fm
    return frame_matched
    """Top-level pipeline orchestrating all three phases."""

    def __init__(self, matcher: KeywordMatcher, detector: OCRDetector,
                 mode: str = "time", interval_sec: float = 1.0,
                 skip_frames: int = 15,
                 dense_window_padding: float = 3.0,
                 dense_interval_sec: float = 0.1,
                 dense_ocr_interval_sec: float = 0.25,
                 refine_boundaries: bool = True,
                 refine_search_window: float = 2.0,
                 padding_before: float = 10.0,
                 padding_after: float = 10.0,
                 wait_after: dict | None = None,
                 allowed_actors: set | None = None):
        self._matcher = matcher
        self._detector = detector
        self._mode = mode
        self._interval_sec = interval_sec
        self._skip_frames = skip_frames
        self._dense_window_padding = dense_window_padding
        self._dense_interval_sec = dense_interval_sec
        self._dense_ocr_interval_sec = dense_ocr_interval_sec
        self._refine_boundaries = refine_boundaries
        self._refine_search_window = refine_search_window
        self._padding_before = padding_before
        self._padding_after = padding_after
        self._wait_after = wait_after
        self._allowed_actors = allowed_actors
        self._config_meta: dict = {}

    def run_full(self, video_path: str, annotations: AnnotationStore,
                 start_frame: int = 0, end_frame: int | None = None,
                 progress_cb: Callable[[float], None] = None,
                 phase_cb: Callable[[str], None] = None,
                 detected_cb: Callable[[float, str], None] = None,
                 raw_ocr_cb: Callable[[float, str, str], None] = None,
                 cancel_check: Callable[[], bool] | None = None) -> tuple[list, object]:
        """Run complete coarse-to-fine pipeline.

        Returns (list of (start_sec, end_sec, action, actor, pattern_id, source), report).
        """
        _run_start = time.time()

        # ── Open video to get info ──
        player = VideoPlayer()
        info = player.open(video_path)
        fps = info.fps
        total_frames = info.total_frames
        player.close()

        end = end_frame if end_frame is not None else (total_frames - 1)
        rois = annotations.to_pixel_rois(info.width, info.height)
        if not rois:
            return [], None

        kill_idx = 0
        for i, roi in enumerate(rois):
            if roi.label == '击杀信息':
                kill_idx = i
                break

        self._config_meta = {
            "pipeline": "coarse_to_fine",
            "mode": self._mode,
            "interval_sec": self._interval_sec,
            "skip_frames": self._skip_frames,
            "dense_window_padding": self._dense_window_padding,
            "dense_interval_sec": self._dense_interval_sec,
            "dense_ocr_interval_sec": self._dense_ocr_interval_sec,
            "refine_boundaries": self._refine_boundaries,
            "refine_search_window": self._refine_search_window,
            "padding_before": self._padding_before,
            "padding_after": self._padding_after,
        }

        stack_cfg = EventStackConfig(
            padding_before=self._padding_before,
            padding_after=self._padding_after,
        )
        if self._wait_after:
            stack_cfg.wait_after.update(self._wait_after)

        # ── Phase 1: Coarse Scan ──
        if phase_cb:
            phase_cb("coarse")
        scanner = CoarseScanner(
            video_path, rois, kill_idx,
            mode=self._mode, interval_sec=self._interval_sec,
            skip_frames=self._skip_frames,
        )
        candidates = scanner.scan(
            start_frame, end, fps,
            progress_cb=lambda p: progress_cb(p * 0.33) if progress_cb else None,
            cancel_check=cancel_check,
        )

        # ── Phase 2: Dense Scan + OCR ──
        if phase_cb:
            phase_cb("dense")
        dense = DenseScanner(
            video_path, rois, kill_idx,
            self._detector, self._matcher,
            dense_interval_sec=self._dense_interval_sec,
            dense_ocr_interval_sec=self._dense_ocr_interval_sec,
            window_padding=self._dense_window_padding,
            allowed_actors=self._allowed_actors,
        )
        counter = CounterTracker()
        stack = EventStackEngine(stack_cfg)
        counter.on_event = stack.push

        signal_events = dense.scan_and_ocr(
            candidates, fps,
            raw_ocr_cb=raw_ocr_cb,
            detected_cb=detected_cb,
            progress_cb=lambda p: progress_cb(33 + p * 0.33) if progress_cb else None,
            cancel_check=cancel_check,
            counter=counter,
        )

        for evt in signal_events:
            stack.push(evt)
        results = stack.flush()
        results = DetectionEngine._merge_overlapping(results)

        # ── Phase 3: Boundary Refinement ──
        if self._refine_boundaries and results:
            if phase_cb:
                phase_cb("refine")
            refiner = BoundaryRefiner(video_path, rois, kill_idx)
            refined: list[DetectionResult] = []
            total = len(results)
            for i, r in enumerate(results):
                if cancel_check and cancel_check():
                    refined.append(r)
                    continue
                refined.append(refiner.refine(
                    r, fps,
                    search_window=self._refine_search_window,
                    cancel_check=cancel_check,
                ))
                if progress_cb:
                    progress_cb(66 + (i + 1) / max(total, 1) * 34)
            results = refined

        if phase_cb:
            phase_cb("done")
        if progress_cb:
            progress_cb(100)

        # ── Build report ──
        elapsed = time.time() - _run_start
        roi_dicts = [{"id": r.label, "label": r.label} for r in rois]
        report = DetectionReport(
            video_path=video_path,
            video_width=info.width, video_height=info.height,
            video_fps=fps,
            video_duration_sec=info.total_frames / max(fps, 1),
            config=self._config_meta, rois=roi_dicts,
        )
        report.summary.elapsed_seconds = elapsed
        report.summary.detections_before_merge = stack.total_pushed
        report.summary.detections_after_merge = len(results)
        report.summary.counter_total_events = len(counter.events)
        report.summary.counter_total_delta = sum(d for _, d in counter.events)

        return results, report
