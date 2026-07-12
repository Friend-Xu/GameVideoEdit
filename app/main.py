"""应用入口 —— 环境验证 + 启动 GUI。

用法: .venv/Scripts/python app/main.py
"""

import os
import sys
from pathlib import Path


def _setup_path() -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    engines = root / "engines"
    if str(engines) not in sys.path:
        sys.path.insert(0, str(engines))


def _setup_env() -> None:
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"


def verify_environment() -> list[str]:
    issues: list[str] = []
    try:
        from app.core.model_loader import ModelManager
        mm = ModelManager()
        missing = [k for k, v in mm.verify_all_models().items() if not v]
        if missing:
            issues.append(f"缺少模型文件: {missing}")
    except Exception as e:
        issues.append(f"模型管理器初始化失败: {e}")

    import subprocess
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5, check=True)
    except Exception:
        issues.append("FFmpeg 未安装或不在 PATH 中")

    try:
        import torch
        if not torch.cuda.is_available():
            issues.append("CUDA 不可用，OCR 将使用 CPU 模式")
    except ImportError:
        issues.append("PyTorch 未安装")
    return issues


def main() -> int:
    _setup_path(); _setup_env()

    from app.utils.logger import setup
    setup(Path(__file__).resolve().parent.parent)

    import logging
    issues = verify_environment()
    if issues:
        for i in issues:
            logging.getLogger("env").warning(i)

    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("GameVideoEdit")
    app.setOrganizationName("GameVideoEdit")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
