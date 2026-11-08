"""The arbitrage board (/board) and the return calculator dialog. Design: docs/DESIGN.md. Data: frame/board.py.

Everything shown is OUR scan, priced by OUR core. Nothing is fetched from another scanner here."""
from __future__ import annotations

import time

from nicegui import ui

from frame import arbtypes, board, calc, sports
from . import style
from .style import badge, cents, esc, money, pct, stat_tile, venue_chip

STAGES = (("price_only", "Price-only (mid)"), ("at_asks", "At best asks"), ("after_fees", "After fees & depth"),
          ("after_risk", "After rule risk"))
CALC_VENUES = {k: v.label for k, v in calc.FEES.items()}
POLY_CAT = {"economics": "economics", "politics": "politics", "sports": "sports", "crypto": "crypto", "culture": "culture",
            "climate_weather": "weather", "science_tech": "tech", "health": "other", "other": "other"}


def _venue_key(v: str) -> str:
    return v if v in calc.FEES else "manual"


# ------------------------------------------------------------------ calculator

def open_calculator(row: board.Row | None = None, host=None, state: dict | None = None) -> None:
    """A modal calculator. With a row, prices, venues and the core's order-book levels are prefilled.
    `host` = a page-level element to build the dialog in (so the board's auto-refresh cannot destroy it);
    `state['dialog']` is set while it is open so the refresh can pause."""
    if host is None:
        host = ui.element("div")
    host.clear()
    if row and row.legs:
        legs = []
        for q, lv in zip(row.legs, row.levels or [[]] * len(row.legs)):
            v = _venue_key(q["venue"])
            legs.append({"venue": v, "side": q["side"], "c": round((q["price"] or 0.5) * 100, 1),
                         "param": calc.POLYMARKET_K.get(POLY_CAT.get(row.category, "other")) if v == "polymarket" else None,
                         "book": [(p, s) for p, s in lv]})
        days = max(0.1, round(row.days, 1)) if row.days else 7.0
        title = row.title
    else:
        legs = [{"venue": "polymarket", "side": "YES", "c": 45.0, "param": 0.05, "book": []},
                {"venue": "novig", "side": "NO", "c": 50.0, "param": 0.06, "book": []}]
        days, title = 7.0, "Calculator"
    S = {"mode": "capital", "size": 1000.0, "days": days, "risk": 0, "payout": (row.floor if row and row.legs else 1.0)}

    def build_legs():
        out = []
        for l in legs:
            out.append(calc.Leg(l["venue"], l["side"], max(0.001, min(0.999, (l["c"] or 0) / 100)), l["param"],
                                [tuple(b) for b in l["book"]]))
        return out

    with host, ui.dialog() as dlg, ui.card().classes("fa-calc").style("min-width:min(920px,94vw);max-width:96vw;max-height:92vh;overflow:auto"):
        if state is not None:
            state["dialog"] = True
            dlg.on("hide", lambda: (state.update(dialog=False), host.clear()))
        with ui.row().classes("items-center w-full"):
            ui.label("Return calculator").classes("fa-h1").style("font-size:22px")
            ui.space()
            ui.button(icon="close", on_click=dlg.close).props("flat round dense")
        ui.label(title).classes("fa-sub")
        pay = S["payout"]
        ui.label(f"Buy one of each leg: the basket is guaranteed to pay {money(pay)} at resolution"
                 + (" (the core's floor for this structure)" if row and row.legs else "")
                 + ". Edit any number; results update as you type. Fee formulas are each venue's published ones."
                 ).classes("fa-sub")

        with ui.row().classes("w-full gap-3 items-end"):
            for i, l in enumerate(legs):
                with ui.column().classes("gap-1").style("min-width:210px;flex:1"):
                    ui.label(f"Leg {i + 1}").classes("fa-sub")
                    ui.select(CALC_VENUES, value=l["venue"], label="Venue",
                              on_change=lambda e, l=l: (l.update(venue=e.value), res.refresh())).props("dense outlined")
                    with ui.row().classes("gap-2 w-full no-wrap"):
                        ui.select(["YES", "NO"], value=l["side"], label="Side",
                                  on_change=lambda e, l=l: l.update(side=e.value)).props("dense outlined").style("width:90px")
                        ui.number("Price (¢)", value=l["c"], min=0.1, max=99.9, step=0.5, format="%.1f",
                                  on_change=lambda e, l=l: (l.update(c=e.value or 0, book=[]), res.refresh())).props("dense outlined")
            with ui.column().classes("gap-1").style("min-width:210px;flex:1"):
                ui.label("Size and time").classes("fa-sub")
                ui.toggle({"capital": "Capital ($)", "contracts": "Contracts"}, value=S["mode"],
                          on_change=lambda e: (S.update(mode=e.value), res.refresh())).props("dense no-caps")
                ui.number("Size", value=S["size"], min=1, step=100,
                          on_change=lambda e: (S.update(size=e.value or 0), res.refresh())).props("dense outlined")
                ui.number("Days to resolution", value=S["days"], min=0.1, step=1,
                          on_change=lambda e: (S.update(days=e.value or 1), res.refresh())).props("dense outlined")
                ui.label("Hold back for settlement risk").classes("fa-sub")
                ui.slider(min=0, max=50, step=5, value=0, on_change=lambda e: (S.update(risk=e.value), res.refresh())
                          ).props("label-always dense").style("margin-top:22px")

        @ui.refreshable
        def res():
            L = build_legs()
            q = calc.capital_to_contracts(L, S["size"]) if S["mode"] == "capital" else S["size"]
            r = calc.evaluate(L, q, payout_per_pair=S["payout"], days=S["days"], resolution_risk=S["risk"] / 100)
            tone = "go" if r.executable else "stop"
            cells = [("Net profit", money(r.profit), tone), ("Return on cost", pct(r.roi, 2), tone),
                     ("Annualised", "—" if r.apy_simple is None else pct(r.apy_simple, 0),
                      ""), ("Cost incl. fees", money(r.cost), ""), ("Fees", money(r.fees + r.exit_fees), ""),
                     ("Break-even total", cents(r.breakeven_total, 1), ""), ("Slippage room / leg", cents(r.slip_per_leg, 2), ""),
                     ("After settlement risk", money(r.risk_adjusted_profit), "")]
            ui.html('<div class="res">' + "".join(
                f'<div class="fa-tile"><div class="k">{esc(k)}</div><div class="v fa-num" style="color:var(--{t if t else "ink"})">{esc(v)}</div></div>'
                for k, v, t in cells) + '</div>', sanitize=False).classes("w-full")
            ui.html(f'<div class="fa-sub">{r.contracts:,.1f} baskets · total price {cents(sum(l.price for l in L), 1)} · '
                    f'compounded annual {"—" if r.apy_compound is None else pct(r.apy_compound, 0)} · '
                    + esc(r.note) + '</div>', sanitize=False)
            rows = "".join(f'<tr><td>{esc(x["venue"])} {esc(x["side"])}</td><td class="fa-num">{cents(x["price"], 1)}</td>'
                           f'<td class="fa-num">{money(x["fee"])}</td><td class="fa-sub">{esc(x["formula"])}</td></tr>' for x in r.legs)
            ui.html('<table style="width:100%;font-size:13px"><tr><th align=left>Leg</th><th align=left>Price</th>'
                    '<th align=left>Fee</th><th align=left>Formula</th></tr>' + rows + '</table>', sanitize=False)
            # size curve: how the return falls as you take more of the book
            sizes = [max(1.0, q * f) for f in (0.05, 0.1, 0.25, 0.5, 1, 2, 4)]
            curve = calc.size_curve(L, sizes, S["days"], S["payout"])
            uniq = []                                  # a book can run out: show each reachable size once
            for c in curve:
                if not uniq or abs(c["contracts"] - uniq[-1]["contracts"]) > 1e-6:
                    uniq.append(c)
            curve = uniq
            has_depth = all(l.book for l in L)
            if len(curve) > 1:
                xs = [c["contracts"] for c in curve]
                ys = [c["profit"] for c in curve]
                lo, hi = min(min(ys), 0), max(max(ys), 1e-9)
                pts = " ".join(f"{(x - xs[0]) / max(1e-9, xs[-1] - xs[0]) * 300:.1f},{58 - (y - lo) / max(1e-9, hi - lo) * 52:.1f}"
                               for x, y in zip(xs, ys))
                zero = 58 - (0 - lo) / max(1e-9, hi - lo) * 52
                note = " (walking the core's order book)" if has_depth else " (flat prices: an upper bound; real books get worse with size)"
                ui.html(f'<div class="fa-sub">Profit by size{esc(note)}</div>'
                        f'<svg class="fa-curve" viewBox="0 0 300 64" preserveAspectRatio="none">'
                        f'<line x1="0" x2="300" y1="{zero:.1f}" y2="{zero:.1f}" stroke="var(--line)" stroke-dasharray="3 3"/>'
                        f'<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="{pts}"/></svg>'
                        f'<div class="fa-sub fa-num">' + " · ".join(f"{c['contracts']:,.0f} → {money(c['profit'])} ({pct(c['roi'], 1)})" for c in curve) + '</div>',
                        sanitize=False)
            if row is not None and row.legs:
                ok = row.real
                ui.html(f'<div class="fa-claim" style="border-color:var(--accent);color:var(--ink);background:var(--flat-bg)">'
                        f'<b>Our core on live books:</b> {badge(row.verdict, row.kind == "screen")} '
                        f'<span class="fa-num">{money(row.profit)} on {money(row.cost)} ({pct(row.roi, 2)})</span> over the book it can fill. '
                        f'{"The calculator above uses flat prices, so it can overstate what the book allows." if ok else "The core finds nothing executable after fees and depth."}</div>',
                        sanitize=False)
        res()
    dlg.open()


