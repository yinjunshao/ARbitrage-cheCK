# Design: the arbitrage board and calculator

Our own design. It borrows from nobody's page: what we took from prediction.com is the *idea* of a ranked board of
cross-venue gaps and a "calculate returns" tool, and the published fee arithmetic (see `frame/calc.py`). Every
number on our board comes from our own scan and our own core.

## Problem
A scanner board is useful only if you can tell, at a glance, **what is claimed, what is verified, and what is just
a price gap**. Most boards show one big ROI figure. Ours has to show why a gap is or isn't real.

## Principles
1. **Evidence first.** Every opportunity shows its *four stages* (price-only gap → at best asks → after fees → after
   depth and rule risk). The verified number is the last one, and it is the headline.
2. **Claimed vs verified.** A claim from another scanner is drawn *outlined and hatched*; a number our core computed
   is *solid*. They are never the same colour.
3. **Never colour alone.** Every verdict has an icon and words as well as a colour.
4. **Numbers are tabular**: monospace, right-aligned, with units.
5. **Calm.** One accent colour, no gradients, motion only for loading and hover.

## Tokens
| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F6F4EF` | `#0F1419` | page |
| `--card` | `#FFFFFF` | `#171D25` | cards, dialogs |
| `--ink` | `#14213D` | `#E8ECF1` | text |
| `--muted` | `#5B6475` | `#9AA5B4` | secondary text |
| `--line` | `#E4E0D6` | `#2A3340` | borders |
| `--accent` | `#0F766E` | `#2DD4BF` | primary actions, links |
| `--go` / `--go-bg` | `#166534` / `#DCFCE7` | `#4ADE80` / `#12301F` | worth a look |
| `--care` / `--care-bg` | `#92400E` / `#FEF3C7` | `#FBBF24` / `#33260B` | look closely |
| `--stop` / `--stop-bg` | `#991B1B` / `#FEE2E2` | `#F87171` / `#361616` | blocked |
| `--flat` / `--flat-bg` | `#475569` / `#F1F5F9` | `#94A3B8` / `#1F2733` | nothing to do |
| `--claim` | `#6D28D9` | `#A78BFA` | another scanner's claim |

Type: system sans for text; `ui-monospace, "SF Mono", Consolas` with `tabular-nums` for every number. Scale
12 / 14 / 16 / 20 / 28. Spacing 4 / 8 / 12 / 16 / 24. Radius 12 (cards), 999 (chips). Shadow `0 1px 2px` only.
Motion 120 ms ease.

## Components
| Component | Purpose | Variants / states |
|---|---|---|
| **StatTile** | one headline number with a label ("Verified now", "Gaps before costs") | default, empty ("0"), loading |
| **FilterBar** | venue chips, kind, min return, min profit, "core-priced only", sort | each control has a clear default; active filters are filled |
| **VenueChip** | venue name with a small mark | polymarket, kalshi, novig, manifold, other |
| **VerdictBadge** | icon + words + colour | worth a look, look closely, blocked, nothing to do, **screen only** (not confirmed) |
| **OpportunityCard** | one opportunity | left stripe = verdict colour; *verified*, *screen*, *claimed* |
| **StageBar** | the four stages as four short bars of one scale | shows where the gap disappears |
| **LegRow** | `BUY YES · Polymarket · 27¢` + `BUY NO · Novig · 63¢` | a plus sign between, total cost under |
| **CalculatorDialog** | capital or contracts, prices, fees, days → profit, return, APY, break-even, size curve | manual, or prefilled from a card |

## OpportunityCard anatomy
```
┃ [Cross-platform] [sports] [Premier League]                         priority 41   2 s old
┃ Aston Villa to win · Man Utd – Aston Villa
┃ BUY YES  Polymarket  27¢   +   BUY NO  Novig  63¢        total 90¢ → pays $1.00
┃ ▇▇▇▇▇▇▇▇▇▇ price-only 10.0¢   ▇▇▇▇▇▇▇ at asks 8.0¢   ▇▇▇ after fees 3.2¢   ▁ after depth 0.0¢
┃ VERIFIED  net return 0.0%   profit $0.00 on $0     APY —     fillable ≈ $0
┃ CLAIMED   prediction.com 9.7%   (outlined, hatched)
┃ [Calculate returns]  [Open in checker]  [Polymarket ↗]  [Novig ↗]
```

## Accessibility
- Cards are `article` regions with an accessible name (the event). The stripe is decorative.
- Badges carry text. Tooltips repeat for keyboard users (focus shows them).
- Contrast: all text/background pairs above are at least 4.5:1.
- The calculator is a modal dialog: focus moves in, Esc closes, and Enter recalculates.

## Open questions
- A PredictIt/Betfair row needs a venue we don't read yet; for now the calculator accepts them as manual legs.
- Whether "screen only" rows (no rule comparison yet) should be hidden by default. They are shown, labelled.
