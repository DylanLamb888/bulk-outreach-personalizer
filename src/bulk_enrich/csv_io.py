"""CSV inspection, header mapping, and domain planning."""

from __future__ import annotations

import csv
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "first_name": ("first_name", "first name", "firstname"),
    "last_name": ("last_name", "last name", "lastname"),
    "email": ("email", "email_address", "email address", "work_email"),
    "email_status": ("email_status", "email status", "email verification status"),
    "job_title": ("job_title", "job title", "title"),
    "job_seniority": ("job_seniority", "job seniority", "seniority"),
    "job_department": ("job_department", "job department", "department"),
    "company_name": ("company_name", "company name", "organization", "organisation"),
    "company_domain": ("company_domain", "company domain", "domain"),
    "company_website": ("company_website", "company website", "website", "company url"),
}


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def detect_columns(headers: list[str]) -> dict[str, str]:
    normalized = {normalize_header(header): header for header in headers}
    mapping: dict[str, str] = {}
    for canonical, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            actual = normalized.get(normalize_header(alias))
            if actual is not None:
                mapping[canonical] = actual
                break
    return mapping


def canonicalize_domain(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def domain_for_row(row: dict[str, str], mapping: dict[str, str]) -> str:
    for canonical in ("company_domain", "company_website"):
        header = mapping.get(canonical)
        if header:
            domain = canonicalize_domain(row.get(header, ""))
            if domain:
                return domain
    return ""


@dataclass(frozen=True)
class CSVSummary:
    path: str
    row_count: int
    headers: list[str]
    column_map: dict[str, str]
    missing_required: dict[str, int]
    domains_present: int
    unique_domain_count: int
    duplicate_domain_rows: int
    unique_email_count: int
    duplicate_email_rows: int
    email_status_present: bool
    missing_email_status_values: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CSVData:
    path: Path
    headers: list[str]
    rows: list[dict[str, str]]
    column_map: dict[str, str]


def load_csv(path: str | Path) -> CSVData:
    csv_path = Path(path).expanduser().resolve()
    if not csv_path.is_file():
        raise FileNotFoundError(f"input CSV not found: {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        if not headers:
            raise ValueError("input CSV has no header row")
        rows = [
            {header: str(row.get(header) or "") for header in headers}
            for row in reader
        ]

    mapping = detect_columns(headers)
    required = ("email", "first_name", "company_name")
    missing_headers = [field for field in required if field not in mapping]
    if not ("company_domain" in mapping or "company_website" in mapping):
        missing_headers.append("company_domain or company_website")
    if missing_headers:
        raise ValueError("input CSV is missing required columns: " + ", ".join(missing_headers))

    return CSVData(path=csv_path, headers=headers, rows=rows, column_map=mapping)


def summarize_csv(data: CSVData) -> CSVSummary:
    rows = data.rows
    mapping = data.column_map

    missing_by_field = {
        canonical: sum(not (row.get(actual) or "").strip() for row in rows)
        for canonical, actual in mapping.items()
    }
    domains = [domain_for_row(row, mapping) for row in rows]
    present_domains = [domain for domain in domains if domain]
    email_header = mapping["email"]
    emails = [
        row.get(email_header, "").strip().casefold()
        for row in rows
        if row.get(email_header, "").strip()
    ]
    email_status_header = mapping.get("email_status", "")

    return CSVSummary(
        path=str(data.path),
        row_count=len(rows),
        headers=data.headers,
        column_map=mapping,
        missing_required=missing_by_field,
        domains_present=len(present_domains),
        unique_domain_count=len(set(present_domains)),
        duplicate_domain_rows=len(present_domains) - len(set(present_domains)),
        unique_email_count=len(set(emails)),
        duplicate_email_rows=len(emails) - len(set(emails)),
        email_status_present=bool(email_status_header),
        missing_email_status_values=(
            sum(not row.get(email_status_header, "").strip() for row in rows)
            if email_status_header
            else len(rows)
        ),
    )


def inspect_csv(path: str | Path) -> CSVSummary:
    return summarize_csv(load_csv(path))


def context_for_row(row: dict[str, str], mapping: dict[str, str]) -> dict[str, Any]:
    context: dict[str, Any] = {
        normalize_header(header): value for header, value in row.items()
    }
    for canonical, actual in mapping.items():
        context[canonical] = row.get(actual, "")
    return context


def write_enriched_csv(
    output_path: str | Path,
    headers: list[str],
    rows: list[dict[str, str]],
    append_fields: list[str],
) -> Path:
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(headers)
    fieldnames.extend(field for field in append_fields if field not in fieldnames)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return destination
