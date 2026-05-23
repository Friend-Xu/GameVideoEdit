# 多Agent协同开发任务拆解

## 任务拆解原则

1. 每个Agent任务独立可验证,有明确的输入/输出/验收标准
2. 任务按依赖关系分Phase,同Phase内的Agent可并行
3. 每个Agent只修改自己负责的模块,不跨模块修改
4. 先建骨架(接口),后填实现(逻辑)
5. 核心逻辑(core/)与UI(ui/)可完全并行开发

## 任务依赖图

```
Phase 0: 基础设施 [3个Agent并行]
  Agent-0A: 目录创建+文件迁移
  Agent-0B: paths.py + config.py
  Agent-0C: model_loader.py + model_registry.json
      ↓
Phase 1: 核心引擎 [5个Agent并行]
  Agent-1A: core/player.py        ← 依赖 0B
  Agent-1B: core/annotator.py     ← 依赖 0B
  Agent-1C: core/keywords.py      ← 无依赖
  Agent-1D: core/detector.py      ← 依赖 0C, 1C
  Agent-1E: core/exporter.py      ← 依赖 0B
      ↓
Phase 2: 工作线程 [2个Agent并行]
  Agent-2A: workers/ocr_worker.py     ← 依赖 1C, 1D
  Agent-2B: workers/export_worker.py  ← 依赖 1E
      ↓
Phase 3: UI组件 [4-6个Agent并行,部分可提前]
  Agent-3A: ui/styles/*.qss           ← 无依赖(可立即启动)
  Agent-3B: ui/log_window.py          ← 无依赖(可立即启动)
  Agent-3C: ui/overlay.py             ← 依赖 1B
  Agent-3D: ui/video_player.py        ← 依赖 1A, 1B, 3C
  Agent-3E: ui/ocr_dialog.py          ← 依赖 2A, 3B
  Agent-3F: ui/export_dialog.py       ← 依赖 2B, 3B
      ↓
Phase 4: 集成 [3个Agent]
  Agent-4A: ui/main_window.py         ← 依赖 3D,3E,3F
  Agent-4B: app/main.py               ← 依赖 4A
  Agent-4C: 脚本+tests                ← 依赖全部
```

## 并行执行建议

```
第1轮(同时): [0A] [0B] [0C]
第2轮(同时): [1A] [1B] [1C] [1D] [1E] + [3A] [3B] (UI无依赖部分可提前)
第3轮(同时): [2A] [2B]
第4轮(同时): [3C] [3D] [3E] [3F]
第5轮(顺序): [4A] → [4B] → [4C]
```

---

## Phase 0: 基础设施

### Agent-0A: 目录创建与文件迁移
```
输入: 当前项目文件结构
输出: 所有新目录 + 文件迁移(旧文件保留不动)
  - 创建目录树
  - EasyOCR_1_7_2/ → engines/easyocr/
  - EasyOCR_1_7_2/models/*.pth → models/easyocr/
  - Script/*.png,*.ico,*.ttf → assets/
  - 创建 models/model_registry.json
验收:
  - 所有目录存在且结构正确
  - python Script/GUI_Main.py 仍可运行(旧代码不受影响)
  - 模型文件MD5不变
```

### Agent-0B: 路径与配置基础设施
```
输入: 无
输出:
  - app/utils/__init__.py
  - app/utils/paths.py (路径解析,基于PROJECT_ROOT)
  - app/utils/config.py (YAML配置加载+验证)
  - config/default.yaml (默认配置)
验收:
  - from app.utils.paths import models_dir 返回正确绝对路径
  - ConfigLoader().load() 返回有效配置字典
  - 所有路径基于PROJECT_ROOT计算
```

### Agent-0C: 模型管理器
```
输入: models/model_registry.json
输出:
  - app/core/__init__.py
  - app/core/model_loader.py (ModelManager单例)
验收:
  - ModelManager().verify_all_models() 返回 {model_id: True/False}
  - ModelManager().get_easyocr_reader() 返回可用Reader
  - 多次调用 get_easyocr_reader() 返回同一实例(模型不重复加载)
```

---

## Phase 1: 核心引擎

### Agent-1A: 视频播放引擎 (core/player.py, ~250行)
```
功能:
  class VideoPlayer:
    open(path) → VideoInfo
    seek(frame) → np.ndarray
    seek_time(seconds) → np.ndarray
    next_frame() → np.ndarray|None
    close()
  @dataclass VideoInfo: path, width, height, fps, total_frames, rotation
技术: decord(主) + OpenCV(后备)
验收: 能打开MP4/MOV/AVI/MKV; seek(1000)返回正确帧; 含单元测试
```

### Agent-1B: 标注数据模型 (core/annotator.py, ~200行)
```
功能:
  @dataclass Region: id, label, center_x, center_y, width, height
  class AnnotationStore:
    load_json / save_json / add_region / remove_region / to_pixel_rois
验收: 加载现有_labels.json不报错; YOLO↔像素互转正确; 含单元测试
```

