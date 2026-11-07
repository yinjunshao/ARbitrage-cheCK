"""Turns scan results into ONE self-contained HTML page (no external files, works offline).

Design goals, in priority order
-------------------------------
1. Answer "is this arbitrage real?" in the first line of every ticket, in plain English.
2. Show WHERE the profit goes: price-only gap -> spread -> order-book depth -> fees ->
   rule risk. Like gross-to-net on a payslip, drawn as bars that all start at one baseline.
3. Show the evidence for the verdict: the clauses that differ, quoted word for word.
4. Show the risk controls working: which checks passed, failed, or didn't apply.
5. Never rely on colour alone: every status has an icon and a word; every value is printed.

Chart rules follow the dataviz method: one series colour, thin marks (16px), 4px rounded
data ends on a single baseline, 2px surface gaps, recessive grid, text in ink tokens,
system sans, light and dark selected separately, hover/focus tooltips that never gate
a value (every value is also printed).
"""
from __future__ import annotations

import html
import json
import math
import re
from dataclasses import asdict
from datetime import datetime

from .engine import LABELS, avg_edges
from .guard import Limits
from .models import question_stem
from .pipeline import Case, PairReport, sort_reports

# --------------------------------------------------------------------------------------
# Styles and script (plain strings: no f-string, so braces are safe)
# --------------------------------------------------------------------------------------
CSS = """
:root{color-scheme:light dark;
  --plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
  --grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);--series:#2a78d6;
  --good:#0ca30c;--good-text:#006300;--warn:#fab219;--crit:#d03b3b;
  --t-high:rgba(208,59,59,.16);--t-med:rgba(250,178,25,.26);--t-low:rgba(137,135,129,.20);}
@media (prefers-color-scheme:dark){:root{
  --plane:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;
  --grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--series:#3987e5;
  --good:#0ca30c;--good-text:#0ca30c;--warn:#fab219;--crit:#d03b3b;
  --t-high:rgba(208,59,59,.30);--t-med:rgba(250,178,25,.28);--t-low:rgba(137,135,129,.30);}}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:40px 20px 72px}
h1{font-size:20px;font-weight:600;margin:0;letter-spacing:-.01em}
.sub{color:var(--ink2);margin:4px 0 0;max-width:64ch}
.hero{margin:56px 0 0}
.hero-fig{font-size:76px;line-height:1;font-weight:600;letter-spacing:-.03em}
.hero-fig .of{color:var(--ink2);font-weight:500}
.hero p{max-width:60ch;margin:14px 0 0}
.hero .lead{font-size:19px;line-height:1.4}
.hero .rest{color:var(--ink2)}
.controls{display:flex;flex-wrap:wrap;gap:10px 34px;margin:40px 0 0;padding:14px 0;
  border-top:1px solid var(--grid);border-bottom:1px solid var(--grid);font-size:13px}
.controls dt{color:var(--ink2)}
.controls dd{margin:0;font-weight:600;font-variant-numeric:tabular-nums}
.controls .on{color:var(--crit)}
.note{color:var(--ink2);font-size:13px;margin:10px 0 0;max-width:70ch}
.card{display:grid;grid-template-columns:44px minmax(0,1fr);column-gap:16px;
  padding:44px 0 4px;border-top:1px solid var(--grid);margin-top:44px}
.card:first-of-type{margin-top:36px}
.stamp{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;margin-top:2px}
.stamp svg,.ci svg{display:block}
.s-good{background:var(--good)}.s-warn{background:var(--warn)}
.s-crit{background:var(--crit)}.s-none{background:var(--axis)}
h2{font-size:19px;line-height:1.35;font-weight:600;margin:0;max-width:62ch;letter-spacing:-.005em}
h2 .vtag{white-space:nowrap}
.mkt{color:var(--ink2);font-size:13.5px;margin:6px 0 0;max-width:70ch}
h3{font-size:14px;font-weight:600;margin:30px 0 4px}
.sec-note{color:var(--ink2);font-size:13px;margin:0 0 6px;max-width:70ch}
.ladder{margin-top:6px}
.lrow{display:grid;grid-template-columns:minmax(170px,270px) minmax(0,1fr) 68px;gap:6px 16px;
  align-items:center;padding:9px 0;border-radius:4px}
.lrow:hover,.lrow:focus{background:var(--grid);outline:none}
.lab b{display:block;font-weight:500}
.lab small{display:block;color:var(--ink2);font-size:12.5px;line-height:1.35}
.track{position:relative;height:16px;border-left:1px solid var(--axis)}
.bar,.ghost{position:absolute;top:0;height:16px}
.bar{left:0;background:var(--series);border-radius:0 4px 4px 0}
.ghost{background:var(--grid);border-radius:0 4px 4px 0}
.lrow:hover .ghost{background:var(--axis)}
.val{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}
.val.zero{color:var(--ink2)}
.total{margin:10px 0 0;max-width:68ch}
.why{margin:22px 0 0;padding:0;list-style:none;max-width:70ch}
.why li{display:grid;grid-template-columns:22px 1fr;gap:6px;padding:4px 0}
.ci{width:16px;height:16px;border-radius:50%;display:grid;place-items:center;margin-top:3px}
.why small,.checks small{display:block;color:var(--ink2);font-size:13px}
.diff{padding:14px 0;border-top:1px solid var(--grid)}
.diff:first-of-type{border-top:0}
.dtop{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
.dtop b{font-weight:600}
.chip{font-size:12px;font-weight:600;padding:1px 9px;border-radius:10px}
.c-high{background:var(--t-high)}.c-medium{background:var(--t-med)}.c-low{background:var(--t-low)}
.dnote{color:var(--ink2);font-size:13.5px;margin:4px 0 0;max-width:68ch}
.quotes{display:grid;grid-template-columns:1fr 1fr;gap:6px 22px;margin:10px 0 0}
.q{border-left:2px solid var(--axis);padding-left:11px;font-size:13.5px;color:var(--ink2);
  overflow-wrap:anywhere}
.q b{display:block;color:var(--ink);font-weight:500;font-size:12.5px}
.agree{color:var(--ink2);max-width:66ch;margin:2px 0 0}
table.pay{border-collapse:collapse;width:100%;max-width:640px;font-size:14px}
.pay th{text-align:left;font-weight:500;color:var(--ink2);font-size:12.5px;
  padding:6px 12px 6px 0;border-bottom:1px solid var(--axis)}
.pay td{padding:9px 12px 9px 0;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums}
.pay .n{text-align:right}.pay th.n{text-align:right}
.pay tr.floor td:first-child{font-weight:600}
.pos{color:var(--good-text);font-weight:600}
details{margin:18px 0 0}
summary{cursor:pointer;font-weight:500;font-size:13.5px;color:var(--ink2);padding:3px 0}
summary:hover,summary:focus-visible{color:var(--ink)}
.checks{display:grid;grid-template-columns:1fr 1fr;gap:6px 30px;margin:10px 0 0;padding:0;
  list-style:none;font-size:13.5px}
.checks li{display:grid;grid-template-columns:22px 1fr;gap:6px}
.rules{display:grid;grid-template-columns:1fr 1fr;gap:6px 22px;margin:10px 0 0}
.rules pre{white-space:pre-wrap;font:13px/1.5 system-ui,sans-serif;color:var(--ink2);margin:4px 0 0;
  overflow-wrap:anywhere}
.rules b{font-weight:500;font-size:12.5px}
mark{color:var(--ink);border-radius:2px;padding:0 1px;border-bottom:2px solid var(--ink2)}
mark.sev-high{background:var(--t-high);border-color:var(--crit)}
mark.sev-medium{background:var(--t-med);border-color:var(--warn)}
mark.sev-low{background:var(--t-low)}
pre.audit{font:12px/1.45 ui-monospace,Menlo,Consolas,monospace;overflow:auto;max-height:340px;
  padding:12px;border:1px solid var(--ring);border-radius:4px;background:var(--surface);margin:8px 0 0}
.surv{margin-top:56px;border-top:1px solid var(--grid);padding-top:40px}
.badge{display:inline-block;font-size:12px;font-weight:600;padding:1px 9px;border-radius:10px;
  background:var(--t-med);margin-left:8px;vertical-align:2px}
svg.chart{width:100%;max-width:680px;height:auto;display:block;touch-action:pan-y}
svg.chart text{fill:var(--muted);font:11.5px system-ui,sans-serif}
svg.chart text.lbl{fill:var(--ink);font-weight:600;font-size:12.5px}
.foot{margin-top:64px;color:var(--ink2);font-size:13px;max-width:70ch}
#tip{position:fixed;z-index:9;pointer-events:none;display:none;background:var(--surface);
  color:var(--ink);border:1px solid var(--ring);border-radius:4px;padding:7px 10px;font-size:12.5px;
  max-width:280px;box-shadow:0 2px 8px rgba(0,0,0,.12)}
#tip strong{display:block;font-size:15px;font-variant-numeric:tabular-nums}
#tip span{color:var(--ink2)}
@media (max-width:680px){
  .hero-fig{font-size:56px}.card{grid-template-columns:36px minmax(0,1fr);column-gap:10px}
  .lrow{grid-template-columns:minmax(0,1fr) 62px}.lrow .track{grid-column:1 / -1;order:3}
  .quotes,.rules,.checks{grid-template-columns:1fr}}
"""

