"""The page people use: type the markets you think are an arbitrage, get a plain answer.

Layout, top to bottom:
  1. The question box (and venue status, limits tucked away).
  2. What we understood: each line you typed and the market we matched it to, so a wrong
     match is obvious before you trust anything.
  3. The answer to YOUR idea in one sentence, then the evidence (the same ticket the report uses).
  4. Related opportunities we found around it, best first. Anything that checks out but is
     worthless is hidden and counted, never silently dropped.
  5. Price signals from play-money venues, clearly not tradeable.
  6. What data we read and the terms we follow.

All text from the internet (market titles, rules) goes through `esc` before it is placed in HTML.
"""
from __future__ import annotations

import html
import math

from .compliance import VENUE_TERMS
from .finder import Findings, Signal
from .guard import Limits
from .pipeline import Case
from .report import CSS, JS, _card, esc

EXAMPLES = [
    ("Two deadlines of one question", ["Will Orion Tech announce a stock split by December 31, 2026?",
                                       "Will Orion Tech announce a stock split by March 31, 2027?"]),
    ("Looks real, is not", ["Will Helix Motors recall the H3 model by December 31, 2026?",
                            "Will Helix Motors recall the H3 model by March 31, 2027?"]),
    ("Same event, two venues", ["Will Zephyr Bank cut interest rates by December 31, 2026?"]),
    ("Price thresholds", ["Will the Vega 500 close above 5,500 on December 31, 2026?",
                         "Will the Vega 500 close above 5,000 on December 31, 2026?"]),
]

APP_CSS = """
.top{display:flex;flex-wrap:wrap;gap:6px 24px;align-items:baseline;justify-content:space-between}
.demo{background:var(--t-med);border-radius:4px;padding:8px 12px;margin:18px 0 0;font-size:13.5px;max-width:74ch}
form.ask{margin:28px 0 0}
form.ask label.big{display:block;font-size:19px;font-weight:600;margin:0 0 4px;letter-spacing:-.005em}
form.ask .hint{color:var(--ink2);font-size:13.5px;margin:0 0 10px;max-width:70ch}
textarea{width:100%;min-height:104px;resize:vertical;font:16px/1.5 system-ui,sans-serif;color:var(--ink);
  background:var(--surface);border:1px solid var(--axis);border-radius:6px;padding:12px 14px}
textarea:focus-visible,input:focus-visible,button:focus-visible,a:focus-visible,summary:focus-visible{
  outline:2px solid var(--series);outline-offset:2px}
.row{display:flex;flex-wrap:wrap;gap:12px 18px;align-items:center;margin:12px 0 0}
button.go{font:600 15px system-ui,sans-serif;background:var(--ink);color:var(--plane);border:0;border-radius:6px;
  padding:11px 22px;cursor:pointer}
button.go[disabled]{opacity:.6;cursor:progress}
.ex{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;font-size:13px;color:var(--ink2)}
.ex a{color:var(--ink);text-decoration:none;border:1px solid var(--axis);border-radius:14px;padding:2px 11px}
.ex a:hover{background:var(--grid)}
.venues{font-size:13px;color:var(--ink2);margin:14px 0 0;max-width:80ch}
.venues b{color:var(--ink);font-weight:600}
.lim{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px 18px;margin:10px 0 0;font-size:13px}
.lim label{display:block;color:var(--ink2)}
.lim input[type=number]{width:100%;font:15px system-ui,sans-serif;padding:6px 8px;border:1px solid var(--axis);
  border-radius:4px;background:var(--surface);color:var(--ink)}
section.block{margin:48px 0 0}
section.block>h2{font-size:21px;margin:0 0 4px}
.understood{list-style:none;margin:10px 0 0;padding:0}
.understood li{padding:10px 0;border-top:1px solid var(--grid)}
.understood li:first-child{border-top:0}
.q-in{color:var(--ink2);font-size:13.5px;overflow-wrap:anywhere}
.m{margin:2px 0 0;overflow-wrap:anywhere}
.tag{display:inline-block;font-size:12px;font-weight:600;padding:1px 9px;border-radius:10px;background:var(--t-low);margin-right:6px}
.tag.play{background:var(--t-med)}
.answer{display:grid;grid-template-columns:44px minmax(0,1fr);column-gap:16px;margin:0}
.answer h2{font-size:28px;line-height:1.2;letter-spacing:-.015em;max-width:40ch}
.answer p{max-width:62ch;margin:10px 0 0}
.fam{margin:8px 0 0;display:flex;flex-wrap:wrap;gap:6px}
.fam .tag{margin:0}
.why-looked{color:var(--ink2);font-size:13.5px;margin:6px 0 0}
.sig{border-collapse:collapse;width:100%;max-width:720px;font-size:14px;margin-top:8px}
.sig th{text-align:left;font-weight:500;color:var(--ink2);font-size:12.5px;padding:6px 12px 6px 0;border-bottom:1px solid var(--axis)}
.sig td{padding:9px 12px 9px 0;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums}
.sig .n{text-align:right}
.terms dl{margin:10px 0 0}
.terms dt{font-weight:600;margin:14px 0 0}
.terms dd{margin:2px 0 0;color:var(--ink2);font-size:13.5px;max-width:78ch}
.terms a{color:var(--ink)}
.quiet{color:var(--ink2);font-size:13.5px;max-width:70ch}
.tight{margin:0}.lead-in{margin-top:14px}.total-note{margin-top:36px}
"""

