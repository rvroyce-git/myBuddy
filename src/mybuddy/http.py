"""Small urllib-based HTTP client. https only, bounded time and size."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPMessage
from typing import IO, Any

USER_AGENT = "MyBuddy/0.1 (+personal news reader)"
TIMEOUT_SECONDS = 20.0
MAX_BYTES = 5 * 1024 * 1024


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


def _require_https(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise HttpError(f"refusing non-https URL: {_safe_url(url)}", url=_safe_url(url))


# "/bot<id>:<secret>" anywhere; on api.telegram.org any "/bot..." segment (even malformed).
_BOT_TOKEN_SEGMENT = re.compile(r"/bot\d+:[^/]*")
_TELEGRAM_BOT_SEGMENT = re.compile(r"/bot[^/]*")


def _safe_url(url: str) -> str:
    """Drop query string and path tail so secrets (e.g. webhook tokens) never hit logs."""
    parts = urllib.parse.urlsplit(url)
    # Telegram puts the bot token in the path (/bot<token>/method): drop it entirely.
    pattern = _TELEGRAM_BOT_SEGMENT if parts.hostname == "api.telegram.org" else _BOT_TOKEN_SEGMENT
    path = pattern.sub("/bot<redacted>", parts.path)
    if len(path) > 40:
        path = path[:40] + "…"
    return f"{parts.scheme}://{parts.hostname or ''}{path}"


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


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
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibHttpClient:
    """Implements models.HttpClient with the standard library."""

    def __init__(
        self,
        *,
        timeout: float = TIMEOUT_SECONDS,
        max_bytes: int = MAX_BYTES,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self._opener = urllib.request.build_opener(_HttpsOnlyRedirect())

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
        req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        safe = _safe_url(url)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                _require_https(resp.geturl())
                body: bytes = resp.read(self.max_bytes + 1)
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read(4096).decode("utf-8", "replace")
            except Exception:
                text = ""
            finally:
                exc.close()
            raise HttpError(
                f"HTTP {exc.code} from {safe}",
                url=safe,
                status=exc.code,
                body=text,
                retry_after=_parse_retry_after(exc.headers.get("Retry-After")),
            ) from None
        except HttpError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise HttpError(f"request to {safe} failed: {reason}", url=safe) from None
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
    except ValueError as exc:
        raise HttpError(f"invalid JSON from {_safe_url(url)}: {exc}", url=_safe_url(url)) from None
