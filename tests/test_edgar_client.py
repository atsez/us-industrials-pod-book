import pytest
import requests
from conftest import FakeResponse, FakeSession

from podbook.config import sec_user_agent
from podbook.edgar.client import EdgarClient, RateLimiter


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_user_agent_must_include_email(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError):
        sec_user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", "Jane Smith")
    with pytest.raises(RuntimeError):
        sec_user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", "Jane Smith jane@example.com")
    assert sec_user_agent() == "Jane Smith jane@example.com"
    with pytest.raises(ValueError):
        EdgarClient("no email here", session=FakeSession([]))


def test_rate_limiter_spaces_calls():
    clock = FakeClock()
    rl = RateLimiter(8, clock=clock, sleep=clock.sleep)
    for _ in range(5):
        rl.wait()
    # four waits of 1/8 s after the first call: 5 calls take at least 0.5 s
    assert clock.t == pytest.approx(0.5)


def test_client_sends_user_agent_and_retries_on_429():
    clock = FakeClock()
    session = FakeSession([FakeResponse(429), FakeResponse(503), FakeResponse(200, {"ok": 1})])
    client = EdgarClient("Jane jane@example.com", session=session, sleep=clock.sleep, clock=clock)
    assert client.get_json("https://data.sec.gov/x") == {"ok": 1}
    assert len(session.calls) == 3
    assert all(c["headers"]["User-Agent"] == "Jane jane@example.com" for c in session.calls)


def test_client_gives_up_after_max_retries():
    clock = FakeClock()
    session = FakeSession([FakeResponse(503)] * 3)
    client = EdgarClient("Jane jane@example.com", session=session, max_retries=2, sleep=clock.sleep, clock=clock)
    with pytest.raises(requests.HTTPError):
        client.get("https://data.sec.gov/x")


def test_cached_json_fetches_once_and_caches_404(tmp_path):
    clock = FakeClock()
    session = FakeSession([FakeResponse(200, {"a": 1}), FakeResponse(404)])
    client = EdgarClient("Jane jane@example.com", session=session, sleep=clock.sleep, clock=clock)
    path = tmp_path / "x.json"
    assert client.cached_json("u1", path) == {"a": 1}
    assert client.cached_json("u1", path) == {"a": 1}
    missing = tmp_path / "y.json"
    assert client.cached_json("u2", missing) is None
    assert client.cached_json("u2", missing) is None
    assert len(session.calls) == 2