APP_JS = """
(function(){
  var f=document.querySelector('form.ask');
  if(f){f.addEventListener('submit',function(){var b=f.querySelector('button.go');
    if(b){b.disabled=true;b.textContent='Checking\\u2026';}});}
})();
"""


def _example_link(lines: list[str]) -> str:
    from urllib.parse import quote
    return "/?q=" + quote("\n".join(lines))


def _form(q: str, limits: Limits, fee_pct: str, venues: list[str], skipped: dict[str, str]) -> str:
    def num(name, label, value, step="any", lo="0"):
        return (f'<div><label for="{name}">{esc(label)}</label>'
                f'<input type="number" id="{name}" name="{name}" value="{esc(value)}" min="{lo}" step="{step}"></div>')
    on = ", ".join(f"<b>{esc(v)}</b>" for v in venues)
    off = " ".join(f"<b>{esc(k)}</b> is off: {esc(v)}" for k, v in skipped.items())
    ex = "".join(f'<a href="{esc(_example_link(lines))}">{esc(name)}</a>' for name, lines in EXAMPLES)
    return (
        '<form class="ask" method="get" action="/">'
        '<label class="big" for="q">Which markets do you think are an arbitrage?</label>'
        '<p class="hint">Paste links from Polymarket, Kalshi or Manifold, or type each event in plain words. '
        'One per line, up to four. We check whether it really locks in a profit after spreads, order-book depth, '
        'fees and the fine print, then look for related opportunities.</p>'
        f'<textarea id="q" name="q" maxlength="1200" placeholder="Will X happen by June 30?&#10;Will X happen by December 31?">{esc(q)}</textarea>'
        f'<div class="row"><button class="go" type="submit">Check it</button>'
        f'<div class="ex"><span>Try one:</span>{ex}</div></div>'
        f'<p class="venues">Reading {on}. {off}</p>'
        '<details><summary>Your limits</summary><div class="lim">'
        + num("max_notional", "Most to put in one trade ($)", f"{limits.max_notional:g}")
        + num("max_total", "Most across all trades ($)", f"{limits.max_total_notional:g}")
        + num("min_edge", "Smallest profit worth it (¢ per share)", f"{limits.min_edge * 100:g}", step="0.1")
        + num("fee", "Fee override (% of money spent, blank = each venue's own)", fee_pct, step="0.1")
        + f'<div><label><input type="checkbox" name="kill" value="1"{" checked" if limits.kill_switch else ""}> '
          'Kill switch: block every trade</label></div></div></details></form>'
    )


