"""Cached public-website HTTP fetching with private-network protection."""

from __future__ import annotations

import codecs
import ipaddress
import socket
import threading
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from typing import Any
from urllib.parse import urldefrag, urlsplit, urlunsplit

from bulk_enrich.cache import JsonCache
from bulk_enrich.models import FetchResult


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; BulkEnrich/0.1; deterministic public-site enrichment)"
)


class UnsafeURLError(ValueError):
    """Raised when a URL could reach a local or non-public network."""


def decode_http_body(payload: bytes, content_encoding: str, max_bytes: int) -> bytes:
    encoding = content_encoding.strip().lower()
    if not encoding or encoding == "identity":
        return payload[:max_bytes]
    if encoding in {"gzip", "x-gzip"}:
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decompressor = zlib.decompressobj()
    else:
        raise ValueError(f"unsupported content encoding: {content_encoding}")
    try:
        decoded = decompressor.decompress(payload, max_bytes + 1)
        if len(decoded) <= max_bytes:
            decoded += decompressor.flush(max_bytes + 1 - len(decoded))
    except zlib.error as exc:
        raise ValueError(f"invalid {content_encoding} response body") from exc
    return decoded[:max_bytes]


def normalize_url(url: str) -> str:
    clean, _fragment = urldefrag(url.strip())
    parts = urlsplit(clean)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise UnsafeURLError("only http and https URLs are allowed")
    if not parts.hostname:
        raise UnsafeURLError("URL is missing a hostname")
    host = parts.hostname.lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeURLError("URL hostname is invalid") from exc
    port = parts.port
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def validate_public_url(url: str) -> None:
    parts = urlsplit(normalize_url(url))
    host = parts.hostname or ""
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeURLError("local hostnames are blocked")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise UnsafeURLError("private, loopback, and reserved IP addresses are blocked")
        return

    try:
        addresses = socket.getaddrinfo(
            host,
            parts.port or (443 if parts.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeURLError(f"hostname could not be resolved: {host}") from exc
    if not addresses:
        raise UnsafeURLError(f"hostname could not be resolved: {host}")
    for address in addresses:
        ip_text = address[4][0]
        if not ipaddress.ip_address(ip_text).is_global:
            raise UnsafeURLError("hostname resolves to a non-public IP address")


def resolve_charset(value: str | None) -> str:
    """Return a decodable charset label, falling back to UTF-8 for unknown ones."""
    if not value:
        return "utf-8"
    try:
        codecs.lookup(value)
    except LookupError:
        return "utf-8"
    return value


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class FetcherSettings:
    timeout: float = 12.0
    retries: int = 1
    max_response_bytes: int = 750_000
    cache_ttl_hours: float = 168.0
    negative_cache_ttl_hours: float = 6.0
    refresh_cache: bool = False
    user_agent: str = DEFAULT_USER_AGENT


class HttpFetcher:
    def __init__(self, cache: JsonCache, settings: FetcherSettings) -> None:
        self.cache = cache
        self.settings = settings
        self._opener = urllib.request.build_opener(SafeRedirectHandler())
        self._lock = threading.Lock()
        self._stats = {
            "cache_hits": 0,
            "network_requests": 0,
            "successful_responses": 0,
            "failed_responses": 0,
        }

    def _increment(self, key: str) -> None:
        with self._lock:
            self._stats[key] += 1

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)

    def fetch(self, url: str) -> FetchResult:
        normalized = normalize_url(url)
        if not self.settings.refresh_cache:
            cached = self.cache.get(
                "http-success", normalized, ttl_hours=self.settings.cache_ttl_hours
            )
            if cached is None:
                cached = self.cache.get(
                    "http-failure",
                    normalized,
                    ttl_hours=self.settings.negative_cache_ttl_hours,
                )
            if cached is not None:
                self._increment("cache_hits")
                return FetchResult.from_dict(cached).cached_copy()

        result = self._network_fetch(normalized)
        namespace = "http-success" if result.success else "http-failure"
        self.cache.put(namespace, normalized, result.to_dict())
        return result

    def _network_fetch(self, url: str) -> FetchResult:
        try:
            validate_public_url(url)
        except UnsafeURLError as exc:
            self._increment("failed_responses")
            return self._failure(url, str(exc))

        last_error = "request failed"
        for attempt in range(self.settings.retries + 1):
            self._increment("network_requests")
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                    "Accept-Encoding": "identity",
                },
            )
            try:
                with self._opener.open(request, timeout=self.settings.timeout) as response:
                    final_url = normalize_url(response.geturl())
                    validate_public_url(final_url)
                    status = int(response.getcode() or 0)
                    content_type = response.headers.get_content_type().lower()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        result = self._failure(
                            url,
                            f"unsupported content type: {content_type}",
                            status_code=status,
                            final_url=final_url,
                            content_type=content_type,
                        )
                        self._increment("failed_responses")
                        return result
                    payload = response.read(self.settings.max_response_bytes + 1)
                    if len(payload) > self.settings.max_response_bytes:
                        payload = payload[: self.settings.max_response_bytes]
                    payload = decode_http_body(
                        payload,
                        response.headers.get("Content-Encoding", ""),
                        self.settings.max_response_bytes,
                    )
                    charset = resolve_charset(response.headers.get_content_charset())
                    body = payload.decode(charset, errors="replace")
                    result = FetchResult(
                        url=url,
                        final_url=final_url,
                        status_code=status,
                        content_type=content_type,
                        body=body,
                        fetched_at=datetime.now(UTC).isoformat(),
                    )
                    self._increment("successful_responses")
                    return result
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                    break
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                last_error = str(getattr(exc, "reason", exc))
            except UnsafeURLError as exc:
                last_error = str(exc)
                break
            except ValueError as exc:
                last_error = str(exc)
                break

            if attempt < self.settings.retries:
                time.sleep(0.25 * (attempt + 1))

        self._increment("failed_responses")
        return self._failure(url, last_error)

    @staticmethod
    def _failure(
        url: str,
        error: str,
        *,
        status_code: int = 0,
        final_url: str = "",
        content_type: str = "",
    ) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=final_url or url,
            status_code=status_code,
            content_type=content_type,
            body="",
            fetched_at=datetime.now(UTC).isoformat(),
            error=error,
        )
