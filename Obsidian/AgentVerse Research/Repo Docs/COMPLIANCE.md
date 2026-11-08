---
tags: [agentverse, repo-doc]
source: docs/COMPLIANCE.md
synced: 2026-11-08 11:58
---
> Copy of `docs/COMPLIANCE.md` from the repo (branch `integrated/llm-and-core`), synced 2026-11-08 11:58. Edit the repo file, then re-run `python scripts/sync_vault_docs.py`. Index: [[Repo Docs Index]]

# Compliance notes (stub, not legal advice)

- Prediction markets only. Crypto prediction markets are allowed but only ever paired with crypto markets on
  the same coin (suggestion rule R2); they are never paired with other categories.
- Read-only public data through the core's allowlisted client. The frame has no HTTP client of its own.
- Kalshi data only with `--kalshi-member` (`run.bat live-kalshi`), app bound to 127.0.0.1. Kalshi prices are
  stripped from `labels.jsonl`, and the core's decision log is skipped when a live Kalshi leg is priced.
  The frame audit log still records the core's derived numbers for that check: clear `data/frame/` after a session.
- No order placement code. Every screen says "Paper only. No orders are placed."
- The frame can never loosen a core guard verdict.
- LLM calls go to Google Gemini (AI Studio key). Market titles and rules text are sent to Google; no personal data.
- Demo/replay markets are arbcheck's invented universe; `data/frame/fixtures/markets.json` holds SAMPLE markets for unit tests.
