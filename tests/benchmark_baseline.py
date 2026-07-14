"""基线: CPU 管线 benchmark。

模拟当前生产路径:
  numpy 帧 → numpy crop → cv2.CLAHE → torch.to(cuda)

用法: D:/Workspace/GameVideoEdit/runtime/python.exe tests/benchmark_baseline.py
"""

import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_common import (
    make_batch, ROI_CONFIGS, DEVICE,
    CPUPreprocessor, BenchmarkRunner, system_info,
)


def main():
    print("=" * 60)
    print("  基线: CPU 管线 (numpy crop + CLAHE + CPU->GPU)")
    print("=" * 60)
    print("[系统]")
    system_info()
    print()

    frames = make_batch(120)
    proc = CPUPreprocessor()

    # ── Stage 1: numpy 裁剪 ──
    runner_crop = BenchmarkRunner("numpy-crop", warmup=5, iterations=100)

    def bench_crop(frame, _idx):
        t0 = time.perf_counter()
        for cfg in ROI_CONFIGS.values():
            _ = frame[cfg["y"]:cfg["y"]+cfg["h"], cfg["x"]:cfg["x"]+cfg["w"]]
        return {"numpy-slice": (time.perf_counter() - t0) * 1000}

    runner_crop.run(frames, bench_crop)
    print(runner_crop.report())
    print()

    # ── Stage 2: 完整 CPU 预处理 → GPU ──
    runner_full = BenchmarkRunner("CPU-preprocess+transfer", warmup=5, iterations=100)

    def bench_full(frame, _idx):
        t0 = time.perf_counter()
        cfg = ROI_CONFIGS["击杀信息"]
        roi = frame[cfg["y"]:cfg["y"]+cfg["h"], cfg["x"]:cfg["x"]+cfg["w"]]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        enhanced = proc._clahe.apply(gray)
        result = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
        t_cpu = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        tensor = torch.from_numpy(result).to(DEVICE, non_blocking=True)
        torch.cuda.synchronize()
        t_gpu = (time.perf_counter() - t0) * 1000

        return {"CPU(CLAHE)": t_cpu, "CPU->GPU": t_gpu}

    runner_full.run(frames, bench_full)
    print(runner_full.report())

    cpu_ms = runner_full.stages["CPU(CLAHE)"].mean
    transfer_ms = runner_full.stages["CPU->GPU"].mean
    print(f"\n  基线总耗时: {cpu_ms + transfer_ms:.3f}ms/帧 "
          f"(CPU={cpu_ms:.3f}ms, 传输={transfer_ms:.3f}ms)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
