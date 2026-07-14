"""Debug: why 0 clips in pool mode? Verify OCR + ROI + keyword match."""
import sys, json
from pathlib import Path
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from app.core.detector import OCRDetector, TextPresenceGate
from app.core.keywords import KeywordMatcher
from app.core.annotator import AnnotationStore
from app.core.player import VideoPlayer

VP = str(PROJECT / "data" / "videos" / "NQLS5621.MP4")
with open(PROJECT / "config" / "roi_templates.json") as f:
    tmpl = json.load(f)
regions = tmpl["templates"][tmpl["default"]]

p = VideoPlayer(); info = p.open(VP)
fps = info.fps

ann = AnnotationStore()
for r in regions:
    w = int(r["width"] * info.width); h = int(r["height"] * info.height)
    x = int(r["center_x"] * info.width - w // 2)
    y = int(r["center_y"] * info.height - h // 2)
    ann.add_region(r["label"], x, y, w, h, info.width, info.height)
rois = ann.to_pixel_rois(info.width, info.height)

print(f"ROIs ({len(rois)}):")
for roi in rois:
    print(f"  {roi.label}: x={roi.x} y={roi.y} w={roi.w} h={roi.h}")

gate = TextPresenceGate()
detector = OCRDetector(gpu=True)
matcher = KeywordMatcher()

text_frames = 0
for sec in [200, 400, 600, 800, 1000, 1200]:
    fn = int(sec * fps)
    frame = p.seek(fn)
    for roi in rois:
        ki = frame[roi.y:roi.y+roi.h, roi.x:roi.x+roi.w]
        has_text, is_new = gate.check(ki)
        if has_text:
            text_frames += 1
            results = detector.detect(ki)
            print(f"\nFrame {fn} ({sec}s) {roi.label}: {len(results)} OCR hits")
            for ocr_r in results[:5]:
                m = matcher.match(ocr_r.text)
                match_str = f"MATCH:{m.action}:{m.actor}" if m else "no match"
                print(f"  '{ocr_r.text}' conf={ocr_r.confidence:.2f} {match_str}")

print(f"\nTotal frames with text: {text_frames}/{6}")
p.close()
