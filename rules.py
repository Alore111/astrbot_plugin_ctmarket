from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Iterable


@dataclass(frozen=True)
class CompiledRule:
    """
    单条监听规则的“编译态”结构。

    说明：
    - group_ids 为空代表不限制群
    - include_* 全为空代表不限制内容（只要群满足就通过）
    - exclude_* 任一命中则直接拒绝
    """

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


def compile_rules(
    raw_rules: object,
    *,
    warn: Callable[[str], None] | None = None,
) -> list[CompiledRule]:
    """
    将配置中的 rules 字段（通常来自 _conf_schema.json）转换成可用于匹配的规则列表。

    - 输入不是 list 或元素不是 dict 时会被跳过。
    - 正则编译失败会忽略该 pattern；如提供 warn 回调会输出一条 warning。
    """

    if not isinstance(raw_rules, list):
        return []
    rules: list[CompiledRule] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip() or "rule"
        enabled = bool(raw.get("enabled", True))

        include_regex_raw = raw.get("include_regex")
        exclude_regex_raw = raw.get("exclude_regex")
        include_regex: tuple[re.Pattern[str], ...] = ()
        exclude_regex: tuple[re.Pattern[str], ...] = ()
        for field_name, field_value in (("include_regex", include_regex_raw), ("exclude_regex", exclude_regex_raw)):
            if not isinstance(field_value, list):
                continue
            compiled: list[re.Pattern[str]] = []
            for p in _to_str_tuple(field_value):
                try:
                    compiled.append(re.compile(p))
                except re.error as e:
                    if warn is not None:
                        warn(f'rule="{name}" invalid_regex_field="{field_name}" pattern="{p}" err="{e}"')
            if field_name == "include_regex":
                include_regex = tuple(compiled)
            else:
                exclude_regex = tuple(compiled)

        rules.append(
            CompiledRule(
                name=name,
                enabled=enabled,
                group_ids=_to_str_tuple(raw.get("group_ids")),
                include_keywords=_to_str_tuple(raw.get("include_keywords")),
                exclude_keywords=_to_str_tuple(raw.get("exclude_keywords")),
                include_regex=include_regex,
                exclude_regex=exclude_regex,
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

