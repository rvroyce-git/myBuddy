"""Chat memory (in memory only), owner /run on demand, and /latest stored reports."""

from __future__ import annotations

import io
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FakeHttp, FixedClock
from test_bot import OWNER, TOPICS, TgHttp, Ticker, msg
from test_bot_access import CHAT_TIME, FRIEND, STRANGER, ai, chat_settings, make, seed_reports

from mybuddy import chat as chatmod
from mybuddy.bot import RUN_BUSY, RUN_OWNER_ONLY, SubscriptionBot
from mybuddy.chat import RESET, ChatResponder
from mybuddy.config import Topic
from mybuddy.ondemand import OnDemandRunner, latest_request, run_lock, run_request
from mybuddy.store import Store


@pytest.fixture
def store(data_dir: Path) -> Iterator[Store]:
    s = Store(data_dir)
    seed_reports(s)
    yield s
    s.close()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(CHAT_TIME)


@pytest.fixture
def log() -> io.StringIO:
    return io.StringIO()


def responder(store: Store, clock: FixedClock, aihttp: FakeHttp, log: io.StringIO) -> ChatResponder:
    return ChatResponder(chat_settings(), aihttp, store, TOPICS, clock, log=log)


def prompt(aihttp: FakeHttp, n: int = -1) -> str:
    return str(aihttp.posts[n][2]["messages"][1]["content"])


# --------------------------------------------------------------------------- memory


def test_follow_up_uses_the_previous_turn(
    store: Store, clock: FixedClock, log: io.StringIO
) -> None:
    aihttp = FakeHttp()
    aihttp.post_responses = [ai("Model X is new [ai_news #1]."), ai("It is fast.")]
    r = responder(store, clock, aihttp, log)
    r.answer(OWNER, "what's new in AI?")
    r.answer(OWNER, "tell me more about it")
    p = prompt(aihttp)
    convo = p[p.index("<<<CONVERSATION") : p.index("CONVERSATION>>>")]
    assert "what's new in AI?" in convo and "Model X is new" in convo
    assert "https://" not in convo  # links appended to answers never go back in
    assert "<<<QUESTION (untrusted data)\ntell me more about it" in p


def test_memory_is_per_chat(store: Store, clock: FixedClock, log: io.StringIO) -> None:
    aihttp = FakeHttp()
    aihttp.post_responses = [ai("One."), ai("Two.")]
    r = responder(store, clock, aihttp, log)
    r.answer(OWNER, "owner private question")
    r.answer(FRIEND, "friend question")
    assert "owner private question" not in prompt(aihttp)
    assert "<<<CONVERSATION" not in prompt(aihttp)


def test_memory_expires_after_30_idle_minutes(
    store: Store, clock: FixedClock, log: io.StringIO
) -> None:
    aihttp = FakeHttp()
    aihttp.post_responses = [ai("One."), ai("Two."), ai("Three.")]
    r = responder(store, clock, aihttp, log)
    r.answer(OWNER, "first question")
    clock.advance(minutes=29)
    r.answer(OWNER, "second question")
    assert "first question" in prompt(aihttp)
    clock.advance(minutes=30)
    r.answer(OWNER, "third question")
    assert "<<<CONVERSATION" not in prompt(aihttp)


def test_memory_is_capped_oldest_first(store: Store, clock: FixedClock, log: io.StringIO) -> None:
    r = responder(store, clock, FakeHttp(), log)
    for i in range(10):
        r.remember(OWNER, f"question {i} " + "x" * 200, f"answer {i}")
    turns = r.history(OWNER)
    assert len(turns) <= chatmod.MEMORY_TURNS
    assert len(chatmod.history_text(turns)) <= chatmod.MEMORY_MAX_CHARS
    assert turns[-1][1] == "answer 9" and turns[0][1] != "answer 0"
    for i in range(10):
        r.remember(FRIEND, f"q{i}", f"a{i}")
    assert [a for _, a in r.history(FRIEND)] == [f"a{i}" for i in range(4, 10)]


def test_reset_forgets(store: Store, clock: FixedClock, log: io.StringIO) -> None:
    aihttp, tg = FakeHttp(), TgHttp()
    aihttp.post_responses = [ai("One."), ai("Two.")]
    bot = make(tg, store, Ticker(), log, aihttp, clock)
    bot.handle_update(msg("first question", chat=OWNER))
    bot.handle_update(msg("/reset", chat=OWNER))
    assert tg.calls[-1][1]["text"] == RESET
    bot.handle_update(msg("second question", chat=OWNER))
    assert "<<<CONVERSATION" not in prompt(aihttp)


def test_memory_never_stored_or_logged(
    store: Store, clock: FixedClock, log: io.StringIO, data_dir: Path
) -> None:
    aihttp = FakeHttp()
    aihttp.post_responses = [ai("Distinctive answer zebra."), ai("Two.")]
    r = responder(store, clock, aihttp, log)
    r.answer(OWNER, "distinctive question walrus")
    r.answer(OWNER, "follow up")
    store._db.commit()
    for f in data_dir.rglob("*"):
        if f.is_file():
            raw = f.read_bytes()
            assert b"walrus" not in raw and b"zebra" not in raw
    assert "walrus" not in log.getvalue() and "zebra" not in log.getvalue()


# --------------------------------------------------------------------------- matching


@pytest.mark.parametrize(
    ("text", "topic"),
    [
        ("send me the security report now", "security"),
        ("run github trending", "github_trending"),
        ("refresh ai news", "ai_news"),
        ("Please run the KEV alerts", "kev_alerts"),
        ("what's new in security?", None),
        ("run something unknown", None),
    ],
)
def test_run_phrases(text: str, topic: str | None) -> None:
    found = run_request(text, TOPICS)
    assert (found.id if found else None) == topic


