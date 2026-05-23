# 环境隔离与模型管理方案

## 一、环境隔离方案

### 虚拟环境

虚拟环境嵌入项目根目录 `.venv/`:

```bash
# Windows: scripts/setup_venv.bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Linux/Mac: scripts/setup_venv.sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 路径管理 (app/utils/paths.py)

所有路径基于项目根目录解析,不依赖任何硬编码绝对路径:

```python
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

def get_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)

def models_dir() -> Path:      return get_path("models")
def engines_dir() -> Path:     return get_path("engines")
def easyocr_dir() -> Path:     return get_path("engines", "easyocr")
def assets_dir() -> Path:      return get_path("assets")
def config_dir() -> Path:      return get_path("config")
def data_dir() -> Path:        return get_path("data")
def temp_dir() -> Path:        return get_path("temp")
```

### 入口文件 (app/main.py)

```python
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# 引擎路径
_engines = _project_root / "engines"
if str(_engines) not in sys.path:
    sys.path.insert(0, str(_engines))

import os
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
```

### 启动时环境验证

```python
def verify_environment() -> list[str]:
    """返回问题列表,空列表表示一切正常"""
    issues = []
    # 检查模型文件
    from app.core.model_loader import ModelManager
    missing = [k for k, v in ModelManager().verify_all_models().items() if not v]
    if missing:
        issues.append(f"缺少模型: {missing}")
    # 检查FFmpeg
    import subprocess
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5, check=True)
    except:
        issues.append("FFmpeg未安装")
    # 检查CUDA
    try:
        import torch
        if not torch.cuda.is_available():
            issues.append("CUDA不可用,OCR使用CPU模式")
    except ImportError:
        issues.append("PyTorch未安装")
    return issues
```

## 二、模型管理方案

### 模型目录结构

```
models/
├── model_registry.json    # 模型清单(唯一真相来源)
├── easyocr/               # EasyOCR模型
│   ├── craft_mlt_25k.pth  # ~80MB
│   └── zh_sim_g2.pth      # ~21MB
└── README.md              # 模型说明
```

### model_registry.json

```json
{
  "version": "1.0",
  "models": {
    "easyocr_craft": {
      "name": "CRAFT Text Detector",
      "file": "easyocr/craft_mlt_25k.pth",
      "size_mb": 79.3,
      "source": "https://github.com/JaidedAI/EasyOCR",
      "required": true
    },
    "easyocr_zh_sim": {
      "name": "Chinese Simplified Recognizer",
      "file": "easyocr/zh_sim_g2.pth",
      "size_mb": 20.9,
      "source": "https://github.com/JaidedAI/EasyOCR",
      "required": true
    }
  },
  "total_size_mb": 100.2
}
```

### 模型加载器 (app/core/model_loader.py)

```python
class ModelManager:
    """单例模型管理器 - 所有模块共享同一个EasyOCR Reader"""
    _instance = None
    _readers: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.model_dir = Path(__file__).resolve().parent.parent.parent / "models"
        self.registry = json.load(open(self.model_dir / "model_registry.json"))

    def get_model_path(self, model_id: str) -> Path:
        return self.model_dir / self.registry["models"][model_id]["file"]

    def get_easyocr_reader(self, gpu: bool = True):
        """获取共享Reader(不重复加载模型)"""
        cache_key = f"easyocr_gpu={gpu}"
        if cache_key not in self._readers:
            import easyocr
            self._readers[cache_key] = easyocr.Reader(
                ['ch_sim', 'en'],
                model_storage_directory=str(self.model_dir / "easyocr"),
                download_enabled=False,
                gpu=gpu
            )
        return self._readers[cache_key]

    def verify_all_models(self) -> dict[str, bool]:
        return {mid: (self.model_dir / info["file"]).exists()
                for mid, info in self.registry["models"].items()
                if info.get("required")}

    def release_all(self):
        for reader in self._readers.values():
            try:
                if hasattr(reader, 'detector'): reader.detector.destroy()
                if hasattr(reader, 'recognizer'): reader.recognizer.destroy()
            except: pass
        self._readers.clear()
```

## 三、依赖管理

### requirements.txt (锁定版本)

```
PySide6==6.9.0
PySide6_Addons==6.9.0
PySide6_Essentials==6.9.0
shiboken6==6.9.0
decord>=0.6.0
opencv-python-headless>=4.8.0
torch>=2.0.0
torchvision>=0.15.0
scipy>=1.10.0
numpy>=1.24.0
Pillow>=9.0.0
scikit-image>=0.19.0
python-bidi>=0.4.2
PyYAML>=6.0
Shapely>=2.0.0
pyclipper>=1.3.0
ninja>=1.11.0
librosa>=0.10.0
PySceneDetect>=0.6.0
```

### .gitignore

```
.venv/
__pycache__/
*.pyc
.idea/
.vscode/
data/videos/
data/output/
temp/
*.tmp
```

## 四、临时文件管理

临时文件统一放在 `temp/` 目录(项目内,不提交Git):

```
temp/
├── repaired_videos/
├── clips/
├── ocr_cache/
└── logs/
```

```python
class TempFileManager:
    def __init__(self):
        self._dir = PROJECT_ROOT / "temp"
        self._dir.mkdir(exist_ok=True)
        self._files: set[Path] = set()

    def create(self, prefix: str, suffix: str) -> Path:
        from uuid import uuid4
        path = self._dir / f"{prefix}_{uuid4().hex[:8]}{suffix}"
        self._files.add(path)
        return path

    def cleanup(self):
        for p in self._files:
            try:
                if p.exists(): p.unlink()
            except OSError: pass
        self._files.clear()

    def cleanup_orphans(self, max_age_hours=24):
        import time
        cutoff = time.time() - max_age_hours * 3600
        for p in self._dir.rglob("*"):
            if p.is_file() and p.stat().st_mtime < cutoff:
                try: p.unlink()
                except OSError: pass
```
