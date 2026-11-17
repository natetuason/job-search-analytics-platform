import httpx
import pytest

from app.sources.http import AccessPolicy, HttpClient, SourceError

POLICY = AccessPolicy(name="test", min_interval_s=2.0, max_retries=3, backoff_base_s=1.0, backoff_max_s=10.0)


class FakeTime:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(responses: list, t: FakeTime) -> tuple[HttpClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return HttpClient(POLICY, transport=httpx.MockTransport(handler), sleep=t.sleep, clock=t.clock), seen


def test_returns_json_and_sends_user_agent() -> None:
    client, seen = make_client([httpx.Response(200, json={"ok": True})], FakeTime())
    assert client.get_json("https://x.test/a") == {"ok": True}
    assert "job-platform" in seen[0].headers["User-Agent"]


def test_retries_server_errors_then_succeeds() -> None:
    t = FakeTime()
    client, seen = make_client([httpx.Response(503), httpx.Response(502), httpx.Response(200, json=[1])], t)
    assert client.get_json("https://x.test/a") == [1]
    assert len(seen) == 3
    # Backoff sleeps grow: attempt 0 in [1, 1.5], attempt 1 in [2, 3] (ignoring throttle sleeps).
    backoffs = [s for s in t.sleeps if s >= 1.0]
    assert 1.0 <= backoffs[0] <= 1.5 and 2.0 <= backoffs[1] <= 3.0


def test_honors_retry_after_capped() -> None:
    t = FakeTime()
    client, _ = make_client(
        [httpx.Response(429, headers={"Retry-After": "5"}), httpx.Response(429, headers={"Retry-After": "999"}),
         httpx.Response(200, json={})],
        t,
    )
    client.get_json("https://x.test/a")
    assert 5.0 in t.sleeps
    assert 10.0 in t.sleeps  # capped at backoff_max_s


def test_retries_network_errors() -> None:
    client, seen = make_client([httpx.ConnectError("boom"), httpx.Response(200, json={})], FakeTime())
    assert client.get_json("https://x.test/a") == {}
    assert len(seen) == 2


def test_gives_up_after_max_retries() -> None:
    client, seen = make_client([httpx.Response(500)] * 4, FakeTime())
    with pytest.raises(SourceError, match="after retries") as info:
        client.get_json("https://x.test/a")
    assert info.value.status == 500
    assert len(seen) == 4  # first try + 3 retries


def test_client_errors_are_not_retried() -> None:
    client, seen = make_client([httpx.Response(404)], FakeTime())
    with pytest.raises(SourceError, match="HTTP 404"):
        client.get_json("https://x.test/a")
    assert len(seen) == 1


def test_non_json_body_is_a_source_error() -> None:
    client, _ = make_client([httpx.Response(200, text="<html>")], FakeTime())
    with pytest.raises(SourceError, match="not JSON"):
        client.get_json("https://x.test/a")


def test_rate_limit_spaces_requests() -> None:
    t = FakeTime()
    client, _ = make_client([httpx.Response(200, json={}), httpx.Response(200, json={})], t)
    client.get_json("https://x.test/a")
    t.now += 0.5
    client.get_json("https://x.test/b")
    assert t.sleeps == [pytest.approx(1.5)]
