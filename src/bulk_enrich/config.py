"""Campaign configuration loading and validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from bulk_enrich.llm_focus import (
    DEFAULT_DOMAINS_PER_CALL,
    DEFAULT_EFFORT,
    DEFAULT_MAX_NOMINAL_USD,
    DEFAULT_MAX_PITCH_WORDS,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    EFFORT_LEVELS,
    FIT_TIERS,
    MAX_DOMAINS_PER_CALL,
    PROVIDERS,
    LlmFocusExample,
    LlmFocusSettings,
)


class CampaignConfigError(ValueError):
    """Raised when a campaign configuration is incomplete or unsafe to use."""


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class CopyTemplate:
    template_id: str
    subject: str
    pitch: str


@dataclass(frozen=True)
class CopyAngle:
    angle_id: str
    signal_types: tuple[str, ...]
    templates: tuple[CopyTemplate, ...]


@dataclass(frozen=True)
class CtaVariant:
    variant_id: str
    text: str
    focus_rules: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class OfferLineVariant:
    variant_id: str
    text: str
    focus_rules: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class RowFallbackField:
    header: str
    confidence: float


@dataclass(frozen=True)
class PersonaFallbackTemplate:
    template_id: str
    personas: tuple[str, ...]
    subject: str
    pitch: str


@dataclass(frozen=True)
class CampaignConfig:
    path: Path
    data: dict[str, Any]

    @property
    def campaign_id(self) -> str:
        return str(self.data["campaign_id"])

    @property
    def status(self) -> str:
        return str(self.data["status"])

    @property
    def output_field(self) -> str:
        return str(self.data["personalization"]["output_field"])

    @property
    def min_confidence(self) -> float:
        return float(self.data["personalization"]["min_confidence"])

    @property
    def max_words(self) -> int:
        return int(self.data["personalization"]["max_words"])

    @property
    def banned_phrases(self) -> tuple[str, ...]:
        return tuple(
            str(item).casefold()
            for item in self.data["personalization"]["banned_phrases"]
        )

    @property
    def blocked_evidence_phrases(self) -> tuple[str, ...]:
        configured = self.data["personalization"].get("blocked_evidence_phrases", [])
        return tuple(str(item).casefold() for item in configured)

    @property
    def llm_focus(self) -> LlmFocusSettings | None:
        """Opt-in model classification settings, or None when disabled."""
        configured = self.data["personalization"].get("llm_focus")
        if not isinstance(configured, dict) or not configured.get("enabled"):
            return None
        examples = tuple(
            LlmFocusExample(
                site=str(example["site"]).strip(),
                fit_tier=str(example["fit_tier"]).strip().casefold(),
                focus=str(example["focus"]).strip(),
                buyer_phrase=str(example["buyer_phrase"]).strip(),
            )
            for example in configured.get("examples", [])
        )
        return LlmFocusSettings(
            enabled=True,
            icp=str(configured["icp"]).strip(),
            exclusions=str(configured.get("exclusions", "")).strip(),
            model=str(configured.get("model", DEFAULT_MODEL)).strip(),
            effort=str(configured.get("effort", DEFAULT_EFFORT)).strip(),
            examples=examples,
            max_evidence_chars=int(configured.get("max_evidence_chars", 3000)),
            provider=str(configured.get("provider", DEFAULT_PROVIDER)).strip(),
            domains_per_call=int(configured.get("domains_per_call", DEFAULT_DOMAINS_PER_CALL)),
            write_pitch=bool(configured.get("write_pitch", True)),
            max_pitch_words=int(configured.get("max_pitch_words", DEFAULT_MAX_PITCH_WORDS)),
            max_nominal_usd=float(configured.get("max_nominal_usd", DEFAULT_MAX_NOMINAL_USD)),
            allow_expensive_models=bool(configured.get("allow_expensive_models", False)),
        )

    @property
    def focus_rules_path(self) -> Path:
        """Resolve the campaign's niche mapping table relative to its JSON file."""
        configured = Path(str(self.data["personalization"]["focus_rules_file"])).expanduser()
        if not configured.is_absolute():
            configured = self.path.parent / configured
        return configured.resolve()

    @property
    def cta_variants(self) -> tuple[CtaVariant, ...]:
        configured = self.data["offer"].get("cta_variants")
        if configured:
            return tuple(
                CtaVariant(
                    variant_id=str(item["id"]),
                    text=str(item["text"]),
                    focus_rules=tuple(
                        str(rule) for rule in item.get("focus_rules", ["*"])
                    ),
                )
                for item in configured
            )
        return (CtaVariant(variant_id="default-cta", text=str(self.data["offer"]["cta"])),)

    @property
    def default_cta_variants(self) -> tuple[CtaVariant, ...]:
        return tuple(
            variant for variant in self.cta_variants if "*" in variant.focus_rules
        )

    @property
    def offer_line_variants(self) -> tuple[OfferLineVariant, ...]:
        configured = self.data["offer"].get("risk_reversal_variants")
        if configured:
            return tuple(
                OfferLineVariant(
                    variant_id=str(item["id"]),
                    text=str(item["text"]),
                    focus_rules=tuple(
                        str(rule) for rule in item.get("focus_rules", ["*"])
                    ),
                )
                for item in configured
            )
        return (
            OfferLineVariant(
                variant_id="default-offer-line",
                text=str(self.data["offer"].get("risk_reversal", "")),
                focus_rules=("*",),
            ),
        )

    @property
    def default_offer_line_variants(self) -> tuple[OfferLineVariant, ...]:
        return tuple(
            variant
            for variant in self.offer_line_variants
            if "*" in variant.focus_rules
        )

    @property
    def angles(self) -> tuple[CopyAngle, ...]:
        return tuple(
            CopyAngle(
                angle_id=str(angle["id"]),
                signal_types=tuple(str(item) for item in angle["signal_types"]),
                templates=tuple(
                    CopyTemplate(
                        template_id=str(template["id"]),
                        subject=str(template["subject"]),
                        pitch=str(template["pitch"]),
                    )
                    for template in angle["templates"]
                ),
            )
            for angle in self.data["personalization"]["angles"]
        )

    @property
    def max_body_words(self) -> int:
        return int(self.data["quality"]["max_body_words"])

    @property
    def max_subject_words(self) -> int:
        return int(self.data["quality"]["max_subject_words"])

    @property
    def max_focus_words(self) -> int:
        return int(self.data["quality"]["max_focus_words"])

    @property
    def max_buyer_phrase_words(self) -> int:
        return int(self.data["quality"]["max_buyer_phrase_words"])

    @property
    def max_source_phrase_words(self) -> int:
        return int(self.data["quality"]["max_source_phrase_words"])

    @property
    def max_cta_words(self) -> int:
        return int(self.data["quality"]["max_cta_words"])

    @property
    def max_offer_line_words(self) -> int:
        return int(self.data["quality"].get("max_offer_line_words", 24))

    @property
    def row_fallback_fields(self) -> tuple[RowFallbackField, ...]:
        configured = self.data["personalization"].get("row_fallback")
        if not configured or not configured.get("enabled", False):
            return ()
        return tuple(
            RowFallbackField(
                header=str(item["header"]),
                confidence=float(item["confidence"]),
            )
            for item in configured["fields"]
        )

    @property
    def fallback_copy_enabled(self) -> bool:
        configured = self.data["personalization"].get("fallback_copy", {})
        return bool(configured.get("enabled", False))

    @property
    def fallback_copy_status(self) -> str:
        configured = self.data["personalization"].get("fallback_copy", {})
        return str(configured.get("status", "review"))

    @property
    def fallback_allow_unmatched_company(self) -> bool:
        configured = self.data["personalization"].get("fallback_copy", {})
        return bool(configured.get("allow_unmatched_company", False))

    @property
    def fallback_allow_single_csv_field(self) -> bool:
        configured = self.data["personalization"].get("fallback_copy", {})
        return bool(configured.get("allow_single_csv_field", False))

    @property
    def fallback_allow_explicit_company_exclusions(self) -> bool:
        configured = self.data["personalization"].get("fallback_copy", {})
        return bool(configured.get("allow_explicit_company_exclusions", False))

    @property
    def fallback_promote_company_review(self) -> bool:
        configured = self.data["personalization"].get("fallback_copy", {})
        return bool(configured.get("promote_company_review", False))

    @property
    def fallback_templates(self) -> tuple[PersonaFallbackTemplate, ...]:
        configured = self.data["personalization"].get("fallback_copy", {})
        return tuple(
            PersonaFallbackTemplate(
                template_id=str(item["id"]),
                personas=tuple(str(persona) for persona in item["personas"]),
                subject=str(item["subject"]),
                pitch=str(item["pitch"]),
            )
            for item in configured.get("templates", [])
        )

    def fallback_templates_for_persona(
        self,
        persona: str,
    ) -> tuple[CopyTemplate, ...]:
        exact = tuple(
            CopyTemplate(item.template_id, item.subject, item.pitch)
            for item in self.fallback_templates
            if persona in item.personas
        )
        if exact:
            return exact
        return tuple(
            CopyTemplate(item.template_id, item.subject, item.pitch)
            for item in self.fallback_templates
            if "*" in item.personas
        )

    @property
    def fallback_qualification_fields(self) -> tuple[str, ...]:
        return tuple(
            str(item) for item in self.data["qualification"]["company"]["fallback_fields"]
        )

    @property
    def fallback_min_agreeing_fields(self) -> int:
        return int(
            self.data["qualification"]["company"]["fallback_min_agreeing_fields"]
        )

    @property
    def ready_title_patterns(self) -> tuple[str, ...]:
        return tuple(
            str(item)
            for item in self.data["qualification"]["contact"]["ready_title_patterns"]
        )

    @property
    def review_title_patterns(self) -> tuple[str, ...]:
        return tuple(
            str(item)
            for item in self.data["qualification"]["contact"]["review_title_patterns"]
        )

    @property
    def excluded_title_patterns(self) -> tuple[str, ...]:
        return tuple(
            str(item)
            for item in self.data["qualification"]["contact"]["exclude_title_patterns"]
        )

    @property
    def contact_priority_patterns(self) -> tuple[str, ...]:
        return tuple(
            str(item)
            for item in self.data["qualification"]["contact"][
                "priority_title_patterns"
            ]
        )

    @property
    def ready_seniorities(self) -> tuple[str, ...]:
        return tuple(
            str(item)
            for item in self.data["qualification"]["contact"]["ready_seniorities"]
        )

    @property
    def review_seniorities(self) -> tuple[str, ...]:
        return tuple(
            str(item)
            for item in self.data["qualification"]["contact"]["review_seniorities"]
        )

    @property
    def accepted_email_statuses(self) -> tuple[str, ...]:
        return tuple(
            str(item)
            for item in self.data["qualification"]["email"]["accepted_statuses"]
        )

    @property
    def review_email_statuses(self) -> tuple[str, ...]:
        return tuple(
            str(item)
            for item in self.data["qualification"]["email"]["review_statuses"]
        )

    @property
    def rejected_email_statuses(self) -> tuple[str, ...]:
        return tuple(
            str(item)
            for item in self.data["qualification"]["email"]["rejected_statuses"]
        )

    @property
    def missing_email_status_action(self) -> str:
        return str(self.data["qualification"]["email"]["missing_status_action"])


