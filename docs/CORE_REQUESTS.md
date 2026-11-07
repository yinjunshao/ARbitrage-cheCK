# Requests for the core (Debby)

The frame does not edit core files. Requests go here.

1. **Push `core/checker` to origin** so the frame can merge it and write `CORE_MAP.md` from real code.
2. **One entry point** that takes legs + a relation + size and returns a priced verdict, e.g.
   `evaluate(rel, size) -> Opportunity`. Pure, no I/O.
3. **Return the guard verdict and each guard check** with the numbers (`verdict`, `guard_checks[{name, passed, detail}]`).
4. **`core_version`** string on every result, for the audit log.
5. **Expose the venue client** as `list_open_markets(venues)` returning normalised markets
   (`venue, id, event_id, title, outcome_label, rules_text, close_time_utc, category, yes/no bid/ask, url`).
6. **Expose `parse_link(url)`** from `venues.py`.
7. If `relations.py --llm` can return quotes per topic (data source, Yes definition, time zone, deadline, tie-break),
   expose it so the frame reuses it instead of duplicating. Note: the frame calls **Gemini**, not Claude.
8. Agree the relation names (see table in `CORE_MAP.md`), especially `mutually_exclusive`.