JS = """
(function(){
  var tip=document.getElementById('tip');
  function put(v,l,x,y){
    tip.textContent='';
    var s=document.createElement('strong');s.textContent=v;
    var p=document.createElement('span');p.textContent=l;
    tip.appendChild(s);tip.appendChild(p);tip.style.display='block';
    var w=tip.offsetWidth,h=tip.offsetHeight;
    tip.style.left=Math.max(8,Math.min(x+14,window.innerWidth-w-8))+'px';
    tip.style.top=Math.max(8,Math.min(y+14,window.innerHeight-h-8))+'px';
  }
  function hide(){tip.style.display='none';}
  document.addEventListener('pointermove',function(e){
    var el=e.target.closest&&e.target.closest('[data-v]');
    if(el){put(el.getAttribute('data-v'),el.getAttribute('data-l')||'',e.clientX,e.clientY);}
    else if(!e.target.closest||!e.target.closest('svg.survsvg')){hide();}
  });
  document.addEventListener('focusin',function(e){
    var el=e.target.closest&&e.target.closest('[data-v]');
    if(el){var r=el.getBoundingClientRect();put(el.getAttribute('data-v'),el.getAttribute('data-l')||'',r.left+24,r.top);}
  });
  document.addEventListener('focusout',hide);
  // Survival chart: a vertical crosshair that snaps to the step curve.
  document.querySelectorAll('svg.survsvg').forEach(function(svg){
    var curve=JSON.parse(svg.getAttribute('data-curve'));
    var x0=+svg.getAttribute('data-x0'),x1=+svg.getAttribute('data-x1'),xmax=+svg.getAttribute('data-xmax');
    var line=svg.querySelector('.cross');
    function at(m){var s=1;for(var i=0;i<curve.length;i++){if(curve[i][0]<=m){s=curve[i][1];}else{break;}}return s;}
    svg.addEventListener('pointermove',function(e){
      var r=svg.getBoundingClientRect(),vb=svg.viewBox.baseVal;
      var px=(e.clientX-r.left)*vb.width/r.width;
      px=Math.max(x0,Math.min(x1,px));
      var m=(px-x0)/(x1-x0)*xmax;
      line.setAttribute('x1',px);line.setAttribute('x2',px);line.style.display='block';
      put(Math.round(at(m)*100)+'% still open','After '+m.toFixed(1)+' minutes',e.clientX,e.clientY);
    });
    svg.addEventListener('pointerleave',function(){line.style.display='none';hide();});
  });
})();
"""

