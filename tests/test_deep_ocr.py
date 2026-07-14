"""Deep OCR test: detector regions, raw OCR without conf filter, different preprocessing"""
import sys
sys.path.insert(0, "D:/Workspace/GameVideoEdit")

import cv2
import numpy as np

from app.core.player import VideoPlayer
from app.core.model_loader import ModelManager
from app.core.keywords import KeywordMatcher

VIDEO_PATH = "D:/Workspace/GameVideoEdit/data/videos/SEBV3987.MP4"
YAML_PATH = "D:/Workspace/GameVideoEdit/config/keywords.yaml"

W, H = 1920, 1334
ROI_X = int((0.5125 - 0.567708 / 2) * W)
ROI_Y = int((0.824213 - 0.044228 / 2) * H)
ROI_W = int(0.567708 * W)
ROI_H = int(0.044228 * H)

player = VideoPlayer()
info = player.open(VIDEO_PATH)
fps = info.fps

mm = ModelManager()
reader = mm.get_easyocr_reader(gpu=True, languages=["ch_sim", "en"])
matcher = KeywordMatcher.from_yaml(YAML_PATH)

clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

test_times = [1646.0, 1646.5, 1647.0, 1647.5, 1648.0]

for ts in test_times:
    fn = int(ts * fps)
    frame = player.seek(fn)
    if frame is None:
        print(f"t={ts:.1f}s fn={fn}: NO FRAME")
        continue

    roi = frame[ROI_Y:ROI_Y + ROI_H, ROI_X:ROI_X + ROI_W]
    print(f"\n{'='*80}")
    print(f"t={ts:.1f}s fn={fn}")

    # 1. EasyOCR detector only
    try:
        h_list, f_list = reader.detect(roi)
        n_h = len(h_list) if h_list else 0
        n_f = len(f_list) if f_list else 0
        print(f"  Detector: {n_h}H + {n_f}F regions")
    except Exception as e:
        print(f"  Detector ERROR: {e}")

    # 2. Standard OCR (CLAHE)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    enhanced = clahe.apply(gray)
    prepped = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
    try:
        raw = reader.readtext(prepped)
        texts = [(t.strip(), c) for b, t, c in raw if c >= 0.3]
        print(f"  CLAHE OCR: {len(texts)} results")
        for txt, conf in texts:
            pf = matcher.has_trigger_prefix(txt)
            m = matcher.match(txt)
            print(f"    '{txt}' c={conf:.3f} pf={pf} m={'HIT' if m else '-'}")
    except Exception as e:
        print(f"  CLAHE OCR error: {e}")

    # 3. OCR on original color (no CLAHE)
    try:
        raw2 = reader.readtext(roi)
        texts2 = [(t.strip(), c) for b, t, c in raw2 if c >= 0.1]
        print(f"  Raw OCR (no CLAHE): {len(texts2)} results")
        for txt, conf in texts2:
            pf = matcher.has_trigger_prefix(txt)
            m = matcher.match(txt)
            print(f"    '{txt}' c={conf:.3f} pf={pf} m={'HIT' if m else '-'}")
    except Exception as e:
        print(f"  Raw OCR error: {e}")

    cv2.imwrite(f"D:/Workspace/GameVideoEdit/tests/roi_{ts:.0f}s.png", roi)

player.close()
print("\nDone.")
