"""Full integration test: OCRDetector with RapidOCR on real killfeed"""
import sys
sys.path.insert(0, "D:/Workspace/GameVideoEdit")
from app.core.detector import OCRDetector
from app.core.keywords import KeywordMatcher
from app.core.player import VideoPlayer

matcher = KeywordMatcher.from_yaml("config/keywords.yaml")

print("=== Test 1: RapidOCR ===")
det = OCRDetector(gpu=True, engine="rapidocr")
W, H = 1920, 1334
ROI_X = int((0.5125 - 0.567708 / 2) * W)
ROI_Y = int((0.824213 - 0.044228 / 2) * H)
ROI_W = int(0.567708 * W)
ROI_H = int(0.044228 * H)

p = VideoPlayer()
info = p.open("data/videos/SEBV3987.MP4")

found = False
for ts in [1646.5, 1647.0, 1647.5]:
    fn = int(ts * info.fps)
    frame = p.seek(fn)
    roi = frame[ROI_Y:ROI_Y+ROI_H, ROI_X:ROI_X+ROI_W]
    results = det.detect(roi)
    has = det.has_text(roi)
    print(f"  t={ts:.1f}: has_text={has} results={len(results)}")
    for r in results:
        m = matcher.match(r.text)
        if m: print(f"    '{r.text}' -> HIT: {m.action}:{m.actor}"); found = True
        else: print(f"    '{r.text}' -> no match")
p.close()
print("PASS" if found else "FAIL")

print("\n=== Test 2: EasyOCR fallback ===")
det2 = OCRDetector(gpu=True, engine="easyocr")
print(f"EasyOCR engine: {type(det2._engine).__name__} OK")
print("DONE")
