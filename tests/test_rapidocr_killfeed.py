"""Test RapidOCR on SEBV3987.MP4 killfeed at 27:26-27:28"""
import sys
sys.path.insert(0, "D:/Workspace/GameVideoEdit")
import cv2
from app.core.player import VideoPlayer
from app.core.keywords import KeywordMatcher
from rapidocr import RapidOCR

VIDEO = "data/videos/SEBV3987.MP4"
W, H = 1920, 1334
ROI_X = int((0.5125 - 0.567708 / 2) * W)
ROI_Y = int((0.824213 - 0.044228 / 2) * H)
ROI_W = int(0.567708 * W)
ROI_H = int(0.044228 * H)

p = VideoPlayer()
info = p.open(VIDEO)
fps = info.fps

engine = RapidOCR()
matcher = KeywordMatcher.from_yaml("config/keywords.yaml")

for ts in [1646.0, 1646.5, 1647.0, 1647.5]:
    fn = int(ts * fps)
    frame = p.seek(fn)
    roi = frame[ROI_Y:ROI_Y+ROI_H, ROI_X:ROI_X+ROI_W]
    result = engine(roi)
    if result.boxes is not None and len(result.boxes) > 0:
        for box, txt, score in zip(result.boxes, result.txts, result.scores):
            pf = matcher.has_trigger_prefix(txt)
            m = matcher.match(txt)
            hit = f"HIT:{m.action}:{m.actor}" if m else "NO"
            print(f"  t={ts:.1f}s: '{txt}' s={score:.3f} pf={pf} {hit}")
    else:
        print(f"  t={ts:.1f}s: no text")
p.close()
print("Done.")
