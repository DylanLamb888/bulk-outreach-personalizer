"""Deterministic commercial-focus mapping for natural outreach copy."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


class CommercialFocusError(ValueError):
    """Raised when focus rules or a derived focus are unsafe to use."""


_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[&'-][A-Za-z0-9]+)*")
_YEAR_RE = re.compile(r"\b(?:since|est(?:ablished)?\.?)\s+(?:18|19|20)\d{2}\b", re.I)
_MORE_RE = re.compile(r"(?:,?\s*(?:&|and)\s+more)\b", re.I)
_PROMOTIONAL_RE = re.compile(
    r"\b(?:world[- ]class|industry[- ]leading|market[- ]leading|premier|unique|"
    r"delicious|forward[- ]thinking|innovative|trusted|leading)\b",
    re.I,
)
_TRAILING_PROMO_RE = re.compile(r"\s+with confidence\b", re.I)
_LEADING_ACTION_RE = re.compile(
    r"^(?:helping|supporting|working with|providing|offering|speciali[sz]ing in)\s+",
    re.I,
)
_UNSAFE_LEADING_RE = re.compile(
    r"^(?:[\"'“”]|we\b|our\b|your\b|my\b|i\b|so\b|for\b|from\b|has\b|have\b|"
    r"is\b|are\b|was\b|were\b|what\b|why\b|where\b|when\b|whether\b|maybe\b|"
    r"discover\b|get\b|an?\s+ability\b)",
    re.I,
)
_UNSAFE_SENTENCE_RE = re.compile(
    r"\b(?:we|our|your|my|i|us|is|are|was|were|has|have|had|would|could|should|"
    r"been|being|offers?|provides?|delivers?|serves?|help(?:s|ing)?|hear|located|"
    r"focused|specifically|trusted|success)\b",
    re.I,
)
_UNSAFE_TRAILING_RE = re.compile(
    r"\b(?:a|an|and|about|by|for|from|in|inspired|of|on|or|our|specifically|the|"
    r"their|to|with|your)\s*$",
    re.I,
)
_SAFE_GENERIC_CATEGORY_RE = re.compile(
    r"\b(?:accounting|advisory|advice|brokerage|bookkeeping|capital|consulting|"
    r"construction|distribution|equipment|finance|financing|flooring|funding|"
    r"investment|parts?|personal care|planning|platform|printing|products?|"
    r"remodeling|rentals?|restoration|services?|skincare|software|solutions?|"
    r"systems?|tequila|tools?|valuation|valuations|wholesale)\b",
    re.I,
)
_COMPANY_STOPWORDS = {
    "and",
    "associates",
    "company",
    "corp",
    "corporation",
    "group",
    "inc",
    "llc",
    "limited",
    "ltd",
    "partners",
    "the",
}
_WEAK_ENDINGS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
_VAGUE_CATEGORY_WORDS = {
    "actionable",
    "advice",
    "advisory",
    "business",
    "businesses",
    "companies",
    "company",
    "expert",
    "general",
    "guidance",
    "help",
    "owner",
    "owners",
    "planning",
    "practical",
    "process",
    "processes",
    "professional",
    "service",
    "services",
    "solution",
    "solutions",
    "strategic",
    "support",
}
_AMBIGUOUS_BUYER_RELATION_RE = re.compile(
    r"^(?:business\s+)?(?:owners?|companies|businesses|customers)\s+with\s+"
    r".*\b(?:advice|advisory|planning|services?|solutions?|support|valuations?)\b",
    re.I,
)
_BUYER_NOUNS = (
    "buyers",
    "businesses",
    "companies",
    "customers",
    "distributors",
    "event planners",
    "firms",
    "founders",
    "homeowners",
    "investors",
    "laboratories",
    "manufacturers",
    "organisations",
    "organizations",
    "owners",
    "property owners",
    "retailers",
    "teams",
)


def phrase_word_count(value: str) -> int:
    return len(_WORD_RE.findall(value))


def _clean_spacing(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" \t\n\r,;:.-")
    value = re.sub(r"\s+([,;:])", r"\1", value)
    return value


def _shorten(value: str, max_words: int) -> str:
    words = value.split()
    if len(words) <= max_words:
        return value
    words = words[:max_words]
    while len(words) > 2 and words[-1].strip(",;:.-").casefold() in _WEAK_ENDINGS:
        words.pop()
    return " ".join(words).rstrip(",;:.-")


def _normalise_generic_case(value: str) -> str:
    """Lower website title case while preserving short all-cap acronyms."""
    words: list[str] = []
    for word in value.split():
        letters = re.sub(r"[^A-Za-z]", "", word)
        words.append(word if len(letters) >= 2 and letters.isupper() else word.casefold())
    return " ".join(words)


def _has_specific_category_language(value: str) -> bool:
    words = {word.casefold() for word in _WORD_RE.findall(value)}
    return bool(words.difference(_VAGUE_CATEGORY_WORDS))


def _generic_focus(source_focus: str, max_words: int) -> str:
    value = _YEAR_RE.sub("", source_focus)
    value = _MORE_RE.sub("", value)
    value = _PROMOTIONAL_RE.sub("", value)
    value = _TRAILING_PROMO_RE.sub("", value)
    value = _LEADING_ACTION_RE.sub("", value)
    value = _clean_spacing(value)

    # A service catalogue is not a human commercial angle. Keep one category.
    if "," in value or ";" in value:
        value = re.split(r"[,;]", value, maxsplit=1)[0]
    value = re.split(r"\s+(?:and|&)\s+", value, maxsplit=1, flags=re.I)[0]
    value = _shorten(_clean_spacing(value), max_words)
    value = _normalise_generic_case(value)
    if phrase_word_count(value) < 2:
        raise CommercialFocusError("commercial focus is too thin after compression")
    return value


def _company_name_leak(company_name: str, focus: str) -> bool:
    company_words = [
        word.casefold()
        for word in _WORD_RE.findall(company_name)
        if word.casefold() not in _COMPANY_STOPWORDS
    ]
    focus_words = [word.casefold() for word in _WORD_RE.findall(focus)]
    if not company_words or not focus_words:
        return False
    first_company_word = company_words[0]
    if len(first_company_word) >= 4 and focus_words[0] == first_company_word:
        return True
    return len(set(company_words) & set(focus_words)) >= 2


def _validate_generic_focus(company_name: str, focus: str) -> str:
    if _company_name_leak(company_name, focus):
        raise CommercialFocusError("generic focus repeats the company name")
    if _UNSAFE_LEADING_RE.search(focus):
        raise CommercialFocusError("generic focus starts like website copy")
    if _UNSAFE_SENTENCE_RE.search(focus):
        raise CommercialFocusError("generic focus contains sentence-style website copy")
    if _UNSAFE_TRAILING_RE.search(focus):
        raise CommercialFocusError("generic focus ends as an incomplete fragment")
    if not _has_specific_category_language(focus):
        raise CommercialFocusError("generic focus is too vague for natural outreach")
    if _AMBIGUOUS_BUYER_RELATION_RE.search(focus):
        raise CommercialFocusError(
            "generic focus describes an ambiguous buyer relationship"
        )
    if not _SAFE_GENERIC_CATEGORY_RE.search(focus):
        raise CommercialFocusError("generic focus has no safe commercial category")
    return focus


def _generic_buyer_phrase(signal_type: str, focus: str, max_words: int) -> str:
    lowered = focus.casefold()
    if lowered.startswith(_BUYER_NOUNS):
        phrase = focus
    elif signal_type == "product":
        phrase = f"buyers looking for {focus}"
    else:
        phrase = f"companies looking for {focus}"
    return _shorten(phrase, max_words)


def _validate_phrase(value: str, label: str, max_words: int) -> str:
    cleaned = _clean_spacing(value)
    if not cleaned:
        raise CommercialFocusError(f"{label} is blank")
    if phrase_word_count(cleaned) > max_words:
        raise CommercialFocusError(f"{label} exceeds {max_words} words")
    if any(token in cleaned.casefold() for token in ("& more", "and more", "since 19", "since 20")):
        raise CommercialFocusError(f"{label} contains website-style filler")
    if "," in cleaned or ";" in cleaned:
        raise CommercialFocusError(f"{label} stacks multiple ideas")
    if _PROMOTIONAL_RE.search(cleaned) or _TRAILING_PROMO_RE.search(cleaned):
        raise CommercialFocusError(f"{label} contains promotional language")
    return cleaned


@dataclass(frozen=True)
class CommercialFocusRule:
    rule_id: str
    priority: int
    pattern: re.Pattern[str]
    signal_types: tuple[str, ...]
    focus: str
    buyer_phrase: str


@dataclass(frozen=True)
class CommercialFocusResult:
    source_focus: str
    focus: str
    buyer_phrase: str
    rule_id: str
    priority: int


@dataclass(frozen=True)
class CommercialFocusTable:
    path: Path
    rules: tuple[CommercialFocusRule, ...]

    @classmethod
    def load(cls, path: str | Path) -> "CommercialFocusTable":
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise CommercialFocusError(f"commercial focus file not found: {resolved}")

        expected = {"id", "priority", "pattern", "signal_types", "focus", "buyer_phrase"}
        rules: list[CommercialFocusRule] = []
        seen: set[str] = set()
        with resolved.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if set(reader.fieldnames or ()) != expected:
                raise CommercialFocusError(
                    "commercial focus CSV must contain exactly: "
                    + ", ".join(sorted(expected))
                )
            for line_number, row in enumerate(reader, start=2):
                rule_id = row["id"].strip()
                if not rule_id or rule_id in seen:
                    raise CommercialFocusError(
                        f"invalid or duplicate commercial focus id on line {line_number}"
                    )
                seen.add(rule_id)
                try:
                    priority = int(row["priority"])
                    pattern = re.compile(row["pattern"].strip(), re.I)
                except (ValueError, re.error) as exc:
                    raise CommercialFocusError(
                        f"invalid commercial focus rule on line {line_number}: {exc}"
                    ) from exc
                signal_types = tuple(
                    item.strip() for item in row["signal_types"].split(";") if item.strip()
                )
                if not signal_types:
                    raise CommercialFocusError(
                        f"commercial focus signal_types is blank on line {line_number}"
                    )
                focus = _validate_phrase(row["focus"], "mapped focus", 12)
                buyer_phrase = _validate_phrase(row["buyer_phrase"], "mapped buyer phrase", 16)
                rules.append(
                    CommercialFocusRule(
                        rule_id=rule_id,
                        priority=priority,
                        pattern=pattern,
                        signal_types=signal_types,
                        focus=focus,
                        buyer_phrase=buyer_phrase,
                    )
                )
        if not rules:
            raise CommercialFocusError("commercial focus CSV contains no rules")
        return cls(path=resolved, rules=tuple(sorted(rules, key=lambda item: item.priority)))

    def resolve(
        self,
        *,
        company_name: str,
        signal_type: str,
        source_focus: str,
        evidence: str,
        max_focus_words: int,
        max_buyer_phrase_words: int,
    ) -> CommercialFocusResult:
        # A company name can hint at a category but is not evidence for one.
        # Mapping rules must match the selected source content itself.
        searchable = " | ".join((source_focus, evidence))
        for rule in self.rules:
            if "*" not in rule.signal_types and signal_type not in rule.signal_types:
                continue
            if not rule.pattern.search(searchable):
                continue
            return CommercialFocusResult(
                source_focus=source_focus,
                focus=_validate_phrase(rule.focus, "commercial focus", max_focus_words),
                buyer_phrase=_validate_phrase(
                    rule.buyer_phrase,
                    "buyer phrase",
                    max_buyer_phrase_words,
                ),
                rule_id=rule.rule_id,
                priority=rule.priority,
            )

        focus = _generic_focus(source_focus, max_focus_words)
        focus = _validate_generic_focus(company_name, focus)
        buyer_phrase = _generic_buyer_phrase(
            signal_type,
            focus,
            max_buyer_phrase_words,
        )
        return CommercialFocusResult(
            source_focus=source_focus,
            focus=_validate_phrase(focus, "commercial focus", max_focus_words),
            buyer_phrase=_validate_phrase(
                buyer_phrase,
                "buyer phrase",
                max_buyer_phrase_words,
            ),
            rule_id="generic-compression",
            priority=1_000_000,
        )


def longest_shared_phrase_words(left: str, right: str) -> int:
    """Return the longest consecutive token sequence shared by two strings."""
    left_words = [item.casefold() for item in _WORD_RE.findall(left)]
    right_words = [item.casefold() for item in _WORD_RE.findall(right)]
    if not left_words or not right_words:
        return 0
    previous = [0] * (len(right_words) + 1)
    longest = 0
    for left_word in left_words:
        current = [0]
        for index, right_word in enumerate(right_words, start=1):
            matched = previous[index - 1] + 1 if left_word == right_word else 0
            current.append(matched)
            longest = max(longest, matched)
        previous = current
    return longest