_FLAG_LABEL = {
    "source": "Where the answer comes from", "definition": "What counts as Yes",
    "timezone": "Deadline time zone", "deadline_semantics": "How the deadline works",
    "inclusion": "What is included", "tie_handling": "Ties and edge cases",
    "venue_oracle": "Who settles it",
}
_SEV_LABEL = {"high": "High risk", "medium": "Medium risk", "low": "Low risk"}

# 24x24 glyphs drawn inside small filled circles
_GLYPH = {
    "check": '<path d="M6.5 12.5l3.7 3.7 7.3-8.2"/>',
    "alert": '<path d="M12 6.5v7M12 17.2v.4"/>',
    "cross": '<path d="M7.5 7.5l9 9M16.5 7.5l-9 9"/>',
    "dash": '<path d="M7.5 12h9"/>',
}


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def _c(x: float, signed: bool = False) -> str:
    """Dollars per share -> cents text. 0.07 -> '7.0¢' (signed: '+7.0¢')."""
    v = x * 100
    mag = f"{abs(v):.1f}¢"
    if signed:
        return ("+" if v >= 0 else "−") + mag
    return ("−" if v < 0 else "") + mag


def _money(x: float) -> str:
    return f"${x:,.0f}" if abs(x) >= 100 else f"${x:,.2f}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _glyph(name: str, stroke: str, size: int, width: float = 2.4) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{stroke}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{_GLYPH[name]}</svg>')


def _stamp(tone: str, icon: str, label: str) -> str:
    stroke = {"good": "#fff", "warn": "#0b0b0b", "crit": "#fff", "none": "var(--ink2)"}[tone]
    return (f'<div class="stamp s-{tone}" role="img" aria-label="{esc(label)}">'
            f'{_glyph(icon, stroke, 20)}</div>')


def _check_icon(status: str) -> str:
    tone, icon, stroke = {"pass": ("good", "check", "#fff"), "warn": ("warn", "alert", "#0b0b0b"),
                          "fail": ("crit", "cross", "#fff"), "na": ("none", "dash", "var(--ink2)")}[status]
    word = {"pass": "Passed", "warn": "Caution", "fail": "Failed", "na": "Not applicable"}[status]
    return (f'<span class="ci s-{tone}" role="img" aria-label="{word}">{_glyph(icon, stroke, 12, 3)}</span>')


# --------------------------------------------------------------------------------------
# Pieces of a ticket
# --------------------------------------------------------------------------------------
def _verdict(r: Case, limits: Limits) -> tuple[str, str, str]:
    """(label, tone, icon) for the stamp."""
    s = r.decision.status
    if s == "OK":
        return "Worth a look", "good", "check"
    if s == "WARN":
        return "Look closely", "warn", "alert"
    if s == "BLOCKED":
        false_arb = r.arb.executable and any(f.severity == "high" for f in r.relation.flags) \
            and not limits.kill_switch
        return ("Not a real arbitrage" if false_arb else "Blocked"), "crit", "cross"
    return "Nothing to do", "none", "dash"


