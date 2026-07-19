"""统一工作项目模型。

Project 是所有工作流状态的单一数据源，贯穿 打开视频 → 标注 → 识别 → 导出 全流程。
所有 UI 组件读/写同一个 Project 实例，不再各自持有零散状态。
"""

import json
import os
from datetime import datetime
from dataclasses import dataclass, field

from app.core.annotator import AnnotationStore
from app.core.exporter import ExportConfig
from app.core.roi_templates import ROITemplateManager


# ---------------------------------------------------------------------------
# 子结构
# ---------------------------------------------------------------------------

@dataclass
class VideoSource:
    """视频元数据（打开视频后设置，之后不可变）"""
    path: str = ""
    width: int = 0
    height: int = 0
    fps: float = 30.0
    total_frames: int = 0

    @property
    def basename(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0] if self.path else ""

    @property
    def dirname(self) -> str:
        return os.path.dirname(self.path) or "."


@dataclass
class DetectionConfig:
    """OCR 识别参数"""
    mode: str = "frame"
    interval_sec: float = 1.0
    skip_frames: int = 15
    post_detect_skip_sec: float = 0.3
    padding_before: float = 5.0
    padding_after: float = 5.0
    merge_gap: float = 30.0
    num_threads: int = 4
    gpu: bool = True
    rotation: int = 0
    allowed_actors: set[str] | None = None
    pipeline_mode: str = "pool"
    cpu_workers: int = 6
    gpu_workers: int = 4
    gate_mode: str = "neural"
    ocr_engine: str = "rapidocr"
    # 后处理参数（所有 pipeline_mode 通用）
    refine_boundaries: bool = False
    refine_search_window: float = 2.0
    cell_divide: bool = False
    cell_min_gap: float = 2.0

    def to_worker_kwargs(self) -> dict:
        return {
            "mode": self.mode,
            "interval_sec": self.interval_sec,
            "skip_frames": self.skip_frames,
            "gate_mode": self.gate_mode,
            "post_detect_skip_sec": self.post_detect_skip_sec,
            "padding_before": self.padding_before,
            "padding_after": self.padding_after,
            "gpu": self.gpu,
            "allowed_actors": self.allowed_actors,
        }


@dataclass
class ClipResult:
    """单个识别结果片段（TimeRange + 事件元数据）"""
    start_sec: float
    end_sec: float
    raw_start_sec: float = 0.0
    raw_end_sec: float = 0.0
    pattern_id: str = ""
    action: str = ""
    actor: str = ""
    raw_text: str = ""
    source: str = "text"
    confidence: float = 1.0
    match_strategy: str = "exact"

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec

    def to_tuple(self) -> tuple[float, float]:
        return (self.start_sec, self.end_sec)


# ---------------------------------------------------------------------------
# 撤销/重做 — 简单 MVP
# ---------------------------------------------------------------------------

class _ResultAction:
    """一条可撤销的操作记录"""

    def __init__(self, desc: str, do_fn, undo_fn):
        self.desc = desc
        self._do = do_fn
        self._undo = undo_fn

    def undo(self): self._undo()
    def redo(self): self._do()


class UndoStack:
    """轻量 undo/redo 栈，最大深度 50"""

    def __init__(self):
        self._undo: list[_ResultAction] = []
        self._redo: list[_ResultAction] = []
        self._max = 50

    def push(self, action: _ResultAction):
        self._undo.append(action)
        self._redo.clear()
        if len(self._undo) > self._max:
            self._undo.pop(0)

    def undo(self) -> str | None:
        if not self._undo:
            return None
        a = self._undo.pop()
        a.undo()
        self._redo.append(a)
        return a.desc

    def redo(self) -> str | None:
        if not self._redo:
            return None
        a = self._redo.pop()
        a.redo()
        self._undo.append(a)
        return a.desc

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear(self):
        self._undo.clear()
        self._redo.clear()


# ---------------------------------------------------------------------------
# 平台状态（mobile / pc 隔离）
# ---------------------------------------------------------------------------

