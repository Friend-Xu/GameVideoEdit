"""GPU 解码基准测试 — 共享工具。

使用 runtime/python.exe 运行：
  D:/Workspace/GameVideoEdit/runtime/python.exe tests/benchmark_xxx.py
"""

import time
import sys
from dataclasses import dataclass, field

import cv2
import numpy as np
import torch


# ── 配置 ──────────────────────────────────────────────

FRAME_W, FRAME_H = 1920, 1080

ROI_CONFIGS = {
    "击杀信息": {"x": 660, "y": 920, "w": 600, "h": 56},
    "淘汰计数": {"x": 40,  "y": 60,  "w": 160, "h": 48},
}

DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"


# ── 合成帧生成 ────────────────────────────────────────

def make_synthetic_frame(width=FRAME_W, height=FRAME_H) -> np.ndarray:
    """模拟游戏画面 BGR numpy (uint8, HWC) —— 暗背景 + ROI 白文字。"""
    frame = np.random.randint(0, 80, (height, width, 3), dtype=np.uint8)
    for _name, roi in ROI_CONFIGS.items():
        x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
        frame[y:y+h, x:x+w] = np.random.randint(10, 40, (h, w, 3), dtype=np.uint8)
        text_h = h // 3
        text_y = y + (h - text_h) // 2
        frame[text_y:text_y+text_h, x+10:x+w-10] = np.random.randint(
            180, 255, (text_h, w-20, 3), dtype=np.uint8)
    return frame


def make_batch(n: int = 100):
    return [make_synthetic_frame() for _ in range(n)]


# ── 计时工具 ──────────────────────────────────────────

@dataclass
class StageStats:
    name: str
    times_ms: list = field(default_factory=list)

    @property
    def mean(self) -> float:
        return float(np.mean(self.times_ms) or 0.0)

    @property
    def std(self) -> float:
        return float(np.std(self.times_ms) or 0.0)

    @property
    def min_val(self) -> float:
        return float(np.min(self.times_ms) or 0.0)

    @property
    def max_val(self) -> float:
        return float(np.max(self.times_ms) or 0.0)

    def summary(self) -> str:
        if not self.times_ms:
            return f"{self.name}: no data"
        return f"{self.name:20s} mean={self.mean:8.3f}ms std={self.std:7.3f}ms min={self.min_val:7.3f}ms max={self.max_val:7.3f}ms"


class BenchmarkRunner:
    """多阶段 benchmark：warmup + N iterations，收集各阶段耗时。"""

    def __init__(self, name: str, warmup: int = 5, iterations: int = 50):
        self.name = name
        self.warmup = warmup
        self.iterations = iterations
        self.stages: dict[str, StageStats] = {}

    def _stage(self, name: str) -> StageStats:
        if name not in self.stages:
            self.stages[name] = StageStats(name)
        return self.stages[name]

    def record(self, stage_name: str, elapsed_ms: float):
        self._stage(stage_name).times_ms.append(elapsed_ms)

    def run(self, frames, fn):
        """逐帧执行 fn(frame, idx) → dict[str, float] (stage_name → ms)。"""
        n = len(frames)
        for i in range(self.warmup):
            fn(frames[i % n], i % n)
        for i in range(self.iterations):
            idx = (self.warmup + i) % n
            stage_times = fn(frames[idx], idx)
            for s_name, elapsed in stage_times.items():
                self.record(s_name, elapsed)

    def report(self) -> str:
        lines = [f"── {self.name} ({self.iterations} iters) ──"]
        total = 0.0
        for s in self.stages.values():
            lines.append(f"  {s.summary()}")
            total += s.mean
        lines.append(f"  {'TOTAL/帧':20s}  {total:.3f}ms")
        return "\n".join(lines)


# ── 预处理实现 ────────────────────────────────────────

class CPUPreprocessor:
    """当前路径: numpy 裁剪 + OpenCV CLAHE → BGR。"""

    def __init__(self):
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    def process_one(self, frame: np.ndarray, roi_cfg: dict) -> np.ndarray:
        x, y, w, h = roi_cfg["x"], roi_cfg["y"], roi_cfg["w"], roi_cfg["h"]
        roi = frame[y:y+h, x:x+w]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        enhanced = self._clahe.apply(gray)
        return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


class GPUPreprocessor:
    """PyTorch GPU: tensor 裁剪 + 加权灰度 + 对比度拉伸 (CLAHE 替代)。"""

    def __init__(self, device: str = DEVICE):
        self._device = device
        self._lo = torch.tensor([0.02], device=device)
        self._hi = torch.tensor([0.98], device=device)

    def process_one_staged(self, frame_np: np.ndarray, roi_cfg: dict):
        """返回 (gpu_tensor_3ch, {stage→ms})。"""
        x, y, w, h = roi_cfg["x"], roi_cfg["y"], roi_cfg["w"], roi_cfg["h"]
        roi_np = frame_np[y:y+h, x:x+w]

        t0 = time.perf_counter()
        tensor = torch.from_numpy(roi_np).to(self._device, non_blocking=True)
        torch.cuda.synchronize()
        t_transfer = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        gray = (tensor[:, :, 0].float() * 0.114 +
                tensor[:, :, 1].float() * 0.587 +
                tensor[:, :, 2].float() * 0.299)
        torch.cuda.synchronize()
        t_gray = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        flat = gray.flatten()
        lo = torch.quantile(flat, self._lo)
        hi = torch.quantile(flat, self._hi)
        scale = 255.0 / (hi - lo + 1e-8)
        enhanced = ((gray - lo) * scale).clamp(0, 255).to(torch.uint8)
        enhanced_3ch = enhanced.unsqueeze(-1).expand(-1, -1, 3).contiguous()
        torch.cuda.synchronize()
        t_contrast = (time.perf_counter() - t0) * 1000

        return enhanced_3ch, {
            "transfer": t_transfer,
            "grayscale": t_gray,
            "contrast": t_contrast,
        }


# ── 一致性检查 ────────────────────────────────────────

def compare_results(cpu_arr: np.ndarray, gpu_tensor: torch.Tensor) -> dict:
    gpu_np = gpu_tensor.cpu().numpy()
    d = cpu_arr.astype(np.float32) - gpu_np.astype(np.float32)
    return {"mse": float(np.mean(d**2)), "mae": float(np.mean(np.abs(d))),
            "max_diff": float(np.max(np.abs(d)))}

def system_info():
    info = {
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "cuda": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A",
        "vram_mb": torch.cuda.get_device_properties(0).total_memory // 1024**2 if torch.cuda.is_available() else 0,
        "opencv": cv2.__version__,
    }
    for k, v in info.items():
        print(f"  {k}: {v}")
    print(f"  device: {DEVICE}")
    return info
