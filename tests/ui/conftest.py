"""Fixtures for the website (site/) browser tests.

Each test gets its own copy of site/ in a temp dir, served by a threaded http.server on a free
port under /myBuddy/ (the GitHub Pages project path). Unknown paths get 404.html with a 404,
like GitHub Pages. Requests to anything but that server are answered locally with an empty body,
so the tests never touch the network and prove the page works without web fonts.

These tests need Playwright (`pip install -e '.[ui]'` + `python -m playwright install chromium`).
Without it every test module skips itself via pytest.importorskip.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import threading
from collections.abc import Callable, Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from ui_support import VISITOR_TZ, make_reports

SITE_DIR = Path(__file__).resolve().parents[2] / "site"
PREFIX = "myBuddy"
HOST = "127.0.0.1"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "ui: browser tests for the static website (needs playwright)"
    )


# ---------------------------------------------------------------- static server


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        page = Path(self.directory) / PREFIX / "404.html"
        if code != 404 or not page.is_file():
            super().send_error(code, message, explain)
            return
        body = page.read_bytes()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


class Site:
    """A private copy of site/ served over HTTP."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / PREFIX
        shutil.copytree(SITE_DIR, self.dir)
        self.data = self.dir / "data"
        self._server = ThreadingHTTPServer((HOST, 0), partial(_Handler, directory=str(root)))
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.origin = f"http://{HOST}:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str = "") -> str:
        return f"{self.origin}/{PREFIX}/{path}"

    def write(self, data: Any, name: str = "reports.json") -> None:
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        (self.data / name).write_text(text, encoding="utf-8")

    def remove(self, name: str) -> None:
        (self.data / name).unlink(missing_ok=True)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def site(tmp_path: Path) -> Iterator[Site]:
    s = Site(tmp_path)
    s.write(make_reports())  # default scenario: 7 complete topics
    yield s
    s.stop()


# ---------------------------------------------------------------- browser harness

DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 390, "height": 844}

READY_JS = """() => document.querySelector('#hlList').children.length > 0
                 || !document.querySelector('#loadErr').hidden"""


class UI:
    """Opens pages against the test's Site and records anything that went wrong in them."""

    def __init__(self, browser: Any, site: Site) -> None:
        self.browser = browser
        self.site = site
        self.contexts: list[Any] = []
        self.errors: list[str] = []
        self.dialogs: list[str] = []
        self.external: list[str] = []
        self._allowed: list[re.Pattern[str]] = []

    def allow_console(self, pattern: str) -> None:
        """Tolerate console errors matching ``pattern`` (e.g. expected 404s)."""
        self._allowed.append(re.compile(pattern))

    def new_context(
        self,
        *,
        viewport: dict[str, int] | None = None,
        color_scheme: str = "light",
        reduced_motion: str = "no-preference",
        init_scripts: tuple[str, ...] = (),
        locale: str = "en-US",
        # Deliberately not IST: the page must convert the IST schedule to the visitor's zone.
        timezone_id: str = VISITOR_TZ,
        **kwargs: Any,
    ) -> Any:
        ctx = self.browser.new_context(
            viewport=viewport or DESKTOP,
            color_scheme=color_scheme,
            reduced_motion=reduced_motion,
            locale=locale,
            timezone_id=timezone_id,
            **kwargs,
        )
        own = re.compile(rf"^https?://{re.escape(HOST)}:{self.site.port}/")

        def _route(route: Any) -> None:
            url = route.request.url
            if own.match(url):
                route.continue_()
            else:  # no real network: answer fonts/CDN/anything else with an empty body
                self.external.append(url)
                route.fulfill(status=200, body="", content_type="text/css")

        ctx.route("**/*", _route)
        for script in init_scripts:
            ctx.add_init_script(script)
        self.contexts.append(ctx)
        return ctx

    def watch(self, page: Any) -> Any:
        def _console(msg: Any) -> None:
            if msg.type == "error":
                self.errors.append(f"console.error: {msg.text} @ {page.url}")

        def _dialog(d: Any) -> None:
            self.dialogs.append(f"{d.type}: {d.message}")
            d.dismiss()

        page.on("console", _console)
        page.on("pageerror", lambda exc: self.errors.append(f"pageerror: {exc}"))
        page.on("dialog", _dialog)
        return page

    def open(
        self,
        path: str = "",
        *,
        context: Any = None,
        clock: bool = False,
        now: str | None = None,
        wait: bool = True,
        **ctx_kwargs: Any,
    ) -> Any:
        """``now`` freezes the page's wall clock (Date) at that ISO instant; timers keep running."""
        ctx = context or self.new_context(**ctx_kwargs)
        page = self.watch(ctx.new_page())
        if clock:
            page.clock.install()
        if now:
            page.clock.set_fixed_time(now)
        page.goto(self.site.url(path))
        if wait:
            wait_ready(page)
        return page

    def check(self) -> None:
        unexpected = [e for e in self.errors if not any(p.search(e) for p in self._allowed)]
        assert not unexpected, "page reported errors:\n" + "\n".join(unexpected)
        assert not self.dialogs, f"unexpected dialogs: {self.dialogs}"

    def close(self) -> None:
        for ctx in self.contexts:
            with contextlib.suppress(Exception):  # best-effort teardown
                ctx.close()


def wait_ready(page: Any) -> None:
    page.wait_for_function(READY_JS)


@pytest.fixture
def ui(browser: Any, site: Site) -> Iterator[UI]:
    harness = UI(browser, site)
    yield harness
    harness.close()


@pytest.fixture
def open_page(ui: UI) -> Callable[..., Any]:
    return ui.open


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    """Fail a passing test body if its pages logged errors, raised, or opened dialogs."""
    result = yield
    harness = getattr(item, "funcargs", {}).get("ui")
    if isinstance(harness, UI):
        harness.check()
    return result
