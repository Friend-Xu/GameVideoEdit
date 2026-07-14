"""Measure GPU memory step-by-step with full Pool pipeline (same as GUI)."""
import sys; sys.path.insert(0, 'D:/Workspace/GameVideoEdit')
from pynvml import nvmlInit, nvmlDeviceGetHandleByIndex, nvmlDeviceGetMemoryInfo, nvmlShutdown
nvmlInit()
h = nvmlDeviceGetHandleByIndex(0)

def mem():
    i = nvmlDeviceGetMemoryInfo(h)
    return i.used / 1024**3

def show(label):
    print(f"  [{label}] {mem():.2f} GB")

print(f"Baseline: {mem():.2f} GB")

from app.core.detector import OCRDetector, DetectionPipeline
from app.core.keywords import KeywordMatcher
from app.core.annotator import AnnotationStore
from app.core.player import VideoPlayer
show("after imports")

detector = OCRDetector(gpu=True, engine='rapidocr')
show("after OCRDetector")

matcher = KeywordMatcher.from_yaml('config/keywords.yaml')
show("after KeywordMatcher")

VIDEO = 'data/videos/SEBV3987.MP4'
player = VideoPlayer()
info = player.open(VIDEO)
player.close()
show("after video open")

anno = AnnotationStore(video_path=VIDEO, width=info.width, height=info.height,
                       fps=info.fps, total_frames=info.total_frames)
anno.replace_regions([
    {'id': 2, 'label': '淘汰计数', 'center_x': 0.115625, 'center_y': 0.018366,
     'width': 0.0625, 'height': 0.030735},
    {'id': 4, 'label': '击杀信息', 'center_x': 0.5125, 'center_y': 0.824213,
     'width': 0.567708, 'height': 0.044228},
])
show("after annotations")

pipeline = DetectionPipeline(
    matcher, detector,
    cpu_workers=6, gpu_workers=4,
    padding_before=10.0, padding_after=10.0,
    mode='time', interval_sec=1.0,
    skip_frames=60,
    gate_mode='neural',
    cell_divide=True,
    cell_min_gap=2.0,
    refine_search_window=2.0,
)
show("after DetectionPipeline")

import threading
cancel = threading.Event()
results, report = pipeline.run_full(
    video_path=VIDEO, annotations=anno,
    start_frame=0, end_frame=int(180 * info.fps),
    cancel_check=cancel.is_set,
)
show(f"after 180s run ({len(results)} events)")

nvmlShutdown()
