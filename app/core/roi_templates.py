"""ROI 模板管理器 —— 一次标注，多视频复用。

模板使用 YOLO 归一化坐标（0~1），与视频分辨率无关。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.paths import config_dir


@dataclass
class ROITemplate:
    name: str
    regions: list[dict] = field(default_factory=list)


class ROITemplateManager:
    """ROI 模板的 CRUD + 默认模板管理。单例。"""

    _instance: "ROITemplateManager | None" = None

    def __new__(cls) -> "ROITemplateManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    @property
    def _path(self) -> Path:
        return config_dir() / "roi_templates.json"

    def _load(self) -> dict:
        if self._loaded:
            return self._data
        self._data: dict = {}
        path = self._path
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._data["default"] = raw.get("default", "")
                self._data["templates"] = raw.get("templates", {})
            except Exception:
                self._data = {"default": "", "templates": {}}
        else:
            self._data = {"default": "", "templates": {}}
        self._loaded = True
        return self._data

    def _save(self):
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def list_names(self) -> list[str]:
        return list(self._load()["templates"].keys())

    def get(self, name: str) -> ROITemplate | None:
        regions = self._load()["templates"].get(name)
        if regions is None:
            return None
        return ROITemplate(name=name, regions=list(regions))

    def save(self, name: str, regions: list[dict]):
        self._load()["templates"][name] = regions
        self._save()

    def delete(self, name: str) -> bool:
        tmpl = self._load()["templates"]
        if name not in tmpl:
            return False
        del tmpl[name]
        if self._data.get("default") == name:
            self._data["default"] = ""
        self._save()
        return True

    @property
    def default_name(self) -> str:
        return self._load().get("default", "")

    def set_default(self, name: str):
        if name and name not in self._load()["templates"]:
            return
        self._load()["default"] = name
        self._save()

    def get_default(self) -> ROITemplate | None:
        name = self.default_name
        if not name:
            return None
        return self.get(name)

    def reload(self):
        self._loaded = False
