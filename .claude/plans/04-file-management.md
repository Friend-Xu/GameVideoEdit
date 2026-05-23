# 文件管理与开发规范

## 一、文件命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| Python模块 | `snake_case` | `video_player.py`, `ocr_worker.py` |
| Python类 | `PascalCase` | `VideoPlayer`, `OCRWorker` |
| 私有模块 | `_前缀` | `_internal.py` |
| 测试文件 | `test_<模块>.py` | `test_player.py` |
| 图标资源 | `kebab-case.png` | `app-icon.png` |
| 字体 | `PascalCase.ttf` | `SimHei.ttf` |
| 样式 | `snake_case.qss` | `light_theme.qss` |
| 配置 | `snake_case.yaml` | `default.yaml` |
| 标注文件 | `{video}_labels.json` | `RPReplay_Final_labels.json` |
| 剪辑数据 | `{video}_clips.json` | `RPReplay_Final_clips.json` |
| 输出视频 | `{video}_highlights.mp4` | `RPReplay_Final_highlights.mp4` |

## 二、目录职责与Git策略

| 目录 | 职责 | Git |
|------|------|-----|
| `.venv/` | Python虚拟环境 | ❌ |
| `app/core/` | 纯Python核心逻辑 | ✅ |
| `app/ui/` | PySide6 GUI | ✅ |
| `app/workers/` | QThread后台线程 | ✅ |
| `app/utils/` | 工具函数 | ✅ |
| `engines/` | 第三方引擎副本 | ✅ |
| `models/` | ML模型文件 | ✅ (LFS for >50MB) |
| `assets/` | 图标/字体 | ✅ |
| `config/` | YAML配置模板 | ✅ |
| `data/` | 用户视频/输出 | ❌ |
| `temp/` | 临时文件 | ❌ |
| `tests/` | 测试代码 | ✅ |
| `scripts/` | 部署/验证脚本 | ✅ |
| `.claude/plans/` | 开发计划 | ✅ |

## 三、导入规范

```python
# ✅ 正确: 从app包导入(基于PROJECT_ROOT)
from app.utils.paths import models_dir
from app.core.player import VideoPlayer
from app.core.model_loader import ModelManager

# ✅ 引擎导入(在model_loader.py中统一处理)
import sys
from pathlib import Path
_engine_path = str(Path(__file__).resolve().parent.parent.parent / "engines" / "easyocr")
if _engine_path not in sys.path:
    sys.path.insert(0, _engine_path)
import easyocr

# ❌ 错误: 相对导入(跨模块不可靠)
from ..utils.paths import models_dir

# ❌ 错误: 硬编码绝对路径
sys.path.insert(0, r"D:\Github\GameVideoEdit\engines\easyocr")
```

### 导入层次规则

```
main.py (可导入任何模块)
  ui/ (可导入 core/ 和 workers/)
    workers/ (可导入 core/)
      core/ (可导入 utils/, engines/)
        utils/ (只导入标准库和第三方库)
```

**禁止**: ui/ 不能导入 core/ 不存在的类; core/ 不能导入 ui/ 的任何内容。

## 四、异常处理规范

### 异常层次 (app/utils/exceptions.py)

```python
class GameVideoEditError(Exception): pass
class VideoError(GameVideoEditError): pass
class VideoOpenError(VideoError): pass
class VideoDecodeError(VideoError): pass
class OCRError(GameVideoEditError): pass
class ModelNotFoundError(GameVideoEditError): pass
class ExportError(GameVideoEditError): pass
class ConfigError(GameVideoEditError): pass
```

### 处理原则

| 层 | 策略 |
|----|------|
| core/ | 抛出明确类型异常,不吞异常 |
| ui/ | 捕获→QMessageBox或日志展示给用户 |
| workers/ | 捕获→error信号传递到UI |
| **禁止** | `except: pass` 和裸 `except Exception: pass` |

## 五、编码规范要点

1. PEP 8, 行长≤100, 所有公共方法有类型提示
2. 公共API写简短docstring,不需要冗余的参数描述
3. 魔法数字用命名常量替代
4. 跨线程通信用 `threading.Event` 替代裸 `bool`
5. 模板方法在 `config/` 中,用户特定值在运行时环境变量
6. 样式用QSS文件,不嵌入Python字符串

## 六、配置管理

### 配置文件层次
```
config/default.yaml     ← 默认值(提交Git)
config/keywords.yaml    ← 关键词(提交Git)
%APPDATA%/GameVideoEdit/user_config.yaml ← 用户覆盖(运行时)
```

### 加载优先级: 用户配置 > 环境变量 > 默认配置

## 七、配置文件结构 (config/default.yaml)

```yaml
app:
  name: "GameVideoEdit"
  version: "2.0.0"
  language: "zh_CN"

ocr:
  engine: "easyocr"
  gpu: true
  languages: ["ch_sim", "en"]
  confidence_threshold: 0.5

detection:
  keyword_file: "keywords.yaml"
  padding_before: 10.0
  padding_after: 10.0
  merge_overlap: true
  max_gap_seconds: 30.0

export:
  ffmpeg_path: "ffmpeg"
  default_encoder: "auto"
  output_format: "mp4"
  quality: 23

ui:
  theme: "light"
  default_window_size: [1200, 800]
```
