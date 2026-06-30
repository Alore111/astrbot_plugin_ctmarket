from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class CompiledRule:
    name: str
    enabled: bool
    group_ids: tuple[str, ...]
    include_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    include_regex: tuple[re.Pattern[str], ...]
    exclude_regex: tuple[re.Pattern[str], ...]

    def matches(self, *, group_id: str, text: str) -> bool:
        if not self.enabled:
            return False
        if self.group_ids and group_id not in self.group_ids:
            return False
        if self.exclude_keywords and any(k and (k in text) for k in self.exclude_keywords):
            return False
        if self.exclude_regex and any(p.search(text) for p in self.exclude_regex):
            return False

        has_include = bool(self.include_keywords or self.include_regex)
        if not has_include:
            return True

        if self.include_keywords and any(k and (k in text) for k in self.include_keywords):
            return True
        if self.include_regex and any(p.search(text) for p in self.include_regex):
            return True
        return False


def _to_str_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    out: list[str] = []
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out.append(s)
    return tuple(out)


def _compile_patterns(patterns: object) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for raw in _to_str_tuple(patterns):
        try:
            compiled.append(re.compile(raw))
        except re.error:
            continue
    return tuple(compiled)


def compile_rules(raw_rules: object) -> list[CompiledRule]:
    if not isinstance(raw_rules, list):
        return []
    rules: list[CompiledRule] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip() or "rule"
        enabled = bool(raw.get("enabled", True))
        rules.append(
            CompiledRule(
                name=name,
                enabled=enabled,
                group_ids=_to_str_tuple(raw.get("group_ids")),
                include_keywords=_to_str_tuple(raw.get("include_keywords")),
                exclude_keywords=_to_str_tuple(raw.get("exclude_keywords")),
                include_regex=_compile_patterns(raw.get("include_regex")),
                exclude_regex=_compile_patterns(raw.get("exclude_regex")),
            )
        )
    return rules


def match_first_rule(
    *,
    rules: Iterable[CompiledRule],
    group_id: str,
    text: str,
) -> str | None:
    for r in rules:
        if r.matches(group_id=group_id, text=text):
            return r.name
    return None