def _understood(f: Findings) -> str:
    items = []
    for res in f.inputs:
        head = f'<div class="q-in">You typed: {esc(res.line)}</div>'
        if res.kind == "unreadable":
            body = f'<p class="m">{esc(res.notes[0])}</p>' if res.notes else ""
        elif not res.matches:
            body = ('<p class="m">We could not find a close match. Try the event in different words, or paste a link.'
                    '</p>')
        else:
            rows = []
            for m in res.matches[:12]:
                mk = m.market
                tags = f'<span class="tag{" play" if not mk.tradeable else ""}">{esc(mk.venue)}' \
                       f'{", play money" if not mk.tradeable else ""}</span>'
                price = f' ({mk.mid_yes * 100:.0f}¢ Yes)' if mk.mid_yes is not None else ""
                how = "from your link" if m.how == "link" else f"{m.score:.0%} word match"
                rows.append(f'<p class="m">{tags}{esc(mk.question)}{esc(price)} <span class="q-in">({esc(how)})</span></p>')
            body = "".join(rows)
        items.append(f"<li>{head}{body}</li>")
    return ('<section class="block"><h2>What we understood</h2>'
            '<p class="quiet">Check these are the markets you meant before you trust anything below.</p>'
            f'<ul class="understood">{"".join(items)}</ul></section>')


def _answer(f: Findings, limits: Limits) -> str:
    v, best = f.verdict, f.best
    s = best.decision.status if best else ""
    if v == "real":
        if s == "OK":
            title, tone, icon = "Yes, this looks like a real arbitrage.", "good", "check"
        else:
            title, tone, icon = "Possibly, but look closely first.", "warn", "alert"
        body = best.decision.headline
    elif v == "false":
        title, tone, icon = "No. It looks like an arbitrage but is not one.", "crit", "cross"
        body = (f"A price-only scanner sees {best.arb.naive_edge * 100:.0f}¢ per share here. "
                "But the contracts' fine print does not line up (see the evidence below), so the profit is not "
                "locked in and you could lose money on both sides.")
    elif v == "blocked":
        title, tone, icon = "Blocked by your risk controls.", "crit", "cross"
        body = best.decision.headline
    elif v == "single":
        title, tone, icon = "On its own, this market has no arbitrage.", "none", "dash"
        body = ("Yes and No together cost about $1, so nothing is locked in. "
                "Give us a second market to compare, or see what we found around it below.")
    elif v == "unlinked":
        title, tone, icon = "No arbitrage: we cannot link these markets.", "none", "dash"
        body = ("Arbitrage needs the outcomes to be logically tied, for example one forcing the other. "
                "We could not find such a link in their rules, so a price difference here is just a difference of opinion, "
                "not a guaranteed profit.")
    elif v == "unmatched":
        title, tone, icon = "We could not find those markets.", "none", "dash"
        body = "See the notes above. A link from Polymarket, Kalshi or Manifold, or the event in different words, usually fixes it."
    else:
        title, tone, icon = "No arbitrage: the prices are consistent.", "none", "dash"
        body = best.decision.headline
    from .report import _stamp
    return ('<section class="block"><h2>The answer for your idea</h2><div class="answer lead-in">'
            f'{_stamp(tone, icon, title)}<div><h2>{esc(title)}</h2><p>{esc(body)}</p></div></div></section>')


def _fam(c: Case) -> str:
    chips = [c.family_label] + sorted({m.venue for m in c.markets})
    tags = "".join(f'<span class="tag">{esc(x)}</span>' for x in chips)
    why = f'<p class="why-looked">{esc(c.note)}</p>' if c.note else ""
    return f'<div class="fam">{tags}</div>{why}'


def _signals(sigs: list[Signal]) -> str:
    if not sigs:
        return ""
    rows = "".join(
        f'<tr><td>{esc(s.anchor.venue)}: {esc(s.anchor.question[:70])}</td>'
        f'<td class="n">{(s.anchor.mid_yes or 0) * 100:.0f}¢</td>'
        f'<td class="n">{s.other.prob * 100:.0f}¢</td>'
        f'<td class="n">{s.gap * 100:+.0f}¢</td></tr>' for s in sigs)
    return ('<section class="block"><h2>Price signals from play-money markets</h2>'
            '<p class="quiet">Manifold uses play money, so you cannot trade these. A big gap is still useful: '
            'it can mean a price is stale, or that the two questions are not quite the same.</p>'
            '<table class="sig"><tr><th>Real-money market</th><th class="n">Its price</th>'
            f'<th class="n">Manifold says</th><th class="n">Gap</th></tr>{rows}</table></section>')


