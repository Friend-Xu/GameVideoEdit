"""方案 B: SPDL 可行性与性能检查。

SPDL 全栈: NVDEC decode → GPU crop → GPU tensor (zero copy)
PyPI wheel 通常不带 NVDEC (需源码编译 + Video Codec SDK)。

用法: D:/Workspace/GameVideoEdit/runtime/python.exe tests/benchmark_spdl.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_common import system_info


def check_spdl():
    results = {}
    try:
        import spdl
        results["installed"] = True
        results["version"] = getattr(spdl, "__version__", "unknown")
        try:
            from spdl.io.utils import built_with_nvcodec, get_ffmpeg_config
            results["nvcodec"] = built_with_nvcodec()
            config = get_ffmpeg_config()
            results["ffmpeg_nvdec"] = "nvdec" in (config or "").lower()
        except Exception as e:
            results["nvcodec_error"] = str(e)
    except ImportError:
        results["installed"] = False
        results["error"] = "spdl not installed (pip install spdl)"
    return results


def main():
    print("=" * 60)
    print("  方案 B: SPDL 可行性检查")
    print("=" * 60)
    print("[系统]")
    system_info()
    print()

    info = check_spdl()
    print("── SPDL 检查 ──")
    for k, v in info.items():
        print(f"  {k}: {v}")

    print()
    if not info.get("installed"):
        print("  [结论] SPDL 未安装。PyPI wheel 不带 NVDEC, 需源码编译。")
        print("  需要: CMake + Ninja + CUDA Toolkit + Video Codec SDK")
        print("        + FFmpeg with NVDEC (系统级变更)")
    elif not info.get("nvcodec"):
        print("  [结论] SPDL 已安装但 NVDEC 不可用 (PyPI wheel 不含 GPU 支持)")
    else:
        print("  [结论] SPDL NVDEC 可用!")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
