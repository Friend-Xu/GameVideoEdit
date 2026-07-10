#!/usr/bin/env python3
"""一键启动脚本"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    print()
    print("=" * 40)
    print("  药药的剪辑工具 v2.0")
    print("=" * 40)
    print()

    runtime_python = ROOT / "runtime" / "python.exe"
    python = str(runtime_python) if runtime_python.exists() else sys.executable
    tag = "runtime" if runtime_python.exists() else "system"
    print(f"[环境] Python: {python} ({tag})")

    print("[检查] 验证依赖...")
    r = subprocess.run(
        [python, "-c", "import PySide6, cv2, yaml, torch, numpy"],
        capture_output=True, cwd=str(ROOT)
    )
    if r.returncode != 0:
        print("[警告] 依赖未安装，正在自动安装...")
        ret = subprocess.run(
            [python, "-m", "pip", "install", "-r", "requirements.txt"],
            cwd=str(ROOT)
        )
        if ret.returncode != 0:
            print("[错误] 依赖安装失败")
            input("Press Enter to exit...")
            return 1

    print("[检查] 验证模型文件...")
    subprocess.run(
        [python, str(ROOT / "scripts" / "verify_models.py")],
        cwd=str(ROOT)
    )

    print()
    print("[启动] 正在启动应用...")
    print()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    result = subprocess.run(
        [python, str(ROOT / "app" / "main.py")],
        cwd=str(ROOT), env=env
    )

    if result.returncode != 0:
        print(f"\n[错误] 应用异常退出 (code: {result.returncode})")
        input("Press Enter to exit...")

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
