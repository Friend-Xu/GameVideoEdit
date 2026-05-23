"""统一模型加载管理器 —— 单例模式。

所有模块通过 ModelManager 获取 EasyOCR Reader，
确保模型只加载一次，避免多线程下重复加载导致 GPU OOM。
"""

import json
import sys
from pathlib import Path
from typing import Any

from app.utils.paths import models_dir, easyocr_engine_dir, easyocr_models_dir


def _ensure_engine_importable() -> None:
    """确保本地 EasyOCR 引擎目录在 sys.path 中"""
    engine_root = str(easyocr_engine_dir().parent)  # engines/
    if engine_root not in sys.path:
        sys.path.insert(0, engine_root)


class ModelManager:
    """单例模型管理器。

    用法:
        mm = ModelManager()
        reader = mm.get_easyocr_reader(gpu=True)
        # 多次调用 get_easyocr_reader 返回同一缓存实例
    """

    _instance: "ModelManager | None" = None
    _readers: dict[str, Any] = {}

    def __new__(cls) -> "ModelManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._model_dir = models_dir()
        self._registry = self._load_registry()
        _ensure_engine_importable()

    def _load_registry(self) -> dict[str, Any]:
        path = self._model_dir / "model_registry.json"
        if not path.exists():
            return {"models": {}}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_model_path(self, model_id: str) -> Path:
        info = self._registry.get("models", {}).get(model_id, {})
        return self._model_dir / info.get("file", "")

    def get_easyocr_reader(self, gpu: bool = True, languages: list[str] | None = None):
        """获取共享的 EasyOCR Reader 实例。

        同一个 (languages, gpu) 组合只创建一个 Reader。
        """
        if languages is None:
            languages = ["ch_sim", "en"]

        lang_key = "+".join(sorted(languages))
        cache_key = f"easyocr_{lang_key}_gpu={gpu}"

        if cache_key not in self._readers:
            import easyocr

            self._readers[cache_key] = easyocr.Reader(
                lang_list=languages,
                model_storage_directory=str(easyocr_models_dir()),
                download_enabled=False,
                gpu=gpu,
            )
        return self._readers[cache_key]

    def verify_all_models(self) -> dict[str, bool]:
        """验证所有标记为 required 的模型文件是否存在"""
        results: dict[str, bool] = {}
        for model_id, info in self._registry.get("models", {}).items():
            if info.get("required", False):
                path = self._model_dir / info.get("file", "")
                results[model_id] = path.exists()
        return results

    def release_all(self) -> None:
        """释放所有模型资源"""
        for reader in self._readers.values():
            try:
                if hasattr(reader, "detector"):
                    reader.detector.destroy()
            except Exception:
                pass
            try:
                if hasattr(reader, "recognizer"):
                    reader.recognizer.destroy()
            except Exception:
                pass
        self._readers.clear()

    @property
    def registry(self) -> dict[str, Any]:
        return self._registry

    @property
    def model_dir(self) -> Path:
        return self._model_dir
