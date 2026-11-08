"""Thin read-only client for Polymarket's public APIs.

* Gamma API (market metadata):  https://gamma-api.polymarket.com
* CLOB API (books, price history): https://clob.polymarket.com

Only public, unauthenticated endpoints are used – nothing here can place real
orders. Parsing is done by pure functions so it can be tested offline.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Iterable

from ..events import PriceEvent, ResolutionEvent
from ..models import Market, OrderBook, PriceLevel, Token

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"


# --------------------------------------------------------------------------- parsing


def _json_list(v: Any) -> list:
    """Gamma encodes some list fields as JSON strings (e.g. '["Yes", "No"]')."""
    if v is None:
        return []
    if isinstance(v, str):
        return json.loads(v) if v else []
    return list(v)


def _iso_to_ts(v: str | None) -> float | None:
    if not v:
        return None
    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()


def parse_gamma_market(raw: dict) -> Market:
    outcomes = _json_list(raw.get("outcomes"))
    token_ids = _json_list(raw.get("clobTokenIds"))
    if len(outcomes) != len(token_ids):
        raise ValueError(f"market {raw.get('id')}: outcomes/token ids mismatch")
    return Market(
        market_id=raw.get("conditionId") or str(raw.get("id")),
        question=raw.get("question", ""),
        tokens=tuple(Token(str(t), str(o)) for t, o in zip(token_ids, outcomes)),
        slug=raw.get("slug", ""),
        tick_size=float(raw.get("orderPriceMinTickSize") or 0.01),
        min_order_size=float(raw.get("orderMinSize") or 5),
        end_ts=_iso_to_ts(raw.get("endDate")),
        active=bool(raw.get("active", True)),
        closed=bool(raw.get("closed", False)),
        group_title=str(raw.get("groupItemTitle") or ""),
        event_slug=str((raw.get("events") or [{}])[0].get("slug", "")) if isinstance(raw.get("events"), list) else "",
        **_parse_fees(raw),
    )


def _parse_fees(raw: dict) -> dict:
    """Read the market's own fee schedule.

    Gamma returns e.g. ``"feesEnabled": true, "feeType": "politics_fees",
    "feeSchedule": {"exponent": 1, "rate": 0.04, "takerOnly": true, ...}``
    (checked against live responses, Nov 2026). Missing fields leave the rate
    unknown so the exchange can fall back to the category table.
    """
    fee_type = str(raw.get("feeType") or "")
    out: dict = {"category": fee_type.removesuffix("_fees")}
    sched = raw.get("feeSchedule")
    if raw.get("feesEnabled") is False:
        out["fee_rate"] = 0.0
    elif isinstance(sched, dict) and sched.get("rate") is not None:
        out["fee_rate"] = float(sched["rate"])
        out["fee_exponent"] = float(sched.get("exponent", 1) or 1)
        out["fee_taker_only"] = bool(sched.get("takerOnly", True))
    return out


def parse_resolution(raw: dict, ts: float | None = None) -> ResolutionEvent | None:
    """Build a ResolutionEvent from a *closed* Gamma market (outcomePrices are the payouts)."""
    if not raw.get("closed"):
        return None
    market = parse_gamma_market(raw)
    prices = [float(p) for p in _json_list(raw.get("outcomePrices"))]
    if len(prices) != len(market.tokens):
        return None
    if ts is None:
        ts = _iso_to_ts(raw.get("closedTime")) or market.end_ts or time.time()
    return ResolutionEvent(ts, market.market_id, {t.token_id: p for t, p in zip(market.tokens, prices)})


def parse_book(raw: dict, ts: float | None = None) -> OrderBook:
    if ts is None:
        stamp = raw.get("timestamp")
        ts = float(stamp) / 1000 if stamp else time.time()  # CLOB timestamps are in ms
    return OrderBook(
        str(raw.get("asset_id", "")),
        ts,
        [PriceLevel(float(lv["price"]), float(lv["size"])) for lv in raw.get("bids", [])],
        [PriceLevel(float(lv["price"]), float(lv["size"])) for lv in raw.get("asks", [])],
    )


def parse_price_history(raw: dict, token_id: str) -> list[PriceEvent]:
    return [PriceEvent(float(pt["t"]), token_id, float(pt["p"])) for pt in raw.get("history", [])]


# --------------------------------------------------------------------------- client


class PolymarketClient:
    def __init__(
        self,
        gamma_url: str = GAMMA_URL,
        clob_url: str = CLOB_URL,
        timeout: float = 15.0,
        session: Any = None,
    ) -> None:
        import requests  # imported lazily so offline use needs no network deps

        self.gamma_url = gamma_url.rstrip("/")
        self.clob_url = clob_url.rstrip("/")
        self.timeout = timeout
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = "polysim/0.1 (read-only research; paper trading only)"
        self.session = session

    def _get(self, base: str, path: str, params: dict | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self.session.get(f"{base}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # ---- Gamma

    def list_markets_raw(
        self,
        limit: int = 20,
        offset: int = 0,
        active: bool | None = True,
        closed: bool | None = False,
        **filters: Any,
    ) -> list[dict]:
        params = {"limit": limit, "offset": offset, **filters}
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()
        return self._get(self.gamma_url, "/markets", params)

    def list_markets(self, **kwargs: Any) -> list[Market]:
        out = []
        for raw in self.list_markets_raw(**kwargs):
            try:
                out.append(parse_gamma_market(raw))
            except (ValueError, KeyError, json.JSONDecodeError):
                continue  # skip malformed / non-CLOB markets
        return out

    def get_market_raw(self, slug: str) -> dict:
        res = self._get(self.gamma_url, "/markets", {"slug": slug})
        if not res:
            raise LookupError(f"no market with slug {slug!r}")
        return res[0]

    def get_market(self, slug: str) -> Market:
        return parse_gamma_market(self.get_market_raw(slug))

    def get_markets_by_condition_ids(self, condition_ids: Iterable[str]) -> list[Market]:
        """Markets by their condition ids (open or closed), in batches of 20."""
        ids, out = list(condition_ids), []
        for i in range(0, len(ids), 20):
            batch = ids[i : i + 20]
            raw = self._get(self.gamma_url, "/markets", {"condition_ids": batch, "limit": len(batch)})
            for r in raw:
                try:
                    out.append(parse_gamma_market(r))
                except (ValueError, KeyError, json.JSONDecodeError):
                    continue
        return out

    def get_event_raw(self, slug: str) -> dict:
        """A Gamma event (e.g. a date or price ladder) with all its markets and rules text."""
        res = self._get(self.gamma_url, "/events", {"slug": slug})
        if not res:
            raise LookupError(f"no event with slug {slug!r}")
        return res[0]

    # ---- CLOB

    def get_order_book(self, token_id: str) -> OrderBook:
        return parse_book(self._get(self.clob_url, "/book", {"token_id": token_id}))

    def get_order_books(self, token_ids: Iterable[str]) -> list[OrderBook]:
        return [self.get_order_book(t) for t in token_ids]

    def get_price_history(
        self,
        token_id: str,
        *,
        interval: str | None = "max",
        start_ts: int | None = None,
        end_ts: int | None = None,
        fidelity: int | None = 60,
    ) -> list[PriceEvent]:
        """Price history for a token. `interval` is one of 1h/6h/1d/1w/1m/max, or pass
        start_ts/end_ts instead. `fidelity` is the resolution in minutes."""
        if start_ts is not None or end_ts is not None:
            interval = None
        raw = self._get(
            self.clob_url,
            "/prices-history",
            {
                "market": token_id,
                "interval": interval,
                "startTs": start_ts,
                "endTs": end_ts,
                "fidelity": fidelity,
            },
        )
        return parse_price_history(raw, token_id)
