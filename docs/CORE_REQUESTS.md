# Requests for the core (Debby)

The frame does not edit `arbcheck-project/`. Requests go here.

1. **Public flag helper.** The adapter calls `HeuristicExtractor._flags(a, b, kind, sa, sb)` (private) to union the
   core's deterministic rule flags. A public `rule_flags(a, b, kind)` would make that seam stable.
2. **Category on `Market`.** Polymarket stubs drop the Gamma category, so the frame guesses crypto from the fee
   (`k == 0.07`) and keywords. A `category` field (or `meta["category"]`) would make the no-crypto rule exact.
3. **Kalshi listing.** A `Kalshi.list_open(limit)` (open events, crypto series dropped, `close_time` re-filtered)
   would let the frame's proactive scan include Kalshi for members.
4. **Provider-neutral LLM call.** `relations.anthropic_call` is the only LLM backend. The frame uses Gemini; a
   `call(system, user) -> str` factory is all `LLMExtractor` needs, so `frame.llm` could supply one if useful.
5. **`core_version`.** `arbcheck.__version__` is 0.1.0; bump it when maths or guard rules change so audit records
   can be tied to a version.
