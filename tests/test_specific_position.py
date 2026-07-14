"""测试 SEBV3987.MP4 在 27:26-27:28 的 OCR 识别情况"""
import sys
sys.path.insert(0, "D:/Workspace/GameVideoEdit")

import cv2
import numpy as np

from app.core.player import VideoPlayer
from app.core.model_loader import ModelManager
from app.core.keywords import KeywordMatcher

VIDEO_PATH = "D:/Workspace/GameVideoEdit/data/videos/SEBV3987.MP4"
YAML_PATH = "D:/Workspace/GameVideoEdit/config/keywords.yaml"

# ROI from SEBV3987.roi.json: center_x=0.5125, center_y=0.824213, w=0.567708, h=0.044228
W, H = 1920, 1334
ROI_X = int((0.5125 - 0.567708 / 2) * W)
ROI_Y = int((0.824213 - 0.044228 / 2) * H)
ROI_W = int(0.567708 * W)
ROI_H = int(0.044228 * H)

print(f"Killfeed ROI: x={ROI_X}, y={ROI_Y}, w={ROI_W}, h={ROI_H}")

player = VideoPlayer()
info = player.open(VIDEO_PATH)
print(f"FPS: {info.fps}, Total frames: {info.total_frames}")
fps = info.fps

mm = ModelManager()
reader = mm.get_easyocr_reader(gpu=True, languages=["ch_sim", "en"])
matcher = KeywordMatcher.from_yaml(YAML_PATH)

# Test frames around 27:26 - 27:31
target_times = sorted(set(
    round(1646 + i * 0.25, 2) for i in range(20)
))

print(f"\n{'='*80}")
print(f"Testing {len(target_times)} frames around 27:26 - 27:31")
print(f"{'='*80}\n")

clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

for ts in target_times:
    fn = int(ts * fps)
    frame = player.seek(fn)
    if frame is None:
        print(f"  t={ts:.2f}s fn={fn}: NO FRAME")
        continue

    roi = frame[ROI_Y:ROI_Y + ROI_H, ROI_X:ROI_X + ROI_W]

    if ts in [1646.0, 1647.0, 1648.0]:
        cv2.imwrite(f"D:/Workspace/GameVideoEdit/tests/frame_{ts:.0f}s.png", roi)

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    enhanced = clahe.apply(gray)
    prepped = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

    try:
        raw = reader.readtext(prepped)
    except Exception as e:
        print(f"  t={ts:.2f}s fn={fn}: OCR ERROR: {e}")
        continue

    if not raw:
        continue

    texts = []
    for bbox, text, conf in raw:
        if conf < 0.3:
            continue
        text = text.strip()
        texts.append((bbox, text, conf))
        has_prefix = matcher.has_trigger_prefix(text)
        match = matcher.match(text)
        match_info = f"MATCH: {match.action}:{match.actor}" if match else "NO MATCH"
        print(f"  t={ts:.2f}s fn={fn}: '{text}' conf={conf:.3f} prefix={has_prefix} {match_info}")

    if len(texts) > 1:
        sorted_texts = sorted(texts, key=lambda x: x[0][0][0])
        joined = "".join(t[1] for t in sorted_texts)
        match = matcher.match(joined)
        if match:
            print(f"  >>> JOINED='{joined}' -> MATCH: {match.action}:{match.actor}")
        else:
            print(f"  >>> JOINED='{joined}' -> NO MATCH")

player.close()
print("\nDone. Screenshots at tests/frame_*.png")