def _require_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise CampaignConfigError(f"'{key}' must be a JSON object")
    return value


def _require_nonempty_string(
    parent: dict[str, Any], key: str, *, label: str | None = None
) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CampaignConfigError(f"'{label or key}' must be a non-empty string")
    return value.strip()


def _require_string_list(
    parent: dict[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
    label: str | None = None,
) -> list[str]:
    value = parent.get(key)
    valid = isinstance(value, list) and all(
        isinstance(item, str) and item.strip() for item in value
    )
    if not valid or (not allow_empty and not value):
        qualifier = "an array" if allow_empty else "a non-empty array"
        raise CampaignConfigError(
            f"'{label or key}' must be {qualifier} of non-empty strings"
        )
    return [str(item).strip() for item in value]


def _validate_slug(value: str, label: str) -> None:
    if not _SLUG_RE.fullmatch(value):
        raise CampaignConfigError(
            f"'{label}' must use lowercase letters, numbers, and hyphens"
        )


def _contains_banned_phrase(value: str, banned_phrases: list[str]) -> str:
    lowered = value.casefold()
    return next((phrase for phrase in banned_phrases if phrase.casefold() in lowered), "")


def forbidden_copy_character(value: str) -> str:
    """Return a human-readable name for a character prohibited in outreach copy."""
    if "\u2014" in value:
        return "em dash"
    return ""


def _validate_llm_focus(configured: Any) -> None:
    if configured is None:
        return
    label = "personalization.llm_focus"
    if not isinstance(configured, dict):
        raise CampaignConfigError(f"'{label}' must be a JSON object")
    allowed = {
        "enabled",
        "icp",
        "exclusions",
        "provider",
        "model",
        "effort",
        "examples",
        "max_evidence_chars",
        "domains_per_call",
        "write_pitch",
        "max_pitch_words",
        "max_nominal_usd",
        "allow_expensive_models",
    }
    unknown = sorted(set(configured).difference(allowed))
    if unknown:
        raise CampaignConfigError(f"'{label}' has unknown keys: " + ", ".join(unknown))
    enabled = configured.get("enabled")
    if not isinstance(enabled, bool):
        raise CampaignConfigError(f"'{label}.enabled' must be true or false")
    if enabled:
        _require_nonempty_string(configured, "icp", label=f"{label}.icp")
    elif "icp" in configured and not isinstance(configured["icp"], str):
        raise CampaignConfigError(f"'{label}.icp' must be a string")
    if "exclusions" in configured and not isinstance(configured["exclusions"], str):
        raise CampaignConfigError(f"'{label}.exclusions' must be a string")
    if "provider" in configured and configured["provider"] not in PROVIDERS:
        raise CampaignConfigError(
            f"'{label}.provider' must be one of: " + ", ".join(PROVIDERS)
        )
    domains_per_call = configured.get("domains_per_call", DEFAULT_DOMAINS_PER_CALL)
    if (
        isinstance(domains_per_call, bool)
        or not isinstance(domains_per_call, int)
        or not 1 <= domains_per_call <= MAX_DOMAINS_PER_CALL
    ):
        raise CampaignConfigError(
            f"'{label}.domains_per_call' must be an integer from 1 to {MAX_DOMAINS_PER_CALL}"
        )
    for flag in ("write_pitch", "allow_expensive_models"):
        if flag in configured and not isinstance(configured[flag], bool):
            raise CampaignConfigError(f"'{label}.{flag}' must be true or false")
    max_pitch_words = configured.get("max_pitch_words", DEFAULT_MAX_PITCH_WORDS)
    if (
        isinstance(max_pitch_words, bool)
        or not isinstance(max_pitch_words, int)
        or not 10 <= max_pitch_words <= 60
    ):
        raise CampaignConfigError(f"'{label}.max_pitch_words' must be an integer from 10 to 60")
    max_nominal = configured.get("max_nominal_usd", DEFAULT_MAX_NOMINAL_USD)
    if (
        isinstance(max_nominal, bool)
        or not isinstance(max_nominal, (int, float))
        or float(max_nominal) <= 0
    ):
        raise CampaignConfigError(f"'{label}.max_nominal_usd' must be a number greater than 0")
    if "model" in configured:
        _require_nonempty_string(configured, "model", label=f"{label}.model")
    if "effort" in configured and configured["effort"] not in EFFORT_LEVELS:
        raise CampaignConfigError(
            f"'{label}.effort' must be one of: " + ", ".join(EFFORT_LEVELS)
        )
    max_chars = configured.get("max_evidence_chars", 3000)
    if (
        isinstance(max_chars, bool)
        or not isinstance(max_chars, int)
        or not 500 <= max_chars <= 12_000
    ):
        raise CampaignConfigError(
            f"'{label}.max_evidence_chars' must be an integer from 500 to 12000"
        )
    examples = configured.get("examples", [])
    if not isinstance(examples, list) or len(examples) > 12:
        raise CampaignConfigError(f"'{label}.examples' must be an array of at most 12 items")
    for index, example in enumerate(examples):
        example_label = f"{label}.examples[{index}]"
        if not isinstance(example, dict):
            raise CampaignConfigError(f"'{example_label}' must be an object")
        for key in ("site", "focus", "buyer_phrase"):
            _require_nonempty_string(example, key, label=f"{example_label}.{key}")
        fit_tier = str(example.get("fit_tier", "")).strip().casefold()
        if fit_tier not in FIT_TIERS:
            raise CampaignConfigError(
                f"'{example_label}.fit_tier' must be core, secondary, or exclude"
            )


def validate_campaign_data(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise CampaignConfigError("campaign configuration must be a JSON object")

    if data.get("schema_version") != "3.0":
        if data.get("schema_version") == "2.0":
            raise CampaignConfigError(
                "campaign schema 2.0 is no longer supported; migrate it to schema 3.0 "
                "and add qualification rules"
            )
        raise CampaignConfigError("'schema_version' must be '3.0'")

    campaign_id = _require_nonempty_string(data, "campaign_id")
    _validate_slug(campaign_id, "campaign_id")

    status = _require_nonempty_string(data, "status")
    if status not in {"test_only", "approved"}:
        raise CampaignConfigError("'status' must be 'test_only' or 'approved'")

    _require_nonempty_string(data, "client_name")

    sender = _require_mapping(data, "sender")
    _require_nonempty_string(sender, "name", label="sender.name")

    offer = _require_mapping(data, "offer")
    _require_nonempty_string(offer, "service", label="offer.service")
    _require_nonempty_string(offer, "audience", label="offer.audience")
    if not isinstance(offer.get("risk_reversal"), str):
        raise CampaignConfigError("'offer.risk_reversal' must be a string")
    _require_nonempty_string(offer, "cta", label="offer.cta")
    cta_variants = offer.get("cta_variants", [])
    if not isinstance(cta_variants, list):
        raise CampaignConfigError("'offer.cta_variants' must be an array")
    cta_ids: set[str] = set()
    cta_copy: list[tuple[str, str]] = []
    has_default_cta = False
    for index, variant in enumerate(cta_variants):
        label = f"offer.cta_variants[{index}]"
        if not isinstance(variant, dict):
            raise CampaignConfigError(f"'{label}' must be an object")
        variant_id = _require_nonempty_string(variant, "id", label=f"{label}.id")
        _validate_slug(variant_id, f"{label}.id")
        if variant_id in cta_ids:
            raise CampaignConfigError(f"duplicate CTA variant id: {variant_id}")
        cta_ids.add(variant_id)
        cta_text = _require_nonempty_string(variant, "text", label=f"{label}.text")
        focus_rules = variant.get("focus_rules", ["*"])
        if (
            not isinstance(focus_rules, list)
            or not focus_rules
            or not all(isinstance(rule, str) and rule.strip() for rule in focus_rules)
        ):
            raise CampaignConfigError(
                f"'{label}.focus_rules' must be a non-empty array of strings"
            )
        normalized_focus_rules = [rule.strip() for rule in focus_rules]
        if len(set(normalized_focus_rules)) != len(normalized_focus_rules):
            raise CampaignConfigError(
                f"'{label}.focus_rules' must not contain duplicates"
            )
        for rule in normalized_focus_rules:
            if rule != "*":
                _validate_slug(rule, f"{label}.focus_rules")
        has_default_cta = has_default_cta or "*" in normalized_focus_rules
        cta_copy.append((f"{label}.text", cta_text))
    if cta_variants and not has_default_cta:
        raise CampaignConfigError(
            "offer.cta_variants must include at least one '*' focus-rule fallback"
        )
    offer_line_variants = offer.get("risk_reversal_variants", [])
    if not isinstance(offer_line_variants, list):
        raise CampaignConfigError("'offer.risk_reversal_variants' must be an array")
    offer_line_ids: set[str] = set()
    offer_line_copy: list[tuple[str, str]] = []
    has_default_offer_line = False
    for index, variant in enumerate(offer_line_variants):
        label = f"offer.risk_reversal_variants[{index}]"
        if not isinstance(variant, dict):
            raise CampaignConfigError(f"'{label}' must be an object")
        variant_id = _require_nonempty_string(variant, "id", label=f"{label}.id")
        _validate_slug(variant_id, f"{label}.id")
        if variant_id in offer_line_ids:
            raise CampaignConfigError(f"duplicate offer-line variant id: {variant_id}")
        offer_line_ids.add(variant_id)
        variant_text = _require_nonempty_string(
            variant, "text", label=f"{label}.text"
        )
        focus_rules = variant.get("focus_rules", ["*"])
        if (
            not isinstance(focus_rules, list)
            or not focus_rules
            or not all(isinstance(rule, str) and rule.strip() for rule in focus_rules)
        ):
            raise CampaignConfigError(
                f"'{label}.focus_rules' must be a non-empty array of strings"
            )
        normalized_focus_rules = [rule.strip() for rule in focus_rules]
        if len(set(normalized_focus_rules)) != len(normalized_focus_rules):
            raise CampaignConfigError(
                f"'{label}.focus_rules' must not contain duplicates"
            )
        for rule in normalized_focus_rules:
            if rule != "*":
                _validate_slug(rule, f"{label}.focus_rules")
        has_default_offer_line = has_default_offer_line or "*" in normalized_focus_rules
        offer_line_copy.append((f"{label}.text", variant_text))
    if offer_line_variants and not has_default_offer_line:
        raise CampaignConfigError(
            "offer.risk_reversal_variants must include at least one '*' focus-rule fallback"
        )
    for list_key in ("approved_claims", "forbidden_claims"):
        _require_string_list(offer, list_key, label=f"offer.{list_key}")

    personalization = _require_mapping(data, "personalization")
    _require_nonempty_string(
        personalization, "objective", label="personalization.objective"
    )
    output_field = _require_nonempty_string(
        personalization, "output_field", label="personalization.output_field"
    )
    _require_nonempty_string(
        personalization,
        "focus_rules_file",
        label="personalization.focus_rules_file",
    )
    max_words = personalization.get("max_words")
    if not isinstance(max_words, int) or not 5 <= max_words <= 100:
        raise CampaignConfigError(
            "'personalization.max_words' must be an integer from 5 to 100"
        )
    if personalization.get("low_confidence_action") not in {"review", "blank"}:
        raise CampaignConfigError(
            "'personalization.low_confidence_action' must be 'review' or 'blank'"
        )
    min_confidence = personalization.get("min_confidence")
    if not isinstance(min_confidence, (int, float)) or isinstance(min_confidence, bool):
        raise CampaignConfigError("'personalization.min_confidence' must be a number")
    if not 0 <= float(min_confidence) <= 1:
        raise CampaignConfigError(
            "'personalization.min_confidence' must be from 0 to 1"
        )
    banned_phrases = _require_string_list(
        personalization,
        "banned_phrases",
        allow_empty=True,
        label="personalization.banned_phrases",
    )
    if "blocked_evidence_phrases" in personalization:
        _require_string_list(
            personalization,
            "blocked_evidence_phrases",
            allow_empty=True,
            label="personalization.blocked_evidence_phrases",
        )

    _validate_llm_focus(personalization.get("llm_focus"))

    row_fallback = personalization.get("row_fallback")
    seen_headers: set[str] = set()
    if row_fallback is not None:
        if not isinstance(row_fallback, dict):
            raise CampaignConfigError(
                "'personalization.row_fallback' must be a JSON object"
            )
        enabled = row_fallback.get("enabled")
        if not isinstance(enabled, bool):
            raise CampaignConfigError(
                "'personalization.row_fallback.enabled' must be true or false"
            )
        fields = row_fallback.get("fields")
        if not isinstance(fields, list) or (enabled and not fields):
            raise CampaignConfigError(
                "'personalization.row_fallback.fields' must be a non-empty array when enabled"
            )
        for index, item in enumerate(fields):
            label = f"personalization.row_fallback.fields[{index}]"
            if not isinstance(item, dict):
                raise CampaignConfigError(f"'{label}' must be an object")
            header = _require_nonempty_string(item, "header", label=f"{label}.header")
            header_identity = header.casefold()
            if header_identity in seen_headers:
                raise CampaignConfigError(f"duplicate row fallback header: {header}")
            seen_headers.add(header_identity)
            confidence = item.get("confidence")
            if (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not 0 <= float(confidence) <= 1
            ):
                raise CampaignConfigError(
                    f"'{label}.confidence' must be a number from 0 to 1"
                )

    fallback_copy = personalization.get("fallback_copy")
    fallback_copy_values: list[tuple[str, str]] = []
    if fallback_copy is not None:
        if not isinstance(fallback_copy, dict):
            raise CampaignConfigError(
                "'personalization.fallback_copy' must be a JSON object"
            )
        enabled = fallback_copy.get("enabled")
        if not isinstance(enabled, bool):
            raise CampaignConfigError(
                "'personalization.fallback_copy.enabled' must be true or false"
            )
        if fallback_copy.get("status", "review") not in {"ready", "review"}:
            raise CampaignConfigError(
                "'personalization.fallback_copy.status' must be ready or review"
            )
        for key in (
            "allow_unmatched_company",
            "allow_single_csv_field",
            "allow_explicit_company_exclusions",
            "promote_company_review",
        ):
            if not isinstance(fallback_copy.get(key, False), bool):
                raise CampaignConfigError(
                    f"'personalization.fallback_copy.{key}' must be true or false"
                )
        templates = fallback_copy.get("templates", [])
        if not isinstance(templates, list) or (enabled and not templates):
            raise CampaignConfigError(
                "'personalization.fallback_copy.templates' must be a non-empty "
                "array when enabled"
            )
        fallback_ids: set[str] = set()
        has_default_fallback = False
        for index, template in enumerate(templates):
            label = f"personalization.fallback_copy.templates[{index}]"
            if not isinstance(template, dict):
                raise CampaignConfigError(f"'{label}' must be an object")
            template_id = _require_nonempty_string(
                template,
                "id",
                label=f"{label}.id",
            )
            _validate_slug(template_id, f"{label}.id")
            if template_id in fallback_ids:
                raise CampaignConfigError(
                    f"duplicate fallback template id: {template_id}"
                )
            fallback_ids.add(template_id)
            personas = _require_string_list(
                template,
                "personas",
                label=f"{label}.personas",
            )
            has_default_fallback = has_default_fallback or "*" in personas
            subject = _require_nonempty_string(
                template,
                "subject",
                label=f"{label}.subject",
            )
            pitch = _require_nonempty_string(
                template,
                "pitch",
                label=f"{label}.pitch",
            )
            fallback_copy_values.extend(
                ((f"{label}.subject", subject), (f"{label}.pitch", pitch))
            )
        if enabled and not has_default_fallback:
            raise CampaignConfigError(
                "personalization.fallback_copy.templates must include a '*' "
                "persona fallback"
            )

    qualification = _require_mapping(data, "qualification")
    company_qualification = _require_mapping(qualification, "company")
    fallback_min = company_qualification.get("fallback_min_agreeing_fields")
    if not isinstance(fallback_min, int) or isinstance(fallback_min, bool) or not 2 <= fallback_min <= 10:
        raise CampaignConfigError(
            "'qualification.company.fallback_min_agreeing_fields' must be an integer from 2 to 10"
        )
    fallback_fields = _require_string_list(
        company_qualification,
        "fallback_fields",
        allow_empty=True,
        label="qualification.company.fallback_fields",
    )
    unknown_fallback_fields = [
        item for item in fallback_fields if item.casefold() not in seen_headers
    ]
    if unknown_fallback_fields:
        raise CampaignConfigError(
            "qualification company fallback fields must also appear in "
            "personalization.row_fallback.fields: "
            + ", ".join(unknown_fallback_fields)
        )

    contact_qualification = _require_mapping(qualification, "contact")
    for key, allow_empty in (
        ("priority_title_patterns", False),
        ("ready_title_patterns", False),
        ("review_title_patterns", True),
        ("exclude_title_patterns", True),
    ):
        patterns = _require_string_list(
            contact_qualification,
            key,
            allow_empty=allow_empty,
            label=f"qualification.contact.{key}",
        )
        for index, pattern in enumerate(patterns):
            try:
                re.compile(pattern, re.I)
            except re.error as exc:
                raise CampaignConfigError(
                    f"invalid qualification contact pattern at {key}[{index}]: {exc}"
                ) from exc
    ready_seniorities = _require_string_list(
        contact_qualification,
        "ready_seniorities",
        label="qualification.contact.ready_seniorities",
    )
    review_seniorities = _require_string_list(
        contact_qualification,
        "review_seniorities",
        allow_empty=True,
        label="qualification.contact.review_seniorities",
    )
    if {item.casefold() for item in ready_seniorities} & {
        item.casefold() for item in review_seniorities
    }:
        raise CampaignConfigError(
            "qualification ready and review seniorities must not overlap"
        )

    email_qualification = _require_mapping(qualification, "email")
    email_status_groups: list[tuple[str, list[str]]] = []
    for key, allow_empty in (
        ("accepted_statuses", False),
        ("review_statuses", True),
        ("rejected_statuses", False),
    ):
        email_status_groups.append(
            (
                key,
                _require_string_list(
                    email_qualification,
                    key,
                    allow_empty=allow_empty,
                    label=f"qualification.email.{key}",
                ),
            )
        )
    seen_statuses: dict[str, str] = {}
    for key, statuses in email_status_groups:
        for status_value in statuses:
            identity = re.sub(r"[^a-z0-9]+", " ", status_value.casefold()).strip()
            if identity in seen_statuses:
                raise CampaignConfigError(
                    f"email status '{status_value}' appears in both {seen_statuses[identity]} and {key}"
                )
            seen_statuses[identity] = key
    if email_qualification.get("missing_status_action") not in {
        "syntax",
        "review",
        "exclude",
    }:
        raise CampaignConfigError(
            "'qualification.email.missing_status_action' must be syntax, review, or exclude"
        )

    angles = personalization.get("angles")
    if not isinstance(angles, list) or not angles:
        raise CampaignConfigError(
            "'personalization.angles' must be a non-empty array"
        )
    angle_ids: set[str] = set()
    template_ids: set[str] = set()
    has_fallback = False
    copy_values: list[tuple[str, str]] = []
    for angle_index, angle in enumerate(angles):
        if not isinstance(angle, dict):
            raise CampaignConfigError(
                f"'personalization.angles[{angle_index}]' must be an object"
            )
        angle_id = _require_nonempty_string(
            angle, "id", label=f"personalization.angles[{angle_index}].id"
        )
        _validate_slug(angle_id, f"personalization.angles[{angle_index}].id")
        if angle_id in angle_ids:
            raise CampaignConfigError(f"duplicate angle id: {angle_id}")
        angle_ids.add(angle_id)
        signal_types = _require_string_list(
            angle,
            "signal_types",
            label=f"personalization.angles[{angle_index}].signal_types",
        )
        has_fallback = has_fallback or "*" in signal_types
        templates = angle.get("templates")
        if not isinstance(templates, list) or not templates:
            raise CampaignConfigError(
                f"'personalization.angles[{angle_index}].templates' must be a non-empty array"
            )
        for template_index, template in enumerate(templates):
            if not isinstance(template, dict):
                raise CampaignConfigError(
                    "each personalization template must be an object"
                )
            label = (
                f"personalization.angles[{angle_index}].templates[{template_index}]"
            )
            template_id = _require_nonempty_string(template, "id", label=f"{label}.id")
            _validate_slug(template_id, f"{label}.id")
            if template_id in template_ids:
                raise CampaignConfigError(f"duplicate template id: {template_id}")
            template_ids.add(template_id)
            subject = _require_nonempty_string(template, "subject", label=f"{label}.subject")
            pitch = _require_nonempty_string(template, "pitch", label=f"{label}.pitch")
            if not any(
                merge_field in pitch
                for merge_field in ("{{company_focus}}", "{{buyer_phrase}}")
            ):
                raise CampaignConfigError(
                    "each personalization pitch must contain '{{company_focus}}' "
                    "or '{{buyer_phrase}}'"
                )
            copy_values.extend(((f"{label}.subject", subject), (f"{label}.pitch", pitch)))
    if not has_fallback:
        raise CampaignConfigError(
            "one personalization angle must include '*' in signal_types as a fallback"
        )

    email = _require_mapping(data, "email")
    body = _require_nonempty_string(email, "body", label="email.body")
    if "{{" + output_field + "}}" not in body:
        raise CampaignConfigError(
            f"'email.body' must contain '{{{{{output_field}}}}}'"
        )
    if not isinstance(email.get("preserve_spintax"), bool):
        raise CampaignConfigError("'email.preserve_spintax' must be true or false")

    quality = _require_mapping(data, "quality")
    integer_ranges = {
        "max_body_words": (10, 200),
        "max_subject_words": (1, 20),
        "opening_words": (2, 8),
        "min_rows": (2, 1_000_000),
        "max_focus_words": (2, 12),
        "max_buyer_phrase_words": (2, 16),
        "max_source_phrase_words": (2, 10),
        "max_cta_words": (3, 20),
        "max_offer_line_words": (3, 40),
    }
    for key, (minimum, maximum) in integer_ranges.items():
        value = quality.get(key)
        if not isinstance(value, int) or not minimum <= value <= maximum:
            raise CampaignConfigError(
                f"'quality.{key}' must be an integer from {minimum} to {maximum}"
            )
    for key in (
        "max_opening_share",
        "max_exact_pitch_share",
        "max_cta_share",
        "max_offer_line_share",
    ):
        value = quality.get(key)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0 < float(value) <= 1
        ):
            raise CampaignConfigError(f"'quality.{key}' must be greater than 0 and at most 1")
    buyer_phrase_share = quality.get("max_buyer_phrase_share")
    if buyer_phrase_share is not None and (
        not isinstance(buyer_phrase_share, (int, float))
        or isinstance(buyer_phrase_share, bool)
        or not 0 < float(buyer_phrase_share) <= 1
    ):
        raise CampaignConfigError(
            "'quality.max_buyer_phrase_share' must be null or greater than 0 and at most 1"
        )
    if quality.get("repetition_action") not in {"warn", "review"}:
        raise CampaignConfigError(
            "'quality.repetition_action' must be 'warn' or 'review'"
        )

    configured_copy = [
        ("offer.service", str(offer["service"])),
        ("offer.risk_reversal", str(offer["risk_reversal"])),
        *offer_line_copy,
        ("offer.cta", str(offer["cta"])),
        *cta_copy,
        ("email.body", body),
        *copy_values,
        *fallback_copy_values,
    ]
    for label, value in configured_copy:
        forbidden_character = forbidden_copy_character(value)
        if forbidden_character:
            raise CampaignConfigError(
                f"'{label}' contains forbidden character: {forbidden_character}"
            )
        banned = _contains_banned_phrase(value, banned_phrases)
        if banned:
            raise CampaignConfigError(
                f"'{label}' contains banned phrase '{banned}'"
            )

    llm_focus = personalization.get("llm_focus")
    if isinstance(llm_focus, dict) and llm_focus.get("enabled") and llm_focus.get("write_pitch", True):
        pitch_words = int(llm_focus.get("max_pitch_words", DEFAULT_MAX_PITCH_WORDS))
        needed = (
            pitch_words
            + int(quality.get("max_offer_line_words", 24))
            + int(quality["max_cta_words"])
            + 8
        )
        if int(quality["max_body_words"]) < needed:
            raise CampaignConfigError(
                "'quality.max_body_words' must be at least "
                f"{needed} when llm_focus.write_pitch is enabled with "
                f"max_pitch_words {pitch_words}; raise it or lower max_pitch_words"
            )

    output = _require_mapping(data, "output")
    fields = output.get("append_fields")
    if (
        not isinstance(fields, list)
        or not fields
        or not all(isinstance(item, str) and item for item in fields)
    ):
        raise CampaignConfigError(
            "'output.append_fields' must be a non-empty array of strings"
        )
    required_output_fields = {
        "personalized_subject",
        output_field,
        "personalized_email",
        "personalization_angle",
        "personalization_template",
        "personalization_signal_type",
        "personalization_source_focus",
        "personalization_focus",
        "personalization_buyer_phrase",
        "personalization_focus_rule",
        "personalization_cta_variant",
        "personalization_cta",
        "personalization_offer_variant",
        "personalization_offer_line",
        "personalization_facts",
        "personalization_source",
        "personalization_evidence",
        "personalization_confidence",
        "personalization_quality_flags",
        "personalization_status",
        "personalization_error",
        "company_fit_status",
        "company_fit_tier",
        "company_fit_rule",
        "company_fit_source",
        "company_fit_evidence",
        "company_fit_reason",
        "contact_fit_status",
        "contact_fit_rule",
        "contact_fit_reason",
        "email_fit_status",
        "email_fit_rule",
        "email_fit_reason",
        "company_contact_status",
        "company_contact_rank",
        "company_contact_count",
        "company_contact_reason",
        "outreach_status",
        "outreach_reason",
    }
    missing_output_fields = required_output_fields.difference(fields)
    if missing_output_fields:
        raise CampaignConfigError(
            "'output.append_fields' is missing required audit fields: "
            + ", ".join(sorted(missing_output_fields))
        )


def load_campaign(path: str | Path) -> CampaignConfig:
    campaign_path = Path(path).expanduser().resolve()
    if not campaign_path.is_file():
        raise CampaignConfigError(f"campaign file not found: {campaign_path}")

    try:
        with campaign_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise CampaignConfigError(f"invalid JSON in {campaign_path}: {exc}") from exc

    validate_campaign_data(data)
    from bulk_enrich.sequence import validate_sequence
    try:
        validate_sequence(data)
    except ValueError as exc:
        raise CampaignConfigError(str(exc)) from exc
    return CampaignConfig(path=campaign_path, data=data)
