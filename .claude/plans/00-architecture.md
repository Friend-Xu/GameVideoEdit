# 模块化架构设计

## 设计目标

1. **环境隔离**: venv嵌入项目根目录，不依赖任何外部环境
2. **模块化**: 单文件单职责,每模块不超过500行
3. **可测试**: 核心逻辑与UI分离,可独立测试
4. **可并行开发**: 模块间接口明确,多Agent可并行开发
5. **统一管理**: 模型、配置、资源集中管理

## 目标目录结构

```
D:\Github\GameVideoEdit\
│
├── .venv/                          # Python虚拟环境(嵌入根目录)
│
├── app/                            # 主应用包
│   ├── __init__.py
│   ├── main.py                     # 唯一入口,只负责启动
│   │
│   ├── core/                       # 核心业务逻辑(纯Python,不依赖Qt)
│   │   ├── __init__.py
│   │   ├── player.py               # 视频播放引擎(decord+OpenCV)
│   │   ├── annotator.py            # ROI标注数据模型
│   │   ├── detector.py             # OCR检测引擎
│   │   ├── exporter.py             # FFmpeg导出引擎
│   │   ├── pipeline.py             # 流水线编排器
│   │   ├── keywords.py             # 关键词匹配引擎
│   │   └── model_loader.py         # 模型加载管理器(单例)
│   │
│   ├── ui/                         # GUI组件(依赖PySide6+core)
│   │   ├── __init__.py
│   │   ├── main_window.py          # 主窗口
│   │   ├── video_player.py         # 视频播放器Widget
│   │   ├── overlay.py              # 标注覆盖层Widget
│   │   ├── ocr_dialog.py           # OCR处理对话框
│   │   ├── export_dialog.py        # 导出对话框
│   │   ├── log_window.py           # 日志窗口
│   │   └── styles/                 # QSS样式文件
│   │       ├── light.qss
│   │       └── dark.qss
│   │
│   ├── workers/                    # 后台工作线程
│   │   ├── __init__.py
│   │   ├── ocr_worker.py           # OCR工作线程
│   │   └── export_worker.py        # 导出工作线程
│   │
│   └── utils/                      # 工具函数
│       ├── __init__.py
│       ├── config.py               # 配置管理
│       ├── paths.py                # 路径解析(一切路径的基准)
│       ├── video_repair.py         # 视频修复工具
│       └── gpu_detect.py           # GPU检测工具
│
├── engines/                        # 绑定的第三方引擎
│   └── easyocr/                    # EasyOCR 1.7.2(保持现有)
│
├── models/                         # 模型文件(统一管理)
│   ├── easyocr/                    # EasyOCR模型
│   │   ├── craft_mlt_25k.pth       # 文本检测模型(~80MB)
│   │   └── zh_sim_g2.pth           # 中文识别模型(~21MB)
│   └── model_registry.json         # 模型清单
│
├── assets/                         # 静态资源
│   ├── icons/                      # 图标文件
│   ├── fonts/                      # 字体文件
│   └── prompts/                    # LLM提示词模板
│
├── config/                         # 配置文件
│   ├── default.yaml                # 默认配置
│   ├── keywords.yaml               # 关键词配置
│   └── presets/                    # 导出预设
│
├── data/                           # 用户数据目录
│   ├── videos/                     # 输入视频
│   └── output/                     # 输出剪辑
│
├── temp/                           # 临时文件(不提交Git)
│
├── tests/                          # 测试套件
│   ├── test_core/
│   └── test_ui/
│
├── scripts/                        # 开发/部署脚本
│   ├── setup_venv.bat
│   ├── setup_venv.sh
│   └── verify_models.py
│
├── requirements.txt                # 项目依赖
├── requirements-dev.txt            # 开发依赖
├── CLAUDE.md
└── .gitignore
```

## 模块依赖关系

```
main.py (入口)
  └── ui/main_window.py
        ├── ui/video_player.py → core/player.py, core/annotator.py, ui/overlay.py
        ├── ui/ocr_dialog.py → core/detector.py, core/keywords.py, workers/ocr_worker.py
        └── ui/export_dialog.py → core/exporter.py, workers/export_worker.py

依赖规则:
  core/ → 不依赖 ui/ 和 PySide6 (纯Python,可独立测试)
  ui/   → 依赖 core/ 和 PySide6
  workers/ → 依赖 core/
  utils/ → 不依赖任何业务模块(只依赖标准库+第三方库)
```

## 核心接口定义

### core/player.py
```python
@dataclass
class VideoInfo:
    path: str; width: int; height: int
    fps: float; total_frames: int; rotation: int

class VideoPlayer:
    """视频播放引擎(纯逻辑,不含UI)"""
    def open(video_path: str) -> VideoInfo
    def seek(frame: int) -> np.ndarray       # BGR帧
    def seek_time(seconds: float) -> np.ndarray
    def next_frame() -> np.ndarray | None
    def close()
    @property video_info: VideoInfo
```

### core/annotator.py
```python
@dataclass
class Region:
    id: int; label: str
    center_x: float; center_y: float  # YOLO归一化
    width: float; height: float

class AnnotationStore:
    def load_json(path: str) -> AnnotationStore
    def save_json(path: str)
    def add_region(label, pixel_x, pixel_y, w, h, img_w, img_h) -> Region
    def remove_region(id: int)
    def to_pixel_rois(img_w, img_h) -> list[PixelROI]
    @property regions: list[Region]
```

### core/detector.py
```python
@dataclass
class OCRResult:
    text: str; confidence: float; bbox: tuple

class OCRDetector:
    """OCR引擎(单例共享模型)"""
    def detect(roi_image: np.ndarray) -> list[OCRResult]
    def detect_batch(rois: list[np.ndarray]) -> list[list[OCRResult]]

class KeywordMatcher:
    def match(text: str) -> MatchResult | None

@dataclass
class MatchResult:
    pattern_id: str; action: str; weapon: str | None; player: str | None
```

### core/exporter.py
```python
@dataclass
class TimeRange: start_sec: float; end_sec: float
@dataclass
class GPUPreference: decoder: str; encoder: str; hwaccel: str

class VideoExporter:
    @staticmethod
    def detect_gpu() -> GPUPreference
    def combine_clips(video, ranges: list[TimeRange], output, progress_cb) -> bool
```

## 迁移步骤

1. 创建目录骨架(不改旧代码)
2. 迁移静态文件(模型、图标、字体)
3. 按依赖顺序重写模块: utils → core → workers → ui → main.py
4. 验证旧代码仍可运行后才删除