# ------------------------------------------------------------------ the board

def _leg_html(q: dict) -> str:
    link = f' <a href="{esc(q["url"])}" target="_blank" rel="noopener noreferrer" title="Open on {esc(q["venue"])}">↗</a>' \
        if str(q.get("url", "")).startswith("https://") else ""
    return (f'<span class="fa-leg"><b>BUY {esc(q["side"])}</b>{venue_chip(q["venue"])}'
            f'<span class="fa-num">{cents(q["price"], 1)}</span>{link}</span>')


def _card_html(r: board.Row) -> str:
    cls = {"worth_a_look": "go", "look_closely": "care", "blocked": "stop"}.get(r.verdict, "")
    chips = (f'<span class="fa-chip type">{esc(arbtypes.label(r.arb_type))}</span>'
             f'<span class="fa-chip type">{esc(r.category)}</span>'
             + (f'<span class="fa-chip type">{esc(sports.LEAGUES.get(r.league, r.league))}</span>' if r.league else "")
             + (' <span class="fa-badge stop">● LIVE game</span>' if r.live else "")
             + "".join(venue_chip(v) for v in r.venues))
    legs = ""
    if r.legs:
        total = sum((q["price"] or 0) for q in r.legs)
        legs = ('<div class="fa-legs">' + ' <span class="fa-plus">+</span> '.join(_leg_html(q) for q in r.legs)
                + f'<span class="fa-tot">total <span class="fa-num">{cents(total, 1)}</span> → pays $1.00</span></div>')
    elif r.n_outcomes > 1:
        legs = f'<div class="fa-tot">Buy all {r.n_outcomes} outcomes of one event; they cannot all win.</div>'
    top = max([abs(r.stages[k]) for k, _ in STAGES] + [0.01])
    bars = '<div class="fa-stages">' + "".join(
        f'<div class="fa-stage{" neg" if r.stages[k] <= 0 else ""}"><span>{esc(label)}</span>'
        f'<span class="t"><i style="width:{max(0, r.stages[k]) / top * 100:.0f}%"></i></span><b class="fa-num">{cents(r.stages[k], 1)}</b></div>'
        for k, label in STAGES) + '</div>'
    if r.real:
        metrics = ('<div class="fa-metrics">'
                   f'<div class="fa-metric"><div class="k">Verified return</div><div class="v fa-num">{pct(r.roi, 2)}</div></div>'
                   f'<div class="fa-metric"><div class="k">Profit</div><div class="v fa-num">{money(r.profit)}</div></div>'
                   f'<div class="fa-metric"><div class="k">On capital</div><div class="v fa-num">{money(r.cost)}</div></div>'
                   f'<div class="fa-metric"><div class="k">Annualised</div><div class="v fa-num">{pct(r.apy, 0)}</div></div>'
                   f'<div class="fa-metric"><div class="k">Resolves in</div><div class="v fa-num">{r.days:.1f} d</div></div></div>')
    else:
        metrics = (f'<div class="fa-flags"><b>Verified return: none.</b> {esc(r.headline)}'
                   + (' A price-only scanner would show this as a gap.' if r.gap_before_costs else '') + '</div>')
    claim = ""
    if r.claim:
        claim = (f'<div class="fa-claim">CLAIMED by {esc(r.claim.get("source"))}: <b class="fa-num">{esc(r.claim.get("roi_pct"))}%</b> '
                 f'return, max wager ${esc(r.claim.get("max_wager_usd"))} · outlined = not verified by us</div>')
    flags = f'<div class="fa-flags">Flags: {esc(", ".join(r.flags))}</div>' if r.flags else ""
    return (f'<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center">{chips}<span style="flex:1"></span>'
            f'{badge(r.verdict, r.kind == "screen")}'
            f'<span class="fa-chip" title="{esc(" · ".join(r.why))}">priority {r.score:.0f}</span>'
            f'<span class="fa-chip">{esc(board.age_text(r.at))} old</span></div>'
            f'<div class="fa-title" style="margin-top:6px">{esc(r.title)}</div>{legs}{bars}{metrics}{claim}{flags}')


