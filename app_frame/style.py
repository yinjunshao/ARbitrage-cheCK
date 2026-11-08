"""Shared look for the whole app: the tokens and components from docs/DESIGN.md as CSS + small HTML helpers.

Light and dark both defined (NiceGUI adds `body--dark`). All text/background pairs are >= 4.5:1. Every dynamic string
that goes into raw HTML must pass through `esc`."""
from __future__ import annotations

import html

from nicegui import ui

CSS = """
:root{--bg:#F6F4EF;--card:#FFFFFF;--ink:#14213D;--muted:#5B6475;--line:#E4E0D6;--accent:#0F766E;
--go:#166534;--go-bg:#DCFCE7;--care:#92400E;--care-bg:#FEF3C7;--stop:#991B1B;--stop-bg:#FEE2E2;
--flat:#475569;--flat-bg:#F1F5F9;--claim:#6D28D9;--claim-bg:#EDE9FE;--shadow:0 1px 2px rgba(20,33,61,.08)}
.body--dark{--bg:#0F1419;--card:#171D25;--ink:#E8ECF1;--muted:#9AA5B4;--line:#2A3340;--accent:#2DD4BF;
--go:#4ADE80;--go-bg:#12301F;--care:#FBBF24;--care-bg:#33260B;--stop:#F87171;--stop-bg:#361616;
--flat:#94A3B8;--flat-bg:#1F2733;--claim:#A78BFA;--claim-bg:#241B3D;--shadow:0 1px 2px rgba(0,0,0,.4)}
body{background:var(--bg)!important;color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.fa-num{font-family:ui-monospace,"SF Mono",Consolas,monospace;font-variant-numeric:tabular-nums}
.fa-wrap{width:100%;max-width:1180px;margin:0 auto;padding:16px}
.fa-header{background:var(--card)!important;color:var(--ink)!important;border-bottom:1px solid var(--line);box-shadow:none!important}
.fa-header a{color:var(--muted);text-decoration:none;padding:6px 10px;border-radius:8px;font-size:14px}
.fa-header a:hover,.fa-header a.on{background:var(--flat-bg);color:var(--ink)}
.fa-brand{font-weight:700;letter-spacing:-.01em;margin-right:12px}
.fa-strip{font-size:12px;color:var(--muted)}
.fa-h1{font-size:28px;font-weight:700;letter-spacing:-.02em;line-height:1.15}
.fa-sub{color:var(--muted);font-size:14px}
.fa-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;width:100%}
.fa-tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;box-shadow:var(--shadow)}
.fa-tile .k{font-size:12px;color:var(--muted)}
.fa-tile .v{font-size:26px;font-weight:700;line-height:1.2}
.fa-tile .s{font-size:12px;color:var(--muted)}
.fa-bar{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;width:100%}
.fa-card{background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);width:100%;
position:relative;padding:14px 16px 12px 20px;overflow:hidden;transition:box-shadow .12s ease}
.fa-card:hover{box-shadow:0 2px 8px rgba(20,33,61,.14)}
.fa-card:before{content:"";position:absolute;left:0;top:0;bottom:0;width:6px;background:var(--flat)}
.fa-card.go:before{background:var(--go)}.fa-card.care:before{background:var(--care)}.fa-card.stop:before{background:var(--stop)}
.fa-chip{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600;
border:1px solid var(--line);background:var(--flat-bg);color:var(--ink);white-space:nowrap}
.fa-chip.v-polymarket{background:#E0E7FF;color:#1E3A8A;border-color:#C7D2FE}
.fa-chip.v-novig{background:#CCFBF1;color:#115E59;border-color:#99F6E4}
.fa-chip.v-kalshi{background:#FCE7F3;color:#9D174D;border-color:#FBCFE8}
.body--dark .fa-chip.v-polymarket{background:#1E2A55;color:#C7D2FE;border-color:#2F3E7A}
.body--dark .fa-chip.v-novig{background:#0F3A36;color:#99F6E4;border-color:#14524C}
.body--dark .fa-chip.v-kalshi{background:#4A1630;color:#FBCFE8;border-color:#6B2145}
.fa-chip.type{background:transparent}
.fa-badge{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:8px;font-size:13px;font-weight:700}
.fa-badge.go{background:var(--go-bg);color:var(--go)}.fa-badge.care{background:var(--care-bg);color:var(--care)}
.fa-badge.stop{background:var(--stop-bg);color:var(--stop)}.fa-badge.flat{background:var(--flat-bg);color:var(--flat)}
.fa-badge.screen{background:transparent;color:var(--muted);border:1px dashed var(--muted)}
.fa-title{font-size:16px;font-weight:650;line-height:1.3}
.fa-legs{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:8px 0}
.fa-leg{display:inline-flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid var(--line);border-radius:10px;background:var(--bg)}
.fa-leg b{font-size:12px;letter-spacing:.03em}
.fa-plus{color:var(--muted);font-weight:700}
.fa-tot{font-size:13px;color:var(--muted)}
.fa-stages{display:grid;gap:4px;margin:6px 0 8px;max-width:640px}
.fa-stage{display:grid;grid-template-columns:150px 1fr 74px;align-items:center;gap:8px;font-size:12px;color:var(--muted)}
.fa-stage .t{height:8px;background:var(--flat-bg);border-radius:99px;overflow:hidden}
.fa-stage .t i{display:block;height:100%;background:var(--accent);border-radius:99px}
.fa-stage.neg .t i{background:repeating-linear-gradient(45deg,var(--stop) 0 4px,transparent 4px 8px)}
.fa-stage b{text-align:right;color:var(--ink)}
.fa-metrics{display:flex;flex-wrap:wrap;gap:20px;margin:6px 0}
.fa-metric .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.fa-metric .v{font-size:18px;font-weight:700}
.fa-claim{display:flex;gap:10px;align-items:center;margin:6px 0;padding:6px 10px;border:1.5px dashed var(--claim);
border-radius:10px;color:var(--claim);background:repeating-linear-gradient(135deg,var(--claim-bg) 0 6px,transparent 6px 12px);font-size:13px}
.fa-flags{font-size:12px;color:var(--muted)}
.fa-empty{padding:28px;text-align:center;color:var(--muted);border:1px dashed var(--line);border-radius:12px;width:100%}
.fa-calc .res{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.fa-curve{width:100%;height:90px}
.q-btn{text-transform:none!important;letter-spacing:0!important;font-weight:600}
"""

