"""Campaign configuration loading and validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class RowFallbackField:
    header: str
    confidence: float


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
    def max_candidate_confidence_drop(self) -> float:
        """Maximum evidence-confidence loss allowed when choosing a later fact."""
        return float(
            self.data["personalization"]["max_candidate_confidence_drop"]
        )

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
                )
                for item in configured
            )
        return (CtaVariant(variant_id="default-cta", text=str(self.data["offer"]["cta"])),)

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


def validate_campaign_data(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise CampaignConfigError("campaign configuration must be a JSON object")

    if data.get("schema_version") != "2.0":
        raise CampaignConfigError("'schema_version' must be '2.0'")

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
        cta_copy.append((f"{label}.text", cta_text))
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
    max_candidate_confidence_drop = personalization.get(
        "max_candidate_confidence_drop"
    )
    if (
        not isinstance(max_candidate_confidence_drop, (int, float))
        or isinstance(max_candidate_confidence_drop, bool)
        or not 0 <= float(max_candidate_confidence_drop) <= 1
    ):
        raise CampaignConfigError(
            "'personalization.max_candidate_confidence_drop' must be a number "
            "from 0 to 1"
        )
    banned_phrases = _require_string_list(
        personalization,
        "banned_phrases",
        allow_empty=True,
        label="personalization.banned_phrases",
    )

    row_fallback = personalization.get("row_fallback")
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
        seen_headers: set[str] = set()
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
        "max_buyer_phrase_share",
        "max_cta_share",
    ):
        value = quality.get(key)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0 < float(value) <= 1
        ):
            raise CampaignConfigError(f"'quality.{key}' must be greater than 0 and at most 1")
    if quality.get("repetition_action") not in {"warn", "review"}:
        raise CampaignConfigError(
            "'quality.repetition_action' must be 'warn' or 'review'"
        )

    configured_copy = [
        ("offer.service", str(offer["service"])),
        ("offer.risk_reversal", str(offer["risk_reversal"])),
        ("offer.cta", str(offer["cta"])),
        *cta_copy,
        ("email.body", body),
        *copy_values,
    ]
    for label, value in configured_copy:
        banned = _contains_banned_phrase(value, banned_phrases)
        if banned:
            raise CampaignConfigError(
                f"'{label}' contains banned phrase '{banned}'"
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
        "personalization_facts",
        "personalization_source",
        "personalization_evidence",
        "personalization_confidence",
        "personalization_quality_flags",
        "personalization_status",
        "personalization_error",
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
    return CampaignConfig(path=campaign_path, data=data)
