"""PC 端到端检测测试 — PUBG PC 中文视频"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.player import VideoPlayer
from app.core.annotator import AnnotationStore
from app.core.detector import OCRDetector, DetectionEngine
from app.core.keywords import KeywordMatcher
from app.core.presets import PresetManager
from glob import glob

# 1. Load video and ROIs
vf = glob('data/videos/*DVR*')
vp = [f for f in vf if f.endswith('.mp4')][0]
roi_path = vp.replace('.mp4', '.roi.json')
print(f'Video: {os.path.basename(vp)}')
print(f'ROI: {os.path.basename(roi_path)}')

player = VideoPlayer()
info = player.open(vp)
print(f'{info.width}x{info.height}, {info.fps:.0f}fps, {info.total_frames}f, {info.total_frames/info.fps:.0f}s')

ann = AnnotationStore.load_json(roi_path)
rois = ann.to_pixel_rois(info.width, info.height)
for r in rois:
    print(f'  ROI \"{r.label}\": ({r.x},{r.y}) {r.w}x{r.h}')

# 2. Load PC preset
pm = PresetManager()
cfg = pm.load('pubg_pc_zh')
matcher = KeywordMatcher.from_dict(cfg)
print(f'Preset: {cfg["meta"]["name"]}, {len(matcher._patterns)} rules')
print(f'ROI prefixes: {dict(matcher._roi_prefixes)}')

# 3. Run detection
detector = OCRDetector(gpu=False, engine='rapidocr')
engine = DetectionEngine(matcher, detector,
    padding_before=5, padding_after=5,
    skip_frames=5, mode='frame', interval_sec=0.3,
    post_detect_skip_sec=0.3)

t0 = time.time()
results, report = engine.run_full(
    video_path=vp, annotations=ann,
    start_frame=0, end_frame=None,
    cancel_check=lambda: False)
elapsed = time.time() - t0

print(f'\n=== {len(results)} events in {elapsed:.1f}s ===')
for r in results:
    print(f'  [{r.start_sec:.1f}s - {r.end_sec:.1f}s] {r.action}:{r.actor}')
    print(f'    text=\"{r.raw_text}\" src={r.source} conf={r.confidence:.2f}')

if report:
    print(f'\nReport: {report.total_frames} frames, {report.frames_with_matches} hits, {report.detections_after_merge} merged')

player.close()
