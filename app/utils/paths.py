"""路径解析工具 —— 项目中一切路径的唯一来源。

所有路径基于 PROJECT_ROOT 计算，不依赖硬编码绝对路径。
"""

from pathlib import Path

# 项目根目录 = 本文件向上3级 (app/utils/paths.py -> app/utils -> app -> root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_path(*parts: str) -> Path:
    """获取项目内任意相对路径的绝对路径"""
    return PROJECT_ROOT.joinpath(*parts)


def models_dir() -> Path:
    return get_path("models")


def easyocr_models_dir() -> Path:
    return get_path("models", "easyocr")


def easyocr_engine_dir() -> Path:
    return get_path("engines", "easyocr")


def assets_dir() -> Path:
    return get_path("assets")


def icons_dir() -> Path:
    return get_path("assets", "icons")


def fonts_dir() -> Path:
    return get_path("assets", "fonts")


def config_dir() -> Path:
    return get_path("config")


def data_dir() -> Path:
    return get_path("data")


def videos_dir() -> Path:
    return get_path("data", "videos")


def output_dir() -> Path:
    return get_path("data", "output")


def temp_dir() -> Path:
    return get_path("temp")


def ensure_dirs() -> None:
    """确保所有运行时需要的目录存在"""
    for d in [models_dir(), data_dir(), videos_dir(), output_dir(), temp_dir()]:
        d.mkdir(parents=True, exist_ok=True)
