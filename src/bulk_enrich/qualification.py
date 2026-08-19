"""Deterministic company, contact, and email qualification gates."""

from __future__ import annotations

import re
from dataclasses import dataclass

from bulk_enrich.config import CampaignConfig
from bulk_enrich.focus import CommercialFocusResult
from bulk_enrich.models import CompanyFact


_EMAIL_RE = re.compile(
    r"(?=.{3,254}\Z)[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}\Z"
)


@dataclass(frozen=True)
class QualificationResult:
    status: str
    rule: str
    reason: str


def normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def valid_email_syntax(value: str) -> bool:
    email = normalize_email(value)
    if not _EMAIL_RE.fullmatch(email):
        return False
    local, _domain = email.rsplit("@", 1)
    return not (
        local.startswith(".")
        or local.endswith(".")
        or ".." in local
    )


def qualify_company(
    fact: CompanyFact | None,
    focus: CommercialFocusResult | None,
    *,
    corroborating_fields: tuple[str, ...] = (),
    fallback_min_agreeing_fields: int = 2,
    min_confidence: float = 0.0,
) -> QualificationResult:
    if fact is None or focus is None:
        return QualificationResult(
            status="excluded",
            rule="no-company-evidence",
            reason="no campaign-mapped company evidence was found",
        )
    if focus.fit_tier == "exclude" or focus.rule_id == "generic-compression":
        return QualificationResult(
            status="excluded",
            rule=focus.rule_id,
            reason="company evidence does not match the campaign target",
        )
    if fact.source_url.startswith("input:"):
        if len(corroborating_fields) < fallback_min_agreeing_fields:
            return QualificationResult(
                status="excluded",
                rule=focus.rule_id,
                reason=(
                    "CSV fallback was not corroborated by "
                    f"{fallback_min_agreeing_fields} approved fields"
                ),
            )
        return QualificationResult(
            status="review",
            rule=focus.rule_id,
            reason=(
                "company fit is supported only by CSV fields: "
                + ", ".join(corroborating_fields)
            ),
        )
    if fact.confidence < min_confidence:
        return QualificationResult(
            status="review",
            rule=focus.rule_id,
            reason=(
                f"first-party evidence confidence {fact.confidence:.2f} is below "
                f"{min_confidence:.2f}"
            ),
        )
    if focus.fit_tier == "secondary":
        return QualificationResult(
            status="review",
            rule=focus.rule_id,
            reason="first-party evidence matches a secondary campaign segment",
        )
    return QualificationResult(
        status="qualified",
        rule=focus.rule_id,
        reason="first-party evidence matches a core campaign segment",
    )


def _first_pattern(value: str, patterns: tuple[str, ...]) -> tuple[int, str] | None:
    for index, pattern in enumerate(patterns):
        if re.search(pattern, value, re.I):
            return index, pattern
    return None


def qualify_contact(
    *,
    first_name: str,
    job_title: str,
    job_seniority: str,
    campaign: CampaignConfig,
) -> QualificationResult:
    if not first_name.strip():
        return QualificationResult(
            status="excluded",
            rule="missing-first-name",
            reason="first name is missing",
        )
    title = job_title.strip()
    if not title:
        return QualificationResult(
            status="excluded",
            rule="missing-title",
            reason="job title is missing",
        )

    excluded = _first_pattern(title, campaign.excluded_title_patterns)
    if excluded is not None:
        return QualificationResult(
            status="excluded",
            rule=f"exclude-title-{excluded[0] + 1}",
            reason="job title matches a campaign exclusion",
        )

    ready = _first_pattern(title, campaign.ready_title_patterns)
    review = _first_pattern(title, campaign.review_title_patterns)
    if ready is None and review is None:
        return QualificationResult(
            status="excluded",
            rule="title-not-targeted",
            reason="job title is outside the campaign's approved contact rules",
        )

    seniority = normalize_label(job_seniority)
    ready_seniorities = {normalize_label(item) for item in campaign.ready_seniorities}
    review_seniorities = {normalize_label(item) for item in campaign.review_seniorities}
    if seniority:
        if seniority not in ready_seniorities | review_seniorities:
            return QualificationResult(
                status="excluded",
                rule="seniority-not-targeted",
                reason="supplied seniority is outside the campaign's approved levels",
            )
        if seniority in review_seniorities:
            return QualificationResult(
                status="review",
                rule=(
                    f"ready-title-{ready[0] + 1}"
                    if ready is not None
                    else f"review-title-{review[0] + 1}"
                ),
                reason="title matches but supplied seniority requires review",
            )

    if review is not None and ready is None:
        return QualificationResult(
            status="review",
            rule=f"review-title-{review[0] + 1}",
            reason="job title matches a review-only contact rule",
        )
    assert ready is not None
    return QualificationResult(
        status="qualified",
        rule=f"ready-title-{ready[0] + 1}",
        reason="job title matches an approved campaign contact rule",
    )


def qualify_email(
    email: str,
    email_status: str,
    campaign: CampaignConfig,
) -> QualificationResult:
    if not email.strip():
        return QualificationResult(
            status="excluded",
            rule="missing-email",
            reason="email is missing",
        )
    if not valid_email_syntax(email):
        return QualificationResult(
            status="excluded",
            rule="invalid-email-syntax",
            reason="email does not pass conservative syntax validation",
        )

    status = normalize_label(email_status)
    if not status:
        action = campaign.missing_email_status_action
        if action == "syntax":
            return QualificationResult(
                status="qualified",
                rule="syntax-only",
                reason="verification status is missing; syntax validation passed",
            )
        if action == "review":
            return QualificationResult(
                status="review",
                rule="missing-verification-status",
                reason="email verification status is missing",
            )
        return QualificationResult(
            status="excluded",
            rule="missing-verification-status",
            reason="email verification status is required by the campaign",
        )

    accepted = {normalize_label(item) for item in campaign.accepted_email_statuses}
    review = {normalize_label(item) for item in campaign.review_email_statuses}
    rejected = {normalize_label(item) for item in campaign.rejected_email_statuses}
    if status in accepted:
        return QualificationResult(
            status="qualified",
            rule=f"email-status-{status.replace(' ', '-')}",
            reason="email provider status is accepted by the campaign",
        )
    if status in review:
        return QualificationResult(
            status="review",
            rule=f"email-status-{status.replace(' ', '-')}",
            reason="email provider status requires review",
        )
    if status in rejected:
        return QualificationResult(
            status="excluded",
            rule=f"email-status-{status.replace(' ', '-')}",
            reason="email provider status is rejected by the campaign",
        )
    return QualificationResult(
        status="review",
        rule="unmapped-email-status",
        reason=f"email provider status '{email_status.strip()}' is not mapped",
    )


def compose_outreach_status(
    company: QualificationResult,
    contact: QualificationResult,
    email: QualificationResult,
    personalization_status: str,
) -> tuple[str, str]:
    gates = (("company", company), ("contact", contact), ("email", email))
    excluded = [f"{name}: {result.reason}" for name, result in gates if result.status == "excluded"]
    if excluded:
        return "excluded", "; ".join(excluded)
    if personalization_status == "error":
        return "error", "personalisation failed"
    review = [f"{name}: {result.reason}" for name, result in gates if result.status == "review"]
    if personalization_status != "ready":
        review.append(f"personalisation status is {personalization_status}")
    if review:
        return "review", "; ".join(review)
    return "ready", "all qualification and personalisation gates passed"
