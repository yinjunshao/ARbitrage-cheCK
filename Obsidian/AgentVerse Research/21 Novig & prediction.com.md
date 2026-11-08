---
tags: [agentverse, data, venues, how-to]
created: 2026-11-08
---
# 21 · Novig as a venue, and checking prediction.com's arbitrage list

Related: [[16 Live Data Sources]] · [[20 Historical Data Test — How To Run]] · [[18 Smart Loop, Sports & Test Data]] · [[17 Run, Config & Operations]].

## Novig (US sports exchange): supported now
- **Public, unauthenticated API:** `api.novig.com/v3/public/…` serves events, markets and order books. We read it the same way we read Polymarket: GET only, allowlisted, rate-spaced, cached. **The signed trading API is never used.**
- **Markets used:** match results for the ticked sports. Football has `MONEYLINE_3_WAY_WIN` ("Aston Villa to win", Yes/No) and `MONEYLINE_3_WAY_DRAW`; other sports use `MONEY`. Player props are skipped.
- **Books:** each outcome has resting bids; the ask on Yes = 1 − the best No bid (Novig's own definition). Contracts pay 1¢, so 100 contracts = one $1 share.
- **Fee:** coefficient 0.06, charged by Novig **in-play only**. We always apply 0.06 × p × (1 − p): cautious, and our reading of the coefficient, to be verified.
- **Rules text:** Novig's API has none. The checker writes a labelled summary ("Summary written by the checker…"), so Gemini's rule comparison treats Novig's side as unquoted. Expect "look closely" at best.
- **Same match, two venues:** matched by code on team names (with aliases like "Man Utd" ↔ "Manchester United FC") and kickoff within 3 h. They're judged first, but still go through Gemini and you before the core prices them.
- **Live check (8 Nov):**
  - Novig had 15 EPL result markets, matched to 15 Polymarket pairs.
  - The core priced Novig's real books.
  - Aston Villa vs Man Utd: "prices look 1.0¢ apart, but spreads and thin order books remove the profit", with definition, settlement-source and deadline flags.
- **App:** "Novig" is a ticked source on the Check page. Pasting a Novig link (any URL containing a Novig event or market id) works in **Check a pair**.
- **Terms:** Novig's Terms of Use (Novig Sweeps, 21+, eligible US states) say nothing about public data. We use it for internal checking only and never redistribute it.

## prediction.com (formerly Prediction Hunt): check *their* claims with *our* core
- **Their scanner** (prediction.com/arbitrage) lists cross-venue "arbitrage" (Polymarket, Kalshi, Novig, PredictIt…) with ROI after fees, a max wager and the legs.
- **Their terms:** **no scraping outside the API**, no redistributing their data, personal or internal use only.
- **The API:**
  - `GET /v2/arb` returns opportunities with `roi_pct`, `max_wager_usd`, `detected_at`, and per leg: platform, `market_id`, `source_url`, price, liquidity and fee.
  - It needs their **Dev tier ($49/month)**; the free tier (1,000 requests a month) doesn't include it.
  - Set `PREDICTION_API_KEY` in `.env`.
- **What we do:** every opportunity becomes a pair in our pipeline: suggestion rules, Gemini rule comparison, your confirmation, then the core on live books. The result page shows **their claimed ROI next to our core's verdict**. This is the clearest demo of a *false-arbitrage checker*.
- **Venues we can't read** (PredictIt, ProphetX, Opinion, …) are skipped with a note, never guessed.

## How to run it yourself
**A · Free, no key: check one of their opportunities by hand**
1. Start the app: double-click `run.bat`, then **1**.
2. In your browser, open https://prediction.com/arbitrage and pick an opportunity, ideally Polymarket ↔ Novig (or Kalshi, if you're a member).
3. Copy its two **Open market** links into **Event A** and **Event B** under *Check a pair*, then press **Check this pair**.
4. On *Opportunities*, read Gemini's rule comparison, then **Confirm & price with core**.
5. On the result page, compare their ROI (as shown on their page) with our verdict, then open **Detailed report** for fees, depth and guard checks.

**B · With a Dev-tier key: check their whole list**
1. Add `PREDICTION_API_KEY=...` to `.env` and restart the app.
2. On the Check page press **Check prediction.com's list**.
3. Each readable opportunity appears on *Opportunities*, with their claim shown. Confirm the ones you want priced.
4. From the command line, `.venv\Scripts\python.exe -m frame.predictioncom` lists their opportunities and whether both legs are readable. It makes no Gemini calls.

**C · Novig in a normal scan:** keep **Novig** and **Sports → Premier League** ticked, press *Scan venues*. Same-match Polymarket ↔ Novig pairs appear on *Opportunities*, found by "Cross-venue".

## What to expect
- **Their gaps can vanish:** a scanner's "+9% ROI" is often gone once the core walks real depth, applies both venues' fees, and flags rule differences (who settles it, regular time vs extra time, voids). We've seen this on every live pair so far.
- **The comparison is the demo:** when their claim and our verdict differ, the detailed report shows why.
- **Not verified:** prediction.com's fee formulas against ours, and the exact Novig fee formula. Their links for Novig must contain a Novig id to be read.
