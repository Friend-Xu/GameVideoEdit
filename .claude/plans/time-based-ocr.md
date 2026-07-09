# Plan: 新增时间间隔 OCR 模式

## 需求重述

当前 OCR 基于帧数间隔（`skip_frames=3`），导致不同帧率视频的 OCR 频率不一致（30fps→10次/秒，60fps→20次/秒）。新增基于时间的 OCR 模式，按固定秒数间隔采样，让 OCR 频率与视频帧率解耦。

## 涉及文件

| 文件 | 改动 |
|------|------|
| `app/workers/ocr_worker.py` | 核心：新增 `mode`、`interval_sec`、`post_detect_skip_sec` 参数，实现时间模式循环 |
| `app/core/detector.py` | `DetectionEngine.__init__` 新增 `mode` 等参数 |
| `app/ui/main_window.py` | `OCRDialog` UI：模式下拉框 + 间隔输入框 |
| `config/default.yaml` | 新增 `detection.mode`、`detection.interval_sec`、`detection.post_detect_skip_sec` |

## 实现步骤

### Step 1: 修改 `DetectionEngine.__init__` — 支持模式参数

- 新增参数: `mode: str = "frame"`, `interval_sec: float = 1.0`, `post_detect_skip_sec: float = 0.3`
- `process_frame()` 逻辑不变（它只负责单帧 OCR），新逻辑在 Worker 层

### Step 2: 修改 `OCRWorker` — 实现时间模式跳帧

- 新增参数: `mode`, `interval_sec`, `post_detect_skip_sec`
- 时间模式下：维护 `current_time`（秒），每次循环 `current_time → 帧号 → seek`
- 核心逻辑:
  ```
  if mode == "time":
      fn = int(current_time * fps)
      if detected: current_time += post_detect_skip_sec
      else:        current_time += interval_sec
  else:
      # 现有帧数逻辑完全保留
  ```
- `player.seek()` 接受帧号，时间模式本质是"计算目标时间 → 换算帧号 → seek"

### Step 3: 修改 `OCRDialog` UI

- 在"参数"区域新增:
  - `QComboBox` OCR 模式: "时间间隔"(默认) / "帧间隔"
  - `QDoubleSpinBox` 间隔秒数: 0.1 ~ 10.0，默认 1.0
- 帧间隔模式下显示 `QSpinBox` 跳帧数（默认 3）
- 传递参数给 `OCRWorker`

### Step 4: 更新 `config/default.yaml`

```yaml
detection:
  mode: "time"                 # "frame" | "time"
  skip_frames: 3               # 帧模式下的跳帧数
  interval_sec: 1.0            # 时间模式下的采样间隔(秒)
  post_detect_skip_sec: 0.3    # 检测命中后的跳跃间隔(秒)
```

## 风险

- **低风险**: 改动集中在 3 个文件，逻辑清晰，帧数模式完全保留

## 复杂度: 低