def register(get_p, scan_cb, busy_cb, send_cb) -> None:
    """Add the /board page. get_p() -> the Pipeline; scan_cb() -> coroutine running a scan; busy_cb = the app's
    'working' dialog; send_cb(row) -> coroutine that sends a screened pair to the checker (Gemini + you)."""

    @ui.page("/board")
    def board_page():
        P = get_p()
        style.header("/board")
        F = {"venues": set(), "kind": "all", "show": "gaps", "type": "all", "cat": "all", "sport": "all",
             "min_roi": 0.0, "min_profit": 0.0, "q": "", "sort": "Best first (recommended)", "limit": 24,
             "dialog": False}
        host = ui.element("div")            # the calculator lives here, outside every refreshable
        F["host"] = host

        with ui.column().classes("fa-wrap gap-3"):
            with ui.row().classes("w-full items-center gap-3"):
                with ui.column().classes("gap-0"):
                    ui.label("Arbitrage board").classes("fa-h1")
                    ui.label("Our own scan, priced by our own core on live order books. Claims are never mixed with "
                             "verified numbers.").classes("fa-sub")
                ui.space()
                ui.button("Calculator", icon="calculate", on_click=lambda: open_calculator(None, host, F)).props("outline")
                scan_btn = ui.button("Scan now", icon="radar")

            tiles = ui.refreshable(lambda: _tiles(P))
            tiles()

            with ui.element("div").classes("fa-bar"):
                with ui.row().classes("w-full items-center gap-3"):
                    ui.input(placeholder="Search events…", on_change=lambda e: (F.update(q=e.value or "", limit=24), body.refresh())
                             ).props("dense outlined clearable").style("min-width:200px")
                    ui.select({"all": "All rows", "verified": "Verified by core", "screen": "Screens (rules not compared)",
                               "structure": "Sets & Yes + No"}, value="all", label="Kind",
                              on_change=lambda e: (F.update(kind=e.value, limit=24), body.refresh())).props("dense outlined").classes("w-48")
                    ui.select({"gaps": "Gaps only (what a price scanner would flag)", "real": "Survives costs only",
                               "all": "Everything checked"}, value="gaps", label="Show",
                              on_change=lambda e: (F.update(show=e.value, limit=24), body.refresh())).props("dense outlined").classes("w-64")
                    ui.select({"all": "Any type", **{t: arbtypes.label(t) for t in arbtypes.ORDER}}, value="all", label="Type",
                              on_change=lambda e: (F.update(type=e.value, limit=24), body.refresh())).props("dense outlined").classes("w-44")
                    ui.select({"all": "Any sport", **sports.LEAGUES}, value="all", label="Sport",
                              on_change=lambda e: (F.update(sport=e.value, limit=24), body.refresh())).props("dense outlined").classes("w-40")
                    ui.select(list(board.SORTS), value=F["sort"], label="Sort",
                              on_change=lambda e: (F.update(sort=e.value), body.refresh())).props("dense outlined").classes("w-56")
                with ui.row().classes("w-full items-center gap-4 mt-2"):
                    ui.label("Venues").classes("fa-sub")
                    for v in ("polymarket", "kalshi", "novig"):
                        ui.checkbox(v.title(), value=True,
                                    on_change=lambda e, v=v: (F["venues"].discard(v) if e.value else F["venues"].add(v),
                                                              body.refresh())).props("dense")
                    ui.space()
                    ui.label("Min verified return").classes("fa-sub")
                    ui.slider(min=0, max=10, step=0.5, value=0,
                              on_change=lambda e: (F.update(min_roi=e.value / 100), body.refresh())).props("label dense").style("width:150px")
                    ui.number("Min profit $", value=0, min=0, step=1,
                              on_change=lambda e: (F.update(min_profit=e.value or 0), body.refresh())).props("dense outlined").style("width:120px")

            body = ui.refreshable(lambda: _body(P, F, body))
            body()
            _how_it_works()

        async def go_scan():
            scan_btn.disable()
            try:
                async with busy_cb("Scanning live markets and pricing every pair with the core"):
                    await scan_cb()
            finally:
                scan_btn.enable()
            tiles.refresh()
            body.refresh()

        scan_btn.on_click(go_scan)
        # live refresh every 15 s, paused while the calculator is open (and it is built outside the list anyway)
        ui.timer(15, lambda: None if F["dialog"] else (tiles.refresh(), body.refresh()))

    def _filtered(P, F) -> list[board.Row]:
        out = []
        for r in board.rows(P):
            if F["venues"] and set(r.venues) & F["venues"]:
                continue
            if F["kind"] != "all" and r.kind != F["kind"]:
                continue
            if F["show"] == "real" and not r.real:
                continue
            if F["show"] == "gaps" and not (r.real or r.gap_before_costs):
                continue
            if F["type"] != "all" and r.arb_type != F["type"]:
                continue
            if F["sport"] != "all" and r.league != F["sport"]:
                continue
            if F["min_roi"] and not (r.real and r.roi >= F["min_roi"]):
                continue
            if F["min_profit"] and not (r.real and r.profit >= F["min_profit"]):
                continue
            if F["q"] and F["q"].lower() not in (r.title + " " + r.category + " " + " ".join(r.venues)).lower():
                continue
            out.append(r)
        out.sort(key=board.SORTS[F["sort"]])
        return out

    def _tiles(P) -> None:
        allr = board.rows(P)
        real = [r for r in allr if r.real and r.kind != "screen"]
        screens = [r for r in allr if r.real and r.kind == "screen"]
        gaps = [r for r in allr if r.gap_before_costs]
        best = max((r.roi for r in real), default=None)
        s = P.last_scan
        ui.html('<div class="fa-tiles">'
                + stat_tile("Verified by our core", str(len(real)), "survive fees and depth")
                + stat_tile("Screens worth a look", str(len(screens)), "rules not compared yet")
                + stat_tile("Price gaps before costs", str(len(gaps)), f"{sum(1 for r in gaps if r.real)} survive costs")
                + stat_tile("Best verified return", "—" if best is None else pct(best, 2), "after fees, depth")
                + stat_tile("Rows checked", str(len(allr)), (f"last scan {s.markets:,} markets" if s else "no scan yet"))
                + '</div>', sanitize=False).classes("w-full")

    def _body(P, F, body) -> None:
        rows = _filtered(P, F)
        if not rows:
            with ui.element("div").classes("fa-empty"):
                ui.label("Nothing to show with these filters." if board.rows(P) else
                         "Nothing priced yet. Press “Scan now”: it reads live markets and prices every pair with the core.")
            return
        ui.label(f"{len(rows)} rows").classes("fa-sub")
        for r in rows[:F["limit"]]:
            with ui.element("article").classes(f"fa-card {({'worth_a_look': 'go', 'look_closely': 'care', 'blocked': 'stop'}).get(r.verdict, '')}"):
                ui.html(_card_html(r), sanitize=False)
                with ui.row().classes("gap-2 mt-1"):
                    ui.button("Calculate returns", icon="calculate",
                              on_click=lambda r=r: open_calculator(r, F["host"], F)).props("dense outline")
                    if r.kind == "screen" and r.pair:
                        async def send(r=r):
                            await send_cb(r)
                        ui.button("Send to checker", icon="fact_check", on_click=send).props("dense").tooltip(
                            "Gemini compares the rules, you confirm, then the core prices it")
                    if r.href:
                        ui.button("Open detail", icon="open_in_new", on_click=lambda r=r: ui.navigate.to(r.href)).props("dense flat")
        if len(rows) > F["limit"]:
            ui.button(f"Show {min(24, len(rows) - F['limit'])} more", on_click=lambda: (F.update(limit=F["limit"] + 24), body.refresh())).props("flat")

    def _how_it_works() -> None:
        with ui.expansion("How the numbers work", icon="functions").classes("w-full").style("background:var(--card);border:1px solid var(--line);border-radius:12px"):
            ui.markdown(
                "**The four stages** (per share, in cents) show where a gap disappears:\n\n"
                "1. *Price-only (mid)*: what a scanner that compares mid prices sees.\n"
                "2. *At best asks*: what you would actually pay for one share of each leg.\n"
                "3. *After fees & depth*: the core walks the real order book level by level, applying each venue's fee, "
                "and stops when another share no longer profits.\n"
                "4. *After rule risk*: after the haircut for differences in how the venues settle.\n\n"
                "**Return** = net profit ÷ cost, over the book the core can fill. **Annualised** = return × 365 ÷ days. "
                "A *verified* row was confirmed by a human after Gemini compared the rules; a *screen* was matched by code "
                "(same match, same team, same kickoff) and priced on live books, but its rules have not been compared.\n\n"
                "**Fee formulas** (the calculator uses these; the core uses its own for the live numbers):")
            ui.html('<table style="font-size:13px;width:100%"><tr><th align=left>Venue</th><th align=left>Taker fee</th><th align=left>Note</th></tr>'
                    + "".join(f'<tr><td>{esc(f.label)}</td><td>{esc(f.formula)}</td><td class="fa-sub">{esc(f.note)}</td></tr>'
                              for f in calc.FEES.values()) + '</table>', sanitize=False)