def _lrow(title: str, sub: str, remaining: float, lost: float, scale: float,
          text: str | None = None) -> str:
    w = max(remaining, 0.0) / scale * 100
    lw = max(lost, 0.0) / scale * 100
    value = text if text is not None else _c(remaining, signed=True)
    bar = f'<span class="bar" style="width:{w:.2f}%"></span>' if w > 0.05 else ""
    gap = 2 if w > 0.05 else 0
    ghost = (f'<span class="ghost" style="left:calc({w:.2f}% + {gap}px);width:{lw:.2f}%"></span>'
             if lw > 0.05 else "")
    zero = " zero" if (remaining <= 0 or text is not None) else ""
    return (f'<div class="lrow" tabindex="0" data-v="{esc(value)}" data-l="{esc(title + ". " + sub)}">'
            f'<div class="lab"><b>{esc(title)}</b><small>{esc(sub)}</small></div>'
            f'<div class="track" role="img" aria-label="{esc(title)}: {esc(value)} per share">{bar}{ghost}</div>'
            f'<div class="val{zero}">{esc(value)}</div></div>')


def _trim(text: str, n: int = 78) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _is_nested(r: Case) -> bool:
    """Same question, two deadlines, same venue: the case the plain 'earlier/later' wording fits."""
    return r.names == ["Earlier contract", "Later contract"] and r.relation.kind == "a_implies_b"


def _ladder(r: Case, scale: float, limits: Limits) -> str:
    a, d = r.arb, r.decision
    naive = a.naive_edge
    if naive <= 0.005 and not a.executable:
        return ""                       # nothing to explain: prices are already consistent
    fills = d.fills if d.status in ("OK", "WARN") and d.fills else a.fills
    rows = [_lrow("What a price-only scanner sees",
                  "The gap between the two mid prices. It ignores every cost.", naive, 0.0, scale)]
    top = a.top_edge
    spread_loss = naive - top
    rows.append(_lrow(
        "After the bid/ask spread",
        (f"Buying at the asking price, not the midpoint, costs {_c(spread_loss)}."
         + (f" Locking this in would now lose {_c(-top)}." if top <= 0 else "")),
        top, naive - max(top, 0.0), scale))
    if a.executable and fills:
        gross, net = avg_edges(fills, a.floor, a.fees)
        shares = sum(f.shares for f in fills)
        depth_loss = top - gross
        rows.append(_lrow(
            "After order-book depth",
            (f"Your {shares:,.0f} shares reach past the best price into worse ones: {_c(depth_loss)} lost."
             if depth_loss > 0.0004 else "The best prices have enough size for the whole position."),
            gross, top - gross, scale))
        prev = gross
        if gross - net > 0.0004:
            kinds = sorted({f.name for f in a.fees if f.name != "No fee"})
            rows.append(_lrow("After fees", f"Fees take {_c(gross - net)} per share. " + " ".join(kinds),
                              net, gross - net, scale))
            prev = net
        soft = sum(limits.haircuts.get(f.severity, 0.02) for f in r.relation.flags if f.severity != "high")
        high = any(f.severity == "high" for f in r.relation.flags)
        if high:
            rows.append(_lrow("After rule differences",
                              "The rules differ in ways that can break the link between the two contracts, "
                              "so this profit is not locked in.", 0.0, prev, scale, text="Blocked"))
        elif soft > 0:
            rows.append(_lrow("After rule-risk haircut",
                              f"Small rule differences are priced in at {_c(soft)} per share.",
                              prev - soft, soft, scale))
        else:
            rows.append(_lrow("After rule-risk haircut", "The rules match, so no haircut is applied.",
                              prev, 0.0, scale))
    return '<div class="ladder">' + "".join(rows) + "</div>"


def _total_line(r: Case) -> str:
    a, d = r.arb, r.decision
    if d.status in ("OK", "WARN") and d.approved_cost > 0:
        ret = d.approved_profit / d.approved_cost
        cap = " The position is capped by your exposure limit." if d.clipped else ""
        return (f'<p class="total">On {d.approved_shares:,.0f} shares ({_money(d.approved_cost)}) you would '
                f'lock in about <b>{_money(d.approved_profit)}</b>: {ret:.1%} over {a.days:.0f} days, '
                f'about {d.risk_adj_ann:.0%} a year while your money is tied up.{cap}</p>')
    if d.status == "BLOCKED" and a.executable and a.shares > 0:
        return (f'<p class="total">A price-only scanner would have you buy {a.shares:,.0f} shares for '
                f'{_money(a.cost)} to "lock in" {_money(a.net_profit)}. Because the rules differ, '
                f'that profit is not guaranteed.</p>')
    return ""


def _why(r: Case) -> str:
    d, a = r.decision, r.arb
    if not (a.executable or a.naive_edge > 0.005):
        return ""
    bad = [c for c in d.checks if c.status in ("fail", "warn")]
    if not bad:
        return ""
    title = "Keep in mind" if d.status in ("OK", "WARN") else "What stopped it"
    items = "".join(f'<li>{_check_icon(c.status)}<div><b>{esc(c.name)}</b><small>{esc(c.detail)}</small></div></li>'
                    for c in bad)
    return f'<h3>{title}</h3><ul class="why">{items}</ul>'


