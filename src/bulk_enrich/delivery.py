"""Auditable, offline replay and compact sequence delivery."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from bulk_enrich.config import CampaignConfig, CopyAngle, CopyTemplate
from bulk_enrich.csv_io import write_enriched_csv
from bulk_enrich.sequence import (
    FOLLOWUPS,
    SEQUENCE_FIELDS,
    editorial_context,
    render_sequence,
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def research_fingerprint(campaign: CampaignConfig) -> str:
    """Conservative allowlist of presentation edits; all other changes require research."""
    data = copy.deepcopy(campaign.data)
    for field in ("sequence", "email", "output", "status", "notes"):
        data.pop(field, None)
    # Template/subject/CTA/offer-line edits do not change company classification.
    for field in ("angles",):
        data["personalization"].pop(field, None)
    for field in ("cta", "cta_variants", "risk_reversal", "risk_reversal_variants"):
        data["offer"].pop(field, None)
    for field in (
        "max_body_words",
        "max_subject_words",
        "max_cta_words",
        "max_offer_line_words",
        "opening_words",
        "max_opening_share",
        "max_exact_pitch_share",
        "max_buyer_phrase_share",
        "max_cta_share",
        "max_offer_line_share",
        "min_rows",
        "repetition_action",
    ):
        data["quality"].pop(field, None)
    return digest(data)


def capture_state(
    rows, jobs, company_keys, column_map, headers, campaign, focuses, hooks
):
    indices = {id(row): i for i, row in enumerate(rows)}
    saved_jobs = []
    for job in jobs:
        value = asdict(job)
        value.pop("row")
        value["index"] = indices[id(job.row)]
        saved_jobs.append(value)
    return {
        "version": 1,
        "rows": copy.deepcopy(rows),
        "jobs": saved_jobs,
        "company_keys": company_keys,
        "column_map": column_map,
        "headers": headers,
        "research_fingerprint": research_fingerprint(campaign),
        "campaign": campaign.data,
        "focus_path": str(focuses.path),
        "focus_sha256": hashlib.sha256(focuses.path.read_bytes()).hexdigest(),
        "hooks_path": str(hooks.path),
        "hooks_sha256": hashlib.sha256(hooks.path.read_bytes()).hexdigest(),
    }


def sequence_rows(rows, state, campaign):
    contexts = {}
    for job in state["jobs"]:
        row = rows[job["index"]]
        context = editorial_context(campaign, job["context"], job["assignment_key"])
        context.update(
            cta=row.get("personalization_cta", ""),
            risk_reversal=row.get("personalization_offer_line", ""),
        )
        contexts[job["index"]] = context
        if "sequence" not in campaign.data or not row.get("personalized_email"):
            continue
        try:
            row.update(
                render_sequence(
                    campaign, context, job["assignment_key"], row["personalized_email"]
                )
            )
        except (KeyError, ValueError) as exc:
            row.update({field: "" for field in FOLLOWUPS})
            row["sequence_status"] = "review"
            row["sequence_error"] = str(exc)
            if row.get("outreach_status") == "ready":
                row["outreach_status"] = "review"
                row["outreach_reason"] = "sequence: " + str(exc)
    return contexts


def bundle_paths(path: Path) -> dict[str, Path]:
    return {
        name: path.with_name(path.stem + suffix)
        for name, suffix in {
            "held": ".held.csv",
            "disposition": ".disposition.csv",
            "previews": ".previews.md",
            "review_queue": ".copy-review.csv",
            "mapping": ".mapping.md",
            "manifest": ".manifest.json",
        }.items()
    }


def validate_paths(input_path, output_path, options):
    source = Path(input_path).resolve()
    outputs = [
        Path(output_path).resolve(),
        Path(str(Path(output_path).resolve()) + ".render-state.json"),
    ]
    for path in (
        options.ready_output_path,
        options.review_output_path,
        options.manifest_path,
    ):
        if path is not None:
            outputs.append(Path(path).resolve())
    if options.manifest_path is None:
        outputs.append(Path(str(Path(output_path).resolve()) + ".manifest.json"))
    if options.smartlead_output_path is not None:
        smart = Path(options.smartlead_output_path).resolve()
        outputs += [smart, *bundle_paths(smart).values()]
    if (
        source in outputs
        or Path(str(source) + ".render-state.json") in outputs
        or len(set(outputs)) != len(outputs)
    ):
        raise ValueError(
            "all delivery, audit, manifest and source paths must be distinct"
        )


def write_delivery(path, rows, state, campaign, contexts):
    path = Path(path).resolve()
    files = bundle_paths(path)
    compact = []
    held = []
    ledger = []
    review_queue = []
    previews = []
    seen = set()
    seen_companies = set()
    previewed = set()
    combinations = set()
    fields = [
        "email",
        "first_name",
        "company_name",
        "personalized_subject",
        "personalized_email",
    ]
    if "sequence" in campaign.data:
        fields += list(FOLLOWUPS)
    for index, row in enumerate(rows):
        context = contexts.get(index, {})
        email = row.get(state["column_map"]["email"], "").strip().casefold()
        key = state["company_keys"][index]
        item = {field: str(context.get(field, row.get(field, ""))) for field in fields}
        item["email"] = email
        reason = row.get("outreach_reason", "")
        eligible = row.get("outreach_status") == "ready"
        if eligible and (
            "sequence" in campaign.data and row.get("sequence_status") != "ready"
        ):
            eligible = False
            reason = row.get("sequence_error", "") or "sequence not validated"
        if eligible and (
            not all(item.values()) or email in seen or key in seen_companies
        ):
            eligible = False
            reason = "missing delivery fields or duplicate contact/company"
        disposition = {
            "source_row": str(index + 2),
            "email": email,
            "company_key": key,
            "status": "included" if eligible else "held",
            "reason": reason,
            "sequence_error": row.get("sequence_error", ""),
            "evidence_source": row.get("company_fit_source", ""),
            "evidence": row.get("company_fit_evidence", ""),
        }
        ledger.append(disposition)
        if not eligible:
            held.append(disposition)
            continue
        compact.append(item)
        seen.add(email)
        seen_companies.add(key)
        combo = (
            context.get("company_focus", ""),
            context.get("buyer_phrase", ""),
            row.get("personalization_template", ""),
        )
        if combo not in combinations:
            combinations.add(combo)
            review_queue.append(
                {
                    "email": email,
                    "company_focus": combo[0],
                    "buyer_phrase": combo[1],
                    "template": combo[2],
                    **{
                        k: item[k]
                        for k in fields
                        if k.startswith(("personalized_", "followup_"))
                    },
                }
            )
        signature = (
            row.get("personalization_template"),
            bool(combo[0]),
            row.get("company_fit_source"),
        )
        if signature not in previewed:
            previewed.add(signature)
            previews.append(
                "## "
                + item["company_name"]
                + "\n\nSubject: "
                + item["personalized_subject"]
            )
            for field in ["personalized_email", *[k for k in FOLLOWUPS if k in item]]:
                previews.append("### " + field + "\n\n" + item[field])

    def write_csv(dest, records, columns):
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(records)

    write_csv(path, compact, fields)
    ledger_fields = [
        "source_row",
        "email",
        "company_key",
        "status",
        "reason",
        "sequence_error",
        "evidence_source",
        "evidence",
    ]
    write_csv(files["held"], held, ledger_fields)
    write_csv(files["disposition"], ledger, ledger_fields)
    write_csv(
        files["review_queue"],
        review_queue,
        list(review_queue[0])
        if review_queue
        else ["email", "company_focus", "buyer_phrase", "template"],
    )
    files["previews"].write_text(
        "# Complete sequence previews\n\n" + "\n\n".join(previews) + "\n"
    )
    files["mapping"].write_text("""# Smartlead mapping

