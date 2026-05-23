"""关键词匹配引擎 —— 纯逻辑。

加载 YAML 配置，编译正则，匹配 OCR 文本。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class MatchResult:
    pattern_id: str
    raw_text: str
    action: str
    actor: str
    groups: tuple
    extract: dict[str, str]


class KeywordMatcher:
    """关键词匹配器"""

    def __init__(self):
        self._patterns: list[tuple[re.Pattern, dict]] = []
        self._trigger_prefixes: list[str] = []

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> "KeywordMatcher":
        matcher = cls()
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        for p in config.get("patterns", []):
            matcher._patterns.append((
                re.compile(p["regex"]),
                {
                    "id": p["id"], "action": p.get("action", ""),
                    "actor": p.get("actor", ""), "extract": p.get("extract", []),
                },
            ))
        matcher._trigger_prefixes = config.get("trigger_prefixes", [])
        return matcher

    def match(self, text: str) -> MatchResult | None:
        if not text or not text.strip():
            return None
        if self._trigger_prefixes and not any(p in text for p in self._trigger_prefixes):
            return None
        for regex, meta in self._patterns:
            m = regex.search(text)
            if m:
                return MatchResult(
                    pattern_id=meta["id"], raw_text=text,
                    action=meta["action"], actor=meta["actor"],
                    groups=m.groups(),
                    extract=dict(zip(meta["extract"], m.groups())),
                )
        return None

    def has_trigger_prefix(self, text: str) -> bool:
        return any(p in text for p in self._trigger_prefixes)
