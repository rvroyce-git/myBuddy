"""Small urllib-based HTTP client. https only, bounded time and size."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.client import HTTPMessage
from typing import IO, Any

USER_AGENT = "MyBuddy/0.1 (+personal news reader)"
TIMEOUT_SECONDS = 20.0
# The socket timeout above applies to each read; this caps a whole slow-drip response.
TOTAL_TIMEOUT_FACTOR = 3.0
MAX_BYTES = 5 * 1024 * 1024
_READ_CHUNK = 64 * 1024
# Headers that must never follow a redirect to a different host.
_CREDENTIAL_HEADERS = ("Authorization", "Cookie", "Proxy-authorization")


class HttpError(Exception):
    """A failed request. `status` is the HTTP status code, or None for network errors."""

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        status: int | None = None,
        body: str = "",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status = status
        self.body = body
        self.retry_after = retry_after


_BAD_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")


def _is_internal_host(host: str) -> bool:
    """True for localhost and IP literals that point inside the machine or private network."""
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(host)
    except ValueError:
        try:  # shorthand IPv4 forms the resolver also accepts: "127.1", "2130706433", "0x7f.1"
            ip = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return False
    return not ip.is_global


def _require_https(url: str) -> None:
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        raise HttpError(f"refusing malformed URL: {_safe_url(url)}", url=_safe_url(url)) from None
    if parts.scheme != "https" or not host:
        raise HttpError(f"refusing non-https URL: {_safe_url(url)}", url=_safe_url(url))
    if _BAD_URL_CHARS.search(url):
        raise HttpError(
            f"refusing URL with spaces or control characters: {_safe_url(url)}",
            url=_safe_url(url),
        )
    if _is_internal_host(host):
        raise HttpError(f"refusing internal address: {_safe_url(url)}", url=_safe_url(url))


# "/bot<id>:<secret>" anywhere; on api.telegram.org any "/bot..." segment (even malformed).
_BOT_TOKEN_SEGMENT = re.compile(r"/bot\d+:[^/]*")
_TELEGRAM_BOT_SEGMENT = re.compile(r"/bot[^/]*")
# Discord webhook URLs carry their secret token in the path: /api/webhooks/<id>/<token>.
_WEBHOOK_SEGMENT = re.compile(r"/webhooks/.*")


def _safe_url(url: str) -> str:
    """Drop query string and path tail so secrets (e.g. webhook tokens) never hit logs."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return "<malformed URL>"
    # Telegram puts the bot token in the path (/bot<token>/method): drop it entirely.
    pattern = _TELEGRAM_BOT_SEGMENT if host == "api.telegram.org" else _BOT_TOKEN_SEGMENT
    path = pattern.sub("/bot<redacted>", parts.path)
    path = _WEBHOOK_SEGMENT.sub("/webhooks/<redacted>", path)
    path = _BAD_URL_CHARS.sub("?", path)
    if len(path) > 40:
        path = path[:40] + "…"
    return f"{parts.scheme}://{_BAD_URL_CHARS.sub('?', host)}{path}"


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return max(0.0, seconds) if seconds == seconds else None  # NaN -> None


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        _require_https(newurl)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and _host(newurl) != _host(req.full_url):
            # Never hand API keys or cookies to another host.
            for name in _CREDENTIAL_HEADERS:
                new.remove_header(name)
        return new


def _host(url: str) -> str | None:
    try:
        return urllib.parse.urlsplit(url).hostname
    except ValueError:
        return None


class UrllibHttpClient:
    """Implements models.HttpClient with the standard library."""

    def __init__(
        self,
        *,
        timeout: float = TIMEOUT_SECONDS,
        max_bytes: int = MAX_BYTES,
        user_agent: str = USER_AGENT,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self.monotonic = monotonic
        self._opener = urllib.request.build_opener(_HttpsOnlyRedirect())

    def _read(self, resp: IO[bytes], safe: str) -> bytes:
        """Read at most max_bytes + 1 bytes, giving up when the whole read takes too long."""
        deadline = self.monotonic() + self.timeout * TOTAL_TIMEOUT_FACTOR
        chunks: list[bytes] = []
        total = 0
        while total <= self.max_bytes:
            chunk = resp.read(min(_READ_CHUNK, self.max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if self.monotonic() > deadline:
                raise HttpError(f"response from {safe} took too long", url=safe)
        return b"".join(chunks)

    def request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        _require_https(url)
        all_headers = {"User-Agent": self.user_agent}
        all_headers.update(headers or {})
        safe = _safe_url(url)
        try:
            req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
            with self._opener.open(req, timeout=self.timeout) as resp:
                _require_https(resp.geturl())
                body = self._read(resp, safe)
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read(4096).decode("utf-8", "replace")
            except Exception:
                text = ""
            finally:
                exc.close()
            retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
            raise HttpError(
                f"HTTP {exc.code} from {safe}",
                url=safe,
                status=exc.code,
                body=text,
                retry_after=_parse_retry_after(retry_after),
            ) from None
        except HttpError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise HttpError(f"request to {safe} failed: {reason}", url=safe) from None
        except (http.client.HTTPException, ValueError) as exc:
            # These messages can quote the full URL (and so a token): report only the type.
            raise HttpError(f"request to {safe} failed: {type(exc).__name__}", url=safe) from None
        if len(body) > self.max_bytes:
            raise HttpError(f"response from {safe} exceeds {self.max_bytes} bytes", url=safe)
        return body

    def get_bytes(self, url: str, *, accept: str | None = None) -> bytes:
        return self.request("GET", url, headers={"Accept": accept} if accept else None)

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        merged = {"Accept": "application/json"}
        merged.update(headers or {})
        return _decode_json(self.request("GET", url, headers=merged), url)

    def post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None) -> Any:
        merged = {"Content-Type": "application/json", "Accept": "application/json"}
        merged.update(headers or {})
        data = json.dumps(payload).encode("utf-8")
        body = self.request("POST", url, data=data, headers=merged)
        if not body.strip():
            return None
        return _decode_json(body, url)


def _decode_json(body: bytes, url: str) -> Any:
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as exc:
        detail = type(exc).__name__ if isinstance(exc, RecursionError) else str(exc)[:200]
        raise HttpError(
            f"invalid JSON from {_safe_url(url)}: {detail}", url=_safe_url(url)
        ) from None
