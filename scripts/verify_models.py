"""验证所有模型文件完整性"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.paths import models_dir


def main():
    registry_path = models_dir() / "model_registry.json"
    if not registry_path.exists():
        print("[ERROR] model_registry.json 不存在")
        return 1

    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    all_ok = True
    for model_id, info in registry.get("models", {}).items():
        path = models_dir() / info["file"]
        if path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            expected = info.get("size_mb", 0)
            ok = abs(size_mb - expected) < 5
            if not ok: all_ok = False
            tag = "OK" if ok else "SIZE_MISMATCH"
            print(f"  [{tag}] {model_id}: {size_mb:.1f}MB (expected {expected:.1f}MB)")
        else:
            print(f"  [MISSING] {model_id}: 文件不存在")
            all_ok = False

    if all_ok:
        print("\n所有模型验证通过!")
    else:
        print("\n存在模型问题，请检查!")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
