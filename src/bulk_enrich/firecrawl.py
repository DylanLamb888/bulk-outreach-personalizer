"""Optional cached Firecrawl page-fetch adapter.

The adapter deliberately uses Firecrawl only as a page-level fallback. Company
URLs are still checked by the same public-network guard used by the fast HTTP
fetcher, while the configured Firecrawl API may point at a local self-hosted
instance.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bulk_enrich.cache import JsonCache
from bulk_enrich.fetcher import UnsafeURLError, normalize_url, validate_public_url
from bulk_enrich.models import FetchResult


DEFAULT_FIRECRAWL_API_URL = "https://api.firecrawl.dev"
FIRECRAWL_ADAPTER_VERSION = "2"


def normalize_api_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Firecrawl API URL must be an http or https URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Firecrawl API URL must not contain credentials, query, or fragment")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def scrape_endpoint(api_url: str) -> str:
    normalized = normalize_api_url(api_url)
    if normalized.endswith("/v1") or normalized.endswith("/v2"):
        return f"{normalized}/scrape"
    return f"{normalized}/v2/scrape"


def hosted_api_requires_key(api_url: str) -> bool:
    return (urlsplit(normalize_api_url(api_url)).hostname or "").casefold() == (
        "api.firecrawl.dev"
    )


@dataclass(frozen=True)
class FirecrawlSettings:
    api_url: str = DEFAULT_FIRECRAWL_API_URL
    api_key: str = ""
    timeout: float = 30.0
    cache_ttl_hours: float = 168.0
    negative_cache_ttl_hours: float = 6.0
    refresh_cache: bool = False
    max_response_bytes: int = 4_000_000
    max_concurrency: int = 4

    def validate(self) -> None:
        normalize_api_url(self.api_url)
        if self.timeout <= 0:
            raise ValueError("Firecrawl timeout must be greater than 0")
        if self.max_response_bytes < 1:
            raise ValueError("Firecrawl maximum response bytes must be at least 1")
        if self.max_concurrency < 1:
            raise ValueError("Firecrawl concurrency must be at least 1")
        if hosted_api_requires_key(self.api_url) and not self.api_key.strip():
            raise ValueError(
                "FIRECRAWL_API_KEY is required when using hosted Firecrawl"
            )


class FirecrawlFetcher:
    """Fetch cleaned HTML through Firecrawl with an independent local cache."""

    def __init__(
        self,
        cache: JsonCache,
        settings: FirecrawlSettings,
        *,
        opener: Any | None = None,
    ) -> None:
        settings.validate()
        self.cache = cache
        self.settings = settings
        self.api_url = normalize_api_url(settings.api_url)
        self.endpoint = scrape_endpoint(self.api_url)
        self._opener = opener or urllib.request.build_opener()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(settings.max_concurrency)
        self._stats = {
            "cache_hits": 0,
            "requests": 0,
            "successful_responses": 0,
            "failed_responses": 0,
        }

    def _increment(self, key: str) -> None:
        with self._lock:
            self._stats[key] += 1

    def stats(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": True,
                "api_url": self.api_url,
                "max_concurrency": self.settings.max_concurrency,
                **self._stats,
            }

    def fetch(self, url: str) -> FetchResult:
        normalized = normalize_url(url)
        try:
            validate_public_url(normalized)
        except UnsafeURLError as exc:
            self._increment("failed_responses")
            return self._failure(normalized, str(exc))

        cache_key = (
            f"{FIRECRAWL_ADAPTER_VERSION}|{self.api_url}|html-with-links|{normalized}"
        )
        if not self.settings.refresh_cache:
            cached = self.cache.get(
                "firecrawl-success",
                cache_key,
                ttl_hours=self.settings.cache_ttl_hours,
            )
            if cached is None:
                cached = self.cache.get(
                    "firecrawl-failure",
                    cache_key,
                    ttl_hours=self.settings.negative_cache_ttl_hours,
                )
            if cached is not None:
                self._increment("cache_hits")
                return FetchResult.from_dict(cached).cached_copy()

        result = self._network_fetch(normalized)
        namespace = "firecrawl-success" if result.success else "firecrawl-failure"
        self.cache.put(namespace, cache_key, result.to_dict())
        return result

    def _network_fetch(self, url: str) -> FetchResult:
        with self._slots:
            return self._perform_network_fetch(url)

    def _perform_network_fetch(self, url: str) -> FetchResult:
        self._increment("requests")
        payload = json.dumps(
            {
                "url": url,
                "formats": ["html"],
                # Company navigation is useful for finding About/Services pages.
                # The downstream parser already excludes nav/footer copy as evidence.
                "onlyMainContent": False,
                "timeout": int(self.settings.timeout * 1000),
                "removeBase64Images": True,
                "blockAds": True,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "BulkEnrich/0.8 Firecrawl fallback",
        }
        if self.settings.api_key.strip():
            headers["Authorization"] = f"Bearer {self.settings.api_key.strip()}"
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers=headers,
            method="POST",
        )

        try:
            with self._opener.open(request, timeout=self.settings.timeout) as response:
                raw = response.read(self.settings.max_response_bytes + 1)
                if len(raw) > self.settings.max_response_bytes:
                    raise ValueError("Firecrawl response exceeded the configured size limit")
                decoded = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = self._http_error_detail(exc)
            self._increment("failed_responses")
            return self._failure(url, f"Firecrawl HTTP {exc.code}{detail}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._increment("failed_responses")
            return self._failure(
                url,
                f"Firecrawl request failed: {getattr(exc, 'reason', exc)}",
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._increment("failed_responses")
            return self._failure(url, f"invalid Firecrawl response: {exc}")

        if not isinstance(decoded, dict) or decoded.get("success") is False:
            error = self._response_error(decoded)
            self._increment("failed_responses")
            return self._failure(url, f"Firecrawl scrape failed: {error}")
        data = decoded.get("data")
        if not isinstance(data, dict):
            self._increment("failed_responses")
            return self._failure(url, "Firecrawl scrape failed: response data is missing")
        html = data.get("html")
        if not isinstance(html, str) or not html.strip():
            self._increment("failed_responses")
            return self._failure(url, "Firecrawl scrape failed: cleaned HTML is empty")

        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        final_url = str(
            metadata.get("sourceURL")
            or metadata.get("sourceUrl")
            or metadata.get("url")
            or url
        )
        try:
            final_url = normalize_url(final_url)
            validate_public_url(final_url)
        except UnsafeURLError as exc:
            self._increment("failed_responses")
            return self._failure(url, f"unsafe Firecrawl result URL: {exc}")

        status_code = metadata.get("statusCode", metadata.get("status_code", 200))
        try:
            status_code = int(status_code)
        except (TypeError, ValueError):
            status_code = 200
        result = FetchResult(
            url=url,
            final_url=final_url,
            status_code=status_code if 200 <= status_code < 300 else 200,
            content_type="text/html",
            body=html,
            fetched_at=datetime.now(UTC).isoformat(),
            provider="firecrawl",
        )
        self._increment("successful_responses")
        return result

    @staticmethod
    def _response_error(payload: object) -> str:
        if isinstance(payload, dict):
            for key in ("error", "message", "detail"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return " ".join(value.strip().split())[:300]
        return "unknown error"

    @classmethod
    def _http_error_detail(cls, error: urllib.error.HTTPError) -> str:
        try:
            raw = error.read(64_000)
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return ""
        detail = cls._response_error(payload)
        return f": {detail}" if detail != "unknown error" else ""

    @staticmethod
    def _failure(url: str, error: str) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            content_type="",
            body="",
            fetched_at=datetime.now(UTC).isoformat(),
            error=error,
            provider="firecrawl",
        )
