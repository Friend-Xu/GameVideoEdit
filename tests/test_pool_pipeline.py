"""验证 Pool 模式端到端工作。"""
import sys, time, json
from pathlib import Path
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from app.core.detector import OCRDetector, DetectionPipeline
from app.core.keywords import KeywordMatcher
from app.core.annotator import AnnotationStore
from app.core.player import VideoPlayer

VP = str(PROJECT / "data" / "videos" / "NQLS5621.MP4")

with open(PROJECT / "config" / "roi_templates.json") as f:
    tmpl = json.load(f)
regions = tmpl["templates"][tmpl["default"]]

p = VideoPlayer(); info = p.open(VP); p.close()
ann = AnnotationStore()
for r in regions:
    w = int(r["width"] * info.width); h = int(r["height"] * info.height)
    x = int(r["center_x"] * info.width - w // 2)
    y = int(r["center_y"] * info.height - h // 2)
    ann.add_region(r["label"], x, y, w, h, info.width, info.height)

detector = OCRDetector(gpu=True)
matcher = KeywordMatcher()
import threading
import queue as qmod

segments = 4
total_frames = info.total_frames
frames_per = total_frames // segments

all_results = []
rq = qmod.Queue()

def run_seg(sid, sf, ef):
    det = OCRDetector(gpu=True)
    pl = DetectionPipeline(matcher, det, cpu_workers=3, gpu_workers=1)
    tr, rp = pl.run_full(VP, ann, start_frame=sf, end_frame=ef)
    rq.put((sid, tr))

threads = []
for i in range(segments):
    sf = i * frames_per
    ef = (i + 1) * frames_per - 1 if i < segments - 1 else total_frames - 1
    t = threading.Thread(target=run_seg, args=(i, sf, ef), daemon=True)
    t.start()
    threads.append(t)

print(f"Video: {info.width}x{info.height}, {info.total_frames} frames")
print(f"Running {segments} pool segments in parallel...")
t0 = time.time()
for t in threads:
    t.join()
elapsed = time.time() - t0

while not rq.empty():
    _, tr = rq.get_nowait()
    all_results.extend(tr)

all_results.sort(key=lambda r: r.start_sec)
results = all_results
elapsed = time.time() - t0
unique = []
for r in all_results:
    rt = (r.start_sec, r.end_sec, r.action, r.actor, r.pattern_id, r.source)
    if not unique or unique[-1] != rt:
        unique.append(rt)

print(f"\nDone in {elapsed:.1f}s  |  {len(unique)} clips found")
for rt in unique:
    print(f"  [{rt[0]:.0f}s-{rt[1]:.0f}s] {rt[2]}:{rt[3]}")