def _diff(r: Case) -> str:
    rel = r.relation
    if rel.basis != "text":
        why = ("Why these are linked" if rel.kind != "complement" else "Why this can be an arbitrage")
        return f'<h3>{why}</h3><p class="agree">{esc(rel.rationale)}</p>'
    head = '<h3>Do the two rules agree?</h3>'
    if rel.kind == "none":
        return head + ('<p class="agree">These two markets have no strict logical link, so there is '
                       'nothing to compare.</p>')
    out = [head]
    if not rel.verified:
        out.append('<p class="agree"><b>Could not be traced.</b> ' + esc("; ".join(rel.problems)) + "</p>")
    if not rel.flags:
        out.append('<p class="agree">' + (
            "Yes. Both contracts resolve on the same trigger, from the same source, on the same clock. "
            "Only the deadline differs." if _is_nested(r) else
            "No differences found between the two rules.") + '</p>')
    n0, n1 = r.names[0], r.names[1]
    for f in sorted(rel.flags, key=lambda f: ("high", "medium", "low").index(f.severity)):
        out.append(
            '<div class="diff"><div class="dtop">'
            f'<b>{esc(_FLAG_LABEL.get(f.kind, f.kind))}</b>'
            f'<span class="chip c-{esc(f.severity)}">{_SEV_LABEL.get(f.severity, f.severity)}</span></div>'
            + (f'<p class="dnote">{esc(f.note)}</p>' if f.note else "")
            + '<div class="quotes">'
              f'<div class="q"><b>{esc(n0)} says</b>{esc(f.clause_a)}</div>'
              f'<div class="q"><b>{esc(n1)} says</b>{esc(f.clause_b)}</div></div></div>')
    return "".join(out)


def _world_text(r: Case, w: tuple[int, ...]) -> str:
    ms, kind = r.markets, r.arb.kind
    if kind == "complement":
        return "It resolves Yes" if w[0] else "It resolves No"
    if kind in ("at_most_one", "partition"):
        won = [i for i, x in enumerate(w) if x]
        return f"“{_trim(ms[won[0]].question, 60)}” happens" if won else "None of the listed outcomes happens"
    if _is_nested(r):
        a, b = ms
        return {(1, 1): f"It happens {a.short}",
                (0, 1): f"It happens, but only after the first deadline ({b.short})",
                (0, 0): f"It does not happen {b.short}"}[w]
    if kind == "equivalent":
        return "The event happens (both resolve Yes)" if w[0] else "It does not happen (both resolve No)"
    n0, n1 = r.names
    return f"{n0} resolves {'Yes' if w[0] else 'No'}, {n1} resolves {'Yes' if w[1] else 'No'}"


def _payoff(r: Case) -> str:
    a, d = r.arb, r.decision
    if not a.executable or not a.scenarios or d.status == "NO_ARB":
        return ""
    rows = sorted(a.scenarios, key=lambda s: s["payout"])
    lead = _legs_sentence(r)
    body = []
    shown = rows if len(rows) <= 7 else rows[:6]
    for i, s in enumerate(shown):
        cls = ' class="floor"' if i == 0 else ""
        body.append(f'<tr{cls}><td>{esc(_world_text(r, s["world"]))}'
                    + (" (worst case)" if i == 0 else "") + '</td>'
                    f'<td class="n">{s["payout"] * 100:.0f}¢</td>'
                    f'<td class="n {"pos" if s["profit"] > 0 else ""}">{_c(s["profit"], signed=True)}</td></tr>')
    if len(rows) > len(shown):
        rest = rows[len(shown):]
        lo, hi = min(x["profit"] for x in rest), max(x["profit"] for x in rest)
        body.append(f'<tr><td>…and {len(rest)} more outcomes</td><td class="n">{rest[0]["payout"] * 100:.0f}¢+</td>'
                    f'<td class="n pos">{_c(lo, signed=True)} to {_c(hi, signed=True)}</td></tr>')
    cond = ("" if d.status != "BLOCKED" else
            " This only holds if the contracts really resolve consistently, which the rule "
            "differences above put in doubt.")
    return ('<h3>What you collect in each outcome</h3>'
            f'<p class="sec-note">{esc(lead)}Amounts are per share bought, before any rule-risk haircut.{esc(cond)}</p>'
            '<table class="pay"><thead><tr><th>If this happens</th><th class="n">You collect</th>'
            '<th class="n">Profit</th></tr></thead><tbody>' + "".join(body) + '</tbody></table>')


def _leg_text(leg, r: Case) -> str:
    i = LABELS.index(leg[0])
    m = r.markets[i]
    if _is_nested(r):
        which = "earlier" if i == 0 else "later"
        return f'"{leg[1].title()}" on the {which} contract ({m.short})'
    return f'"{leg[1].title()}" on {m.venue}: “{_trim(m.question, 70)}”'


def _legs_sentence(r: Case) -> str:
    legs = r.arb.legs or ()
    if len(legs) > 4 and len({l[1] for l in legs}) == 1:
        return f'Buy "{legs[0][1].title()}" on all {len(legs)} outcomes. '
    texts = [_leg_text(l, r) for l in legs]
    if not texts:
        return ""
    return "Buy " + (", ".join(texts[:-1]) + " and " + texts[-1] if len(texts) > 1 else texts[0]) + ". "


