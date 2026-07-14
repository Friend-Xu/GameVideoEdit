"""将 Pool 模式类追加到 detector.py 末尾 (不修改旧代码)。"""
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DETECTOR = PROJECT / "app" / "core" / "detector.py"

content = DETECTOR.read_text(encoding="utf-8")
if "class CPUWorker" in content:
    print("Pool 模式已安装，跳过。")
    sys.exit(0)

NEW = """


# ═══════════════════════════════════════════════════════════════
# Pool 模式: 解耦 CPU/GPU Worker (config pipeline_mode: pool)
# ═══════════════════════════════════════════════════════════════

class CPUWorker(threading.Thread):
    \"\"\"CPU 工作线程: 视频解码 + 文字门控 + CLAHE 预处理。

    frame_req_queue -> VideoPlayer.seek -> TextPresenceGate
    -> 无文字: push to skip_queue
    -> 有文字: CLAHE all ROIs -> push to prepped_queue\"\"\"

    def __init__(self, video_path: str, rois: list, fps: float,
                 detector: "OCRDetector", kill_roi_index: int,
                 frame_req_queue, prepped_queue, skip_queue,
                 text_gate: "TextPresenceGate" = None):
        super().__init__(daemon=True)
        self._video_path = video_path
        self._rois = rois
        self._fps = fps
        self._detector = detector
        self._kill_roi_index = kill_roi_index
        self._req = frame_req_queue
        self._prepped = prepped_queue
        self._skip = skip_queue
        self._gate = text_gate or TextPresenceGate()
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
                    if frame is None:
                        self._skip.put((fn, 0.0))
                        continue
                    ts = fn / max(self._fps, 1)
                    roi = self._rois[self._kill_roi_index]
                    if hasattr(roi, 'x'):
                        ki = frame[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]
                    else:
                        ki = frame[roi['y']:roi['y'] + roi['h'],
                                   roi['x']:roi['x'] + roi['w']]
                    has_text, is_new = self._gate.check(ki)
                    if not has_text:
                        self._skip.put((fn, ts))
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
                    self._skip.put((fn, 0.0))
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
    \"\"\"GPU 工作线程: OCR 识别 + 关键词匹配。

    prepped_queue -> GPU OCR -> KeywordMatch -> CounterTrack -> TextFusion
    -> FrameResult to result_queue\"\"\"

    def __init__(self, detector: "OCRDetector", matcher: "KeywordMatcher",
                 prepped_queue, result_queue,
                 counter: "CounterTracker" = None,
                 allowed_actors: set = None,
                 raw_ocr_cb=None):
        super().__init__(daemon=True)
        self._detector = detector
        self._matcher = matcher
        self._prepped_queue = prepped_queue
        self._result_queue = result_queue
        self._counter = counter or CounterTracker()
        self.allowed_actors = allowed_actors
        self._raw_ocr_cb = raw_ocr_cb
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
        frame_matched = None
        for roi_img, roi in prepped_rois:
            label = (getattr(roi, 'label', '') if hasattr(roi, 'label')
                     else roi.get('label', ''))
            if label == '淘汰计数':
                for ocr_r in self._detector.detect_raw(roi_img):
                    self._counter.feed(timestamp, ocr_r.text)
                    if self._raw_ocr_cb:
                        self._raw_ocr_cb(timestamp, ocr_r.text, label)
            else:
                ocr_results = self._detector.detect_raw(roi_img)
                if not ocr_results:
                    continue
                if len(ocr_results) > 1:
                    sorted_r = sorted(ocr_results, key=lambda r: r.bbox[0])
                    joined = "".join(r.text for r in sorted_r)
                    jm = self._matcher.match(joined)
                    if jm and (self.allowed_actors is None or jm.actor in self.allowed_actors):
                        self._fusion.feed(timestamp, joined,
                                          min(r.confidence for r in ocr_results))
                        for ocr_r in ocr_results:
                            if self._raw_ocr_cb:
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
    \"\"\"解耦流水线: CPU Pool -> GPU Pool -> 按 fn 排序 -> EventStack。

    替代 DetectionEngine.run_full() 的调度逻辑。\"\"\"

    def __init__(self, matcher, detector,
                 cpu_workers: int = 3, gpu_workers: int = 2,
                 padding_before: float = 10.0, padding_after: float = 10.0,
                 merge_gap: float = 30.0,
                 allowed_actors: set = None,
                 wait_after: dict = None):
        self._matcher = matcher
        self._detector = detector
        self._cpu_n = cpu_workers
        self._gpu_n = gpu_workers
        self.padding_before = padding_before
        self.padding_after = padding_after
        self.merge_gap = merge_gap
        self.allowed_actors = allowed_actors
        self._wait_after = wait_after
        self._config_meta: dict = {}

    def run_full(self, video_path: str, annotations,
                 start_frame: int = 0, end_frame: int = None,
                 progress_cb=None, detected_cb=None,
                 raw_ocr_cb=None, cancel_check=None):
        import queue
        from app.core.player import VideoPlayer

        _t0 = time.time()
        player = VideoPlayer()
        info = player.open(video_path)
        fps = info.fps
        end = end_frame or (info.total_frames - 1)
        rois = annotations.to_pixel_rois(info.width, info.height)
        player.close()

        self._config_meta = {
            "mode": "pool", "cpu_workers": self._cpu_n,
            "gpu_workers": self._gpu_n,
            "padding_before": self.padding_before,
            "padding_after": self.padding_after,
            "merge_gap": self.merge_gap,
        }

        if not rois:
            return [], None

        kill_idx = 0
        for i, r in enumerate(rois):
            label = (getattr(r, 'label', '') if hasattr(r, 'label')
                     else r.get('label', ''))
            if label == '击杀信息':
                kill_idx = i
                break

        max_q = self._cpu_n * 3
        frame_req = queue.Queue(maxsize=max_q)
        prepped_q = queue.Queue()
        skip_q = queue.Queue()
        result_q = queue.Queue()

        cpu_pool = [CPUWorker(video_path, rois, fps, self._detector,
                              kill_idx, frame_req, prepped_q, skip_q)
                    for _ in range(self._cpu_n)]
        for w in cpu_pool:
            w.start()

        gpu_pool = [GPUWorker(self._detector, self._matcher,
                              prepped_q, result_q,
                              CounterTracker() if i == 0 else None,
                              self.allowed_actors,
                              raw_ocr_cb if i == 0 else None)
                    for i in range(self._gpu_n)]
        for w in gpu_pool:
            w.start()

        stack = EventStackEngine(EventStackConfig(
            wait_after=self._wait_after or {},
            padding_before=self.padding_before,
            padding_after=self.padding_after,
        ))

        frame_nos = []
        fn = start_frame
        while fn <= end:
            if cancel_check and cancel_check():
                break
            frame_nos.append(fn)
            fn += 3
            if progress_cb:
                pct = (fn - start_frame) / max(end - start_frame, 1) * 100
                progress_cb(min(pct, 50.0))

        submitted = 0
        total = len(frame_nos)
        pending: dict[int, tuple] = {}
        next_idx = 0
        batch = self._cpu_n * 2

        while submitted < total or next_idx < total:
            while (submitted < total
                   and submitted - next_idx < batch * 2):
                try:
                    frame_req.put_nowait(frame_nos[submitted])
                    submitted += 1
                except queue.Full:
                    break

            for _ in range(batch):
                try:
                    fn_s, ts = skip_q.get_nowait()
                    pending[fn_s] = (fn_s, ts, None)
                    continue
                except queue.Empty:
                    pass
                try:
                    fn_r, ts, matched = result_q.get_nowait()
                    pending[fn_r] = (fn_r, ts, matched)
                except queue.Empty:
                    break

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
                        raw_text=matched.raw_text, source="text"))
                    if detected_cb:
                        detected_cb(ts, matched.raw_text)
                next_idx += 1

            if progress_cb:
                pct = 50.0 + (next_idx / max(total, 1)) * 50.0
                progress_cb(min(pct, 100.0))

        for w in cpu_pool:
            w.stop()
        for w in gpu_pool:
            w.stop()

        elapsed = time.time() - _t0
        results = stack.flush()
        results = DetectionEngine._merge_overlapping(results)

        report = DetectionReport(
            video_path=video_path,
            video_width=info.width, video_height=info.height,
            video_fps=fps,
            video_duration_sec=info.total_frames / max(fps, 1),
            config=self._config_meta,
            rois=[{"id": i, "label": getattr(r, 'label', str(r))}
                  for i, r in enumerate(rois)],
        )
        s = report.summary
        s.elapsed_seconds = elapsed
        s.detections_before_merge = stack.total_pushed
        s.detections_after_merge = len(results)
        report.results = [
            {"start_sec": r.start_sec, "end_sec": r.end_sec,
             "action": r.action, "actor": r.actor,
             "pattern_id": r.pattern_id, "source": r.source}
            for r in results
        ]
        return results, report
"""

with open(DETECTOR, 'a', encoding='utf-8') as f:
    f.write(NEW)

print("Pool 模式已安装到 detector.py")
print("  新增: CPUWorker, GPUWorker, DetectionPipeline")
print("  旧代码: 未修改")
