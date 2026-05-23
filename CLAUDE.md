# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A PySide6 desktop app for automatically creating game highlight clips (和平精英/PUBG). The user marks regions of interest on video frames, OCR scans those regions for kill-event text, and FFmpeg exports highlight segments.

## Entry points and running

- **Main application**: `python Script/GUI_Main.py` — the full working app
- **Alternate GUI**: `python GUI/main.py` — lighter/similar version
- **Launch script**: `__启动脚本.bat` — launches Claude Code with ECC plugin checks (Windows Terminal)
- There is no test suite

## Architecture

The app follows a 3-stage pipeline: **Mark → Detect → Export**, each in its own file.

### Stage 1: Mark (`Script/GUI_Main.py`)

`MainWindow` contains a `VideoPlayer` widget that uses OpenCV (`cv2.VideoCapture`) to play videos frame-by-frame. Users drag rectangles on an `OverlayWidget` to mark ROIs. Annotations are saved as JSON in a custom format with YOLO-normalized coordinates:

```json
{
  "video_path": "...",
  "width": 1920, "height": 1080,
  "fps": 30, "total_frames": 54000,
  "rotation": 0,
  "regions": [
    {"id": 1, "label": "击杀提示", "center_x": 0.5, "center_y": 0.1, "width": 0.3, "height": 0.05}
  ]
}
```

Annotation files are named `{video_name}_labels.json` and saved alongside the source video.

### Stage 2: Detect (`Script/ocr_processor.py`)

`OCRProcessDialog` loads annotation JSON and spawns multiple `OCRProcessor` threads (QThread). Each thread:
1. Analyzes H.264 stream for corruption (`analyze_h264_stream`)
2. Repairs damaged video with FFmpeg if needed (multiple repair strategies based on error type)
3. Scans assigned frame segments with EasyOCR (`'ch_sim', 'en'`)
4. Matches OCR text against the pattern: `"你使用" + keyword` (keywords: `"淘汰了"`, `"击倒了"`)
5. Returns detected time ranges, merged across overlapping intervals

Bundled EasyOCR 1.7.2 lives in `EasyOCR_1_7_2/` with models in `EasyOCR_1_7_2/models/`. The reader is initialized with `download_enabled=False` and `model_storage_directory` pointing to the local models dir.

### Stage 3: Export (`Script/FFmpeg_processor.py`)

`ExportDialog` loads `{video_name}_clips.json` (output of Stage 2) and feeds clip ranges to `VideoEditor.combine_clips()`. The editor uses FFmpeg as primary backend with OpenCV and MoviePy as fallbacks. Supports GPU acceleration (NVIDIA NVENC, AMD AMF, Intel QSV). Output is a single concatenated MP4.

### Support modules

- `Script/log_window.py` — `LogWindow` (QDockWidget) with color-coded tree view for per-type log entries
- `GUI/ui_main.py` — Qt Designer-generated UI file (PySide6 UIC output), not the main runtime entry point
- `Script/opencv_draw_box.py` — utility for drawing bounding boxes on images
- `Script/test_script/` — ad-hoc test/experiment scripts, not a formal test suite

## Dependencies

- **PySide6** (6.9.0) — Qt for Python
- **OpenCV** (`cv2`) — video I/O and frame manipulation
- **EasyOCR** (bundled 1.7.2) — OCR engine
- **FFmpeg** (system dependency) — video repair, cutting, merging
- **MoviePy** (optional fallback) — alternative video processing backend

## Key conventions

- Video coordinates use the **original (pre-rotation) video dimensions** — rotation transforms are applied only for display
- Annotation files use `_labels.json` suffix; clip data files use `_clips.json` suffix
- Multi-threading: each `OCRProcessor` runs in its own QThread and handles a fixed segment of the video
- Video repair creates temp files in `tempfile.mkdtemp()` — cleaned up on completion
- QSettings org/app: `"GameVideoEdit"` / `"PeaceEliteHighlights"`
- The app sets `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1` for Chinese character support
