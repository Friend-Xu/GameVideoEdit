"""方案 C: decord 可行性检查。

decord GPU 需源码编译 (cmake -DUSE_CUDA=ON)。CPU 版可 pip install decord。
项目当前使用 OpenCV VideoCapture 读取视频。

用法: D:/Workspace/GameVideoEdit/runtime/python.exe tests/benchmark_decord_gpu.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_common import system_info


def main():
    print("=" * 60)
    print("  方案 C: decord 可行性检查")
    print("=" * 60)
    print("[系统]")
    system_info()
    print()

    results = {}
    try:
        import decord
        results["installed"] = True
        results["version"] = decord.__version__
        try:
            import decord.gpu
            results["gpu_available"] = True
        except (ImportError, AttributeError):
            results["gpu_available"] = False
            results["note"] = "CPU-only build (pip wheel)"
    except ImportError:
        results["installed"] = False
        results["note"] = "not installed (pip install decord)"

    for k, v in results.items():
        print(f"  {k}: {v}")

    print()
    if not results.get("installed"):
        print("  [结论] decord 未安装。pip install decord 装 CPU 版。")
        print("  GPU 版需要: cmake -DUSE_CUDA=ON + 源码编译。")
    elif not results.get("gpu_available"):
        print("  [结论] decord CPU-only。建议: CPU 解码 + 方案A(GPU预处理) 组合。")
    else:
        print("  [结论] decord GPU 可用!")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
