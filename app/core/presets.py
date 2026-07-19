"""游戏预设管理器 —— 纯逻辑。

管理 config/presets/ 目录下的预设 YAML 文件。
提供 list/load/save/delete/import/export 操作。
"""

import json
import re
from pathlib import Path

import yaml

from app.utils.paths import config_dir


PRESETS_DIR = config_dir() / "presets"


class PresetManager:
    """预设管理器。"""

    def __init__(self, presets_dir: Path | None = None):
        self._dir = Path(presets_dir) if presets_dir else PRESETS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def list(self, platform: str | None = None) -> list[dict]:
        """列出所有预设的元信息。platform 为 None 时返回全部。"""
        result = []
        for f in sorted(self._dir.glob("*.yaml")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    config = yaml.safe_load(fp) or {}
                meta = config.get("meta", {})
                pf = meta.get("platform", "mobile")
                if platform is not None and pf != platform:
                    continue
                result.append({
                    "name": meta.get("name", f.stem),
                    "game": meta.get("game", ""),
                    "language": meta.get("language", ""),
                    "version": meta.get("version", ""),
                    "file": f.name,
                    "platform": pf,
                    "rule_count": len(config.get("rules", [])),
                })
            except Exception:
                continue
        return result

    def load(self, name: str) -> dict:
        """按文件名（不含扩展名）加载预设，返回完整 config dict。"""
        path = self._resolve(name)
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def save(self, name: str, config: dict) -> None:
        """保存预设。name 为不含扩展名的文件名。"""
        name = _slugify(name)
        path = self._dir / f"{name}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def delete(self, name: str) -> bool:
        """删除预设，返回是否成功。"""
        path = self._resolve(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def export_json(self, name: str, target: str | Path) -> None:
        """导出预设为 JSON 文件。"""
        config = self.load(name)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def import_json(self, source: str | Path) -> str:
        """从 JSON 文件导入预设，保存为 YAML，返回预设名。"""
        with open(source, "r", encoding="utf-8") as f:
            config = json.load(f)
        meta = config.get("meta", {})
        name = meta.get("name", Path(source).stem)
        file_name = _slugify(name)
        self.save(file_name, config)
        return file_name

    def _resolve(self, name: str) -> Path:
        name = name.replace(".yaml", "").replace(".yml", "")
        path = self._dir / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"预设不存在: {name}")
        return path


def _slugify(name: str) -> str:
    """将预设名转换为合法文件名。"""
    name = name.lower().strip().replace(" ", "_")
    return re.sub(r"[^a-z0-9_\-]", "", name)
