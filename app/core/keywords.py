"""关键词匹配引擎 —— 纯逻辑。

加载 YAML 配置，从信号片段自动构建正则，匹配 OCR 文本。
"""

import re
from dataclasses import dataclass
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
    """关键词匹配器 —— 信号拆解 + 自动正则生成"""

    def __init__(self):
        self._patterns: list[tuple[re.Pattern, dict]] = []
        self._trigger_prefixes: list[str] = []

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> "KeywordMatcher":
        matcher = cls()
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        descriptors = config.get("descriptors", [])
        rules = config.get("rules", [])

        if not rules and config.get("patterns"):
            matcher._load_legacy(config)
        else:
            matcher._load_rules(rules, descriptors)

        matcher._trigger_prefixes = config.get("trigger_prefixes", [])
        return matcher

    def _load_legacy(self, config: dict) -> None:
        for p in config.get("patterns", []):
            self._patterns.append((
                re.compile(p["regex"]),
                {
                    "id": p["id"], "action": p.get("action", ""),
                    "actor": p.get("actor", ""), "extract": p.get("extract", []),
                },
            ))

    def _load_rules(self, rules: list[dict], descriptors: list[str]) -> None:
        entries = []
        for rule in rules:
            descs = rule.get("descriptors_override", descriptors)
            regex = self._build_regex(rule, descs)
            priority = 0
            if rule.get("require_signal"):
                priority = 2
            elif not rule.get("anti_signal"):
                priority = 1
            entries.append((priority, regex, {
                "id": rule["id"],
                "action": rule.get("action", ""),
                "actor": rule.get("actor", ""),
                "anti_signal": rule.get("anti_signal"),
            }))
        entries.sort(key=lambda e: e[0], reverse=True)
        self._patterns = [(e[1], e[2]) for e in entries]

    @staticmethod
    def _build_regex(rule: dict, descriptors: list[str]) -> re.Pattern:
        actor = rule["actor_signal"]
        action = rule["action"]
        desc_part = ""
        if descriptors:
            descs = "(" + "|".join(descriptors) + ")"
            desc_part = descs if rule.get("require_descriptor") else descs + "?"
        if rule.get("require_signal"):
            rs = rule["require_signal"]
            pat = actor + r".+?" + rs + r".*?" + desc_part + r".*?" + action + r"了?"
        else:
            pat = actor + r".+?" + desc_part + r".*?" + action + r"了?"
        return re.compile(pat)

    def match(self, text: str) -> MatchResult | None:
        if not text or not text.strip():
            return None
        if self._trigger_prefixes and not any(p in text for p in self._trigger_prefixes):
            return None
        for regex, meta in self._patterns:
            if meta.get("anti_signal") and meta["anti_signal"] in text:
                continue
            m = regex.search(text)
            if m:
                return MatchResult(
                    pattern_id=meta["id"], raw_text=text,
                    action=meta["action"], actor=meta["actor"],
                    groups=m.groups(),
                    extract={"full_match": m.group()},
                )
        return None

    def has_trigger_prefix(self, text: str) -> bool:
        return any(p in text for p in self._trigger_prefixes)