def install() -> None:
    """Add the CSS to EVERY page (shared), once, at app start (call it before ui.run)."""
    ui.add_css(CSS, shared=True)


def brand() -> None:
    """Per-page: the brand colour (Quasar's primary)."""
    ui.colors(primary="#0F766E")


def esc(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def cents(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{d}f}¢"


def money(x: float | None) -> str:
    if x is None:
        return "—"
    return f"${x:,.2f}" if abs(x) < 100 else f"${x:,.0f}"


def pct(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{d}f}%"


VERDICT = {   # key -> (css class, icon, words)
    "worth_a_look": ("go", "✓", "Worth a look"),
    "look_closely": ("care", "!", "Look closely"),
    "blocked": ("stop", "✗", "Blocked"),
    "nothing_to_do": ("flat", "–", "Nothing to do"),
}


def badge(verdict: str, screen: bool = False) -> str:
    cls, icon, words = VERDICT.get(verdict, ("flat", "•", verdict))
    if screen:
        return (f'<span class="fa-badge screen" title="Priced by the core from live books, but the rule texts have '
                f'not been compared yet. Send it to the checker to verify.">{icon} {esc(words)} · screen only</span>')
    return f'<span class="fa-badge {cls}">{icon} {esc(words)}</span>'


def venue_chip(v: str) -> str:
    return f'<span class="fa-chip v-{esc(v)}">{esc(v.title())}</span>'


def stat_tile(label: str, value: str, sub: str = "") -> str:
    return f'<div class="fa-tile"><div class="k">{esc(label)}</div><div class="v fa-num">{esc(value)}</div><div class="s">{esc(sub)}</div></div>'


def header(active: str = "") -> None:
    """Top navigation. `active` = the current path."""
    brand()
    nav = (("/board", "Board"), ("/", "Scan & check"), ("/candidates", "Opportunities"), ("/suggestions", "Suggestions"),
           ("/log", "Log"))
    with ui.header().classes("fa-header items-center gap-1 px-4 py-2"):
        ui.label("False-Arb Checker").classes("fa-brand")
        for path, name in nav:
            ui.link(name, path).classes("on" if path == active else "")
        ui.space()
        dark = ui.dark_mode()
        ui.button(icon="dark_mode", on_click=dark.toggle).props("flat round dense").tooltip("Light / dark")
