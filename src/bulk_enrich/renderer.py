"""Strict merge-field rendering that leaves single-brace spintax untouched."""

from __future__ import annotations

import re
from typing import Any

from bulk_enrich.config import CampaignConfig


_MERGE_FIELD_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render_merge_fields(template: str, context: dict[str, Any]) -> str:
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            missing.add(key)
            return match.group(0)
        value = context[key]
        return "" if value is None else str(value)

    rendered = _MERGE_FIELD_RE.sub(replace, template)
    if missing:
        raise KeyError("missing merge fields: " + ", ".join(sorted(missing)))
    return rendered


def render_template(template: str, context: dict[str, Any]) -> str:
    """Render double-brace merge fields while preserving single-brace spintax."""
    return render_merge_fields(template, context)


def render_email(
    campaign: CampaignConfig,
    row_context: dict[str, Any],
    personalized_pitch: str,
    subject_template: str,
) -> tuple[str, str]:
    data = campaign.data
    context = dict(row_context)
    context.update(
        {
            campaign.output_field: personalized_pitch,
            "risk_reversal": row_context.get(
                "risk_reversal", data["offer"].get("risk_reversal", "")
            ),
            "cta": row_context.get("cta", data["offer"].get("cta", "")),
            "sender_name": data["sender"]["name"],
        }
    )
    subject = render_merge_fields(subject_template, context).strip()
    body = render_merge_fields(data["email"]["body"], context).strip()
    body = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", body)
    return subject, body


def word_count(value: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", value))
