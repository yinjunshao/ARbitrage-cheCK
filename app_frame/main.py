"""NiceGUI app: Check · Candidates · Suggestions · Result · Log.  Paper only. No orders are placed.

    python -m app_frame.main                    # live Polymarket via arbcheck's read-only client + live Gemini
    python -m app_frame.main --kalshi-member    # also Kalshi (members only, on your own computer)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nicegui import ui  # noqa: E402

from frame.pipeline import Pipeline, build  # noqa: E402
from frame.schemas import Market  # noqa: E402

RELATIONS = ["same_event", "a_implies_b", "b_implies_a", "mutually_exclusive", "exhaustive_set"]
USER = "analyst"
P: Pipeline | None = None
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
        ui.label(f"LIVE data · Polymarket on · Kalshi {'ON' if P.cfg.kalshi_member else 'off (run.bat kalshi)'} · "
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
        status = ui.label("")
        if P.last_scan:
            s = P.last_scan
            status.set_text(f"Last scan {s.scan}: {s.markets} markets, {s.candidates} candidates, {s.rule_refused} refused "
                            f"by rules, {s.judged} judged, {s.surfaced} for review, {s.deferred} deferred.")

        async def scan():
            status.set_text("Scanning live markets… (pulling events, then Gemini; can take a few minutes)")
            from nicegui import run
            s = await run.io_bound(P.scan, ["polymarket", "kalshi"])
            for n in s.notes:
                ui.notify(n, type="info", multi_line=True)
            ui.navigate.to("/candidates")

        ui.button("Scan venues for related markets", on_click=scan)
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
                ui.notify("Reading both links and asking Gemini…", type="info")
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
                case = await run.io_bound(P.check_pair, a, b)
            ui.navigate.to("/candidates")

        ui.button("Check this pair", on_click=run_check)


@ui.page("/candidates")
def candidates_page():
    banner()
    with ui.column().classes("p-4 w-full"):
        queue = P.review_queue()
        hidden = sum(1 for c in P.cases.values() if c.state == "filtered")
        ui.label(f"{len(queue)} candidate(s) awaiting confirmation · {hidden} filtered as unrelated/unclear/low confidence")
        done = [c for c in P.cases.values() if c.core is not None]
        if done:
            with ui.row():
                for c in done:
                    ui.link(f"{c.judged.a.title[:40]} → {c.core.verdict}", f"/result/{c.id}").classes("text-sm mr-4")
        for c in queue:
            v = c.judged.verdict
            with ui.card().classes("w-full"):
                ui.label(f"{v.relation} · confidence {v.confidence} · {'quotes verified' if c.judged.verified else 'UNVERIFIED'}"
                         f" · found by {c.how} · {c.judged.a.category or '?'} / {c.judged.b.category or '?'}").classes("font-bold")
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
                        await run.io_bound(P.price, c.id, float(budget.value or 0))
                        ui.navigate.to(f"/result/{c.id}")

                    ui.button("Confirm & price with core", on_click=confirm)
                    ui.button("Reject", color="red",
                              on_click=lambda c=c, note=note: (P.reject(c.id, USER, note.value or ""), ui.navigate.reload()))


@ui.page("/result/{case_id}")
def result_page(case_id: str):
    banner()
    c = P.cases.get(case_id)
    with ui.column().classes("p-4 w-full max-w-5xl"):
        if c is None:
            ui.label("Unknown case.")
            return
        if c.state == "error":
            ui.label(f"Core call failed: {c.error}").classes("text-red-700")
            return
        core, n = c.core, c.narrated
        with ui.card().classes(f"w-full {VERDICT_STYLE[core.verdict]}"):
            ui.label(f"CORE VERDICT: {core.verdict.replace('_', ' ').upper()}  ({core.status})").classes("text-2xl font-bold")
            ui.label(core.headline).classes("text-lg")
            ui.label(f"{c.relation.relation} → core kind {core.kind} · {core.core_version} · flags: "
                     f"{', '.join(core.flags) or 'none'}").classes("text-sm")
        with ui.row().classes("w-full gap-8"):
            with ui.column():
                ui.label("Where the edge goes (per share)").classes("font-bold")
                for label, val in (("Price-only scanner (mid)", core.naive_edge), ("At best asks, before fees", core.top_edge),
                                   ("Whole fillable book, after depth and fees", core.avg_edge),
                                   ("Guard-approved size, after fees and rule-risk haircut", core.risk_adj_edge)):
                    ui.label(f"{label}: {cents(val)}")
                ui.label(f"Haircut {cents(core.haircut_per_share)} · basket floor ${core.floor:.0f} · legs {', '.join(core.legs) or '-'}")
            with ui.column():
                ui.label("Sizing (guard-approved)").classes("font-bold")
                ui.label(f"{core.approved_shares:,.0f} shares · cost ${core.approved_cost:,.2f} · profit ${core.approved_profit:,.2f}"
                         + (" (clipped to cap)" if core.clipped else ""))
                ui.label(f"Book allows {core.shares:,.0f} shares · fees ${core.fee_cost:,.2f} · {core.days:.0f} days · "
                         f"risk-adj. annualised {core.risk_adj_ann:.0%}")
                for leg, fee in core.fees.items():
                    ui.label(f"{leg}: {fee}").classes("text-xs text-gray-600")
        ui.label("Guard checks").classes("font-bold mt-2")
        icon = {"pass": "✓", "warn": "!", "fail": "✗", "na": "–"}
        for g in core.checks:
            ui.label(f"{icon[g.status]} {g.name}: {g.detail}").classes("text-sm")
        if core.scenarios:
            ui.label("Payoff in every still-possible world (per basket share)").classes("font-bold mt-2")
            ui.table(columns=[{"name": k, "label": k, "field": k} for k in ("world", "payout", "profit")],
                     rows=[{"world": str(s["world"]), "payout": f"${s['payout']}", "profit": cents(s["profit"])}
                           for s in core.scenarios])
        ui.separator()
        ui.label(f"Explanation ({n.source}; {'every number traced to the core' if n.grounded else 'NOT grounded'})").classes("font-bold")
        if n.ungrounded_numbers:
            ui.label(f"Gemini draft rejected for numbers not in the core result: {n.ungrounded_numbers}; showing template."
                     ).classes("text-orange-700")
        ui.label(n.narrative.headline).classes("text-lg")
        ui.label(n.narrative.why_gap)
        ui.markdown("**What could go wrong**\n" + "\n".join(f"- {x}" for x in n.narrative.what_could_go_wrong))
        ui.markdown("**Check before approving**\n" + "\n".join(f"- {x}" for x in n.narrative.human_checks))
        ui.label(f"Suggested: {n.narrative.recommendation}")
        if c.state in ("narrated", "review"):
            with ui.row():
                b = ui.button("Approve (paper)", on_click=lambda: (P.decide(case_id, "approved", USER), ui.navigate.reload()))
                if core.verdict in ("blocked", "nothing_to_do"):
                    b.disable()
                ui.button("Review later", on_click=lambda: (P.decide(case_id, "review", USER), ui.navigate.reload()))
                ui.button("Kill", color="red", on_click=lambda: (P.decide(case_id, "killed", USER), ui.navigate.reload()))
        else:
            ui.label(f"Decision: {c.state}")


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