def _find(text: str, clause: str):
    if not clause:
        return None
    i = text.find(clause)
    if i >= 0:
        return i, i + len(clause)
    pat = r"\s+".join(re.escape(p) for p in clause.split())
    m = re.search(pat, text, re.I) if pat else None
    return (m.start(), m.end()) if m else None


def _highlight(rules: str, flags, side: str) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    spans = []
    for f in flags:
        hit = _find(rules, f.clause_a if side == "a" else f.clause_b)
        if hit:
            spans.append([hit[0], hit[1], f.severity])
    spans.sort()
    merged: list[list] = []
    for s in spans:
        if merged and s[0] < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], s[1])
            if rank[s[2]] > rank[merged[-1][2]]:
                merged[-1][2] = s[2]
        else:
            merged.append(s)
    out, pos = [], 0
    for s, e, sev in merged:
        out.append(esc(rules[pos:s]))
        out.append(f'<mark class="sev-{sev}">{esc(rules[s:e])}</mark>')
        pos = e
    out.append(esc(rules[pos:]))
    return "".join(out)


def _full_rules(r: Case) -> str:
    ms = r.markets[:6]
    flags = r.relation.flags if len(r.markets) == 2 else []
    cols = []
    for i, m in enumerate(ms):
        text = _highlight(m.rules, flags, "a" if i == 0 else "b") if flags else esc(m.rules)
        label = r.names[i] if len(r.markets) == 2 else f"{m.venue}: {_trim(m.question, 60)}"
        sub = f' ({esc(m.short)})' if _is_nested(r) else ""
        cols.append(f'<div><b>{esc(label)}{sub}</b><pre>{text}</pre></div>')
    more = f'<p class="sec-note">Showing {len(ms)} of {len(r.markets)} markets.</p>' if len(r.markets) > len(ms) else ""
    return ('<details><summary>Read the full rules' + (' (differences highlighted)' if flags else '')
            + '</summary><div class="rules">' + "".join(cols) + '</div>' + more + '</details>')


def _all_checks(r: Case) -> str:
    items = "".join(f'<li>{_check_icon(c.status)}<div>{esc(c.name)}<small>{esc(c.detail)}</small></div></li>'
                    for c in r.decision.checks)
    return f'<details><summary>All {len(r.decision.checks)} risk checks</summary><ul class="checks">{items}</ul></details>'


def _audit(r: Case) -> str:
    payload = {"pair_id": r.pair_id, "extractor": r.relation.extractor,
               "relation": asdict(r.relation),
               "arb": {k: v for k, v in asdict(r.arb).items() if k != "scenarios"},
               "decision": {k: v for k, v in asdict(r.decision).items() if k != "fills"}}
    return ('<details><summary>Audit trail (everything behind this verdict)</summary>'
            f'<pre class="audit">{esc(json.dumps(payload, indent=2, default=str))}</pre></details>')


def _title(r: Case) -> str:
    ms = r.markets
    if len(ms) == 1:
        return f'{esc(ms[0].venue)}: {esc(ms[0].question)}'
    if _is_nested(r) or (len(ms) == 2 and question_stem(ms[0].question) == question_stem(ms[1].question)
                         and ms[0].venue == ms[1].venue):
        stem = re.split(r"\s+by\s+", ms[0].question.strip(), maxsplit=1, flags=re.I)[0].rstrip("?")
        return f'{esc(stem)}? Comparing “{esc(ms[0].short)}” with “{esc(ms[1].short)}”.'
    if len(ms) == 2:
        return (f'<b>{esc(ms[0].venue)}</b>: {esc(ms[0].question)}<br>'
                f'<b>{esc(ms[1].venue)}</b>: {esc(ms[1].question)}')
    names = "; ".join(esc(_trim(m.question, 50)) for m in ms[:3])
    return f'{len(ms)} linked outcomes: {names}' + (f'; and {len(ms) - 3} more' if len(ms) > 3 else '')


def _consistent(r: Case) -> str:
    """When there is nothing to explain: say, in numbers, why the prices are fine."""
    ms, kind = r.markets, r.relation.kind
    mids = [m.mid_yes for m in ms]
    if any(x is None for x in mids):
        return ""
    if _is_nested(r):
        return ('<h3>Are the prices in the right order?</h3><p class="agree">Yes. The earlier deadline is '
                f'priced at {mids[0] * 100:.0f}¢ and the later one at {mids[1] * 100:.0f}¢, which is the right way round: '
                'a later deadline can only be more likely.</p>')
    if kind in ("a_implies_b", "b_implies_a") and len(ms) == 2:
        hi, lo = (0, 1) if kind == "a_implies_b" else (1, 0)
        return ('<h3>Are the prices in the right order?</h3><p class="agree">Yes. The harder condition is priced at '
                f'{mids[hi] * 100:.0f}¢ and the easier one at {mids[lo] * 100:.0f}¢, which is the right way round.</p>')
    if kind == "equivalent" and len(ms) == 2:
        return ('<h3>Do the prices match?</h3><p class="agree">They are close enough that costs eat any gap: '
                f'{esc(ms[0].venue)} says {mids[0] * 100:.0f}¢ and {esc(ms[1].venue)} says {mids[1] * 100:.0f}¢ '
                'for what should be the same outcome.</p>')
    if kind in ("at_most_one", "partition"):
        total = sum(mids)
        return ('<h3>Do the prices add up?</h3><p class="agree">The Yes prices add up to '
                f'{total * 100:.0f}¢. Anything far from 100¢ would be a gap; this is close enough that '
                'spreads and fees remove it.</p>')
    return ""


