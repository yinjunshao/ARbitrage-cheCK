"""NiceGUI app: Check · Candidates · Result · Log.  Paper only. No orders are placed.

    python -m app_frame.main --replay     # offline: fixtures + recorded LLM outputs + fake core
    python -m app_frame.main              # live Gemini (GEMINI_API_KEY) + FRAME_CORE (fake|real)
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


def banner():
    ui.query("body").style("background:#f8fafc;color:#0f172a")
    with ui.header().classes("items-center bg-slate-800"):
        ui.label("False-Arb Checker · LLM frame").classes("text-lg font-bold")
        for path, name in (("/", "Check"), ("/candidates", "Candidates"), ("/log", "Log")):
            ui.link(name, path).classes("text-white ml-4")
    with ui.row().classes("w-full bg-amber-200 text-black p-2"):
        ui.label("PAPER ONLY. No orders are placed.").classes("font-bold")
        if P.adapter.name == "fake":
            ui.label("FAKE CORE: numbers are placeholders, not computed from prices.").classes("text-red-700 font-bold")
        ui.label(f"LLM: {getattr(P.llm, 'name', '?')} · judge model: {P.cfg.judge_model} · core: {P.adapter.name}")


def market_card(m: Market, quotes: list[str]):
    with ui.card().classes("w-[48%]"):
        ui.label(f"{m.venue.upper()} · {m.title}").classes("font-bold")
        ui.label(f"closes {m.close_time_utc or '?'} · Yes {m.yes_bid}/{m.yes_ask}").classes("text-xs text-gray-500")
        text = m.rules_text or "(no rules text)"
        for q in quotes:  # highlight verified quotes
            text = text.replace(q, f"**{q}**")
        ui.markdown(text).classes("text-sm")


@ui.page("/")
def check_page():
    banner()
    with ui.column().classes("p-4 w-full"):
        ui.label("Proactive scan").classes("text-xl")
        ui.button("Scan venues for related markets", on_click=lambda: (P.scan(), ui.navigate.to("/candidates")))
        ui.separator()
        ui.label("Check a pair: paste two links, or enter both markets by hand").classes("text-xl")
        l1, l2 = ui.input("Link A").classes("w-full"), ui.input("Link B").classes("w-full")
        manual = {}
        with ui.expansion("Manual entry"):
            with ui.row().classes("w-full"):
                for side in ("A", "B"):
                    with ui.column().classes("w-[48%]"):
                        manual[side] = {
                            "venue": ui.select(["polymarket", "kalshi", "predictit", "manual"], value="manual", label=f"{side} venue"),
                            "title": ui.input(f"{side} title"), "rules": ui.textarea(f"{side} rules text"),
                            "close": ui.input(f"{side} close (ISO UTC)"),
                            "ask": ui.number(f"{side} Yes ask", format="%.3f"), "bid": ui.number(f"{side} Yes bid", format="%.3f"),
                        }

        def run():
            if l1.value and l2.value:
                try:
                    a, b = P.adapter.parse_link(l1.value), P.adapter.parse_link(l2.value)
                except Exception as e:
                    ui.notify(f"Link parsing needs the core: {e}", type="negative"); return
                if not a or not b:
                    ui.notify("Link not recognised (only Polymarket/Kalshi/Manifold links).", type="warning"); return
            else:
                def mk(s):
                    f = manual[s]
                    return Market(venue=f["venue"].value, id=f"manual-{s}-{abs(hash(f['title'].value))}",
                                  title=f["title"].value or "", rules_text=f["rules"].value or "",
                                  close_time_utc=f["close"].value or None, yes_ask=f["ask"].value, yes_bid=f["bid"].value)
                a, b = mk("A"), mk("B")
                if not a.title or not b.title:
                    ui.notify("Enter both titles.", type="warning"); return
            case = P.check_pair(a, b)
            ui.navigate.to(f"/candidates#{case.id}")

        ui.button("Check this pair", on_click=run)


@ui.page("/candidates")
def candidates_page():
    banner()
    with ui.column().classes("p-4 w-full"):
        queue = P.review_queue()
        hidden = sum(1 for c in P.cases.values() if c.state == "filtered")
        ui.label(f"{len(queue)} candidate(s) awaiting confirmation · {hidden} filtered as unrelated/unclear/low confidence")
        for c in queue:
            v = c.judged.verdict
            with ui.card().classes("w-full"):
                ui.label(f"{v.relation} · confidence {v.confidence} · {'quotes verified' if c.judged.verified else 'UNVERIFIED'}"
                         f" · source {c.judged.source}").classes("font-bold")
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
                    size = ui.number("Size (contracts)", value=100)
                    note = ui.input("Note")

                    def confirm(c=c, rel=rel, size=size, note=note):
                        if not rel.value:
                            ui.notify("Pick a relation or reject.", type="warning"); return
                        P.confirm(c.id, USER, rel.value, note.value or "")
                        P.price(c.id, float(size.value or 0))
                        ui.navigate.to(f"/result/{c.id}")

                    ui.button("Confirm & price", on_click=confirm)
                    ui.button("Reject", color="red",
                              on_click=lambda c=c, note=note: (P.reject(c.id, USER, note.value or ""), ui.navigate.reload()))


@ui.page("/result/{case_id}")
def result_page(case_id: str):
    banner()
    c = P.cases.get(case_id)
    with ui.column().classes("p-4 w-full"):
        if c is None:
            ui.label("Unknown case."); return
        if c.state == "error":
            ui.label(f"Core call failed: {c.error}").classes("text-red-700"); return
        core, n = c.core, c.narrated
        ui.label(f"CORE VERDICT: {core.verdict.replace('_', ' ').upper()}").classes("text-2xl font-bold")
        ui.label(f"core_version {core.core_version} · flags: {', '.join(core.flags) or 'none'}")
        rows = [{"field": k, "value": str(v)} for k, v in core.model_dump().items() if k not in ("guard_checks", "flags")]
        ui.table(columns=[{"name": "field", "label": "Core field", "field": "field"},
                          {"name": "value", "label": "Value", "field": "value"}], rows=rows).classes("w-[600px]")
        ui.label("Guard checks").classes("font-bold mt-2")
        for g in core.guard_checks:
            ui.label(f"{'✓' if g.passed else '✗'} {g.name} {g.detail}")
        ui.separator()
        ui.label(f"Narrative ({n.source}; {'grounded' if n.grounded else 'NOT grounded'})").classes("font-bold")
        if n.ungrounded_numbers:
            ui.label(f"LLM draft rejected for invented numbers: {n.ungrounded_numbers}; showing template.").classes("text-orange-700")
        ui.label(n.narrative.headline).classes("text-lg")
        ui.label(n.narrative.why_gap)
        ui.markdown("**What could go wrong**\n" + "\n".join(f"- {x}" for x in n.narrative.what_could_go_wrong))
        ui.markdown("**Check before approving**\n" + "\n".join(f"- {x}" for x in n.narrative.human_checks))
        ui.label(f"Suggested: {n.narrative.recommendation}")
        if c.state in ("narrated", "review"):
            with ui.row():
                blocked = core.verdict in ("blocked", "nothing_to_do")
                b = ui.button("Approve (paper)", on_click=lambda: (P.decide(case_id, "approved", USER), ui.navigate.reload()))
                if blocked:
                    b.disable()
                ui.button("Review later", on_click=lambda: (P.decide(case_id, "review", USER), ui.navigate.reload()))
                ui.button("Kill", color="red", on_click=lambda: (P.decide(case_id, "killed", USER), ui.navigate.reload()))
        else:
            ui.label(f"Decision: {c.state}")


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
    ap.add_argument("--replay", action="store_true", help="offline: fixtures, recorded LLM outputs, fake core")
    ap.add_argument("--port", type=int, default=8080)
    args, _ = ap.parse_known_args()
    P = build(replay=args.replay)
    ui.run(title="False-Arb Checker", port=args.port, reload=False, show=False, dark=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
