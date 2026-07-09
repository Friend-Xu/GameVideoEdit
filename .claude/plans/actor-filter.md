# Plan: Actor 过滤 — 排除队友击杀/阵亡

## 需求
OCR 当前匹配所有 actor（自己/队友/敌人/未知），导致队友的击杀和阵亡也混入高光片段。新增 actor 过滤器，默认只保留"自己"。

## 改动范围

| 文件 | 改动 |
|------|------|
| `app/core/detector.py` | `DetectionEngine` 新增 `allowed_actors`，`process_frame` 匹配后按 actor 过滤 |
| `app/workers/ocr_worker.py` | `OCRWorker` 新增 `allowed_actors`，透传给 `DetectionEngine` |
| `app/ui/main_window.py` | `OCRDialog` 新增 actor 过滤下拉框，透传给 `OCRWorker` |

## 步骤

### Step 1: DetectionEngine — actor 过滤
- `__init__` 新增 `allowed_actors: set | None = None`（None = 不过滤）
- `process_frame` 中匹配成功后，检查 `match_result.actor` 是否在 `allowed_actors` 中

### Step 2: OCRWorker — 透传参数
- 新增 `allowed_actors` 参数，构建 `DetectionEngine` 时传入

### Step 3: OCRDialog UI
- 新增下拉框: "仅自己"(默认) / "自己+队友击杀" / "全部"
- `_start` 中根据选项构建 `allowed_actors` set，传给 `OCRWorker`

## 风险: 低
向后兼容，`allowed_actors=None` 时行为不变。