def test_latest_phrases() -> None:
    found = latest_request("show me today's security", TOPICS)
    assert found is not None and found.id == "security"
    assert latest_request("send me the security report now", TOPICS) is None


# --------------------------------------------------------------------------- /run


class FakeRunner(OnDemandRunner):
    def __init__(self, data_dir: Path, gate: threading.Event | None = None) -> None:
        self.ran: list[str] = []
        self.gate = gate

        def run(topic: Topic) -> str:
            if self.gate is not None:
                self.gate.wait(5)
            self.ran.append(topic.id)
            return f"Done ✅ {topic.title} sent."

        super().__init__(run, data_dir)


def run_bot(
    store: Store, clock: FixedClock, log: io.StringIO, runner: OnDemandRunner
) -> tuple[SubscriptionBot, TgHttp, FakeHttp]:
    tg, aihttp = TgHttp(), FakeHttp()
    bot = make(tg, store, Ticker(), log, aihttp, clock, runner=runner, chat_public=True)
    return bot, tg, aihttp


def texts(tg: TgHttp) -> list[str]:
    return [str(p.get("text")) for m, p in tg.calls if m == "sendMessage"]


def test_owner_run_natural_language(
    store: Store, clock: FixedClock, log: io.StringIO, data_dir: Path
) -> None:
    runner = FakeRunner(data_dir)
    bot, tg, aihttp = run_bot(store, clock, log, runner)
    bot.handle_update(msg("send me the security report now", chat=OWNER))
    assert runner.thread is not None
    runner.thread.join(5)
    assert runner.ran == ["security"] and not aihttp.posts
    assert texts(tg)[-1].startswith("Done")
    assert all(p["chat_id"] == OWNER for _, p in tg.calls)


def test_run_is_owner_only(
    store: Store, clock: FixedClock, log: io.StringIO, data_dir: Path
) -> None:
    runner = FakeRunner(data_dir)
    bot, tg, _ = run_bot(store, clock, log, runner)
    bot.handle_update(msg("/run security", chat=STRANGER))
    assert texts(tg) == [RUN_OWNER_ONLY] and runner.thread is None


def test_run_unknown_topic_lists_topics(
    store: Store, clock: FixedClock, log: io.StringIO, data_dir: Path
) -> None:
    runner = FakeRunner(data_dir)
    bot, tg, _ = run_bot(store, clock, log, runner)
    bot.handle_update(msg("/run nonsense", chat=OWNER))
    assert "security" in texts(tg)[-1] and runner.thread is None


def test_one_run_at_a_time(
    store: Store, clock: FixedClock, log: io.StringIO, data_dir: Path
) -> None:
    gate = threading.Event()
    runner = FakeRunner(data_dir, gate)
    bot, tg, _ = run_bot(store, clock, log, runner)
    bot.handle_update(msg("/run security", chat=OWNER))
    bot.handle_update(msg("/run ai_news", chat=OWNER, uid=2))
    assert texts(tg)[-1] == RUN_BUSY
    gate.set()
    assert runner.thread is not None
    runner.thread.join(5)
    assert runner.ran == ["security"]


def test_run_waits_for_the_shared_file_lock(data_dir: Path) -> None:
    runner = FakeRunner(data_dir)
    replies: list[str] = []
    with run_lock(data_dir):  # e.g. the scheduler is mid-run
        assert runner.start(TOPICS[0], replies.append)
        assert runner.thread is not None
        runner.thread.join(0.3)
        assert runner.ran == []
    runner.thread.join(5)
    assert runner.ran == [TOPICS[0].id] and replies


def test_owner_run_delivers_to_owner_only(
    data_dir: Path, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mybuddy.cli import run as runmod
    from mybuddy.deliver import FanoutDeliverer
    from mybuddy.engine import Deps, RunResult

    seen: list[object] = []

    def fake_run_topic(topic: Topic, deps: Deps, **_: object) -> RunResult:
        seen.append(deps.deliverer)
        return RunResult(topic.id, "delivered")

    monkeypatch.setattr(runmod, "run_topic", fake_run_topic)
    settings = chat_settings(data_dir=data_dir, fake=True)
    reply = runmod.owner_run(settings, TOPICS, clock, frozenset())(TOPICS[0])
    assert reply.startswith("Done") and not isinstance(seen[0], FanoutDeliverer)


# --------------------------------------------------------------------------- /latest


@pytest.mark.parametrize("text", ["show me today's security", "/latest security"])
def test_public_latest_uses_stored_report_without_ai(
    store: Store, clock: FixedClock, log: io.StringIO, data_dir: Path, text: str
) -> None:
    bot, tg, aihttp = run_bot(store, clock, log, FakeRunner(data_dir))
    bot.handle_update(msg(text, chat=STRANGER))
    assert not aihttp.posts
    [(_, payload)] = tg.calls
    assert payload["parse_mode"] == "HTML" and payload["chat_id"] == STRANGER
    assert "Patch your routers" in payload["text"]
    assert 'href="https://security.example/router"' in payload["text"]


def test_latest_hides_owner_only_health(
    store: Store, clock: FixedClock, log: io.StringIO, data_dir: Path
) -> None:
    bot, tg, _ = run_bot(store, clock, log, FakeRunner(data_dir))
    bot.handle_update(msg("/latest health_check", chat=STRANGER))
    assert "Which report?" in texts(tg)[-1]
