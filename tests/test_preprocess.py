"""Test different preprocessing methods for killfeed OCR"""
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

for ts in [1646.0, 1646.5, 1647.0]:
    fn = int(ts * fps)
    frame = player.seek(fn)
    roi = frame[ROI_Y:ROI_Y + ROI_H, ROI_X:ROI_X + ROI_W]

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    print(f"\nt={ts:.1f}s: gray min={gray.min()} max={gray.max()} mean={gray.mean():.0f}")

    for thresh in [180, 200, 220, 240]:
        _, binary = cv2.threshold(gray, thresh, 255, cv2.THRESH_BINARY)
        print(f"  thresh={thresh}: white={cv2.countNonZero(binary)}")

    # Try inverted OCR
    inverted = cv2.bitwise_not(gray)
    inv_bgr = cv2.cvtColor(inverted, cv2.COLOR_GRAY2BGR)
    try:
        raw = reader.readtext(inv_bgr)
        texts = [(t.strip(), c) for b, t, c in raw if c >= 0.3]
        print(f"  Inverted: {len(texts)} results")
        for txt, conf in texts:
            print(f"    '{txt}' c={conf:.3f}")
    except Exception as e:
        print(f"  Inverted error: {e}")

    # Try adaptive threshold
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 15, 4)
    ad_bgr = cv2.cvtColor(adaptive, cv2.COLOR_GRAY2BGR)
    try:
        raw2 = reader.readtext(ad_bgr)
        texts2 = [(t.strip(), c) for b, t, c in raw2 if c >= 0.3]
        print(f"  Adaptive: {len(texts2)} results")
        for txt, conf in texts2:
            print(f"    '{txt}' c={conf:.3f}")
    except Exception as e:
        print(f"  Adaptive error: {e}")

    # Try taller ROI
    taller = frame[max(0, ROI_Y-20):min(H, ROI_Y+ROI_H+20), ROI_X:ROI_X+ROI_W]
    gray2 = cv2.cvtColor(taller, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray2)
    prepped = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
    try:
        raw3 = reader.readtext(prepped)
        texts3 = [(t.strip(), c) for b, t, c in raw3 if c >= 0.3]
        print(f"  Taller ROI: {len(texts3)} results")
        for txt, conf in texts3:
            pf = matcher.has_trigger_prefix(txt)
            m = matcher.match(txt)
            print(f"    '{txt}' c={conf:.3f} pf={pf} m={'HIT' if m else '-'}")
    except Exception as e:
        print(f"  Taller error: {e}")

    # Try different color channels
    for ch_name, ch_idx in [("B", 0), ("G", 1), ("R", 2)]:
        ch = roi[:, :, ch_idx]
        ch_bgr = cv2.cvtColor(ch, cv2.COLOR_GRAY2BGR)
        try:
            raw4 = reader.readtext(ch_bgr)
            texts4 = [(t.strip(), c) for b, t, c in raw4 if c >= 0.3]
            if texts4:
                print(f"  {ch_name} channel: {len(texts4)} results")
                for txt, conf in texts4:
                    print(f"    '{txt}' c={conf:.3f}")
        except Exception:
            pass

player.close()
print("\nDone.")
