"""A tiny local web app (standard library only) around finder + ui.

Security and compliance choices, each tested:
  * Listens on 127.0.0.1 by default. If it is made reachable from other computers, Kalshi is
    switched off (Kalshi's terms do not let its data be shared with other people).
  * Only GET. Nothing is stored; the audit log is off unless you pass --log.
  * The Host header must be this machine, which blocks DNS-rebinding attacks on a local app.
  * A strict Content-Security-Policy (no outside scripts, styles or images), nosniff,
    no-referrer, no framing.
  * Inputs are length-limited and every number is clamped to a sane range.
  * Requests are handled one at a time, so the polite rate limits in compliance.py hold.
"""
from __future__ import annotations

import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .compliance import ReadOnlyClient, is_loopback
from .demo_data import DEMO_NOW, build_demo_registry
from .finder import MAX_LINE_CHARS, MAX_LINES, find
from .guard import DecisionLog, Guard, Limits
from .relations import HeuristicExtractor
from .ui import csp, render_app
from .venues import build_registry

MAX_Q_CHARS = 1200
MAX_QUERY_BYTES = 4000


def _num(params: dict, key: str, default: float, lo: float, hi: float) -> tuple[float, bool]:
    raw = (params.get(key) or [""])[0].strip()
    if raw == "":
        return default, True
    try:
        v = float(raw)
    except ValueError:
        return default, False
    if v != v or v < lo or v > hi:                       # NaN or out of range
        return default, False
    return v, True


def parse_request(query: str) -> tuple[str, Limits, str, str]:
    """(text, limits, fee as typed, error message). Never raises on bad input."""
    params = urllib.parse.parse_qs(query[:MAX_QUERY_BYTES], keep_blank_values=True)
    q = (params.get("q") or [""])[0][:MAX_Q_CHARS]
    bad: list[str] = []
    notional, ok1 = _num(params, "max_notional", 300.0, 1, 1_000_000)
    total, ok2 = _num(params, "max_total", 1000.0, 1, 10_000_000)
    edge_c, ok3 = _num(params, "min_edge", 1.0, 0, 50)
    fee, ok4 = _num(params, "fee", 0.0, 0, 20)
    for ok, name in ((ok1, "most per trade"), (ok2, "most across trades"), (ok3, "smallest profit"), (ok4, "fee")):
        if not ok:
            bad.append(name)
    fee_raw = (params.get("fee") or [""])[0].strip() if ok4 else ""
    limits = Limits(max_notional=notional, max_total_notional=max(total, notional),
                    min_edge=edge_c / 100, kill_switch=(params.get("kill") or [""])[0] == "1")
    err = ("Those limits were not valid numbers, so the defaults were used for: " + ", ".join(bad) + ".") if bad else ""
    return q, limits, fee_raw, err


class App:
    """Everything a request needs. One lock serialises checks so rate limits stay polite."""

    def __init__(self, demo: bool = False, kalshi_member: bool = False, host: str = "127.0.0.1",
                 llm=None, log_path: str | None = None, client: ReadOnlyClient | None = None):
        self.demo, self.host = demo, host
        self.extractor = llm or HeuristicExtractor()
        self.log = DecisionLog(log_path) if log_path else None
        self.lock = threading.Lock()
        if demo:
            self.registry = None
        else:
            self.registry = build_registry(client or ReadOnlyClient(), kalshi_member, is_loopback(host))

    def venue_status(self) -> tuple[list[str], dict[str, str]]:
        if self.demo:
            return ["Polymarket", "Kalshi", "Manifold"], {}
        return list(self.registry.venues), dict(self.registry.skipped)

    def page(self, query: str) -> str:
        q, limits, fee_raw, err = parse_request(query)
        names, skipped = self.venue_status()
        findings = None
        if q.strip():
            lines = [ln for ln in q.replace("\r", "").split("\n")]
            fee_rate = float(fee_raw) / 100 if fee_raw else 0.0
            with self.lock:
                registry = build_demo_registry() if self.demo else self.registry
                now = DEMO_NOW if self.demo else datetime.now(timezone.utc)
                findings = find(lines, registry, self.extractor, Guard(limits), now, fee_rate, self.log)
        return render_app(q=q, limits=limits, fee_pct=fee_raw, venues=names, skipped=skipped,
                          findings=findings, demo=self.demo, error=err)


def make_handler(app: App, port_getter):
    class Handler(BaseHTTPRequestHandler):
        server_version = "arbcheck"
        sys_version = ""

        def _host_ok(self) -> bool:
            """Local app: only answer requests addressed to this machine (blocks DNS rebinding)."""
            if not is_loopback(app.host):
                return True                              # the person chose to expose it
            host = (self.headers.get("Host") or "").strip().lower()
            name = host.split("]")[0].lstrip("[") if host.startswith("[") else host.rsplit(":", 1)[0]
            return is_loopback(name)

        def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", csp())
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_GET(self) -> None:                       # noqa: N802
            if not self._host_ok():
                return self._send(403, "Forbidden host.", "text/plain; charset=utf-8")
            parts = urllib.parse.urlsplit(self.path)
            if parts.path != "/":
                return self._send(404, "Not found.", "text/plain; charset=utf-8")
            try:
                self._send(200, app.page(parts.query))
            except Exception:                           # never show a stack trace to the browser
                self.log_error("request failed", exc_info=False)
                self._send(500, "Something went wrong while checking. Try again, or try fewer markets.",
                           "text/plain; charset=utf-8")

        do_HEAD = do_GET                                # noqa: N815

        def _refuse(self) -> None:
            self._send(405, "This app only reads. It has no way to send or change anything.",
                       "text/plain; charset=utf-8")

        do_POST = do_PUT = do_DELETE = do_PATCH = _refuse   # noqa: N815

        def log_message(self, fmt, *args) -> None:      # do not log what people typed
            pass

    return Handler


def serve(app: App, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(app, lambda: server.server_port))
    return server
