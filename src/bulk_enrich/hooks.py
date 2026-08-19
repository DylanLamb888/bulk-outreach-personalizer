"""Editable job-title-to-hook rules."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HookMatch:
    persona: str
    hook: str
    pattern: str
    priority: int


@dataclass(frozen=True)
class HookRule:
    priority: int
    pattern_text: str
    pattern: re.Pattern[str]
    persona: str
    hook: str


class TitleHookTable:
    def __init__(self, rules: list[HookRule], path: Path) -> None:
        if not rules:
            raise ValueError("title-hook table contains no rules")
        self.rules = sorted(rules, key=lambda rule: rule.priority)
        self.path = path

    @classmethod
    def load(cls, path: str | Path) -> "TitleHookTable":
        table_path = Path(path).expanduser().resolve()
        if not table_path.is_file():
            raise FileNotFoundError(f"title-hook table not found: {table_path}")
        with table_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"priority", "title_pattern", "persona", "hook"}
            missing = required.difference(reader.fieldnames or [])
            if missing:
                raise ValueError(
                    "title-hook table is missing columns: " + ", ".join(sorted(missing))
                )
            rules: list[HookRule] = []
            for line_number, row in enumerate(reader, start=2):
                try:
                    priority = int((row.get("priority") or "").strip())
                    pattern_text = (row.get("title_pattern") or "").strip()
                    persona = (row.get("persona") or "").strip()
                    hook = (row.get("hook") or "").strip()
                    if not pattern_text or not persona or not hook:
                        raise ValueError("blank required value")
                    pattern = re.compile(pattern_text, re.IGNORECASE)
                except (ValueError, re.error) as exc:
                    raise ValueError(
                        f"invalid title-hook rule on line {line_number}: {exc}"
                    ) from exc
                rules.append(HookRule(priority, pattern_text, pattern, persona, hook))
        return cls(rules, table_path)

    def match(self, title: str) -> HookMatch:
        normalized = title.strip()
        for rule in self.rules:
            if rule.pattern.search(normalized):
                return HookMatch(rule.persona, rule.hook, rule.pattern_text, rule.priority)
        raise ValueError("title-hook table has no fallback rule")