Map email, first_name and company_name to standard fields; remaining columns
are custom variables with their exact CSV names. Email 1 subject is
{{personalized_subject}} and its entire body is {{personalized_email}}.
For steps 2 and 3 use {{followup_2a}} / {{followup_2b}} and
{{followup_3a}} / {{followup_3b}} as A/B alternatives, not four consecutive sends.
Omit follow-up steps when those columns are absent. Bodies already include the
signature and configured P.S. Do not append duplicates. Verify paragraph breaks
and every step in the platform preview. Set timing and stop-on-reply in Smartlead.
Mark opt-out replies unsubscribed and suppress future outreach to honour the P.S.
This export does not upload, schedule, send or configure unsubscribe handling.

Automated checks passed for included rows. Editorial review is pending: read
copy-review.csv for every distinct service/buyer/template combination and complete
previews. Grammar checks are not proof of relevance or persuasive copy.
""")
    report = {
        "input_rows": len(rows),
        "included": len(compact),
        "held": len(held),
        "automated_checks": "passed",
        "editorial_review": "pending",
        "platform_preview": "not_verified",
        "uploaded": False,
        "sent": False,
        "campaign_snapshot": campaign.data,
        "source_audit_sha256": state.get("audit_sha256"),
        "files": {k: str(v) for k, v in files.items()},
        "upload": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    files["manifest"].write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    return report


def save_state(output_path, state):
    from bulk_enrich.pipeline import _atomic_json, sha256_file

    state["audit_sha256"] = sha256_file(output_path)
    dest = Path(str(output_path) + ".render-state.json")
    _atomic_json(dest, state)
    return str(dest)


def run_render_only(*, input_path, output_path, campaign, options):
    # No CSV discovery, provider preflight, fetcher, classifier or subprocess path.
    from bulk_enrich.pipeline import (
        _RenderJob,
        _build_copy,
        _balanced_ctas_for_rendered_domains,
        _balanced_offer_lines_for_rendered_domains,
        _refresh_outreach_status,
        _deduplicate_emails,
        _apply_batch_quality,
        _sequence_company_contacts,
        _atomic_json,
    )

    validate_paths(input_path, output_path, options)
    source = Path(input_path).resolve()
    output = Path(output_path).resolve()
    state = json.loads(Path(str(source) + ".render-state.json").read_text())
    if (
        state.get("version") != 1
        or state.get("audit_sha256") != hashlib.sha256(source.read_bytes()).hexdigest()
    ):
        raise ValueError(
            "audit changed or unsupported render-state; run enrichment again"
        )
    if state["research_fingerprint"] != research_fingerprint(campaign):
        raise ValueError(
            "targeting or classification inputs changed; run enrichment again"
        )
    for kind in ("focus", "hooks"):
        if (
            hashlib.sha256(Path(state[kind + "_path"]).read_bytes()).hexdigest()
            != state[kind + "_sha256"]
        ):
            raise ValueError(f"{kind} rules changed; run enrichment again")
    rows = copy.deepcopy(state["rows"])
    jobs = []
    for saved in state["jobs"]:
        value = dict(saved)
        index = value.pop("index")
        angle = value.pop("angle")
        if angle:
            angle = CopyAngle(
                angle["angle_id"],
                tuple(angle["signal_types"]),
                tuple(CopyTemplate(**t) for t in angle["templates"]),
            )
        jobs.append(_RenderJob(row=rows[index], angle=angle, **value))
    groups = [(j.assignment_key, j.focus_rule) for j in jobs]
    ctas = _balanced_ctas_for_rendered_domains(campaign.cta_variants, groups)
    offers = _balanced_offer_lines_for_rendered_domains(
        campaign.offer_line_variants, groups
    )
    for job in jobs:
        try:
            rendered = _build_copy(
                campaign,
                job.context,
                job.assignment_key,
                job.signal_type,
                job.source_evidence,
                focus_rule=job.focus_rule,
                angle=job.angle,
                max_pitch_words=job.max_pitch_words,
                cta_variant=ctas[job.assignment_key],
                offer_variant=offers[job.assignment_key],
            )
            job.row.update(
                personalized_subject=rendered.subject,
                personalized_email=rendered.body,
                personalization_template=rendered.template_id,
                personalization_angle=rendered.angle_id,
                personalization_cta=rendered.cta,
                personalization_cta_variant=rendered.cta_variant_id,
                personalization_offer_line=rendered.offer_line,
                personalization_offer_variant=rendered.offer_variant_id,
            )
            job.row[campaign.output_field] = rendered.pitch
        except (KeyError, ValueError) as exc:
            job.row.update(
                personalization_status="error",
                personalization_error=str(exc),
                personalized_email="",
            )
    for row in rows:
        _refresh_outreach_status(row)
    _deduplicate_emails(rows, state["column_map"]["email"], campaign)
    quality = _apply_batch_quality(rows, state["company_keys"], campaign)
    for row in rows:
        _refresh_outreach_status(row)
    _sequence_company_contacts(
        rows,
        state["company_keys"],
        campaign,
        seniority_header=state["column_map"].get("job_seniority", ""),
        title_header=state["column_map"].get("job_title", ""),
    )
    contexts = sequence_rows(rows, state, campaign)
    append = list(campaign.data["output"]["append_fields"])
    if "sequence" in campaign.data:
        append = list(dict.fromkeys([*append, *SEQUENCE_FIELDS]))
    write_enriched_csv(output, state["headers"], rows, append)
    for dest, status in (
        (options.ready_output_path, "ready"),
        (options.review_output_path, "review"),
    ):
        if dest:
            write_enriched_csv(
                dest,
                state["headers"],
                [r for r in rows if r["outreach_status"] == status],
                append,
            )
    replay_state = {**state, "campaign": campaign.data}
    state_path = save_state(output, replay_state)
    report = {
        "mode": "render_only",
        "output": {"path": str(output), "row_count": len(rows)},
        "render_state": state_path,
        "quality": quality,
        "source_audit": str(source),
        "qualification": dict(Counter(r["outreach_status"] for r in rows)),
        "llm_focus": {"calls": 0, "nominal_cost_usd": 0},
        "http": {"requests": 0},
        "editorial_review": "pending",
        "platform_preview": "not_verified",
    }
    if options.smartlead_output_path:
        report["delivery"] = write_delivery(
            options.smartlead_output_path, rows, replay_state, campaign, contexts
        )
        report["delivery"]["llm_focus"] = report["llm_focus"]
        _atomic_json(Path(report["delivery"]["files"]["manifest"]), report["delivery"])
    _atomic_json(options.manifest_path or Path(str(output) + ".manifest.json"), report)
    return report