def _card(r: Case, scale: float, limits: Limits, extra: str = "") -> str:
    label, tone, icon = _verdict(r, limits)
    d = r.decision
    flags_open = r.relation.flags
    parts = [
        f'<article class="card">{_stamp(tone, icon, label)}<div>',
        f'<h2><span class="vtag">{esc(label)}.</span> {esc(d.headline)}</h2>',
        f'<p class="mkt">{_title(r)}</p>',
        extra,
    ]
    ladder = _ladder(r, scale, limits)
    if ladder:
        parts.append('<h3>Where the profit goes</h3>'
                     '<p class="sec-note">Cents earned per share bought. The grey part of each bar is what that step took away.</p>'
                     + ladder + _total_line(r))
    else:
        parts.append(_consistent(r))
    parts.append(_why(r))
    parts.append(_diff(r))
    parts.append(_payoff(r))
    parts.append(_all_checks(r))
    parts.append(_full_rules(r) if flags_open or r.relation.kind != "none" else "")
    parts.append(_audit(r))
    parts.append('</div></article>')
    return "".join(parts)


# --------------------------------------------------------------------------------------
# Page-level pieces
# --------------------------------------------------------------------------------------
def _hero(reports: list[Case], limits: Limits) -> str:
    look = [r for r in reports if r.arb.naive_edge > 0.005]
    hold = [r for r in look if r.decision.status in ("OK", "WARN")]
    rules = [r for r in look if r.decision.status == "BLOCKED" and not limits.kill_switch
             and any(f.severity == "high" for f in r.relation.flags)]
    costs = [r for r in look if r.decision.status == "NO_ARB"]
    if look:
        fig = f'{len(hold)}<span class="of"> of {len(look)}</span>'
        lead = ("apparent arbitrages hold up once spreads, order-book depth and the fine print are counted."
                if len(look) != 1 else "apparent arbitrage holds up once spreads, order-book depth and the fine print are counted.")
    else:
        fig, lead = "0", "pairs look like an arbitrage on prices alone."
    bits = []
    if rules:
        bits.append(f"{_plural(len(rules), 'had rules that do not line up', 'had rules that do not line up')}")
    if costs:
        bits.append(f"{_plural(len(costs), 'lost its profit', 'lost their profit')} to spreads, thin order books or "
                    "the cost of tying up money")
    rest = ("The others were stopped before they could cost you anything: " + ", and ".join(bits) + "."
            if bits else "")
    if limits.kill_switch:
        rest = "The kill switch is on, so every trade is blocked."
    return (f'<section class="hero"><div class="hero-fig">{fig}</div>'
            f'<p class="lead">{esc(lead)}</p>' + (f'<p class="rest">{esc(rest)}</p>' if rest else "") + '</section>')


def _controls(limits: Limits) -> str:
    def item(k, v, cls=""):
        return f'<div><dt>{esc(k)}</dt><dd class="{cls}">{esc(v)}</dd></div>'
    return ('<dl class="controls">'
            + item("Kill switch", "ON, all trades blocked" if limits.kill_switch else "Off",
                   "on" if limits.kill_switch else "")
            + item("Max per trade", _money(limits.max_notional))
            + item("Max across all trades", _money(limits.max_total_notional))
            + item("Min profit after costs", _c(limits.min_edge) + " per share")
            + item("Min extractor confidence", f"{limits.min_confidence:.0%}")
            + item("Min yearly return", f"{limits.min_annualised:.0%}")
            + '</dl><p class="note">Read-only: this tool never places orders. The language model only reads '
              'the rules. Every number and every limit is plain code, and the model can only make a trade '
              'less likely, never more.</p>')


def _nice_max(x: float) -> float:
    for step in (8, 12, 20, 40, 60, 80, 120, 160, 240, 360, 480, 720, 1440):   # divisible by 4: clean ticks
        if x <= step:
            return float(step)
    return float(math.ceil(x / 1440) * 1440)


