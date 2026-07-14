"""Test brightness normalization for dim killfeed text (max pixel only 193)"""
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

for ts in [1646.0, 1646.5, 1647.0, 1647.25, 1647.5]:
    fn = int(ts * fps)
    frame = player.seek(fn)
    roi = frame[ROI_Y:ROI_Y + ROI_H, ROI_X:ROI_X + ROI_W]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    print(f"\nt={ts:.2f}s fn={fn} gray max={gray.max()} mean={gray.mean():.0f}")

    # 1. Brightness normalization
    norm = ((gray.astype(np.float32) - gray.min()) /
            max(gray.max() - gray.min(), 1) * 255).astype(np.uint8)
    try:
        raw = reader.readtext(cv2.cvtColor(norm, cv2.COLOR_GRAY2BGR))
        texts = [(t.strip(), c) for b, t, c in raw if c >= 0.2]
        print(f"  Norm: {len(texts)}")
        for txt, conf in texts:
            pf = matcher.has_trigger_prefix(txt); m = matcher.match(txt)
            print(f"    '{txt}' c={conf:.3f} pf={pf} m={'HIT' if m else '-'}")
    except Exception as e:
        print(f"  Norm error: {e}")

    # 2. Norm + CLAHE
    clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(4, 4))
    nclahe = clahe.apply(norm)
    try:
        raw2 = reader.readtext(cv2.cvtColor(nclahe, cv2.COLOR_GRAY2BGR))
        texts2 = [(t.strip(), c) for b, t, c in raw2 if c >= 0.2]
        print(f"  Norm+CLAHE: {len(texts2)}")
        for txt, conf in texts2:
            pf = matcher.has_trigger_prefix(txt); m = matcher.match(txt)
            print(f"    '{txt}' c={conf:.3f} pf={pf} m={'HIT' if m else '-'}")
    except Exception as e:
        print(f"  Norm+CLAHE error: {e}")

    # 3. Gamma correction
    gamma = 0.5
    gam = ((gray.astype(np.float32) / 255.0) ** gamma * 255).astype(np.uint8)
    try:
        raw3 = reader.readtext(cv2.cvtColor(gam, cv2.COLOR_GRAY2BGR))
        texts3 = [(t.strip(), c) for b, t, c in raw3 if c >= 0.2]
        print(f"  Gamma(0.5): {len(texts3)}")
        for txt, conf in texts3:
            pf = matcher.has_trigger_prefix(txt); m = matcher.match(txt)
            print(f"    '{txt}' c={conf:.3f} pf={pf} m={'HIT' if m else '-'}")
    except Exception as e:
        print(f"  Gamma error: {e}")

    # 4. Original color
    try:
        raw4 = reader.readtext(roi)
        texts4 = [(t.strip(), c) for b, t, c in raw4 if c >= 0.2]
        print(f"  Color: {len(texts4)}")
        for txt, conf in texts4:
            pf = matcher.has_trigger_prefix(txt); m = matcher.match(txt)
            print(f"    '{txt}' c={conf:.3f} pf={pf} m={'HIT' if m else '-'}")
    except Exception as e:
        print(f"  Color error: {e}")

    # 5. Color per-channel normalization
    norm_c = np.zeros_like(roi)
    for c in range(3):
        ch = roi[:, :, c].astype(np.float32)
        norm_c[:, :, c] = ((ch - ch.min()) / max(ch.max() - ch.min(), 1) * 255).astype(np.uint8)
    try:
        raw5 = reader.readtext(norm_c)
        texts5 = [(t.strip(), c) for b, t, c in raw5 if c >= 0.2]
        print(f"  ColorNorm: {len(texts5)}")
        for txt, conf in texts5:
            pf = matcher.has_trigger_prefix(txt); m = matcher.match(txt)
            print(f"    '{txt}' c={conf:.3f} pf={pf} m={'HIT' if m else '-'}")
    except Exception as e:
        print(f"  ColorNorm error: {e}")

player.close()
print("\nDone.")
