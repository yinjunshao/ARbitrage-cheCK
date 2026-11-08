---
tags: [agentverse, build, ui, design, how-to]
created: 2026-11-08
---
# 22 · The arbitrage board, the return calculator, and the new design

Related: [[21 Novig & prediction.com]] · [[18 Smart Loop, Sports & Test Data]] · [[14 Built App — How It Works]] · [[17 Run, Config & Operations]]. Repo docs: `docs/DESIGN.md` (the design spec), `docs/FRAME.md`.

## What this is
Our own version of a "cross-venue arbitrage board", **using our checker**. We took the *idea* of a ranked board plus a "calculate returns" tool, and the **published fee arithmetic**. We did **not** use their page, data or code. Every row comes from our scan, priced by our core. Open it at **http://127.0.0.1:8080/board** (first link in the new top bar).

## What the board shows
Each opportunity is one card:
- **Chips** for its type, category, sport and venues; a **LIVE game** tag; a **priority** score (hover for the reasons); and how old the check is.
- **The legs**, e.g. `BUY YES · Polymarket · 63.0¢  +  BUY NO · Novig · 36.5¢  — total 99.5¢ → pays $1.00`, with a link to each venue.
- **The four stages**, in cents per share: price-only, at best asks, after fees and depth, after rule risk. You can see exactly where a gap disappears.
- **Verified return**, profit, capital, annualised return and days to resolution. If nothing survives costs, the card says so plainly.
- **Flags** from the core (rule differences, "who settles it").
- **Calculate returns**, and (for screens) **Send to checker**.

**Three kinds of row, never mixed up:**

| Kind | What it is | Badge |
|---|---|---|
| **Verified** | a human confirmed it after Gemini compared the rules; the core priced it on live books | solid verdict |
| **Screen** | matched by code (same match on two venues, ladders, exact score ⇒ result); priced on live books, but **rules not compared** | dashed "screen only" |
| **Structure** | a venue-declared outcome set, or Yes + No of one market | solid verdict |

**Claims from another scanner** (like prediction.com's) appear as an outlined, hatched box next to ours, never merged into our numbers.

**Headline tiles** are the honest ones:
- verified by our core;
- screens worth a look;
- **price gaps before costs, and how many survive**: the false-arbitrage count a naive scanner would show;
- best verified return;
- rows checked.

**Filters:** search, kind, show (*gaps only* / *survives costs only* / *everything*), type, sport, venues, minimum verified return and minimum profit. **Sorts:** best first, highest verified return, most profit, highest annualised, most liquidity, soonest, newest. The board refreshes itself every 15 seconds and pauses while the calculator is open.

## The math
**Their published method** (from prediction.com's `/v2/arb` docs): total cost = YES price + NO price; net profit = $1 − cost − entry fees; ROI = profit ÷ cost. Fees are per-venue formulas with rounding, and **fees are not linear**, so the fee for one contract can't be scaled up: apply the formula to the whole order.

**The formulas** (in `frame/calc.py`, shown in the app's "How the numbers work"):

| Venue | Taker fee |
|---|---|
| Kalshi | 0.07 × Q × P × (1 − P), up to the cent |
| Polymarket | k × Q × P × (1 − P), up to a tenth of a cent (k by category: crypto 0.07, sports/economics/culture/weather/other 0.05, finance/politics/mentions/tech 0.04, geopolitics 0) |
| Polymarket US | Θ × Q × P × (1 − P), up to the cent (Θ default 0.06) |
| Novig | 0.06 × Q × P × (1 − P) (charged in-play; we always apply it) |
| Opinion | Q × P × (1 − P), up to the cent |
| Predict.fun | (bps ÷ 10,000) × Q × P, up to the cent (default 200 bps) |
| ProphetX | 0.02 × Q × (1 − P) |
| PredictIt | no entry fee; a 10% fee on profit at withdrawal, modelled as an exit fee |

**What we add**, because a headline ROI hides what decides whether a gap is real:
1. **Depth:** the core walks the real order book level by level and stops when another share no longer profits. The calculator's *size curve* shows where the return falls to zero.
2. **Capital to contracts:** it solves for how many baskets your capital buys, fees included.
3. **Break-even and slippage room:** the highest total price that still pays, and cents of slippage each leg can absorb.
4. **Time:** simple annualised (return × 365 ÷ days, as the core does) and compounded.
5. **Settlement risk:** a haircut you choose for the chance venues settle differently.
6. **The payout is the core's floor**, not always $1. Buying "No" on every outcome of a four-outcome set pays $3 per basket.

## A bug the test caught
My first calculator assumed a $1 payout for every structure. On a four-outcome set the core's guaranteed floor is $3, so the calculator showed a **−66% loss on what the core calls a gain**. The payout now comes from the core.

**Cross-check, now a test:** on the core's own order book, fee coefficients and floor, the calculator's profit at the core's size equals the core's within fee rounding ($12.76 on $887).

## The design
Full spec in `docs/DESIGN.md`. In short:
- **Our own look:** warm paper background, deep ink text, **one accent (teal)**, light and dark. The verdict colours always carry an icon and words, never colour alone.
- **Numbers** are monospace and tabular.
- **Claimed vs verified:** claimed numbers are *outlined and hatched*; verified numbers are *solid*.
- **Components:** stat tiles, filter bar, venue chips, verdict badges, opportunity card, stage bars, calculator dialog.
- **Applied app-wide:** a shared top bar (Board · Scan & check · Opportunities · Suggestions · Log), a slim honesty strip ("PAPER ONLY…") and a light/dark toggle. The older pages (Opportunities, result) use the new header and tokens; their inner cards keep the earlier layout.

## How to run it yourself
1. Double-click `run.bat`, choose **1**. The app opens; click **Board** in the top bar.
2. Press **Scan now**. It uses the sources, kinds and sports ticked on *Scan & check* and takes about 3–5 minutes. Keep **Novig** and **Sports → Premier League** ticked so Polymarket ↔ Novig screens appear.
3. Read the tiles. Look at *Price gaps before costs* against *survive costs*.
4. On a card, press **Calculate returns** and change the size or prices. For a screened pair, press **Send to checker** to have Gemini compare the rules, then confirm it on *Opportunities*.
5. No network or Gemini needed: `.venv\Scripts\python.exe scripts\preview_board.py` serves the board on http://127.0.0.1:8090/board using the core's invented demo markets. One of them is a real arbitrage. Use this to look at the design only; **none of those markets are real**.

## What to expect
- A live scan today found Polymarket ↔ Novig Premier League screens. For example Liverpool: 63.0¢ + 36.5¢ = 99.5¢, a 1.2¢ price gap that the core shows dies at the fees-and-depth stage (0.5¢). That honest "gap dies here" view is the product.
- **Not verified:** Novig's exact fee formula; our reading of each venue's category fees against its own page.