def _survival(sv: dict) -> str:
    curve = [(t / 60.0, s) for t, s in sv["curve"]]
    xmax = _nice_max(max(t for t, _ in curve) if len(curve) > 1 else 5)
    W, H, L, R, T, B = 680, 250, 52, 22, 16, 40
    x0, x1, y0, y1 = L, W - R, T, H - B

    def X(m): return x0 + (x1 - x0) * m / xmax
    def Y(s): return y1 - (y1 - y0) * s

    d = f"M{X(0):.1f},{Y(1):.1f}"
    last = 1.0
    for t, s in curve[1:]:
        d += f" H{X(t):.1f} V{Y(s):.1f}"
        last = s
    d += f" H{X(xmax):.1f}"
    grid = "".join(f'<line x1="{x0}" x2="{x1}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="var(--grid)" stroke-width="1"/>'
                   f'<text x="{x0 - 8}" y="{Y(v) + 4:.1f}" text-anchor="end">{int(v * 100)}%</text>'
                   for v in (0, .25, .5, .75, 1))
    xt = "".join(f'<text x="{X(m):.1f}" y="{y1 + 18}" text-anchor="middle">{m:g}</text>'
                 for m in [xmax * i / 4 for i in range(5)])
    med = sv.get("median_min")
    med_mark = ""
    if med is not None:
        med_mark = (f'<circle cx="{X(med):.1f}" cy="{Y(.5):.1f}" r="4" fill="var(--series)" '
                    f'stroke="var(--surface)" stroke-width="2"/>'
                    f'<text class="lbl" x="{X(med) + 10:.1f}" y="{Y(.5) - 8:.1f}">Median: {med:.0f} min</text>')
    svg = (f'<svg class="chart survsvg" viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="Share of price gaps still open over time. Median {med:.0f} minutes." '
           if med is not None else f'<svg class="chart survsvg" viewBox="0 0 {W} {H}" role="img" aria-label="Survival curve" ')
    svg += (f'data-curve="{esc(json.dumps([[round(t, 3), round(s, 4)] for t, s in curve]))}" '
            f'data-x0="{x0}" data-x1="{x1}" data-xmax="{xmax}">'
            f'{grid}<line x1="{x0}" x2="{x1}" y1="{y1}" y2="{y1}" stroke="var(--axis)" stroke-width="1"/>{xt}'
            f'<text x="{(x0 + x1) / 2}" y="{H - 4}" text-anchor="middle">Minutes since the gap opened</text>'
            f'<path d="{d}" fill="none" stroke="var(--series)" stroke-width="2" stroke-linejoin="round" '
            f'stroke-linecap="round"/>{med_mark}'
            f'<line class="cross" x1="0" x2="0" y1="{y0}" y2="{y1}" stroke="var(--ink2)" stroke-width="1" '
            f'style="display:none"/></svg>')
    marks = [m for m in (1, 2, 5, 10, 15, 30, 60) if m <= xmax]

    def S(m):
        s = 1.0
        for t, v in curve:
            if t <= m:
                s = v
        return s
    rows = "".join(f'<tr><td>{m} min</td><td class="n">{S(m):.0%}</td></tr>' for m in marks)
    badge = '<span class="badge">Synthetic demo data</span>' if sv.get("synthetic") else ""
    caveat = ("This curve comes from randomly generated snapshots. It only shows how the maths works. "
              "Run the collector against live order books before drawing any conclusion. "
              if sv.get("synthetic") else "")
    return ('<section class="surv"><h2 style="font-size:19px">How long do gaps like this last?' + badge + '</h2>'
            f'<p class="sec-note">{esc(caveat)}Based on {sv["n_episodes"]} gaps ({sv["n_open"]} still open when '
            'collection stopped, which the method counts as "at least this long"). Short-lived gaps are the norm, '
            'so a gap you can see on screen may already be gone.</p>'
            + svg +
            '<details><summary>Table view</summary><table class="pay" style="max-width:320px"><thead><tr>'
            '<th>Time since opening</th><th class="n">Gaps still open</th></tr></thead><tbody>'
            + rows + '</tbody></table></details></section>')


def render_report(reports: list[Case], limits: Limits, survival: dict | None = None,
                  demo: bool = False, generated: datetime | None = None) -> str:
    reports = sort_reports(reports)
    top = max([r.arb.naive_edge for r in reports] + [0.10])
    scale = math.ceil(top * 50) / 50                  # round the shared scale up to 2 cents
    cards = "".join(_card(r, scale, limits) for r in reports)
    sub = ("Checks whether price gaps between linked prediction markets are real arbitrage, or only look "
           "real because the rules of the two contracts differ.")
    demo_note = ('<p class="note"><b>Demo data.</b> Every market, company and price on this page is invented, '
                 'so the page can run offline. Nothing here is a real trading opportunity.</p>' if demo else "")
    stamp = generated.strftime("%d %b %Y, %H:%M UTC") if generated else ""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Arbcheck: is this arbitrage real?</title>'
            f'<style>{CSS}</style></head><body><div class="wrap">'
            '<header><h1>Arbcheck: is this arbitrage real?</h1>'
            f'<p class="sub">{esc(sub)}</p></header>'
            + _hero(reports, limits) + demo_note + _controls(limits) + cards
            + (_survival(survival) if survival else "")
            + '<p class="foot">Not financial advice. A price gap that survives every check here can still lose '
              'money if a platform changes a rule, settles late or goes down. '
              + (f'Generated {esc(stamp)}.' if stamp else '') + '</p>'
            + '</div><div id="tip" role="tooltip"></div>'
            f'<script>{JS}</script></body></html>')