def _terms() -> str:
    items = []
    for t in VENUE_TERMS.values():
        items.append(
            f'<dt>{esc(t.name)}{" (play money)" if not t.tradeable else ""}</dt>'
            f'<dd><b>What we read:</b> {esc(t.uses)}<br><b>How often:</b> {esc(t.our_rate)}<br>'
            f'<b>Their rules:</b> {esc(t.their_rule)}<br><b>Good to know:</b> {esc(t.restriction)}<br>'
            f'<a href="{esc(t.terms_url)}" rel="noreferrer noopener">Terms</a> and '
            f'<a href="{esc(t.docs_url)}" rel="noreferrer noopener">API docs</a></dd>')
    return ('<section class="block terms"><details><summary>What data we read and the rules we follow</summary>'
            '<p class="quiet">This tool only reads public market data through each venue\'s documented API. It never logs in, '
            'never places an order and never scrapes web pages. This is a summary, not legal advice: the venues\' own terms '
            f'are what count.</p><dl>{"".join(items)}</dl></details></section>')


def render_app(*, q: str, limits: Limits, fee_pct: str, venues: list[str], skipped: dict[str, str],
               findings: Findings | None, demo: bool, error: str = "") -> str:
    body = []
    if demo:
        body.append('<p class="demo"><b>Demo mode.</b> Every market, company and price here is invented, so the app can run '
                    'offline. Nothing on this page is a real opportunity.</p>')
    body.append(_form(q, limits, fee_pct, venues, skipped))
    if error:
        body.append(f'<p class="demo" role="alert">{esc(error)}</p>')
    if findings is not None:
        f = findings
        body.append(_understood(f))
        if f.notes:
            body.append('<p class="quiet">' + " ".join(esc(n) for n in f.notes) + "</p>")
        body.append(_answer(f, limits))
        shown_related = [c for c in f.related if c.decision.status != "NO_ARB"]
        hidden = len(f.related) - len(shown_related)
        top = max([c.arb.naive_edge for c in f.yours + shown_related] + [0.10])
        scale = math.ceil(top * 50) / 50
        if f.yours:
            body.append('<section class="block"><h2>The evidence</h2>'
                        '<p class="quiet">Every number below is plain code. A language model, if used, only reads the rules.</p>'
                        + "".join(_card(c, scale, limits, _fam(c)) for c in f.yours[:4]) + "</section>")
        if shown_related:
            body.append('<section class="block"><h2>Related opportunities we found</h2>'
                        '<p class="quiet">Linked to the markets you gave us. Best first.</p>'
                        + "".join(_card(c, scale, limits, _fam(c)) for c in shown_related[:6]) + "</section>")
        elif f.yours:
            body.append('<section class="block"><h2>Related opportunities</h2>'
                        '<p class="quiet">We looked at the rest of the event, the same event on the other venues, and each '
                        'market\'s Yes and No, and found nothing that survives costs.</p></section>')
        body.append(_signals(f.signals))
        total = f.checked
        body.append(f'<p class="quiet total-note">Checked {total} combination{"s" if total != 1 else ""} in total'
                    + (f"; {hidden} related one{'s' if hidden != 1 else ''} had no gap left after costs and "
                       f"{'are' if hidden != 1 else 'is'} hidden." if hidden else ".") + "</p>")
    body.append(_terms())
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Arbcheck: is your arbitrage real?</title>'
            f'<style>{CSS}{APP_CSS}</style></head><body><div class="wrap">'
            '<header class="top"><h1>Arbcheck</h1><p class="sub tight">Is the arbitrage you spotted real? '
            'Read-only, never places orders.</p></header>'
            + "".join(body)
            + '<p class="foot">Not financial advice. A gap that survives every check here can still lose money if a '
              'platform changes a rule, settles late or goes down. Check each venue\'s fees before trading; they change.</p>'
            '</div><div id="tip" role="tooltip"></div>'
            f'<script>{JS}{APP_JS}</script></body></html>')


def csp() -> str:
    """Content-Security-Policy that allows exactly our one stylesheet and one script, by hash."""
    import base64
    import hashlib

    def h(text: str) -> str:
        return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"
    return ("default-src 'none'; "
            f"style-src {h(CSS + APP_CSS)}; style-src-attr 'unsafe-inline'; "
            f"script-src {h(JS + APP_JS)}; "
            "img-src data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
