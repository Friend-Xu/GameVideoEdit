"""配置管理 —— 加载和验证 YAML 配置文件。

加载优先级: 用户配置 > 环境变量 > 默认配置
"""

import os
from pathlib import Path
from typing import Any

import yaml

from app.utils.paths import config_dir


class ConfigLoader:
    """配置加载器，支持多层覆盖"""

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        """加载配置，按优先级合并"""
        default_path = config_dir() / "default.yaml"
        if default_path.exists():
            with open(default_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}

        self._apply_env_overrides()

        user_path = self._user_config_path()
        if user_path.exists():
            with open(user_path, "r", encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
                self._deep_merge(self._config, user_config)

        return self._config

    def get(self, *keys: str, default: Any = None) -> Any:
        """按路径获取配置值，例如 get('ocr', 'gpu') → True"""
        if not self._config:
            self.load()
        node: Any = self._config
        for key in keys:
            if isinstance(node, dict):
                node = node.get(key)
            else:
                return default
            if node is None:
                return default
        return node

    @property
    def config(self) -> dict[str, Any]:
        if not self._config:
            self.load()
        return self._config

    def _apply_env_overrides(self) -> None:
        """应用 GVE_ 前缀的环境变量覆盖"""
        for key, value in os.environ.items():
            if not key.startswith("GVE_"):
                continue
            config_key = key[4:].lower()
            parts = config_key.split("_")
            node: Any = self._config
            for part in parts[:-1]:
                if part not in node:
                    node[part] = {}
                node = node[part]
            v_lower = value.lower()
            if v_lower in ("true", "yes", "1"):
                typed_value: Any = True
            elif v_lower in ("false", "no", "0"):
                typed_value = False
            elif v_lower.isdigit():
                typed_value = int(value)
            elif v_lower.replace(".", "").isdigit():
                typed_value = float(value)
            else:
                typed_value = value
            node[parts[-1]] = typed_value

    @staticmethod
    def _user_config_path() -> Path:
        if os.name == "nt":
            base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        return base / "GameVideoEdit" / "user_config.yaml"

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ConfigLoader._deep_merge(base[key], value)
            else:
                base[key] = value


_config_loader: ConfigLoader | None = None


def load_config() -> dict[str, Any]:
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader.load()


def get_config(*keys: str, default: Any = None) -> Any:
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
        _config_loader.load()
    return _config_loader.get(*keys, default=default)
