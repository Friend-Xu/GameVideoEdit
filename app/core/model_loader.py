"""统一模型加载管理器 —— 单例模式。

所有模块通过 ModelManager 获取 EasyOCR Reader，
确保模型只加载一次，避免多线程下重复加载导致 GPU OOM。
"""

import json
import sys
import threading
from pathlib import Path
from typing import Any

from app.utils.paths import models_dir, easyocr_engine_dir, easyocr_models_dir, rapidocr_models_dir


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
    _rapidocr_local = threading.local()

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

    # ── 统一引擎入口 ──

    def get_engine(self, engine_type: str = "rapidocr", gpu: bool = True,
                   languages: list[str] | None = None):
        """统一引擎入口：ModelManager 是所有 OCR 引擎的唯一来源。

        engine_type: "rapidocr" | "easyocr"
        """
        if engine_type == "rapidocr":
            return self._get_rapidocr_engine(gpu)
        return self._get_easyocr_reader(gpu, languages)

    # ── EasyOCR ──

    def _get_easyocr_reader(self, gpu: bool = True, languages: list[str] | None = None):
        """获取共享的 EasyOCR Reader 实例（单例缓存）。"""
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

    def _get_rapidocr_engine(self, gpu: bool = True):
        """获取 RapidOCR 引擎实例（thread-local 缓存，每线程独立 GPU session）。"""
        cache_key = f"rapidocr_gpu={gpu}"
        store = getattr(self._rapidocr_local, "readers", None)
        if store is None:
            store = {}
            self._rapidocr_local.readers = store
        if cache_key not in store:
            from rapidocr import RapidOCR
            mdir = rapidocr_models_dir()
            store[cache_key] = RapidOCR(params={
                "Det.model_path": str(mdir / "ch_PP-OCRv4_det_infer.onnx"),
                "Cls.model_path": str(mdir / "ch_ppocr_mobile_v2.0_cls_infer.onnx"),
                "Rec.model_path": str(mdir / "ch_PP-OCRv4_rec_infer.onnx"),
                "Rec.rec_keys_path": str(mdir / "ppocr_keys_v1.txt"),
                "EngineConfig.onnxruntime.use_cuda": gpu,
                "EngineConfig.onnxruntime.cuda_ep_cfg.arena_extend_strategy": "kSameAsRequested",
                "EngineConfig.onnxruntime.cuda_ep_cfg.gpu_mem_limit": 1024 * 1024 * 1024,
                "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": "DEFAULT",
                "Det.box_thresh": 0.3,
                "Det.limit_side_len": 480,
                "Global.max_side_len": 480,
            })
        return store[cache_key]

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
