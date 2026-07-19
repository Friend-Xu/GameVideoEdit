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
    platform: str = "mobile"


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

    @staticmethod
    def _unwrap(entry) -> list[dict]:
        """兼容旧格式 (bare list) 和新格式 ({platform, regions})。"""
        if isinstance(entry, list):
            return entry
        if isinstance(entry, dict):
            return entry.get("regions", [])
        return []

    @staticmethod
    def _platform_of(entry) -> str:
        if isinstance(entry, dict) and "platform" in entry:
            return entry["platform"]
        return "mobile"

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

    def list_names(self, platform: str | None = None) -> list[str]:
        result = []
        templates = self._load()["templates"]
        for name, entry in templates.items():
            if platform is None or self._platform_of(entry) == platform:
                result.append(name)
        return result

    def get(self, name: str) -> ROITemplate | None:
        entry = self._load()["templates"].get(name)
        if entry is None:
            return None
        regions = self._unwrap(entry)
        platform = self._platform_of(entry)
        return ROITemplate(name=name, regions=list(regions), platform=platform)

    def save(self, name: str, regions: list[dict], platform: str = "mobile"):
        self._load()["templates"][name] = {
            "platform": platform,
            "regions": regions,
        }
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
        d = self._load().get("default", "")
        if isinstance(d, str):
            return d
        return d.get("mobile", "") if isinstance(d, dict) else ""

    def _default_for(self, platform: str) -> str:
        d = self._load().get("default", "")
        if isinstance(d, str):
            return d if platform == "mobile" else ""
        return d.get(platform, "") if isinstance(d, dict) else ""

    def set_default(self, name: str, platform: str = "mobile"):
        if name and name not in self._load()["templates"]:
            return
        d = self._load().get("default", "")
        if not isinstance(d, dict):
            d = {}
        d[platform] = name
        self._load()["default"] = d
        self._save()

    def get_default(self) -> ROITemplate | None:
        name = self.default_name
        if not name:
            return None
        return self.get(name)

    def get_default_for(self, platform: str) -> ROITemplate | None:
        name = self._default_for(platform)
        if not name:
            return None
        return self.get(name)

    def reload(self):
        self._loaded = False
