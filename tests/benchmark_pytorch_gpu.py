"""方案 A: PyTorch GPU 预处理 benchmark。

路径:
  numpy 帧 → torch.from_numpy(cuda) → GPU crop → GPU 灰度 → GPU 对比度拉伸

零额外依赖 —— 只用 torch + numpy + cv2。

用法: D:/Workspace/GameVideoEdit/runtime/python.exe tests/benchmark_pytorch_gpu.py
"""

import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_common import (
    make_batch, ROI_CONFIGS, DEVICE,
    GPUPreprocessor, CPUPreprocessor,
    BenchmarkRunner, system_info, compare_results,
)


def main():
    print("=" * 60)
    print("  方案 A: PyTorch GPU 预处理 (零额外依赖)")
    print("=" * 60)
    print("[系统]")
    system_info()
    print()

    frames = make_batch(120)
    gpu_proc = GPUPreprocessor(DEVICE)
    cpu_proc = CPUPreprocessor()

    # ── 逐阶段测量 ──

    runner = BenchmarkRunner("GPU-pipeline", warmup=5, iterations=100)

    def bench_stages(frame, _idx):
        cfg = ROI_CONFIGS["击杀信息"]
        roi_np = frame[cfg["y"]:cfg["y"]+cfg["h"], cfg["x"]:cfg["x"]+cfg["w"]]

        t0 = time.perf_counter()
        tensor = torch.from_numpy(roi_np).to(DEVICE, non_blocking=True)
        torch.cuda.synchronize()
        t1 = time.perf_counter()

        gray = (tensor[:, :, 0].float() * 0.114 +
                tensor[:, :, 1].float() * 0.587 +
                tensor[:, :, 2].float() * 0.299)
        torch.cuda.synchronize()
        t2 = time.perf_counter()

        flat = gray.flatten()
        lo = torch.quantile(flat, gpu_proc._lo)
        hi = torch.quantile(flat, gpu_proc._hi)
        scale = 255.0 / (hi - lo + 1e-8)
        enhanced = ((gray - lo) * scale).clamp(0, 255).to(torch.uint8)
        _ = enhanced.unsqueeze(-1).expand(-1, -1, 3).contiguous()
        torch.cuda.synchronize()
        t3 = time.perf_counter()

        return {
            "transfer": (t1 - t0) * 1000,
            "grayscale": (t2 - t1) * 1000,
            "contrast": (t3 - t2) * 1000,
        }

    runner.run(frames, bench_stages)
    print(runner.report())

    gpu_total = sum(s.mean for s in runner.stages.values())
    print(f"\n  GPU 总耗时: {gpu_total:.3f}ms/帧")

    # ── 一致性 ──
    print("\n── 一致性 (CPU CLAHE vs GPU contrast) ──")
    test_frame = frames[0]
    cfg = ROI_CONFIGS["击杀信息"]
    cpu_result = cpu_proc.process_one(test_frame, cfg)
    gpu_tensor, _ = gpu_proc.process_one_staged(test_frame, cfg)
    diff = compare_results(cpu_result, gpu_tensor)
    print(f"  MSE={diff['mse']:.2f}  MAE={diff['mae']:.2f}  max_diff={diff['max_diff']:.0f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
