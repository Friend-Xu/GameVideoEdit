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
    pattern_id: str = ""
    action: str = ""
    actor: str = ""
    raw_text: str = ""
    source: str = "text"

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
# Project
# ---------------------------------------------------------------------------

class Project:
    """统一工作项目 — 整个会话的唯一数据源"""

    def __init__(self):
        self.source = VideoSource()
        self.annotations = AnnotationStore()
        self.detection = DetectionConfig()
        self.results: list[ClipResult] = []
        self.export = ExportConfig()
        self._undo_stack = UndoStack()
        self._dirty = False
        self.last_detection: str = ""

    # ---- 视频 ----

    def set_video(self, path: str, width: int, height: int,
                  fps: float, total_frames: int):
        self.source = VideoSource(
            path=path, width=width, height=height,
            fps=fps, total_frames=total_frames,
        )
        self.annotations = AnnotationStore(
            video_path=path, width=width, height=height,
            fps=fps, total_frames=total_frames,
        )
        self.export.output_path = os.path.join(
            self.source.dirname, f"{self.source.basename}_highlights.mp4")
        self._auto_load_roi()
        self._auto_load_project()

    # ---- ROI 自动管理 ----

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

    def _auto_load_roi(self):
        path = self.roi_path
        if path and os.path.exists(path):
            try:
                self.annotations = AnnotationStore.load_json(path)
                return
            except Exception:
                pass
        tmpl = ROITemplateManager().get_default()
        if tmpl and tmpl.regions:
            self.annotations.replace_regions(tmpl.regions)
            self.auto_save_roi()

    def _auto_load_project(self):
        path = self.project_path
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                src = data.get("source", {})
                src_path = os.path.abspath(src.get("path", ""))
                my_path = os.path.abspath(self.source.path)
                if src_path == my_path:
                    self.results.clear()
                    for r in data.get("results", []):
                        self.results.append(ClipResult(
                            start_sec=r["start_sec"], end_sec=r["end_sec"],
                            pattern_id=r.get("pattern_id", ""),
                            action=r.get("action", ""), actor=r.get("actor", ""),
                            raw_text=r.get("raw_text", ""),
                            source=r.get("source", "text"),
                        ))
                    self.last_detection = data.get("last_detection", "")
                    det = data.get("detection", {})
                    if det:
                        cfg = self.detection
                        for k in ("mode", "interval_sec", "skip_frames",
                                   "post_detect_skip_sec", "padding_before",
                                   "padding_after", "merge_gap", "num_threads",
                                   "rotation"):
                            if k in det:
                                setattr(cfg, k, det[k])
                        if "allowed_actors" in det and det["allowed_actors"]:
                            cfg.allowed_actors = set(det["allowed_actors"])
                    exp = data.get("export", {})
                    if exp:
                        for k in ("output_path", "ffmpeg_path", "quality", "preset", "use_gpu"):
                            if k in exp:
                                setattr(self.export, k, exp[k])
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

    # ---- 片段结果（带 undo/redo） ----

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
        self._undo_stack.push(
            _ResultAction(f"删除片段 {index + 1}", do, undo))
        self._dirty = True
        return removed

    def adjust_result(self, index: int, start_sec: float,
                      end_sec: float) -> bool:
        if index < 0 or index >= len(self.results):
            return False
        old_start = self.results[index].start_sec
        old_end = self.results[index].end_sec

        def do():
            self.results[index].start_sec = start_sec
            self.results[index].end_sec = end_sec

        def undo():
            self.results[index].start_sec = old_start
            self.results[index].end_sec = old_end

        do()
        self._undo_stack.push(
            _ResultAction(f"调整片段 {index + 1}", do, undo))
        self._dirty = True
        return True

    def undo(self) -> str | None:
        desc = self._undo_stack.undo()
        if desc:
            self._dirty = True
        return desc

    def redo(self) -> str | None:
        desc = self._undo_stack.redo()
        if desc:
            self._dirty = True
        return desc

    @property
    def can_undo(self) -> bool:
        return self._undo_stack.can_undo

    @property
    def can_redo(self) -> bool:
        return self._undo_stack.can_redo

    # ---- 序列化 ----

    def to_dict(self) -> dict:
        return {
            "version": "2.0",
            "last_detection": datetime.now().isoformat(),
            "source": {
                "path": self.source.path,
                "width": self.source.width,
                "height": self.source.height,
                "fps": self.source.fps,
                "total_frames": self.source.total_frames,
            },
            "detection": {
                "mode": self.detection.mode,
                "interval_sec": self.detection.interval_sec,
                "skip_frames": self.detection.skip_frames,
                "post_detect_skip_sec": self.detection.post_detect_skip_sec,
                "padding_before": self.detection.padding_before,
                "padding_after": self.detection.padding_after,
                "merge_gap": self.detection.merge_gap,
                "num_threads": self.detection.num_threads,
                "rotation": self.detection.rotation,
                "allowed_actors": (sorted(self.detection.allowed_actors)
                                   if self.detection.allowed_actors else None),
            },
            "results": [
                {
                    "start_sec": r.start_sec,
                    "end_sec": r.end_sec,
                    "pattern_id": r.pattern_id,
                    "action": r.action,
                    "actor": r.actor,
                    "raw_text": r.raw_text,
                    "source": r.source,
                }
                for r in self.results
            ],
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
        src = data.get("source", {})
        if src.get("path"):
            proj.set_video(
                src["path"], src.get("width", 0), src.get("height", 0),
                src.get("fps", 30.0), src.get("total_frames", 0),
            )
            proj.results.clear()
        det = data.get("detection", {})
        if det:
            cfg = proj.detection
            for k in ("mode", "interval_sec", "skip_frames",
                       "post_detect_skip_sec", "padding_before",
                       "padding_after", "merge_gap", "num_threads",
                       "rotation"):
                if k in det:
                    setattr(cfg, k, det[k])
            if "allowed_actors" in det and det["allowed_actors"]:
                cfg.allowed_actors = set(det["allowed_actors"])
        for r in data.get("results", []):
            proj.results.append(ClipResult(
                start_sec=r["start_sec"], end_sec=r["end_sec"],
                pattern_id=r.get("pattern_id", ""),
                action=r.get("action", ""), actor=r.get("actor", ""),
                raw_text=r.get("raw_text", ""),
                source=r.get("source", "text"),
            ))
        exp = data.get("export", {})
        if exp:
            for k in ("output_path", "ffmpeg_path", "quality", "preset", "use_gpu"):
                if k in exp:
                    setattr(proj.export, k, exp[k])
        proj.last_detection = data.get("last_detection", "")
        proj._dirty = False
        return proj

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def result_count(self) -> int:
        return len(self.results)
