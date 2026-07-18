"""关键词匹配引擎 —— 纯逻辑。

加载 YAML 配置，从信号片段自动构建正则，匹配 OCR 文本。
支持 exact 和 fuzzy 两种匹配策略，规则级声明，互不兜底。
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
    confidence: float = 1.0
    strategy: str = "exact"


class KeywordMatcher:
    """关键词匹配器 —— 信号拆解 + 自动正则生成。"""

    def __init__(self):
        self._patterns: list[tuple[re.Pattern, dict]] = []
        self._trigger_prefixes: list[str] = []
        self._rules_config: list[dict] = []
        self._descriptors: list[str] = []

    # ── 构造入口 ──

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> "KeywordMatcher":
        matcher = cls()
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        matcher._init_from_config(config)
        return matcher

    @classmethod
    def from_dict(cls, config: dict) -> "KeywordMatcher":
        """从内存 dict 构建（PresetManager 调用）。"""
        matcher = cls()
        matcher._init_from_config(config)
        return matcher

    def _init_from_config(self, config: dict) -> None:
        descriptors = config.get("descriptors", [])
        self._descriptors = list(descriptors)
        rules = config.get("rules", [])

        if not rules and config.get("patterns"):
            self._load_legacy(config)
        else:
            self._load_rules(rules, descriptors)

        self._trigger_prefixes = config.get("trigger_prefixes", [])

    def to_dict(self) -> dict:
        """导出当前规则为 dict（用于保存预设）。"""
        return {
            "descriptors": list(self._descriptors),
            "trigger_prefixes": list(self._trigger_prefixes),
            "rules": [dict(r) for r in self._rules_config],
        }

    # ── 规则加载 ──

    def _load_legacy(self, config: dict) -> None:
        for p in config.get("patterns", []):
            self._patterns.append((
                re.compile(p["regex"]),
                {
                    "id": p["id"], "action": p.get("action", ""),
                    "actor": p.get("actor", ""), "extract": p.get("extract", []),
                    "strategy": "exact",
                },
            ))

    def _load_rules(self, rules: list[dict], descriptors: list[str]) -> None:
        self._rules_config = list(rules)
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
                "strategy": rule.get("match_strategy", "exact"),
                "threshold": rule.get("similarity_threshold", 0.85),
                "_signals": self._collect_signals(rule),
            }))
        entries.sort(key=lambda e: e[0], reverse=True)
        self._patterns = [(e[1], e[2]) for e in entries]

    @staticmethod
    def _collect_signals(rule: dict) -> list[str]:
        """收集规则中所有信号词，用于 fuzzy 匹配构造 canonical 文本。"""
        signals = [rule.get("actor_signal", "")]
        if rule.get("require_signal"):
            signals.append(rule["require_signal"])
        signals.append(rule.get("action", ""))
        return [s for s in signals if s]

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

    # ── 匹配 ──

    def match(self, text: str) -> MatchResult | None:
        if not text or not text.strip():
            return None
        if self._trigger_prefixes and not any(p in text for p in self._trigger_prefixes):
            return None
        for regex, meta in self._patterns:
            if meta.get("anti_signal") and meta["anti_signal"] in text:
                continue
            strategy = meta.get("strategy", "exact")
            result = self._fuzzy_match(text, regex, meta) if strategy == "fuzzy" \
                else self._exact_match(text, regex, meta)
            if result:
                return result
        return None

    def _exact_match(self, text: str, regex: re.Pattern, meta: dict) -> MatchResult | None:
        m = regex.search(text)
        if not m:
            return None
        return MatchResult(
            pattern_id=meta["id"], raw_text=text,
            action=meta["action"], actor=meta["actor"],
            groups=m.groups(), extract={"full_match": m.group()},
            confidence=1.0, strategy="exact",
        )

    def _fuzzy_match(self, text: str, _regex: re.Pattern, meta: dict) -> MatchResult | None:
        """Fuzzy 策略：每个信号词独立 partial_ratio，取平均值。

        单一路径，不先试 regex 再降级。
        """
        signals = meta.get("_signals", [])
        if not signals:
            return None
        try:
            from rapidfuzz import fuzz
        except ImportError:
            return self._exact_match(text, _regex, meta)

        threshold = meta.get("threshold", 0.85)
        scores = [fuzz.partial_ratio(s, text) / 100.0 for s in signals]
        score = sum(scores) / len(scores)
        if score < threshold:
            return None
        return MatchResult(
            pattern_id=meta["id"], raw_text=text,
            action=meta["action"], actor=meta["actor"],
            groups=(), extract={"full_match": text},
            confidence=round(score, 4), strategy="fuzzy",
        )

    def has_trigger_prefix(self, text: str) -> bool:
        return any(p in text for p in self._trigger_prefixes)
