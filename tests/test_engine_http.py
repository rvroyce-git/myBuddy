"""UrllibHttpClient behaviour, with the network replaced by a fake opener."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from typing import Any

import pytest

from mybuddy.http import USER_AGENT, HttpError, UrllibHttpClient, _HttpsOnlyRedirect


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str) -> None:
        super().__init__(body)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> FakeResponse:
        return self


class FakeOpener:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.requests: list[Any] = []

    def open(self, req: Any, timeout: float) -> Any:
        self.requests.append((req, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def client(result: Any, **kw: Any) -> tuple[UrllibHttpClient, FakeOpener]:
    c = UrllibHttpClient(**kw)
    opener = FakeOpener(result)
    c._opener = opener  # type: ignore[assignment]
    return c, opener


def test_get_json_sets_headers_and_timeout() -> None:
    c, opener = client(FakeResponse(b'{"ok": 1}', "https://api.example/x"))
    assert c.get_json("https://api.example/x", headers={"Authorization": "Bearer t"}) == {"ok": 1}
    req, timeout = opener.requests[0]
    assert timeout == 20.0
    assert req.get_header("User-agent") == USER_AGENT
    assert req.get_header("Authorization") == "Bearer t"


def test_post_json_body_and_empty_reply() -> None:
    c, opener = client(FakeResponse(b"", "https://hook.example/"))
    assert c.post_json("https://hook.example/", {"a": 1}) is None
    req, _ = opener.requests[0]
    assert json.loads(req.data) == {"a": 1} and req.get_method() == "POST"


def test_rejects_http_and_redirect_to_http() -> None:
    c, _ = client(FakeResponse(b"", "https://x"))
    with pytest.raises(HttpError, match="non-https"):
        c.get_bytes("http://insecure.example/")
    c, _ = client(FakeResponse(b"x", "http://downgraded.example/"))
    with pytest.raises(HttpError, match="non-https"):
        c.get_bytes("https://ok.example/")
    with pytest.raises(HttpError, match="non-https"):
        _HttpsOnlyRedirect().redirect_request(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            302,
            "Found",
            Message(),  # type: ignore[arg-type]
            "http://evil.example/",
        )


def test_size_cap() -> None:
    c, _ = client(FakeResponse(b"x" * 11, "https://big.example/"), max_bytes=10)
    with pytest.raises(HttpError, match="exceeds"):
        c.get_bytes("https://big.example/")


def test_http_error_carries_status_and_retry_after() -> None:
    headers = Message()
    headers["Retry-After"] = "4"
    err = urllib.error.HTTPError(
        "https://api.example/secret-token-path", 429, "Too Many", headers, io.BytesIO(b"slow")
    )
    c, _ = client(err)
    with pytest.raises(HttpError) as info:
        c.get_bytes("https://api.example/secret?token=abc")
    assert info.value.status == 429 and info.value.retry_after == 4.0
    assert info.value.body == "slow" and "token=abc" not in str(info.value)


def test_network_error_and_bad_json() -> None:
    c, _ = client(urllib.error.URLError("no route"))
    with pytest.raises(HttpError, match="no route") as info:
        c.get_bytes("https://down.example/")
    assert info.value.status is None
    c, _ = client(FakeResponse(b"not json", "https://api.example/"))
    with pytest.raises(HttpError, match="invalid JSON"):
        c.get_json("https://api.example/")
