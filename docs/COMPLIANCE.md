# Compliance notes (stub, not legal advice)

- Prediction markets only. Crypto categories are excluded at discovery.
- Read-only public data through the core's allowlisted client. The frame has no HTTP client of its own.
- Kalshi data only with `FRAME_KALSHI_MEMBER=1`; not stored beyond the cache.
- No order placement code. Every screen says "Paper only. No orders are placed."
- The frame can never loosen a core guard verdict.
- LLM calls go to Google Gemini (AI Studio key). Market titles and rules text are sent to Google; no personal data.
- `data/frame/fixtures/` contains illustrative SAMPLE markets, not real venue data.
