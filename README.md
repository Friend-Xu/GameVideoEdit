# GameVideoEdit 药药的剪辑工具

> 和平精英 / PUBG 游戏精彩击杀自动剪辑工具
> Auto highlight clipping for PUBG / Peace Elite gameplay

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6-green.svg)](https://pypi.org/project/PySide6/)

---

A desktop application that detects kill events in gameplay videos via OCR and exports highlight clips.
通过 OCR 识别游戏视频中的击杀文字，自动导出精彩击杀片段。

---

## Features | 功能

| | |
|---|---|
| Video Player / 视频播放器 | frame-by-frame, rotation, drag-and-drop, audio playback / 逐帧、旋转、拖拽、音频播放 |
| Keyboard Shortcuts / 快捷键 | Space: play/pause, ←→: prev/next frame / 空格: 播放暂停, 方向键: 逐帧 |
| Smart Seek / 智能跳转 | double-click clip → jump 1.5s before trigger / 双击片段精准跳转 |
| ROI Annotation / 区域标注 | multi-label boxes, YOLO-format export / 多标签框选、YOLO格式 |
| OCR Detection / 文字识别 | EasyOCR + GPU acceleration, multi-threaded / GPU加速、多线程 |
| Smart Export / 智能导出 | FFmpeg + NVENC/AMF/QSV hardware encode / GPU硬件编码 |
| PCL2-style UI / PCL2界面 | light/dark themes, dockable log viewer / 双主题、日志窗口 |

## Architecture | 架构

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│  Stage 1 │───▶│  Stage 2 │───▶│  Stage 3 │
│   Mark   │    │  Detect  │    │  Export  │
│  标注区域 │    │  OCR检测 │    │  视频导出 │
└──────────┘    └──────────┘    └──────────┘

app/
├── core/          # Pure logic / 纯逻辑
│   ├── player.py  # Video engine / 视频引擎
│   ├── annotator  # Label model / 标注数据模型
│   ├── keywords   # Pattern matching / 关键词匹配
│   ├── detector   # OCR pipeline / OCR流水线
│   └── exporter   # FFmpeg engine / 导出引擎
├── ui/            # PySide6 GUI
│   ├── main_window  # Main window / 主窗口
│   ├── video_player # Player widget / 播放器
│   └── overlay      # Annotation overlay / 标注层
└── workers/       # QThread workers / 后台线程
```

## Quick Start | 快速开始

**Prerequisites / 前置要求:** Python 3.10+, FFmpeg (in PATH)

```bash
git clone https://github.com/Friend-Xu/GameVideoEdit.git
cd GameVideoEdit
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

# GPU加速 (optional / 可选)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

**Launch / 启动:**
```bash
python app/main.py
# or double-click / 或双击 启动.bat
```

**Workflow / 使用流程:**
1. Open video / 打开视频 → 2. Draw ROIs / 框选击杀提示区 → 3. Save labels / 保存标注 → 4. OCR detect / OCR识别 → 5. Export / 导出集锦

## Configuration | 配置

Edit `config/default.yaml` / 编辑 `config/default.yaml`:

| Key | Default | Description / 说明 |
|-----|---------|---------------------|
| `ocr.gpu` | true | Enable GPU / 启用GPU加速 |
| `ocr.confidence_threshold` | 0.5 | Min OCR confidence / 最低置信度 |
| `detection.padding_before` | 10 | Seconds before kill / 击杀前秒数 |
| `detection.padding_after` | 10 | Seconds after kill / 击杀后秒数 |
| `export.quality` | 23 | CRF/CQ (lower=better / 越低越好) |

## License | 许可

MIT © [Friend-Xu](https://github.com/Friend-Xu)