@dataclass
class PlatformState:
    """每个平台独立的状态：检测参数、标注、结果。"""
    detection: "DetectionConfig"
    annotations: "AnnotationStore"
    results: list["ClipResult"] = field(default_factory=list)
    preset_file: str = ""

    def to_dict(self) -> dict:
        d = self.detection.__dict__.copy()
        if d.get("allowed_actors"):
            d["allowed_actors"] = sorted(d["allowed_actors"])
        return {
            "detection": d,
            "results": [
                {
                    "start_sec": r.start_sec, "end_sec": r.end_sec,
                    "raw_start_sec": r.raw_start_sec, "raw_end_sec": r.raw_end_sec,
                    "pattern_id": r.pattern_id, "action": r.action, "actor": r.actor,
                    "raw_text": r.raw_text, "source": r.source,
                    "confidence": r.confidence, "match_strategy": r.match_strategy,
                }
                for r in self.results
            ],
            "preset_file": self.preset_file,
        }

    @classmethod
    def from_dict(cls, data: dict, video_path: str = "",
                  width: int = 0, height: int = 0, fps: float = 30.0,
                  total_frames: int = 0) -> "PlatformState":
        det = DetectionConfig()
        det_data = data.get("detection", {})
        for k in ("mode", "interval_sec", "skip_frames", "post_detect_skip_sec",
                   "padding_before", "padding_after", "merge_gap", "num_threads",
                   "rotation", "gate_mode", "refine_boundaries", "refine_search_window",
                   "cell_divide", "cell_min_gap", "pipeline_mode", "cpu_workers",
                   "gpu_workers", "ocr_engine"):
            if k in det_data:
                setattr(det, k, det_data[k])
        if "allowed_actors" in det_data and det_data["allowed_actors"]:
            det.allowed_actors = set(det_data["allowed_actors"])
        ann = AnnotationStore(video_path, width, height, fps, total_frames)
        results = []
        for r in data.get("results", []):
            results.append(ClipResult(
                start_sec=r["start_sec"], end_sec=r["end_sec"],
                raw_start_sec=r.get("raw_start_sec", r["start_sec"]),
                raw_end_sec=r.get("raw_end_sec", r["end_sec"]),
                pattern_id=r.get("pattern_id", ""),
                action=r.get("action", ""), actor=r.get("actor", ""),
                raw_text=r.get("raw_text", ""),
                source=r.get("source", "text"),
                confidence=r.get("confidence", 1.0),
                match_strategy=r.get("match_strategy", "exact"),
            ))
        return cls(detection=det, annotations=ann, results=results,
                   preset_file=data.get("preset_file", ""))


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class Project:
    """统一工作项目 — 整个会话的唯一数据源。

    平台隔离：mobile / pc 各自保有独立的 annotations, detection, results。
    切换 platform 时自动保存/恢复，互不污染。
    """

    def __init__(self):
        self.source = VideoSource()
        self.export = ExportConfig()
        self._undo_stack = UndoStack()
        self._dirty = False
        self.last_detection: str = ""
        self._platform: str = "mobile"
        self._mobile = PlatformState(DetectionConfig(), AnnotationStore(), [])
        self._pc = PlatformState(DetectionConfig(), AnnotationStore(), [])

    # ── platform ──

    @property
    def platform(self) -> str:
        return self._platform

    @platform.setter
    def platform(self, value: str):
        if self._platform == value:
            return
        self._platform = value

    @property
    def _active(self) -> PlatformState:
        return self._mobile if self._platform == "mobile" else self._pc

    # ── 代理属性：所有对 annotations/detection/results 的读写都转发到当前平台 ──

    @property
    def annotations(self) -> AnnotationStore:
        return self._active.annotations

    @annotations.setter
    def annotations(self, value: AnnotationStore):
        self._active.annotations = value

    @property
    def detection(self) -> DetectionConfig:
        return self._active.detection

    @detection.setter
    def detection(self, value: DetectionConfig):
        self._active.detection = value

    @property
    def results(self) -> list[ClipResult]:
        return self._active.results

    @results.setter
    def results(self, value: list[ClipResult]):
        self._active.results = value

    @property
    def preset_file(self) -> str:
        return self._active.preset_file

    @preset_file.setter
    def preset_file(self, value: str):
        self._active.preset_file = value

    # ── 视频 ──

    def set_video(self, path: str, width: int, height: int,
                  fps: float, total_frames: int):
        self.source = VideoSource(
            path=path, width=width, height=height,
            fps=fps, total_frames=total_frames,
        )
        for ps in (self._mobile, self._pc):
            ps.annotations = AnnotationStore(
                video_path=path, width=width, height=height,
                fps=fps, total_frames=total_frames,
            )
        self.export.output_path = os.path.join(
            self.source.dirname, f"{self.source.basename}_highlights.mp4")
        self._auto_load_all()

    # ── ROI / Project 自动加载（分平台） ──

    def _auto_load_all(self):
        self._auto_load_roi()
        self._auto_load_project()

    def _auto_load_roi(self, platform: str | None = None):
        pf = platform or self._platform
        state = self._mobile if pf == "mobile" else self._pc
        path = self.roi_path
        # 跨平台注释：.roi.json 不区分平台，共用一份
        if path and os.path.exists(path):
            try:
                loaded = AnnotationStore.load_json(path)
                state.annotations = loaded
                return
            except Exception:
                pass
        tmpl = ROITemplateManager().get_default_for(pf)
        if tmpl and tmpl.regions:
            state.annotations.replace_regions(tmpl.regions)
        self.auto_save_roi()

    @property
    def roi_path(self) -> str:
        if not self.source.path:
            return ""
        return os.path.join(self.source.dirname,
                            f"{self.source.basename}.roi.json")

    @property
    def project_path(self) -> str:
        if not self.source.path:
            return ""
        return os.path.join(self.source.dirname,
                            f"{self.source.basename}.project.json")

    def _auto_load_project(self):
        path = self.project_path
        if not path or not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            src = data.get("source", {})
            src_path = os.path.abspath(src.get("path", ""))
            my_path = os.path.abspath(self.source.path)
            if src_path != my_path:
                return False
            # 加载两个平台的独立状态
            for pf_key in ("mobile", "pc"):
                pf_data = data.get(pf_key, {})
                if pf_data:
                    state = self._mobile if pf_key == "mobile" else self._pc
                    restored = PlatformState.from_dict(pf_data,
                        self.source.path, self.source.width, self.source.height,
                        self.source.fps, self.source.total_frames)
                    state.detection = restored.detection
                    state.annotations = restored.annotations
                    state.results = restored.results
                    state.preset_file = restored.preset_file
            self.last_detection = data.get("last_detection", "")
            exp = data.get("export", {})
            if exp:
                for k in ("output_path", "ffmpeg_path", "quality", "preset", "use_gpu"):
                    if k in exp:
                        setattr(self.export, k, exp[k])
            self._dirty = False
            return True
        except Exception:
            pass
        return False

    def auto_save(self):
        path = self.project_path
        if path and self.results:
            try:
                self.save_json(path)
            except Exception:
                pass

    def auto_save_roi(self):
        path = self.roi_path
        if path and self.annotations.region_count > 0:
            self.annotations.save_json(path)

    # ── 片段结果 ──

    def recompute_padding(self, padding_before: float, padding_after: float):
        for r in self.results:
            if r.raw_start_sec > 0 or r.raw_end_sec > 0:
                r.start_sec = max(0.0, r.raw_start_sec - padding_before)
                r.end_sec = r.raw_end_sec + padding_after
        self.detection.padding_before = padding_before
        self.detection.padding_after = padding_after
        self._dirty = True

    def set_results(self, items: list[ClipResult]):
        old = list(self.results)
        def do(): self.results.clear(); self.results.extend(items)
        def undo(): self.results.clear(); self.results.extend(old)
        do()
        self._undo_stack.push(_ResultAction("设置识别结果", do, undo))
        self._dirty = True
        self.auto_save()

    def remove_result(self, index: int) -> ClipResult | None:
        if index < 0 or index >= len(self.results):
            return None
        removed = self.results[index]
        def do(): del self.results[index]
        def undo(): self.results.insert(index, removed)
        del self.results[index]
        self._undo_stack.push(_ResultAction(f"删除片段 {index + 1}", do, undo))
        self._dirty = True
        return removed

    def adjust_result(self, index: int, start_sec: float,
                      end_sec: float) -> bool:
        if index < 0 or index >= len(self.results):
            return False
        old_start = self.results[index].start_sec
        old_end = self.results[index].end_sec
        def do(): self.results[index].start_sec = start_sec; self.results[index].end_sec = end_sec
        def undo(): self.results[index].start_sec = old_start; self.results[index].end_sec = old_end
        do()
        self._undo_stack.push(_ResultAction(f"调整片段 {index + 1}", do, undo))
        self._dirty = True
        return True

    def undo(self) -> str | None:
        desc = self._undo_stack.undo()
        if desc: self._dirty = True
        return desc

    def redo(self) -> str | None:
        desc = self._undo_stack.redo()
        if desc: self._dirty = True
        return desc

    @property
    def can_undo(self) -> bool: return self._undo_stack.can_undo

    @property
    def can_redo(self) -> bool: return self._undo_stack.can_redo

    @property
    def is_dirty(self) -> bool: return self._dirty

    @property
    def result_count(self) -> int: return len(self.results)

    # ── 序列化 ──

    def to_dict(self) -> dict:
        return {
            "version": "3.0",
            "platform": self._platform,
            "last_detection": datetime.now().isoformat(),
            "source": {
                "path": self.source.path,
                "width": self.source.width,
                "height": self.source.height,
                "fps": self.source.fps,
                "total_frames": self.source.total_frames,
            },
            "mobile": self._mobile.to_dict(),
            "pc": self._pc.to_dict(),
            "export": {
                "output_path": self.export.output_path,
                "ffmpeg_path": self.export.ffmpeg_path,
                "quality": self.export.quality,
                "preset": self.export.preset,
                "use_gpu": self.export.use_gpu,
            },
        }

    def save_json(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        self._dirty = False

    @classmethod
    def load_json(cls, path: str) -> "Project":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        proj = cls()
        proj._platform = data.get("platform", "mobile")
        src = data.get("source", {})
        if src.get("path"):
            proj.source = VideoSource(
                path=src["path"], width=src.get("width", 0),
                height=src.get("height", 0), fps=src.get("fps", 30.0),
                total_frames=src.get("total_frames", 0),
            )
            # v3: 加载两个平台独立状态
            for pf_key in ("mobile", "pc"):
                pf_data = data.get(pf_key, {})
                if pf_data:
                    state = proj._mobile if pf_key == "mobile" else proj._pc
                    restored = PlatformState.from_dict(pf_data,
                        proj.source.path, proj.source.width, proj.source.height,
                        proj.source.fps, proj.source.total_frames)
                    state.detection = restored.detection
                    state.annotations = restored.annotations
                    state.results = restored.results
                    state.preset_file = restored.preset_file
        # v2 兼容：旧格式只有"detection"/"results" → 塞进 mobile
        else:
            v2_det = data.get("detection", {})
            v2_results = data.get("results", [])
            if v2_det or v2_results:
                state = proj._mobile
                restored = PlatformState.from_dict(
                    {"detection": v2_det, "results": v2_results},
                    proj.source.path, proj.source.width, proj.source.height,
                    proj.source.fps, proj.source.total_frames,
                )
                state.detection = restored.detection
                state.results = restored.results
        exp = data.get("export", {})
        if exp:
            for k in ("output_path", "ffmpeg_path", "quality", "preset", "use_gpu"):
                if k in exp:
                    setattr(proj.export, k, exp[k])
        proj.last_detection = data.get("last_detection", "")
        proj._dirty = False
        return proj
