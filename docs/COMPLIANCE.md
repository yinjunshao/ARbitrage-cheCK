# Compliance notes (stub, not legal advice)

- Prediction markets only. Crypto categories are excluded at discovery.
- Read-only public data through the core's allowlisted client. The frame has no HTTP client of its own.
- Kalshi data only with `--kalshi-member` (`run.bat live-kalshi`), app bound to 127.0.0.1. Kalshi prices are
  stripped from `labels.jsonl`, and the core's decision log is skipped when a live Kalshi leg is priced.
  The frame audit log still records the core's derived numbers for that check: clear `data/frame/` after a session.
- No order placement code. Every screen says "Paper only. No orders are placed."
- The frame can never loosen a core guard verdict.
- LLM calls go to Google Gemini (AI Studio key). Market titles and rules text are sent to Google; no personal data.
- Demo/replay markets are arbcheck's invented universe; `data/frame/fixtures/markets.json` holds SAMPLE markets for unit tests.
