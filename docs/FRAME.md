# LLM frame (branch `frame/llm-layer`)

```
discover → candidates → judge (Gemini) → rule-diff (Gemini, quotes verified)
   → [HUMAN CONFIRM] → core.price → narrate (Gemini, grounded) → approve / review / kill → audit chain
```

## Run

```bash
make setup          # or: uv venv .venv && uv pip install -r requirements-frame.txt
make test           # 22 offline tests; live Gemini test runs only if GEMINI_API_KEY is set
make demo           # offline: SAMPLE fixtures, recorded LLM outputs, fake core → http://localhost:8080
cp .env.example .env && make live   # Gemini via AI Studio key
```

No `make` on Windows: `.venv/Scripts/python.exe -m app_frame.main --replay`.

## What is real and what is not

| Part | State |
|---|---|
| Gemini client (`frame/llm.py`) | Written against `google-genai` 3.0 with JSON-schema output. **Not yet called live** (no key here). Model ID `gemini-4-argon` is a placeholder from the plan; confirm in AI Studio. |
| Judge / rule-diff / narrate + verification | Done, tested with fake LLM. Paraphrased quotes downgrade to `unclear`; invented numbers are rejected, retried once, then replaced by a template. |
| Numeric core | **Fake.** `FakeCoreAdapter` returns fixed placeholder numbers. `RealCoreAdapter` is a guess; see `CORE_MAP.md`. |
| Discovery | Fixtures only. Live discovery must go through the core's compliant client (not wired). |
| Embeddings | Hashing embedder by default; bge-small if `sentence-transformers` is installed. |
| Few-shot from labels | Done; split by event, sealed set never retrieved. |
| Audit log | SHA-256 hash chain, JSONL, verified on the Log page. |
| Eval script, paper ledger | Not started. |

## Files

`frame/` config, schemas, adapter (core seam), llm (Gemini/fake/replay), discover, candidates, judge, rulediff,
narrate, ground, fewshot, pipeline, audit · `app_frame/main.py` NiceGUI · `tests/frame/` · `data/frame/fixtures/`
