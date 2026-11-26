"""Candidate retrieval: query building, host policy, parsing, pacing, breaker, audit."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.core.tools.circuit import BreakerRegistry, CircuitBreaker
from src.services import scholarly_retrieval as sr
from src.services.scholarly_retrieval import (
    ALLOWED_HOSTS,
    CONSENT_RECIPIENTS,
    EgressEvent,
    RetrievalBudget,
    build_queries,
    make_client,
    retrieve_candidates,
)

OPENALEX = re.compile(r"https://api\.openalex\.org/works(\?.*)?")
SENTINEL_KEY = "canary-value-for-log-test"


class Clock:
    """Injected time: sleeping advances ``now`` and is logged."""

    def __init__(self) -> None:
        self.t = 0.0
        self.log: list[tuple[str, str, float]] = []

    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=self.t)

    async def sleep(self, seconds: float) -> None:
        self.log.append(("sleep", "", seconds))
        self.t += seconds


class Pacer:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.now = clock.now
        self.open_slots = 0
        self.max_open = 0

    async def sleep(self, seconds: float) -> None:
        await self.clock.sleep(seconds)

    @contextlib.asynccontextmanager
    async def slot(self, host: str):
        self.open_slots += 1
        self.max_open = max(self.max_open, self.open_slots)
        self.clock.log.append(("enter", host, self.clock.t))
        try:
            yield
        finally:
            self.clock.log.append(("exit", host, self.clock.t))
            self.open_slots -= 1


class StuckPacer(sr.RealTime):
    @contextlib.asynccontextmanager
    async def slot(self, host: str):
        await asyncio.sleep(60)
        yield


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def events() -> list[EgressEvent]:
    return []


@pytest.fixture
def run(clock, events, monkeypatch):
    monkeypatch.setattr(sr, "_BREAKERS", BreakerRegistry(_breaker))

    async def record(event: EgressEvent) -> None:
        events.append(event)

    async def go(text: str = DOC, *, pacer=None, budget=None):
        async with make_client() as http:
            return await retrieve_candidates(
                text,
                budget=budget or RetrievalBudget(),
                http=http,
                record_egress=record,
                pacer=pacer or Pacer(clock),
            )

    return go


def _breaker() -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=3, cooldown_seconds=60)


def para(words: list[str]) -> str:
    return " ".join(words)


DOC = "\n\n".join(
    [
        para(
            [
                "quantum",
                "entanglement",
                "decoherence",
                "photonic",
                "lattice",
                "oscillator",
                "the",
                "and",
            ]
        ),
        para(
            [
                "quantum",
                "entanglement",
                "photonic",
                "tunneling",
                "once-only",
                "xylophone",
            ]
        ),
        para(["decoherence", "lattice", "tunneling", "magnetization", "the", "of"]),
    ]
)


POOL = [
    "quantum",
    "entanglement",
    "decoherence",
    "photonic",
    "lattice",
    "oscillator",
    "tunneling",
    "magnetization",
]
DOC_MANY = "\n\n".join(para([POOL[(i + k) % 8] for k in range(4)]) for i in range(8))


def work(wid="W1", title="A paper", abstract=("alpha", "beta"), **kw) -> dict:
    index: dict[str, list[int]] = {}
    for pos, w in enumerate(abstract):
        index.setdefault(w, []).append(pos)
    base = {
        "id": f"https://openalex.org/{wid}",
        "doi": None,
        "title": title,
        "authorships": [{"author": {"display_name": "Jane Doe"}}],
        "abstract_inverted_index": index or None,
        "best_oa_location": None,
        "ids": {},
        "locations": [],
        "language": "en",
        "publication_year": 2020,
    }
    base.update(kw)
    return base


def search_ok(httpx_mock, *works, **kw):
    httpx_mock.add_response(url=OPENALEX, json={"results": list(works)}, **kw)


# -- 1 build_queries ---------------------------------------------------------


def test_queries_are_sorted_whitelisted_and_bounded():
    queries = build_queries(DOC)
    assert queries
    for q in queries:
        assert 1 <= len(q) <= 6 and list(q) == sorted(q)
        assert all(re.fullmatch(r"[a-z0-9-]{5,40}", t) for t in q)


def test_queries_never_include_a_single_paragraph_term():
    terms = {t for q in build_queries(DOC) for t in q}
    assert "xylophone" not in terms and "once-only" not in terms
    assert "quantum" in terms


def test_queries_contain_no_three_consecutive_document_tokens():
    tokens = DOC.split()
    triples = {tuple(tokens[i : i + 3]) for i in range(len(tokens) - 2)}
    for q in build_queries(DOC):
        for i in range(len(q) - 2):
            assert tuple(q[i : i + 3]) not in triples


def test_non_ascii_terms_are_dropped_and_counted():
    text = "\n\n".join(["quantum entanglement lattice 量子纠缠"] * 3)
    plan = sr.plan_queries(text)
    assert plan.terms_dropped_non_ascii >= 1
    assert all(t.isascii() for q in plan.queries for t in q)


def test_queries_are_deterministic_and_skip_the_reference_list():
    assert build_queries(DOC) == build_queries(DOC)
    refs = (
        DOC
        + "\n\nReferences\n\n"
        + "\n\n".join(["[1] zebra crossing xenon quartz"] * 3)
    )
    assert not any("zebra" in q for q in build_queries(refs))


def test_query_limit_is_applied():
    many = "\n\n".join(
        f"alpha{i}word beta{i}word shared-term common-term" for i in range(40)
    )
    assert len(build_queries(many, limit=5)) <= 5


# -- 2 host policy -----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/x",
        "http://api.openalex.org/works",
        "https://export.arxiv.org/x",
    ],
)
async def test_requests_outside_the_allowlist_never_leave(httpx_mock, url):
    async with make_client() as http:
        with pytest.raises(sr.HostNotAllowedError):
            await http.get(url)
    assert httpx_mock.get_requests() == []


async def test_a_client_without_the_host_hook_is_refused(run, clock, events):
    async def record(event):
        events.append(event)

    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError):
            await retrieve_candidates(
                DOC,
                budget=RetrievalBudget(),
                http=http,
                record_egress=record,
                pacer=Pacer(clock),
            )


def test_consent_recipients_and_hosts():
    assert CONSENT_RECIPIENTS == ("api.openalex.org", "arxiv.org")
    assert frozenset(CONSENT_RECIPIENTS) == ALLOWED_HOSTS


# -- 3 redirects, 4 size cap -------------------------------------------------


async def test_redirect_is_recorded_and_not_followed(httpx_mock, run, events):
    httpx_mock.add_response(
        url=OPENALEX, status_code=302, headers={"location": "https://x.example/"}
    )
    res = await run(budget=RetrievalBudget(max_queries=1))
    assert res.openalex.failures == {"redirect": 1}
    assert len(httpx_mock.get_requests()) == 1
    assert [e.status for e in events] == ["redirect"]


async def test_oversize_json_aborts_the_read(httpx_mock, run):
    served = {"n": 0}

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(48):
                served["n"] += 64 * 1024
                yield b"x" * 64 * 1024

    httpx_mock.add_response(url=OPENALEX, stream=Body())
    res = await run(budget=RetrievalBudget(max_queries=1))
    assert res.openalex.failures == {"oversize": 1}
    assert served["n"] <= sr.MAX_JSON_BYTES + 128 * 1024


# -- 5 inverted index, licences, year ----------------------------------------


async def test_abstract_licence_and_year_are_parsed(httpx_mock, run):
    search_ok(
        httpx_mock,
        work(
            abstract=("hello", "world"),
            best_oa_location={"license": "cc-by"},
            doi="https://doi.org/10.1234/abc",
            publication_year=2019,
        ),
    )
    res = await run(budget=RetrievalBudget(max_queries=1))
    (c,) = res.candidates
    assert c.text == "hello world" and c.text_kind == "abstract"
    assert (
        c.licence.identifier == "cc-by" and c.licence.declared_by.value == "api_field"
    )
    assert c.doi == "10.1234/abc" and c.year == 2019
    assert c.link == "https://openalex.org/W1"


async def test_missing_licence_is_undeclared(httpx_mock, run):
    search_ok(httpx_mock, work())
    (c,) = (await run(budget=RetrievalBudget(max_queries=1))).candidates
    assert c.licence.declared_by.value == "undeclared"


async def test_hostile_inverted_index_is_a_parse_error(httpx_mock, run):
    bad = work(abstract=())
    bad["abstract_inverted_index"] = {"word": [sr.MAX_ABSTRACT_POSITION + 1]}
    search_ok(httpx_mock, bad, work(wid="W2"))
    res = await run(budget=RetrievalBudget(max_queries=1))
    assert res.openalex.failures == {"parse_error": 1}
    assert [c.source_id for c in res.candidates] == ["W2"]


async def test_too_many_abstract_words_is_a_parse_error(httpx_mock, run):
    bad = work(abstract=())
    bad["abstract_inverted_index"] = {
        f"w{i}": [i] for i in range(sr.MAX_ABSTRACT_WORDS + 1)
    }
    search_ok(httpx_mock, bad)
    res = await run(budget=RetrievalBudget(max_queries=1))
    assert res.openalex.failures == {"parse_error": 1} and res.candidates == ()


# -- 6 arXiv -----------------------------------------------------------------


async def fake_extract(data: bytes):
    from src.core.acquisition.pdf_text import ExtractedText, PageSpan

    text = "full text of " + data.decode()
    return ExtractedText(text, (PageSpan(1, 0, len(text)),), "a" * 64, "b" * 64), 0


@pytest.fixture
def extract_spy(monkeypatch):
    calls: list[bytes] = []

    async def spy(data: bytes):
        calls.append(data)
        return await fake_extract(data)

    monkeypatch.setattr(sr, "extract_pdf_sandboxed", spy)
    return calls


def arxiv_work(wid="W1", aid="2101.00001"):
    return work(
        wid=wid, locations=[{"landing_page_url": f"https://arxiv.org/abs/{aid}"}]
    )


async def test_arxiv_pdf_goes_only_to_the_sandbox(httpx_mock, run, extract_spy):
    search_ok(httpx_mock, arxiv_work())
    httpx_mock.add_response(
        url="https://arxiv.org/pdf/2101.00001", content=b"%PDF-bytes"
    )
    res = await run(budget=RetrievalBudget(max_queries=1))
    assert extract_spy == [b"%PDF-bytes"]
    (c,) = res.candidates
    assert c.text_kind == "full_text" and c.text == "full text of %PDF-bytes"
    assert res.arxiv.full_text_count == 1 and res.openalex.abstract_only_count == 0


@pytest.mark.parametrize(
    "landing",
    [
        "https://arxiv.org/abs/../../etc",
        "https://evil.example/abs/2101.00001",
        "https://arxiv.org.evil.example/abs/2101.00001",
    ],
)
async def test_bad_arxiv_urls_yield_no_id_and_no_request(
    httpx_mock, run, extract_spy, landing
):
    search_ok(httpx_mock, work(locations=[{"landing_page_url": landing}]))
    res = await run(budget=RetrievalBudget(max_queries=1))
    assert all(r.url.host == "api.openalex.org" for r in httpx_mock.get_requests())
    assert extract_spy == [] and res.arxiv.queries_sent == 0


async def test_sandbox_failure_keeps_the_abstract(httpx_mock, run, monkeypatch):
    async def boom(data):
        raise sr.DocumentIngestionError("malformed_pdf")

    monkeypatch.setattr(sr, "extract_pdf_sandboxed", boom)
    search_ok(httpx_mock, arxiv_work())
    httpx_mock.add_response(url="https://arxiv.org/pdf/2101.00001", content=b"%PDF-x")
    res = await run(budget=RetrievalBudget(max_queries=1))
    assert res.candidates[0].text_kind == "abstract"
    assert res.arxiv.failures == {"extract_error": 1}


async def test_fulltext_budget_is_respected(httpx_mock, run, extract_spy):
    search_ok(httpx_mock, *(arxiv_work(f"W{i}", f"2101.0000{i}") for i in range(1, 5)))
    for i in range(1, 5):
        httpx_mock.add_response(
            url=f"https://arxiv.org/pdf/2101.0000{i}",
            content=b"%PDF-x",
            is_optional=True,
        )
    res = await run(budget=RetrievalBudget(max_queries=1, max_fulltext=2))
    assert res.arxiv.queries_sent == 2 and res.arxiv.full_text_count == 2


# -- 7 pacing ----------------------------------------------------------------


async def test_every_request_is_inside_a_slot_and_spaced(
    httpx_mock, run, clock, extract_spy
):
    search_ok(
        httpx_mock, arxiv_work("W1", "2101.00001"), arxiv_work("W2", "2101.00002")
    )
    for i in (1, 2):
        httpx_mock.add_response(
            url=f"https://arxiv.org/pdf/2101.0000{i}", content=b"%PDF-x"
        )
    pacer = Pacer(clock)
    await run(pacer=pacer, budget=RetrievalBudget(max_queries=1))
    arxiv = [(k, t) for k, h, t in clock.log if h == "arxiv.org" and k == "enter"]
    assert len(arxiv) == 2 and arxiv[1][1] - arxiv[0][1] >= 3.0
    assert pacer.max_open == 1
    kinds = [k for k, _, _ in clock.log]
    assert kinds.index("sleep") > kinds.index("enter") and kinds.index(
        "sleep"
    ) < kinds.index("exit")


async def test_a_slot_that_never_opens_hits_the_deadline_and_sends_nothing(
    httpx_mock, run
):
    res = await run(
        pacer=StuckPacer(), budget=RetrievalBudget(max_queries=1, deadline_s=0.05)
    )
    assert res.openalex.deadline_reached and httpx_mock.get_requests() == []


# -- 8 breaker ---------------------------------------------------------------


async def test_three_failures_open_the_breaker_and_the_fourth_sends_nothing(
    httpx_mock, run, events
):
    for _ in range(3):
        httpx_mock.add_response(url=OPENALEX, status_code=503)
    res = await run(DOC_MANY, budget=RetrievalBudget(max_queries=4))
    assert len(httpx_mock.get_requests()) == 3
    assert res.openalex.failures == {"http_503": 3, "breaker_open": 1}
    assert [e.status for e in events] == [503, 503, 503, "breaker_open"]


async def test_a_cancelled_request_releases_the_half_open_slot(monkeypatch):
    clock = Clock()
    registry = BreakerRegistry(_breaker)
    monkeypatch.setattr(sr, "_BREAKERS", registry)
    breaker = registry.for_tool("api.openalex.org")
    for _ in range(3):
        breaker.record_failure(clock.now())
    clock.t = 120
    abandoned = []
    real = breaker.record_abandoned
    monkeypatch.setattr(
        type(breaker), "record_abandoned", lambda self: (abandoned.append(1), real())
    )

    async def never(self, *a, **k):
        await asyncio.sleep(60)

    async def record(event):
        pass

    class Cancelling(Pacer):
        def slot(self, host):
            return self

        async def __aenter__(self):
            raise asyncio.CancelledError

        async def __aexit__(self, *exc):
            return None

    async with make_client() as http:
        with pytest.raises(asyncio.CancelledError):
            await retrieve_candidates(
                DOC,
                budget=RetrievalBudget(max_queries=1),
                http=http,
                record_egress=record,
                pacer=Cancelling(clock),
            )
    assert abandoned and breaker.allows(clock.now())


# -- 9 egress, 10 logging ----------------------------------------------------


async def test_every_attempt_emits_one_event_without_terms_urls_or_keys(
    httpx_mock, run, events, monkeypatch
):
    monkeypatch.setattr(sr.settings, "OPENALEX_API_KEY", SENTINEL_KEY)
    search_ok(httpx_mock, work())
    search_ok(httpx_mock, work(), status_code=503)
    await run(budget=RetrievalBudget(max_queries=2))
    assert len(events) == 2 and events[0].term_count >= 1
    blob = repr(events)
    for secret in (SENTINEL_KEY, "http", "quantum", "entanglement"):
        assert secret not in blob


async def test_nothing_sensitive_reaches_any_log(httpx_mock, run, monkeypatch, caplog):
    import structlog

    monkeypatch.setattr(sr.settings, "OPENALEX_API_KEY", SENTINEL_KEY)
    search_ok(httpx_mock, work())
    root = logging.getLogger()
    old = root.level
    root.setLevel(logging.DEBUG)
    caplog.set_level(logging.DEBUG)
    try:
        with structlog.testing.capture_logs() as captured:
            await run(budget=RetrievalBudget(max_queries=1))
    finally:
        root.setLevel(old)
    text = caplog.text + repr(captured)
    for secret in (SENTINEL_KEY, "quantum", "entanglement"):
        assert secret not in text


def test_http_loggers_are_gated_at_warning():
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


# -- 11 hostile metadata, 12 deadline ----------------------------------------


async def test_hostile_titles_and_authors_are_sanitized(httpx_mock, run):
    title = "<img src=https://x.example/a.png>\U000e0041hi " + "z" * 5000
    hostile = work(
        title=title,
        authorships=[
            {"author": {"display_name": "Eve https://evil.example\U000e0041"}}
        ],
    )
    search_ok(httpx_mock, hostile)
    (c,) = (await run(budget=RetrievalBudget(max_queries=1))).candidates
    assert len(c.title) <= 300
    assert "http" not in c.title and "<" not in c.title and "\U000e0041" not in c.title
    assert all("http" not in a and "\U000e0041" not in a for a in c.authors)


async def test_deadline_keeps_partial_candidates_and_says_so(httpx_mock, run, clock):
    search_ok(httpx_mock, work())
    clock.t = 0
    real = clock.sleep

    async def slow(seconds):
        await real(seconds)
        clock.t += 1000

    clock.sleep = slow
    res = await run(budget=RetrievalBudget(max_queries=3, deadline_s=10))
    assert res.openalex.deadline_reached and len(res.candidates) == 1
    assert res.openalex.queries_sent == 1


# -- dedup and ranking -------------------------------------------------------


async def test_dedup_and_ranking_by_hits_then_year(httpx_mock, run):
    a = work(wid="W1", doi="https://doi.org/10.1234/a", publication_year=2018)
    b = work(wid="W2", publication_year=2022)
    dup = work(wid="W9", doi="https://doi.org/10.1234/a", publication_year=2018)
    search_ok(httpx_mock, a, b)
    search_ok(httpx_mock, dup)
    res = await run(budget=RetrievalBudget(max_queries=2))
    assert [c.source_id for c in res.candidates] == ["W1", "W2"]


def test_terms_outside_the_whitelist_are_never_sent():
    odd = ["tiny", "snake_case", "x" * 41, "12345", "caf\u00e9ine", "quo\"te", "ok-term"]
    text = "\n\n".join(para(["alphabet", "wordlist", "gamma-ray", *odd]) for _ in range(3))
    terms = {t for q in build_queries(text) for t in q}
    assert terms == {"alphabet", "wordlist", "gamma-ray", "ok-term"}
