"""Full pool pipeline test with RapidOCR on SEBV3987.MP4"""
import sys, time, threading
sys.path.insert(0, "D:/Workspace/GameVideoEdit")

from app.core.detector import OCRDetector, DetectionPipeline
from app.core.keywords import KeywordMatcher
from app.core.annotator import AnnotationStore
from app.core.player import VideoPlayer

VIDEO = "data/videos/SEBV3987.MP4"
CONFIG = "config/keywords.yaml"

print("=== Loading config ===")
matcher = KeywordMatcher.from_yaml(CONFIG)

# Open video to get dimensions
player = VideoPlayer()
info = player.open(VIDEO)
print(f"  Video: {info.width}x{info.height}, {info.fps}fps, {info.total_frames} frames")
player.close()

# Create annotations with Ipad template
anno = AnnotationStore(video_path=VIDEO, width=info.width, height=info.height,
                       fps=info.fps, total_frames=info.total_frames)
anno.replace_regions([
    {"id": 2, "label": "淘汰计数", "center_x": 0.115625, "center_y": 0.018366,
     "width": 0.0625, "height": 0.030735},
    {"id": 4, "label": "击杀信息", "center_x": 0.5125, "center_y": 0.824213,
     "width": 0.567708, "height": 0.044228},
])
print(f"  ROIs: {anno.region_count} regions")

print("=== Creating OCRDetector (RapidOCR) ===")
detector = OCRDetector(gpu=True, engine="rapidocr")
print(f"  Engine type: {detector._engine_type}")

# Same settings as default.yaml (pool + cell_divide + neural gate)
pipeline = DetectionPipeline(
    matcher, detector,
    cpu_workers=6, gpu_workers=4,
    padding_before=10.0, padding_after=10.0,
    mode="time", interval_sec=1.0,
    skip_frames=60,
    gate_mode="neural",
    cell_divide=True,
    cell_min_gap=2.0,
    refine_search_window=2.0,
)

cancel = threading.Event()
progress_history = []

def on_progress(pct):
    if int(pct) % 10 == 0:
        print(f"  [PROGRESS] {pct:.0f}%")

def on_detected(ts, text):
    print(f"  [DETECT] {ts:.1f}s: {text[:80]}")

def on_raw_ocr(ts, text, label):
    if text.strip():
        print(f"  [OCR] {ts:.1f}s [{label}]: {text[:60]}")

# Limit to first 5 minutes for quick validation
end_sec = 300
start_frame = 0
end_frame = int(end_sec * info.fps)
print(f"=== Running pool pipeline (0-{end_sec}s, {end_frame} frames) ===")
t0 = time.time()

results, report = pipeline.run_full(
    video_path=VIDEO,
    annotations=anno,
    start_frame=start_frame,
    end_frame=end_frame,
    progress_cb=on_progress,
    detected_cb=on_detected,
    raw_ocr_cb=on_raw_ocr,
    cancel_check=cancel.is_set,
)

elapsed = time.time() - t0
print(f"\n=== Done in {elapsed:.1f}s ===")
print(f"Results: {len(results)} events")
for r in sorted(results, key=lambda r: r.start_sec):
    print(f"  [{r.start_sec:.1f}s - {r.end_sec:.1f}s] {r.action}:{r.actor} ({r.source})")

# Check specific target
target_hit = any(
    abs(r.start_sec - 1646.5) < 10 or abs(r.center - 1647) < 10
    for r in results
)
print(f"\nTarget '战场主宰...' at ~1647s: {'HIT' if target_hit else 'MISS'}")

if report:
    print(f"\nReport summary:")
    s = report.summary
    print(f"  Total frames scanned: {s.total_frames}")
    print(f"  OCR hits: {s.frames_with_ocr_hits}")
    print(f"  Matches: {s.frames_with_matches}")
    print(f"  Dropped: {s.dropped_total}")
    if s.dropped_by_reason:
        for reason, count in s.dropped_by_reason.items():
            print(f"    {reason}: {count}")
    print(f"  Before merge: {s.detections_before_merge}")
    print(f"  After merge: {s.detections_after_merge}")
