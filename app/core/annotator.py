"""标注数据模型 —— 纯逻辑，不含UI。

YOLO归一化坐标 ↔ 像素坐标互转，JSON 读写。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Region:
    id: int
    label: str
    center_x: float
    center_y: float
    width: float
    height: float


@dataclass
class PixelROI:
    x: int; y: int; w: int; h: int


@dataclass
class AnnotationData:
    video_path: str = ""
    width: int = 0
    height: int = 0
    fps: float = 30.0
    total_frames: int = 0
    rotation: int = 0
    regions: list[Region] = field(default_factory=list)


class AnnotationStore:
    """标注数据管理器"""

    def __init__(self, video_path: str = "", width: int = 0, height: int = 0,
                 fps: float = 30.0, total_frames: int = 0, rotation: int = 0):
        self._data = AnnotationData(video_path, width, height, fps, total_frames, rotation)
        self._next_id: int = 1

    @classmethod
    def load_json(cls, path: str) -> "AnnotationStore":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        store = cls(
            video_path=raw.get("video_path", ""),
            width=raw.get("width", 0),
            height=raw.get("height", 0),
            fps=raw.get("fps", 30.0),
            total_frames=raw.get("total_frames", 0),
            rotation=raw.get("rotation", 0),
        )
        max_id = 0
        for r in raw.get("regions", []):
            store._data.regions.append(Region(
                id=r["id"], label=r["label"],
                center_x=r["center_x"], center_y=r["center_y"],
                width=r["width"], height=r["height"],
            ))
            if r["id"] > max_id:
                max_id = r["id"]
        store._next_id = max_id + 1
        return store

    def save_json(self, path: str) -> None:
        data = self.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def to_dict(self) -> dict:
        return {
            "video_path": self._data.video_path,
            "width": self._data.width,
            "height": self._data.height,
            "fps": self._data.fps,
            "total_frames": self._data.total_frames,
            "rotation": self._data.rotation,
            "regions": [
                {"id": r.id, "label": r.label,
                 "center_x": r.center_x, "center_y": r.center_y,
                 "width": r.width, "height": r.height}
                for r in self._data.regions
            ],
        }

    def add_region(self, label: str, pixel_x: int, pixel_y: int,
                   pixel_w: int, pixel_h: int, img_w: int, img_h: int) -> Region:
        cx = (pixel_x + pixel_w / 2) / img_w
        cy = (pixel_y + pixel_h / 2) / img_h
        nw = pixel_w / img_w
        nh = pixel_h / img_h
        region = Region(self._next_id, label, round(cx, 6), round(cy, 6), round(nw, 6), round(nh, 6))
        self._data.regions.append(region)
        self._next_id += 1
        return region

    def remove_region(self, region_id: int) -> bool:
        for i, r in enumerate(self._data.regions):
            if r.id == region_id:
                del self._data.regions[i]
                return True
        return False

    def to_pixel_rois(self, img_w: int, img_h: int) -> list[PixelROI]:
        rois: list[PixelROI] = []
        for r in self._data.regions:
            px = max(0, int((r.center_x - r.width / 2) * img_w))
            py = max(0, int((r.center_y - r.height / 2) * img_h))
            pw = min(int(r.width * img_w), img_w - px)
            ph = min(int(r.height * img_h), img_h - py)
            rois.append(PixelROI(px, py, pw, ph))
        return rois

    @property
    def regions(self) -> list[Region]:
        return list(self._data.regions)

    @property
    def region_count(self) -> int:
        return len(self._data.regions)

    @property
    def video_path(self) -> str:
        return self._data.video_path

    @video_path.setter
    def video_path(self, value: str):
        self._data.video_path = value

    @property
    def rotation(self) -> int:
        return self._data.rotation

    @rotation.setter
    def rotation(self, value: int):
        self._data.rotation = value
