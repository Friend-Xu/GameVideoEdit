"""Legacy DetectionEngine 对照: 验证是否找到 clips。"""
import sys, json, time
from pathlib import Path
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from app.core.detector import OCRDetector, DetectionEngine
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
engine = DetectionEngine(matcher, detector, mode="frame", skip_frames=60)

print(f"Legacy test: {info.width}x{info.height}, skip=60 (~1fps)")
t0 = time.time()
results, report = engine.run_full(VP, ann, start_frame=0, end_frame=None)
elapsed = time.time() - t0
print(f"Done in {elapsed:.1f}s  |  {len(results)} clips")
for r in results:
    print(f"  [{r.start_sec:.0f}s-{r.end_sec:.0f}s] {r.action}:{r.actor}")