### Agent-1C: 关键词匹配引擎 (core/keywords.py, ~200行)
```
输入: config/keywords.yaml
功能: 加载模式 → 编译正则 → 并行匹配 → 提取击杀类型/武器/玩家名
keywords.yaml示例:
  patterns:
    - id: kill_self
      regex: "你使用了(\\S+)淘汰了(\\S+)"
      action: "击杀"
      extract: [weapon, target]
    - id: knockdown_self
      regex: "你使用了(\\S+)击倒了(\\S+)"
      action: "击倒"
      extract: [weapon, target]
    - id: kill_teammate
      regex: "你的队友(\\S+)使用了(\\S+)淘汰了(\\S+)"
      action: "击杀"
      extract: [teammate, weapon, target]
验收: 对游戏文案正确匹配; 对非击杀文本返回None; 含单元测试
```

### Agent-1D: OCR检测引擎 (core/detector.py, ~250行)
```
功能:
  class OCRDetector:
    detect(roi_image: np.ndarray) → list[OCRResult]
    detect_batch(rois) → list[list[OCRResult]]
  class DetectionEngine:
    process_frame(frame, rois) → FrameResult
验收: 对击杀截图正确检测; GPU/CPU模式均可; 含单元测试; 模型不重复加载
```

### Agent-1E: 视频导出引擎 (core/exporter.py, ~300行)
```
功能:
  class VideoExporter:
    @staticmethod detect_gpu() → GPUPreference
    combine_clips(video, ranges, output, progress_cb) → bool
    cut_clip / merge_clips / preprocess_if_needed
关键改进: GPU检测只在初始化时运行一次; 预处理改为按需; 合并用-g 1全I帧
验收: 导出MP4正确; GPU检测覆盖3家; 含单元测试
```

---

## Phase 2: 工作线程

### Agent-2A: OCR工作线程 (workers/ocr_worker.py, ~200行)
```
功能:
  class OCRWorker(QThread):
    signals: progress, detected, log, finished, error
    run() / cancel()  # 使用threading.Event替代裸bool
  class MultiOCRManager(QObject):
    start(video, annotation, num_threads)
    cancel_all()
验收: 多线程正确; cancel安全; 进度聚合正确; 含集成测试
```

### Agent-2B: 导出工作线程 (workers/export_worker.py, ~100行)
```
功能:
  class ExportWorker(QThread):
    signals: progress, log, finished, error
    run() / cancel()
验收: 导出不阻塞UI; cancel安全; 含集成测试
```

---

## Phase 3: UI组件

### Agent-3A: QSS样式文件 (ui/styles/, 无依赖,可立即启动)
```
输出: light.qss + dark.qss
验收: 覆盖常用控件; PCL2风格浅色调; 与PySide6 6.9兼容
```

### Agent-3B: 日志窗口 (ui/log_window.py, ~200行, 无依赖,可立即启动)
```
功能: LogWindow(QDockWidget) - add_log/clear/save/copy
验收: 6种颜色级别; 右键菜单; 自动滚动; 可脱离DockWidget独立使用
```

### Agent-3C: 标注覆盖层 (ui/overlay.py, ~200行)
```
依赖: core/annotator.py
功能: OverlayWidget(QWidget) - 虚线边框+标签+选中高亮
验收: 虚线无填充; 标签文字对齐; 缩放时坐标正确
```

### Agent-3D: 视频播放器Widget (ui/video_player.py, ~400行)
```
依赖: core/player.py, core/annotator.py, ui/overlay.py
关键修复: toggle_play()真正启动QTimer; 标注不影响播放; 拖放导入; 历史路径
验收: 播放30fps; 拖动跳转; 标注对齐; 旋转同步; 右键编辑标注框
```

### Agent-3E: OCR对话框 (ui/ocr_dialog.py, ~300行)
```
依赖: workers/ocr_worker.py, ui/log_window.py
修复: 连接真实OCR引擎(非模拟数据); 线程数配置; 结果自动合并
验收: 完整OCR流程; 取消释放资源; 进度正确; 结果可保存
```

### Agent-3F: 导出对话框 (ui/export_dialog.py, ~250行)
```
依赖: workers/export_worker.py, ui/log_window.py
修复: 连接真实导出引擎; GPU选项; 自动加载_clips.json
验收: 完整导出流程; 取消清理临时文件; 日志带颜色
```

---

## Phase 4: 集成

### Agent-4A: 主窗口 (ui/main_window.py, ~300行)
```
依赖: 3D,3E,3F
功能: 集成所有Widget; 主题切换; 三阶段按钮串联
验收: Mark→Detect→Export完整流程可运行
```

### Agent-4B: 入口 (app/main.py, ~80行)
```
依赖: 4A
功能: 环境验证 → 路径初始化 → QApplication → 异常捕获 → 资源释放
验收: python app/main.py 启动; 环境问题有明确提示
```

### Agent-4C: 脚本与测试
```
依赖: 全部
输出:
  - scripts/setup_venv.bat, setup_venv.sh
  - scripts/verify_models.py
  - tests/test_core/test_player.py
  - tests/test_core/test_annotator.py
  - tests/test_core/test_detector.py
  - tests/test_core/test_exporter.py
  - tests/test_core/test_keywords.py
验收: setup_venv.bat一键安装; pytest全部通过; 核心覆盖率>70%
```

---

## 质量门禁

每个Agent代码合并前: `python -m py_compile`通过 + import不报错 + 测试通过 + 无硬编码路径 + 无裸except
