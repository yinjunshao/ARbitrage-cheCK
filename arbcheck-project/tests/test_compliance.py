"""The compliance layer is a safety property, so it gets tests that try to break it."""
import json

import pytest

from arbcheck.compliance import FetchError, ReadOnlyClient, Refused, check_url, is_loopback

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com/book"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2/markets/ABC"
MANI = "https://api.manifold.markets/v0/search-markets"


class Fake:
    """A transport that records requests and replays canned answers."""
    def __init__(self, *answers):
        self.answers = list(answers) or [(200, b"{}", {})]
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers))
        a = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        return a


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def client(*answers, kalshi=False, ttl=60.0):
    fake, clk = Fake(*answers), Clock()
    c = ReadOnlyClient(kalshi_enabled=kalshi, transport=fake, clock=clk, sleep=clk.sleep, cache_ttl=ttl)
    return c, fake, clk


# ---- what may be requested ---------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://gamma-api.polymarket.com/events?slug=x", GAMMA, CLOB + "?token_id=1",
    "https://gamma-api.polymarket.com/public-search?q=a", MANI, "https://api.manifold.markets/v0/slug/abc",
])
def test_allowed_urls_pass(url):
    check_url(url, kalshi_enabled=False)


@pytest.mark.parametrize("url", [
    "http://gamma-api.polymarket.com/markets",                # not https
    "https://evil.example.com/markets",                       # not on the allowlist
    "https://gamma-api.polymarket.com/orders",                # not a data endpoint
    "https://clob.polymarket.com/order",                      # placing orders
    "https://clob.polymarket.com/orders",
    "https://clob.polymarket.com/auth/api-key",
    "https://api.manifold.markets/v0/bet",                    # betting
    "https://gamma-api.polymarket.com/markets/../orders",     # path tricks
    "https://gamma-api.polymarket.com//markets",
    "https://gamma-api.polymarket.com/markets%2f..%2forders",
    "https://user:pw@gamma-api.polymarket.com/markets",       # credentials in the URL
    "https://gamma-api.polymarket.com:8443/markets",          # odd port
    "https://gamma-api.polymarket.com.evil.com/markets",      # look-alike host
    "file:///etc/passwd",
])
def test_everything_else_is_refused(url):
    with pytest.raises(Refused):
        check_url(url, kalshi_enabled=True)


@pytest.mark.parametrize("url", [
    "https://api.elections.kalshi.com/trade-api/v2/portfolio/orders",
    "https://api.elections.kalshi.com/trade-api/v2/portfolio/balance",
    "https://api.elections.kalshi.com/trade-api/v2/exchange/status",
    "https://api.elections.kalshi.com/trade-api/v2/login",
])
def test_kalshi_trading_and_account_endpoints_never_allowed(url):
    with pytest.raises(Refused):
        check_url(url, kalshi_enabled=True)


def test_kalshi_refused_unless_enabled():
    with pytest.raises(Refused):
        check_url(KALSHI, kalshi_enabled=False)
    check_url(KALSHI, kalshi_enabled=True)
    c, fake, _ = client()
    with pytest.raises(Refused):
        c.get(KALSHI)
    assert fake.calls == []                                   # refused BEFORE any request was made


def test_refused_requests_send_nothing():
    c, fake, _ = client()
    with pytest.raises(Refused):
        c.get("https://clob.polymarket.com/order")
    assert fake.calls == [] and c.requests_sent == 0


# ---- how it behaves ----------------------------------------------------------------------
def test_sends_descriptive_user_agent_and_only_json_accept():
    c, fake, _ = client((200, b'{"a": 1}', {}))
    assert c.get(GAMMA, {"slug": "x"}) == {"a": 1}
    url, headers = fake.calls[0]
    assert "slug=x" in url
    assert "arbcheck" in headers["User-Agent"].lower()
    assert "@" not in headers["User-Agent"]                   # never leaks the user's email


def test_cache_serves_repeat_requests_for_a_minute():
    c, fake, clk = client((200, b"[1]", {}))
    c.get(GAMMA); c.get(GAMMA)
    assert len(fake.calls) == 1 and c.cache_hits == 1
    clk.t += 61
    c.get(GAMMA)
    assert len(fake.calls) == 2


def test_per_host_spacing():
    c, fake, clk = client((200, b"[]", {}))
    c.get(GAMMA, {"a": 1}); c.get(GAMMA, {"a": 2})
    assert clk.slept and clk.slept[0] > 0                     # the second call had to wait its turn


def test_retries_with_backoff_then_succeeds():
    c, fake, clk = client((429, b"", {}), (503, b"", {}), (200, b'{"ok": true}', {}))
    assert c.get(GAMMA) == {"ok": True}
    assert len(fake.calls) == 3 and sum(clk.slept) >= 3


def test_honours_retry_after_but_caps_it():
    c, fake, clk = client((429, b"", {"Retry-After": "2"}), (429, b"", {"Retry-After": "999"}), (200, b"{}", {}))
    c.get(GAMMA)
    assert 2.0 in clk.slept and max(clk.slept) <= 10.0


def test_gives_up_after_a_few_retries():
    c, fake, _ = client((500, b"", {}))
    with pytest.raises(FetchError) as e:
        c.get(GAMMA)
    assert len(fake.calls) == 4 and e.value.status == 500    # first try + 3 retries, never a loop


def test_client_errors_are_not_retried():
    c, fake, _ = client((404, b"", {}))
    with pytest.raises(FetchError) as e:
        c.get(GAMMA)
    assert len(fake.calls) == 1 and e.value.status == 404


def test_rejects_oversized_and_invalid_bodies():
    c, _, _ = client((200, b"x" * (5 * 1024 * 1024 + 1), {}))
    with pytest.raises(FetchError):
        c.get(GAMMA)
    c, _, _ = client((200, b"<html>not json</html>", {}))
    with pytest.raises(FetchError):
        c.get(GAMMA)


def test_only_get_is_ever_used():
    import inspect
    from arbcheck import compliance
    src = inspect.getsource(compliance)
    assert 'method="GET"' in src
    for verb in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"'):
        assert verb not in src


def test_loopback_helper():
    assert is_loopback("127.0.0.1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.5")
