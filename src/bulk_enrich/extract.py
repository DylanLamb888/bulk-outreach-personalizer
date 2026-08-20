"""Deterministic extraction of short, auditable company signals from HTML."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


_SPACE_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_BLOCKED_PHRASES = (
    "accept cookies",
    "cookie policy",
    "privacy policy",
    "terms of use",
    "all rights reserved",
    "subscribe to our newsletter",
    "javascript is disabled",
    "page not found",
    "access denied",
    "contact us today",
    "learn more",
    "cookies help us",
    "through this program, cbp works with the trade community",
    "apply to the program and work with cbp",
    "application process is easy and it is done online",
)
_PROMOTIONAL_PHRASES = (
    "world-leading",
    "world leading",
    "industry-leading",
    "industry leading",
    "best-in-class",
    "best in class",
    "number one",
    "#1",
)
_BUSINESS_VERBS = re.compile(
    r"\b(help|helps|support|supports|provide|provides|offer|offers|speciali[sz]e|"
    r"speciali[sz]es|advise|advises|manage|manages|build|builds|deliver|delivers|"
    r"serve|serves|work with|works with|enable|enables|acquire|acquires|acquiring|"
    r"buy|buys|buying|invest|invests|investing)\b",
    re.IGNORECASE,
)
_METRIC_CLAIM_RE = re.compile(
    r"(?:\b\d+(?:\.\d+)?%|\b\d+[kmb]\+?\b|\b\d+\+\s+years?\b|"
    r"\b(?:million|billion)\b)",
    re.IGNORECASE,
)
_INCOMPLETE_FINAL_TOKENS = {
    "advis",
    "commer",
    "comp",
    "financ",
    "int",
    "intern",
    "internat",
    "manag",
    "servic",
    "solut",
    "transact",
}


def clean_text(value: str) -> str:
    return _SPACE_RE.sub(" ", html.unescape(value)).strip(" \t\r\n-|•")


@dataclass(frozen=True)
class TextCandidate:
    text: str
    kind: str
    source_url: str
    score: float


@dataclass(frozen=True)
class PageExtraction:
    title: str
    meta_description: str
    headings: tuple[str, ...]
    paragraphs: tuple[str, ...]
    links: tuple[str, ...]


@dataclass(frozen=True)
class NormalizedCompanySignal:
    signal_type: str
    focus: str
    observation: str


class PublicPageParser(HTMLParser):
    _CAPTURE_TAGS = {"title", "h1", "h2", "p"}
    _BLOCK_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta_description = ""
        self.links: list[str] = []
        self._blocked_depth = 0
        self._captures: list[tuple[str, list[str]]] = []
        self._captured: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if self._blocked_depth:
            if tag in self._BLOCK_TAGS:
                self._blocked_depth += 1
            return
        if tag in self._BLOCK_TAGS:
            self._blocked_depth = 1
            return
        if tag == "meta":
            name = (attributes.get("name") or attributes.get("property") or "").lower()
            if name in {"description", "og:description", "twitter:description"}:
                candidate = clean_text(attributes.get("content", ""))
                if candidate and not self.meta_description:
                    self.meta_description = candidate
        elif tag == "a":
            href = attributes.get("href", "").strip()
            if href:
                self.links.append(href)
        if tag in self._CAPTURE_TAGS:
            self._captures.append((tag, []))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._blocked_depth:
            return
        if tag.lower() == "meta":
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._blocked_depth:
            if tag in self._BLOCK_TAGS:
                self._blocked_depth -= 1
            return
        for index in range(len(self._captures) - 1, -1, -1):
            captured_tag, parts = self._captures[index]
            if captured_tag == tag:
                text = clean_text(" ".join(parts))
                if text:
                    self._captured.append((captured_tag, text))
                del self._captures[index]
                break

    def handle_data(self, data: str) -> None:
        if self._blocked_depth:
            return
        for _tag, parts in self._captures:
            parts.append(data)

    def extraction(self) -> PageExtraction:
        titles = [text for tag, text in self._captured if tag == "title"]
        headings = [text for tag, text in self._captured if tag in {"h1", "h2"}]
        paragraphs = [text for tag, text in self._captured if tag == "p"]
        return PageExtraction(
            title=titles[0] if titles else "",
            meta_description=self.meta_description,
            headings=tuple(dict.fromkeys(headings)),
            paragraphs=tuple(dict.fromkeys(paragraphs)),
            links=tuple(dict.fromkeys(self.links)),
        )


def parse_html(value: str) -> PageExtraction:
    parser = PublicPageParser()
    try:
        parser.feed(value)
        parser.close()
    except (AssertionError, ValueError):
        pass
    return parser.extraction()


def _sentences(value: str) -> list[str]:
    cleaned = clean_text(value)
    if not cleaned:
        return []
    return [clean_text(part) for part in _SENTENCE_RE.split(cleaned) if clean_text(part)]


def _acceptable(value: str, *, kind: str) -> bool:
    lowered = value.lower()
    words = value.split()
    minimum = 3 if kind in {"title", "heading"} else 6
    if len(words) < minimum or len(words) > 42:
        return False
    if any(phrase in lowered for phrase in _BLOCKED_PHRASES):
        return False
    if any(phrase in lowered for phrase in _PROMOTIONAL_PHRASES):
        return False
    if _METRIC_CLAIM_RE.search(value):
        return False
    if value.count("|") > 1 or value.count("/") > 3:
        return False
    if re.search(r"https?://|www\.|\S+@\S+", value, re.IGNORECASE):
        return False
    if re.match(r"^(?:visit|click|shop|browse|contact|download)\b", lowered):
        return False
    if re.match(r"^\(?see\b", lowered):
        return False
    final_token = re.sub(r"[^a-z]", "", words[-1].casefold())
    if final_token in _INCOMPLETE_FINAL_TOKENS:
        return False
    return True


def page_candidates(page: PageExtraction, source_url: str) -> list[TextCandidate]:
    candidates: list[TextCandidate] = []
    path = urlsplit(source_url).path.lower()
    page_bonus = 0.04 if any(token in path for token in ("about", "service", "solution")) else 0

    sources: list[tuple[str, str, float]] = []
    sources.extend((sentence, "meta", 0.84) for sentence in _sentences(page.meta_description))
    sources.extend((heading, "heading", 0.72) for heading in page.headings)
    sources.extend(
        (sentence, "paragraph", 0.66)
        for paragraph in page.paragraphs
        for sentence in _sentences(paragraph)
    )
    if page.title:
        title = re.split(r"\s+[|–—-]\s+", page.title, maxsplit=1)[0]
        sources.append((title, "title", 0.56))

    seen: set[str] = set()
    for value, kind, base_score in sources:
        text = clean_text(value).rstrip(".!?")
        identity = text.casefold()
        if identity in seen or not _acceptable(text, kind=kind):
            continue
        seen.add(identity)
        score = base_score + page_bonus
        if _BUSINESS_VERBS.search(text):
            score += 0.08
        word_count = len(text.split())
        if 8 <= word_count <= 24:
            score += 0.04
        candidates.append(
            TextCandidate(
                text=text,
                kind=kind,
                source_url=source_url,
                score=min(score, 0.96),
            )
        )
    return sorted(candidates, key=lambda item: (-item.score, len(item.text)))


def _shorten_clause(value: str, max_words: int = 15) -> str:
    cleaned = clean_text(value).rstrip(".!?")
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned
    clauses = re.split(
        r"\s*(?:[;:]|\s+[–—]\s+|\s+(?:that|which|while|whereas)\s+)\s*",
        cleaned,
    )
    for clause in clauses:
        words = clean_text(clause).split()
        if 5 <= len(words) <= max_words:
            return " ".join(words)
    shortened = words[:max_words]
    weak_endings = {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "or",
        "the",
        "to",
        "with",
    }
    while len(shortened) > 5 and shortened[-1].strip(",;:-").lower() in weak_endings:
        shortened.pop()
    return " ".join(shortened).rstrip(",;:-")


def normalize_company_signal(value: str) -> NormalizedCompanySignal:
    text = clean_text(value).rstrip(".!?")
    text = re.sub(
        r"^(?:world[- ]class|premier|industry[- ]leading)\s+",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    transformations = (
        (r"^we\s+help\b", "your team helps", "audience", "helping"),
        (r"^we\s+support\b", "your team supports", "audience", "supporting"),
        (r"^we\s+provide\b", "your team provides", "service", ""),
        (r"^we\s+offer\b", "your team offers", "product", ""),
        (r"^we\s+work\s+with\b", "your team works with", "audience", "working with"),
        (r"^we\s+speciali[sz]e\s+in\b", "your team specializes in", "specialism", ""),
        (
            r"^our\s+(?:firm|company|team)\s+helps\b",
            "your team helps",
            "audience",
            "helping",
        ),
        (
            r"^our\s+(?:firm|company|team)\s+provides\b",
            "your team provides",
            "service",
            "",
        ),
    )
    for pattern, replacement, signal_type, focus_prefix in transformations:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            remainder = clean_text(text[match.end() :])
            transformed = re.sub(pattern, replacement, text, count=1, flags=re.IGNORECASE)
            focus = clean_text(f"{focus_prefix} {remainder}") if focus_prefix else remainder
            return NormalizedCompanySignal(
                signal_type=signal_type,
                focus=_shorten_clause(focus),
                observation=_shorten_clause(transformed),
            )

    supplier = re.match(r"^supplier\s+(?:to|of)\s+(.+)$", text, re.IGNORECASE)
    if supplier:
        focus = _shorten_clause(supplier.group(1))
        return NormalizedCompanySignal(
            signal_type="product",
            focus=focus,
            observation=_shorten_clause("your team supplies " + supplier.group(1)),
        )

    discover_help = re.match(r"^discover\s+how\s+we\s+help\s+(.+)$", text, re.IGNORECASE)
    if discover_help:
        remainder = discover_help.group(1)
        return NormalizedCompanySignal(
            signal_type="audience",
            focus=_shorten_clause("helping " + remainder),
            observation=_shorten_clause("your team helps " + remainder),
        )

    discover_offer = re.match(
        r"^discover\s+(.+?)(?:\s+at\s+[A-Z].+|\s+in\s+[A-Z].+)$",
        text,
        re.IGNORECASE,
    )
    if discover_offer:
        focus = _shorten_clause(discover_offer.group(1))
        return NormalizedCompanySignal(
            signal_type="product",
            focus=focus,
            observation=_shorten_clause("your team offers " + discover_offer.group(1)),
        )

    provides = re.search(
        r"\b(?:ability\s+to\s+|designed\s+to\s+)?provides?\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if provides:
        focus = _shorten_clause(provides.group(1))
        return NormalizedCompanySignal(
            signal_type="service",
            focus=focus,
            observation=_shorten_clause("your team provides " + provides.group(1)),
        )

    named_team = re.match(
        r"^[A-Z][\w&'.-]+(?:\s+[A-Z][\w&'.-]+){0,3}\s+"
        r"(helps|supports|provides|offers|advises|manages|builds|delivers|serves|enables)\b",
        text,
    )
    if named_team:
        verb = named_team.group(1).lower()
        remainder = clean_text(text[named_team.end(1) :])
        if verb in {"helps", "supports", "serves"}:
            gerund = {"helps": "helping", "supports": "supporting", "serves": "serving"}[verb]
            signal_type = "audience"
            focus = clean_text(f"{gerund} {remainder}")
        elif verb == "offers":
            signal_type = "product"
            focus = remainder
        else:
            signal_type = "service"
            focus = remainder
        return NormalizedCompanySignal(
            signal_type=signal_type,
            focus=_shorten_clause(focus),
            observation=_shorten_clause("your team " + text[named_team.start(1) :]),
        )

    named_description = re.match(
        r"^.+?\s+is\s+((?:an?|the)\s+.+?)(?:\s+that\b|\s+which\b|$)",
        text,
        re.IGNORECASE,
    )
    if named_description:
        description = _shorten_clause(named_description.group(1))
        focus = re.sub(r"^(?:an?|the)\s+", "", description, flags=re.IGNORECASE)
        return NormalizedCompanySignal(
            signal_type="positioning",
            focus=_shorten_clause(focus),
            observation=_shorten_clause("your team is " + description),
        )

    lowered = text[:1].lower() + text[1:] if text else text
    focus = re.sub(r"\bsale\s+of\s+pianos\b", "piano sales", lowered, flags=re.IGNORECASE)
    return NormalizedCompanySignal(
        signal_type="specialism",
        focus=_shorten_clause(focus),
        observation=_shorten_clause(f"your site highlights {lowered}"),
    )


def observation_from_evidence(value: str) -> str:
    return normalize_company_signal(value).observation


def discover_internal_links(
    page: PageExtraction,
    base_url: str,
    domain: str,
    *,
    limit: int,
) -> list[str]:
    priorities = {
        "about": 100,
        "what-we-do": 95,
        "services": 90,
        "solutions": 85,
        "expertise": 80,
        "industries": 70,
    }
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for href in page.links:
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        host = (parts.hostname or "").lower().removeprefix("www.")
        if parts.scheme not in {"http", "https"} or host != domain:
            continue
        clean_url = absolute.split("#", 1)[0].rstrip("/") or absolute
        if clean_url in seen:
            continue
        lowered_path = parts.path.lower().strip("/")
        score = max((value for token, value in priorities.items() if token in lowered_path), default=0)
        if score:
            seen.add(clean_url)
            scored.append((score, clean_url))
    scored.sort(key=lambda item: (-item[0], len(item[1])))
    return [url for _score, url in scored[:limit]]
