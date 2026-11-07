import urllib.parse
"""The local web app: answers, escaping, and the security and compliance guard rails."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from arbcheck.compliance import ReadOnlyClient
from arbcheck.web import App, parse_request, serve


@pytest.fixture
def demo_server():
    app = App(demo=True)
    srv = serve(app, "127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}", srv
    srv.shutdown(); srv.server_close()


def get(url, headers=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def test_home_page_has_form_and_demo_banner(demo_server):
    base, _ = demo_server
    code, body, h = get(base + "/")
    assert code == 200 and "Which markets do you think are an arbitrage?" in body and "Demo mode" in body
    assert 'name="q"' in body and "Check it" in body


def test_security_headers(demo_server):
    base, _ = demo_server
    _, _, h = get(base + "/")
    csp = h["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "sha256-" in csp and "unsafe-eval" not in csp
    assert "script-src 'unsafe-inline'" not in csp
    assert h["X-Content-Type-Options"] == "nosniff" and h["Referrer-Policy"] == "no-referrer"
    assert h["X-Frame-Options"] == "DENY" and h["Cache-Control"] == "no-store"


def test_false_arbitrage_answer_end_to_end(demo_server):
    base, _ = demo_server
    q = ("Will Helix Motors recall the H3 model by December 31, 2026?\n"
         "Will Helix Motors recall the H3 model by March 31, 2027?")
    code, body, _ = get(base + "/?" + urllib.parse.urlencode({"q": q}))
    assert code == 200 and "No. It looks like an arbitrage but is not one." in body
    assert "What we understood" in body and "Where the profit goes" in body


def test_real_arbitrage_and_related(demo_server):
    base, _ = demo_server
    code, body, _ = get(base + "/?" + urllib.parse.urlencode({"q": "Will Harbor FC win the Northvale Cup?"}))
    assert "Related opportunities we found" in body and "Multi-outcome set" in body


def test_user_input_is_escaped(demo_server):
    base, _ = demo_server
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    code, body, _ = get(base + "/?" + urllib.parse.urlencode({"q": evil}))
    assert code == 200 and "<script>alert(1)" not in body and "onerror=alert" not in body.replace("&lt;img src=x onerror=alert(2)&gt;", "")
    assert "&lt;script&gt;" in body


def test_foreign_host_header_is_rejected(demo_server):
    base, _ = demo_server
    code, _, _ = get(base + "/", {"Host": "evil.example.com"})
    assert code == 403
    code, _, _ = get(base + "/", {"Host": "localhost:8765"})
    assert code == 200


def test_only_get_is_accepted(demo_server):
    base, _ = demo_server
    for m in ("POST", "PUT", "DELETE", "PATCH"):
        code, _, _ = get(base + "/", method=m) if m == "DELETE" else (None, None, None)
        if code is not None:
            assert code == 405
    req = urllib.request.Request(base + "/", data=b"x=1", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 405


def test_other_paths_404_and_no_stack_traces(demo_server):
    base, _ = demo_server
    assert get(base + "/admin")[0] == 404 and get(base + "/../../etc/passwd")[0] in (404, 400)


def test_bad_limits_fall_back_to_defaults_with_a_message():
    q, limits, fee, err = parse_request("q=x&max_notional=abc&min_edge=-5&fee=999&max_total=nan")
    assert limits.max_notional == 300 and limits.min_edge == 0.01 and fee == "" and "defaults" in err
    q, limits, fee, err = parse_request("q=x&max_notional=50&max_total=10&kill=1&fee=0.5")
    assert limits.max_total_notional >= limits.max_notional and limits.kill_switch and fee == "0.5" and not err


def test_long_input_is_cut():
    q, *_ = parse_request("q=" + "a" * 50_000)
    assert len(q) <= 1200


def test_kill_switch_via_the_url(demo_server):
    base, _ = demo_server
    q = urllib.parse.urlencode({"q": "Will Orion Tech announce a stock split by December 31, 2026?\n"
                                     "Will Orion Tech announce a stock split by March 31, 2027?", "kill": "1"})
    _, body, _ = get(base + "/?" + q)
    assert "Blocked by your risk controls." in body and "Yes, this looks like a real arbitrage." not in body


def test_kalshi_off_by_default_and_when_exposed():
    class Fake:
        def __call__(self, *a): return 200, b"[]", {}
    off = App(demo=False, client=ReadOnlyClient(transport=Fake()))
    names, skipped = off.venue_status()
    assert "Kalshi" not in names and "Kalshi" in skipped
    exposed = App(demo=False, kalshi_member=True, host="0.0.0.0", client=ReadOnlyClient(transport=Fake()))
    names, skipped = exposed.venue_status()
    assert "Kalshi" not in names and "another computer" in skipped["Kalshi"]
    local = App(demo=False, kalshi_member=True, client=ReadOnlyClient(transport=Fake()))
    assert "Kalshi" in local.venue_status()[0]


def test_no_audit_log_written_by_default(demo_server, tmp_path, monkeypatch):
    base, _ = demo_server
    monkeypatch.chdir(tmp_path)
    get(base + "/?q=Will+Harbor+FC+win+the+Northvale+Cup%3F")
    assert list(tmp_path.iterdir()) == []
