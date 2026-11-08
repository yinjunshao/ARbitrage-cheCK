"""NiceGUI app: Check · Candidates · Suggestions · Result · Log.  Paper only. No orders are placed.

    python -m app_frame.main                    # live Polymarket via arbcheck's read-only client + live Gemini
    python -m app_frame.main --kalshi-member    # also Kalshi (members only, on your own computer)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nicegui import ui  # noqa: E402

from frame import arbtypes  # noqa: E402
from frame.pipeline import Pipeline, build  # noqa: E402
from frame.schemas import Market  # noqa: E402

RELATIONS = ["same_event", "a_implies_b", "b_implies_a", "mutually_exclusive", "exhaustive_set"]
USER = "analyst"
P: Pipeline | None = None
SCANNING = False   # one scan at a time, shared by every open tab
MODE = "live"
VERDICT_STYLE = {"worth_a_look": "bg-green-100 text-green-900", "look_closely": "bg-amber-100 text-amber-900",
                 "blocked": "bg-red-100 text-red-900", "nothing_to_do": "bg-slate-100 text-slate-800"}


def cents(x: float) -> str:
    return f"{x * 100:.1f}¢"


def banner():
    ui.query("body").style("background:#f8fafc;color:#0f172a")
    with ui.header().classes("items-center bg-slate-800"):
        ui.label("False-Arb Checker").classes("text-lg font-bold")
        for path, name in (("/", "Check"), ("/candidates", "Candidates"), ("/suggestions", "Suggestions"), ("/log", "Log")):
            ui.link(name, path).classes("text-white ml-4")
    with ui.row().classes("w-full bg-amber-200 text-black p-2 gap-4"):
        ui.label("PAPER ONLY. No orders are placed.").classes("font-bold")
        if P.adapter.name == "fake":
            ui.label("FAKE CORE: placeholder numbers.").classes("text-red-700 font-bold")
        if getattr(P.adapter, "demo", False):
            ui.label("TEST MARKETS: invented.").classes("text-red-700 font-bold")
        ui.label(f"LIVE data · Polymarket on · Kalshi {'ON' if P.cfg.kalshi_member else 'off (set FRAME_KALSHI_MEMBER=1 in .env)'} · "
                 f"LLM {P.cfg.judge_model} · core {P.adapter.name}")


def market_card(m: Market, quotes: list[str]):
    with ui.card().classes("w-[48%]"):
        ui.label(f"{m.venue.upper()} · {m.title}").classes("font-bold")
        ui.label(f"closes {m.close_time_utc or '?'} · Yes bid/ask {m.yes_bid}/{m.yes_ask}").classes("text-xs text-gray-500")
        text = m.rules_text or "(no rules text)"
        for q in quotes:  # highlight verified quotes
            if q and q in text:
                text = text.replace(q, f"**{q}**")
        ui.markdown(text).classes("text-sm")


@ui.page("/")
def check_page():
    banner()
    with ui.column().classes("p-4 w-full max-w-5xl"):
        ui.label("Proactive scan of live markets").classes("text-xl")
        ui.label(f"Pulls the top {getattr(P.adapter, 'max_events', '?')} open events by 24h volume, applies the "
                 f"suggestion rules (crypto only with the same coin; same category only), and lets Gemini judge up to "
                 f"{P.cfg.max_judge_per_scan} new pairs. You confirm before the core prices anything.").classes("text-sm")
        with ui.expansion("What kinds of arbitrage it looks for", icon="rule").classes("w-full border rounded"):
            for t in arbtypes.ORDER:
                info = arbtypes.TYPES[t]
                ui.markdown(f"**{info['label']}**: {info['what']}  \n_Main risk: {info['risk']}_")
            ui.label("Same-market and multi-outcome checks need no LLM; cross-platform and logical pairs are judged "
                     "by Gemini and confirmed by you before the core prices them.").classes("text-xs text-gray-500")
        status = ui.label("")
        if P.last_scan:
            s = P.last_scan
            status.set_text(f"Last scan {s.scan}: {s.markets} markets, {s.candidates} candidates, {s.rule_refused} refused "
                            f"by rules, {s.judged} judged, {s.surfaced} for review, {s.deferred} deferred.")

        progress = ui.label("").classes("text-sm text-blue-700")
        spinner = ui.spinner(size="md")
        spinner.visible = False
        # Live progress: the scan runs in a worker thread; this timer shows where it is up to.
        ui.timer(0.5, lambda: progress.set_text(P.status if SCANNING else progress.text))

        async def scan():
            global SCANNING
            if SCANNING:
                ui.notify("A scan is already running.", type="warning")
                return
            SCANNING, spinner.visible = True, True
            btn.disable()
            status.set_text("Scanning live markets (usually 1–3 minutes).")
            from nicegui import run
            try:
                s = await run.io_bound(P.scan, ["polymarket", "kalshi"])
            except Exception as e:
                ui.notify(f"Scan failed: {type(e).__name__}: {e}", type="negative", multi_line=True)
                return
            finally:
                SCANNING, spinner.visible = False, False
                btn.enable()
            for n in s.notes:
                ui.notify(n, type="info", multi_line=True)
            ui.navigate.to("/candidates")

        btn = ui.button("Scan venues for related markets", on_click=scan)
        ui.separator()
        ui.label("Check a pair: paste two links or describe two events").classes("text-xl")
        l1, l2 = ui.input("Event A (link or words)").classes("w-full"), ui.input("Event B (link or words)").classes("w-full")
        ui.label("Polymarket, Kalshi (members only) and Manifold links. A link to a multi-outcome event checks each "
                 "outcome against its closest match on the other side.").classes("text-xs text-gray-500")
        manual = {}
        with ui.expansion("Or enter both markets by hand"):
            with ui.row().classes("w-full"):
                for side in ("A", "B"):
                    with ui.column().classes("w-[48%]"):
                        manual[side] = {
                            "venue": ui.select(["polymarket", "kalshi", "manual"], value="manual", label=f"{side} venue"),
                            "title": ui.input(f"{side} question"), "rules": ui.textarea(f"{side} rules text"),
                            "close": ui.input(f"{side} close (ISO UTC, e.g. 2027-03-31T23:59:00Z)"),
                            "bid": ui.number(f"{side} Yes bid", format="%.3f"), "ask": ui.number(f"{side} Yes ask", format="%.3f"),
                            "depth": ui.number(f"{side} shares at the ask", value=100),
                        }

        async def run_check():
            from nicegui import run
            if l1.value and l2.value:
                async with busy("Reading both links and asking Gemini"):
                    cases, notes = await run.io_bound(P.check_lines, l1.value, l2.value)
                if not cases:
                    ui.notify(" ".join(notes), type="warning", multi_line=True)
                    return
            else:
                def mk(s):
                    f = manual[s]
                    return Market(venue=f["venue"].value, id=f"manual-{s}-{abs(hash((f['title'].value, f['close'].value)))}",
                                  title=f["title"].value or "", rules_text=f["rules"].value or "",
                                  close_time_utc=f["close"].value or None, yes_ask=f["ask"].value,
                                  yes_bid=f["bid"].value, depth=f["depth"].value)
                a, b = mk("A"), mk("B")
                if not a.title or not b.title:
                    ui.notify("Enter both questions (or two links).", type="warning")
                    return
                async with busy("Asking Gemini about this pair"):
                    case = await run.io_bound(P.check_pair, a, b)
            ui.navigate.to("/candidates")

        ui.button("Check this pair", on_click=run_check)


class busy:
    """A 'working' dialog for slow steps: spinner, what the pipeline is doing right now, and elapsed seconds.
    Usage: `async with busy("Pricing with the core"): await run.io_bound(...)`."""

    def __init__(self, text: str):
        self.text = text

    async def __aenter__(self):
        self.t0 = time.time()
        P._say(self.text)
        with ui.dialog().props("persistent") as self.dlg, ui.card().classes("items-center gap-2 p-6 min-w-[22rem]"):
            ui.spinner(size="xl")
            ui.label(self.text).classes("font-bold text-center")
            self.detail = ui.label("").classes("text-sm text-gray-600 text-center")
        self.dlg.open()
        self.timer = ui.timer(0.4, lambda: self.detail.set_text(f"{P.status}  ({time.time() - self.t0:.0f}s)"))
        return self

    async def __aexit__(self, *exc):
        self.timer.cancel()
        self.dlg.close()
        self.dlg.delete()


CONF_RANK = {"high": 0, "medium": 1, "low": 2}
VERDICT_RANK = {"worth_a_look": 0, "look_closely": 1, "blocked": 2, "nothing_to_do": 3}
CAND_SORTS = {
    "Confidence (high first)": lambda c: (CONF_RANK.get(c.judged.verdict.confidence, 3), -c.similarity),
    "Most similar": lambda c: -c.similarity,
    "Closing soonest": lambda c: min(c.judged.a.close_time_utc or "9", c.judged.b.close_time_utc or "9"),
    "Category": lambda c: (c.category, CONF_RANK.get(c.judged.verdict.confidence, 3)),
    "Arbitrage type": lambda c: (arbtypes.ORDER.index(c.arb_type) if c.arb_type in arbtypes.ORDER else 9,
                                 CONF_RANK.get(c.judged.verdict.confidence, 3)),
}
FIND_SORTS = {
    "Best verdict first": lambda f: (VERDICT_RANK[f.core.verdict], -f.core.avg_edge),
    "Biggest edge after costs": lambda f: -f.core.avg_edge,
    "Highest price-only gap": lambda f: -f.core.naive_edge,
    "Category": lambda f: (f.category, VERDICT_RANK[f.core.verdict]),
}


def type_chip(t: str):
    info = arbtypes.TYPES.get(t, {})
    with ui.row().classes("items-center gap-1"):
        ui.badge(arbtypes.label(t)).props("color=indigo")
        ui.tooltip(info.get("what", ""))


@ui.page("/candidates")
def candidates_page():
    banner()
    f = {"type": "all", "cat": "all", "conf": "any", "how": "any", "sort": "Confidence (high first)",
         "fsort": "Best verdict first"}
    with ui.column().classes("p-4 w-full"):
        ui.label("Opportunities").classes("text-2xl font-bold")
        cats = sorted({c.category for c in P.cases.values()} | {x.category for x in P.findings.values()})
        types = ["all"] + [t for t in arbtypes.ORDER]
        with ui.row().classes("items-end gap-4 w-full"):
            ui.select({t: ("All types" if t == "all" else arbtypes.label(t)) for t in types}, value="all", label="Type",
                      on_change=lambda e: (f.update(type=e.value), listing.refresh())).classes("w-52")
            ui.select(["all"] + cats, value="all", label="Category",
                      on_change=lambda e: (f.update(cat=e.value), listing.refresh())).classes("w-44")
            ui.select({"any": "Any confidence", "medium": "Medium or high", "high": "High only"}, value="any",
                      label="Confidence", on_change=lambda e: (f.update(conf=e.value), listing.refresh())).classes("w-44")
            ui.select({"any": "Any source", "cross_venue": "Cross-venue", "within_event": "Within one event",
                       "cross_event": "Across events", "user": "Typed by you"}, value="any", label="Found by",
                      on_change=lambda e: (f.update(how=e.value), listing.refresh())).classes("w-44")
            ui.select(list(CAND_SORTS), value=f["sort"], label="Sort suggestions by",
                      on_change=lambda e: (f.update(sort=e.value), listing.refresh())).classes("w-56")
            ui.select(list(FIND_SORTS), value=f["fsort"], label="Sort priced results by",
                      on_change=lambda e: (f.update(fsort=e.value), listing.refresh())).classes("w-56")
        listing = ui.refreshable(lambda: _listing(f))
        listing()


def _keep(c_type: str, category: str, f: dict) -> bool:
    return f["type"] in ("all", c_type) and f["cat"] in ("all", category)


def _listing(f: dict):
    # ---- priced from structure alone: no LLM, nothing to confirm -------------------------------------------
    finds = sorted((x for x in P.findings.values() if _keep(x.arb_type, x.category, f)), key=FIND_SORTS[f["fsort"]])
    ui.label(f"Priced by the core from structure alone ({len(finds)})").classes("text-lg font-bold mt-2")
    ui.label("Outcome sets the venue declares exclusive, and Yes + No of single markets. No LLM judgement is needed, "
             "so the core prices them directly.").classes("text-xs text-gray-500")
    if not finds:
        ui.label("None yet. Run a scan on the Check page.").classes("text-sm")
    for x in finds[:60]:
        with ui.card().classes(f"w-full py-2 {VERDICT_STYLE[x.core.verdict]}"):
            with ui.row().classes("items-center w-full gap-3"):
                type_chip(x.arb_type)
                ui.badge(x.category).props("color=grey")
                ui.link(x.title, f"/finding/{x.id}").classes("font-bold")
                ui.label(f"{VERDICT_WORDS[x.core.verdict][0]} · price-only {cents(x.core.naive_edge)} · "
                         f"after costs {cents(x.core.avg_edge)}").classes("text-sm")
    if len(finds) > 60:
        ui.label(f"…and {len(finds) - 60} more. Narrow with the filters above.").classes("text-xs text-gray-500")

    # ---- pairs Gemini judged: a human confirms before the core prices -----------------------------------------
    queue = [c for c in P.review_queue() if _keep(c.arb_type, c.category, f)
             and (f["conf"] == "any" or CONF_RANK.get(c.judged.verdict.confidence, 3) <= CONF_RANK[f["conf"]])
             and (f["how"] == "any" or c.how == f["how"])]
    queue.sort(key=CAND_SORTS[f["sort"]])
    hidden = sum(1 for c in P.cases.values() if c.state == "filtered")
    ui.label(f"Related pairs awaiting your confirmation ({len(queue)})").classes("text-lg font-bold mt-4")
    ui.label(f"{hidden} more were judged unrelated, unclear or low confidence and are hidden.").classes("text-xs text-gray-500")
    done = [c for c in P.cases.values() if c.core is not None and _keep(c.arb_type, c.category, f)]
    if done:
        ui.label("Already priced").classes("font-bold mt-1")
        with ui.row():
            for c in sorted(done, key=lambda c: VERDICT_RANK[c.core.verdict]):
                ui.link(f"{c.judged.a.title[:40]} → {c.core.verdict.replace('_', ' ')}", f"/result/{c.id}").classes("text-sm mr-4")
    for c in queue[:80]:
        _candidate_card(c)
    if len(queue) > 80:
        ui.label(f"…and {len(queue) - 80} more. Narrow with the filters above.").classes("text-xs text-gray-500")


def _candidate_card(c):
    v = c.judged.verdict
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-2"):
            type_chip(c.arb_type)
            ui.badge(f"{c.category}").props("color=grey")
            ui.label(f"{v.relation} · confidence {v.confidence} · {'quotes verified' if c.judged.verified else 'UNVERIFIED'}"
                     f" · found by {c.how}").classes("font-bold")
        if c.rule_note:
            ui.label(f"⚠ Suggestion rule {c.rule_note} (shown because you asked for this pair)").classes(
                "text-orange-700 text-sm")
        ui.label(v.reasoning).classes("text-sm")
        with ui.row().classes("w-full"):
            market_card(c.judged.a, v.evidence_a)
            market_card(c.judged.b, v.evidence_b)
        if c.rules:
            ui.label(f"Rules: {c.rules.diff.overall}" + (" (escalated by verification)" if c.rules.escalated else ""))
            for d in c.rules.diff.differences:
                ok = lambda q: "" if not q or q not in c.rules.unverified_quotes else " ⚠ unverified"
                ui.markdown(f"- **{d.topic} · {d.severity}**: {d.explanation}  \n"
                            f"  A: _{d.quote_a or '(silent)'}_{ok(d.quote_a)}  \n  B: _{d.quote_b or '(silent)'}_{ok(d.quote_b)}")
        with ui.row().classes("items-center"):
            rel = ui.select(RELATIONS, value=v.relation if v.relation in RELATIONS else None, label="Relation")
            budget = ui.number("Max notional ($)", value=300)
            note = ui.input("Note")

            async def confirm(c=c, rel=rel, budget=budget, note=note):
                from nicegui import run
                if not rel.value:
                    ui.notify("Pick a relation or reject.", type="warning")
                    return
                P.confirm(c.id, USER, rel.value, note.value or "")
                async with busy("Pricing with the core: reading live order books"):
                    await run.io_bound(P.price, c.id, float(budget.value or 0))
                ui.navigate.to(f"/result/{c.id}")

            ui.button("Confirm & price with core", on_click=confirm)
            ui.button("Reject", color="red",
                      on_click=lambda c=c, note=note: (P.reject(c.id, USER, note.value or ""), ui.navigate.reload()))


VERDICT_WORDS = {
    "worth_a_look": ("Worth a closer look", "The prices leave a gap that survives fees and the size you could actually trade."),
    "look_closely": ("Possible gap, with risks to check", "There is a gap on paper, but something below could make it disappear."),
    "blocked": ("Do not trade this", "A safety check failed, so the numbers can't be trusted as a risk-free gap."),
    "nothing_to_do": ("No profit here", "After spreads and fees the prices don't leave anything to capture."),
}
RELATION_WORDS = {
    "equivalent": "are the same event", "same_event": "are the same event",
    "a_implies_b": "are linked: if A happens, B must happen too", "b_implies_a": "are linked: if B happens, A must happen too",
    "mutually_exclusive": "can't both happen", "exhaustive_set": "together cover every outcome",
}


def _money(x: float) -> str:
    return f"${x:,.2f}" if abs(x) < 100 else f"${x:,.0f}"


def _plain_facts(core) -> list[str]:
    """The few numbers a person needs, in sentences. Every figure comes from the core result."""
    if core.verdict == "nothing_to_do":
        return [f"Buying every leg at today's asks leaves {cents(core.top_edge)} a share before fees "
                f"and {cents(core.avg_edge)} after fees and depth. That is not a profit."]
    if core.approved_shares <= 0:
        return [f"On paper the gap is {cents(core.top_edge)} a share at the best asks and {cents(core.avg_edge)} after "
                f"fees and depth, but the safety checks approved no size to trade. See the details below."]
    facts = [f"Trading about {core.approved_shares:,.0f} shares would cost {_money(core.approved_cost)} "
             f"and pay back roughly {_money(core.approved_profit)} more."]
    facts.append(f"The money is tied up for about {core.days:.0f} days"
                 + (f", which works out near {core.risk_adj_ann:.0%} a year after allowing for rule risk." if core.risk_adj_ann else "."))
    if core.clipped:
        facts.append("The size is capped by your risk limit, not by the market.")
    return facts


def render_report(core, n, market_lines: list[str], relation_text: str, arb_type: str, relation: str = "",
                  judged_text: str = "", decision=None):
    """One result, short report first, details behind an arrow. Used by pairs (/result) and findings (/finding)."""
    title, gist = VERDICT_WORDS.get(core.verdict, (core.verdict, ""))
    with ui.card().classes(f"w-full {VERDICT_STYLE[core.verdict]}"):
        ui.label(title).classes("text-2xl font-bold")
        ui.label(n.narrative.headline).classes("text-lg")
        ui.label(gist).classes("text-sm")
    with ui.row().classes("items-center gap-2"):
        type_chip(arb_type)
        ui.label(arbtypes.TYPES.get(arb_type, {}).get("what", "")).classes("text-xs text-gray-500")
    for line in market_lines:
        ui.label(line).classes("text-sm")
    if relation_text:
        ui.label(relation_text).classes("text-xs text-gray-500")

    ui.label("In short").classes("text-lg font-bold mt-2")
    for line in _plain_facts(core):
        ui.label(line)
    ui.label("Why the gap exists").classes("font-bold mt-1")
    ui.label(n.narrative.why_gap)
    if n.narrative.what_could_go_wrong:
        ui.label("What could go wrong").classes("font-bold mt-1")
        ui.markdown("\n".join(f"- {x}" for x in n.narrative.what_could_go_wrong[:3]))
    ui.label("Check before approving").classes("font-bold mt-1")
    ui.markdown("\n".join(f"- {x}" for x in n.narrative.human_checks[:4]))
    ui.label(f"Suggested: {str(n.narrative.recommendation).replace('_', ' ')}").classes("font-bold")
    if decision:
        decision()

    with ui.expansion("Detailed report: statistics, checks and workings", icon="analytics").classes("w-full border rounded mt-3"):
        ui.label(f"Core verdict {core.verdict.replace('_', ' ')} ({core.status}) · {core.headline}").classes("text-sm")
        ui.label(f"{relation or 'structure'} → core kind {core.kind} · type {core.family or arb_type} · flags: "
                 f"{', '.join(core.flags) or 'none'} · {core.core_version}").classes("text-xs text-gray-600")

        ui.label("1 · Where the edge goes (per share)").classes("font-bold mt-3")
        ui.table(columns=[{"name": k, "label": k, "field": k, "align": "left"} for k in ("stage", "edge")],
                 rows=[{"stage": s, "edge": cents(v)} for s, v in (
                     ("Price-only scanner (mid prices)", core.naive_edge),
                     ("At best asks, before fees", core.top_edge),
                     ("Whole fillable book, after depth and fees", core.avg_edge),
                     ("Guard-approved size, after fees and rule-risk haircut", core.risk_adj_edge))]).props("dense flat")
        ui.label(f"Rule-risk haircut {cents(core.haircut_per_share)} · basket floor ${core.floor:.0f} · "
                 f"legs {', '.join(core.legs) or '-'}").classes("text-xs text-gray-600")

        ui.label("2 · Size and cost").classes("font-bold mt-3")
        ui.table(columns=[{"name": k, "label": k, "field": k, "align": "left"} for k in ("item", "value")],
                 rows=[{"item": k, "value": v} for k, v in (
                     ("Guard-approved shares", f"{core.approved_shares:,.0f}" + (" (clipped to cap)" if core.clipped else "")),
                     ("Approved cost", f"${core.approved_cost:,.2f}"),
                     ("Approved profit", f"${core.approved_profit:,.2f}"),
                     ("Book allows", f"{core.shares:,.0f} shares"),
                     ("Fees", f"${core.fee_cost:,.2f}"),
                     ("Days to resolution", f"{core.days:.0f}"),
                     ("Risk-adjusted annualised", f"{core.risk_adj_ann:.0%}"))]).props("dense flat")
        for leg, fee in core.fees.items():
            ui.label(f"{leg}: {fee}").classes("text-xs text-gray-600")

        ui.label("3 · Guard checks").classes("font-bold mt-3")
        icon = {"pass": "✓", "warn": "!", "fail": "✗", "na": "–"}
        for g in core.checks:
            ui.label(f"{icon[g.status]} {g.name}: {g.detail}").classes("text-sm")

        if core.scenarios:
            ui.label("4 · Payoff in every still-possible world (per basket share)").classes("font-bold mt-3")
            ui.table(columns=[{"name": k, "label": k, "field": k, "align": "left"} for k in ("world", "payout", "profit")],
                     rows=[{"world": str(s["world"]), "payout": f"${s['payout']}", "profit": cents(s["profit"])}
                           for s in core.scenarios]).props("dense flat")

        ui.label("5 · How the explanation was produced").classes("font-bold mt-3")
        ui.label(f"Written by {n.source}; "
                 f"{'every number traced back to the core result' if n.grounded else 'NOT grounded in the core result'}."
                 ).classes("text-sm")
        if n.ungrounded_numbers:
            ui.label(f"Gemini draft rejected for numbers not in the core result: {n.ungrounded_numbers}; showing template."
                     ).classes("text-orange-700 text-sm")
        if judged_text:
            ui.label(judged_text).classes("text-sm")


@ui.page("/result/{case_id}")
def result_page(case_id: str):
    banner()
    c = P.cases.get(case_id)
    with ui.column().classes("p-4 w-full max-w-4xl gap-3"):
        if c is None:
            ui.label("Unknown case.")
            return
        if c.state == "error":
            ui.label(f"Core call failed: {c.error}").classes("text-red-700")
            return
        core, n = c.core, c.narrated
        a, b = c.judged.a, c.judged.b

        def decision():
            if c.state in ("narrated", "review"):
                with ui.row():
                    btn = ui.button("Approve (paper)", on_click=lambda: (P.decide(case_id, "approved", USER), ui.navigate.reload()))
                    if core.verdict in ("blocked", "nothing_to_do"):
                        btn.disable()
                    ui.button("Review later", on_click=lambda: (P.decide(case_id, "review", USER), ui.navigate.reload()))
                    ui.button("Kill", color="red", on_click=lambda: (P.decide(case_id, "killed", USER), ui.navigate.reload()))
            else:
                ui.label(f"Decision: {c.state}").classes("font-bold")

        jv = c.judged.verdict
        render_report(core, n, [f"{a.venue.title()}: {a.title}", f"{b.venue.title()}: {b.title}"],
                      f"These {RELATION_WORDS.get(c.relation.relation, c.relation.relation.replace('_', ' '))}.",
                      c.arb_type, c.relation.relation,
                      f"Judged by Gemini: {jv.relation} ({jv.confidence}). {jv.reasoning}", decision)


@ui.page("/finding/{fid:path}")
def finding_page(fid: str):
    banner()
    x = P.findings.get(fid)
    with ui.column().classes("p-4 w-full max-w-4xl gap-3"):
        if x is None:
            ui.label("Unknown result (findings are rebuilt on every scan).")
            return
        render_report(x.core, x.narrated, [f"{l.venue.title()}: {l.title}" for l in x.legs[:12]]
                      + ([f"…and {len(x.legs) - 12} more outcomes."] if len(x.legs) > 12 else []),
                      "Priced from structure alone: no LLM judgement and nothing to confirm.", x.arb_type)


@ui.page("/suggestions")
def suggestions_page():
    banner()
    with ui.column().classes("p-4 w-full"):
        ok, bad = P.suggestions.log.verify()
        counts = P.suggestions.counts()
        ui.label("Suggestion log: every pair considered, including the ones the rules refused").classes("text-xl")
        ui.label(("Hash chain verified ✓" if ok else f"Hash chain BROKEN at record {bad}") + " · " +
                 " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))).classes(
            "text-green-700" if ok else "text-red-700 font-bold")
        ui.label(f"{P.cfg.suggestions_path} (also written by the monitor: run.bat monitor)").classes("text-xs text-gray-500")
        show = ui.select(["all", "surfaced", "rule_refused", "judged", "filtered", "deferred", "human", "priced", "repriced"],
                         value="all", label="Show")
        rows = []
        for r in P.suggestions.latest()[:1000]:
            a, b = r.get("a", {}), r.get("b", {})
            rows.append({"pair": r["pair"], "last": r["last"],
                         "a": f"{a.get('venue', '')}: {a.get('title', '')[:70]}", "b": f"{b.get('venue', '')}: {b.get('title', '')[:70]}",
                         "cats": f"{a.get('category')}/{b.get('category')}", "how": r.get("how", ""),
                         "result": r.get("verdict") or r.get("relation") or r.get("action") or "",
                         "why": (f"{r.get('rule', '')} {r.get('reason', '')}" if r["last"] == "rule_refused" else r.get("reason", ""))[:120]})
        cols = [{"name": k, "label": k, "field": k, "align": "left"} for k in ("last", "a", "b", "cats", "how", "result", "why")]
        table = ui.table(columns=cols, rows=rows, row_key="pair").classes("w-full")
        show.on_value_change(lambda e: table.update_rows(rows if e.value == "all" else [r for r in rows if r["last"] == e.value]))


@ui.page("/log")
def log_page():
    banner()
    with ui.column().classes("p-4 w-full"):
        ok, bad = P.audit.verify()
        ui.label("Hash chain verified ✓" if ok else f"Hash chain BROKEN at record {bad}").classes(
            "text-green-700 font-bold" if ok else "text-red-700 font-bold")
        recs = P.audit.records()[-200:][::-1]
        rows = [{"seq": r["seq"], "kind": r["kind"], "hash": r["hash"][:12],
                 "summary": str(r["payload"])[:160]} for r in recs]
        ui.table(columns=[{"name": k, "label": k, "field": k} for k in ("seq", "kind", "hash", "summary")],
                 rows=rows).classes("w-full")


def main():
    global P
    ap = argparse.ArgumentParser()
    ap.add_argument("--kalshi-member", action="store_true", help="I am a Kalshi member running this on my own computer")
    ap.add_argument("--port", type=int, default=8080)
    args, _ = ap.parse_known_args()
    from dataclasses import replace

    from frame.config import get_config
    cfg = get_config()
    if args.kalshi_member:
        cfg = replace(cfg, kalshi_member=True)
    P = build(cfg=cfg)  # live venues + live Gemini
    ui.run(title="False-Arb Checker", port=args.port, host="127.0.0.1", reload=False, show=False, dark=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
