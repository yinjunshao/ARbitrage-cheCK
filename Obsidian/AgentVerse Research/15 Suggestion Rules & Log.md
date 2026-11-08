---
tags: [agentverse, build, rules, compliance]
created: 2026-11-07
---
# 15 · Suggestion rules and the suggestion log

Related: [[14 Built App — How It Works]] · [[16 Live Data Sources]] · [[17 Run, Config & Operations]]. Code: `frame/rules.py`, `frame/categories.py`, `frame/suggestions.py`. Doc copy: `docs/FRAME.md`.

Rules decide which pairs the app is **allowed to suggest**. They run **before any Gemini call** (so they cost nothing), and **every refusal is written to the suggestion log with the rule and the reason**.

> **Updated 8 Nov: rules are now `suggest-rules/3`.**
> - **New R0:** the same market twice, or a market already effectively settled, is refused.
> - **R5:** also refuses markets that have already closed.
> - **R3:** matches on any shared category.
> - **R6:** applies only to same-question pairs; nested ladders are screened by the core instead.
>
> The table below is the original v2. See [[18 Smart Loop, Sports & Test Data]] for the review and the fixes.

## The six rules (`suggest-rules/2`)
| Rule | What it refuses | Why |
|---|---|---|
| **R1** | Any pair containing a **Manifold** market | Play money; a signal only, never a leg |
| **R2** | **Crypto only with crypto, and only the same coin** (BTC with BTC, never BTC with ETH; a coin that can't be identified is refused) | Requested by the team: "crypto can only be with crypto, and of the same type" |
| **R3** | Pairs in **different categories** (economics vs politics, …) | "Same type" for non-crypto markets |
| **R4** | Pairs where either side's **category is unknown** | Unknown is not assumed to match |
| **R5** | Either market closing **more than 45 days from now**; a missing close date | Arbitrage only works near the deadline, when prices are pinned by resolution and capital comes back quickly |
| **R6** | The two markets closing **more than 14 days apart** | The same question over a different timeframe or year is a different bet, not a price gap |

R5/R6 came from a team remark: *real arbitrage in prediction markets only works close to the deadline, when prices are locked*, so "the same event in another year" is not an opportunity. Limits are settable: `FRAME_MAX_DAYS_TO_CLOSE` (45) and `FRAME_MAX_CLOSE_GAP_DAYS` (14).

**Effect on one live scan:** R5 refused 6,987 pairs and R6 7 (R2 902, R3 1,496). The pile waiting for Gemini fell from ≈13,000 to ≈4,900. A long "Iran blockade by Nov / Dec / Mar" chain disappeared; what remains closes within days (e.g. Bitcoin thresholds for Nov 2–8).

**Pairs the user types in** are never refused (they asked for that exact check); a failed rule shows as a **warning** on the case instead.

### How categories work
Polymarket event **tags** and Kalshi event **category** are mapped to one vocabulary: crypto, economics, politics, sports, science_tech, climate_weather, culture, health, other. Crypto wins over any other tag. R2's "same coin" uses a coin list (BTC, ETH, SOL …); a title naming two coins is "unidentified".

## The suggestion log (`data/frame/suggestions.jsonl`)
Tamper-evident (SHA-256 hash chain, each record includes the previous hash). It records, per pair:
`candidate` → `rule_refused` (rule + reason) → `judged` (Gemini's relation and confidence) → `surfaced` / `filtered` → `human` (confirm / reject, who, note) → `priced` / `repriced` (core verdict) → `venue_set` (exclusive sets priced without the LLM).

Design choices:
- **Once per pair** for `candidate` and `rule_refused`, so a re-scan doesn't rewrite 7 MB each time.
- **Kalshi numbers are redacted** from log rows (`kalshi_redacted`); Kalshi data is never stored.
- The **Suggestions page** shows the chain check, counts by kind, and a filterable table (all / surfaced / refused / judged / human / priced …).
- Also written by the monitor, so a forward record builds up for evaluation (`docs/PROTOCOL.md`).

### Robustness (fixed after the first live runs)
- **Two programs can share one log.** The app and the monitor write the same file; on Windows that corrupted two lines at first. Appends now take an OS file lock and re-read the chain tail when another process wrote. A bad line is skipped and `verify()` reports it instead of crashing; a test runs three processes writing at once.
- A short run once made the chain check fail at the old damaged line; that damage predates the fix.
