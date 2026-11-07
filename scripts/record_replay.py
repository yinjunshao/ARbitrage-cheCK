"""Record REAL Gemini outputs on arbcheck's invented demo universe into data/frame/fixtures/llm_replay.json,
so `--replay` runs the full demo offline with genuine model answers.

Needs GEMINI_API_KEY. Auto-confirms every surfaced pair with the judge's own relation (a stand-in for
the human), prices it with the real core, and narrates it, so narrate outputs are recorded too.
"""
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from frame.audit import AuditLog  # noqa: E402
from frame.config import get_config  # noqa: E402
from frame.llm import GeminiClient, RecordingLLM  # noqa: E402
from frame.pipeline import build  # noqa: E402

tmp = Path(tempfile.mkdtemp())
cfg = replace(get_config(), audit_path=tmp / "audit.jsonl", labels_path=tmp / "labels.jsonl", cache_dir=tmp / "cache")
llm = RecordingLLM(GeminiClient(cfg, AuditLog(tmp / "llm_audit.jsonl")))
t0 = time.time()
p = build(demo=True, cfg=cfg, llm=llm)
p.scan()
print(f"judged {len(p.cases)} pairs in {time.time() - t0:.0f}s; {len(p.review_queue())} surfaced", flush=True)
for c in list(p.review_queue()):
    p.confirm(c.id, "recorder")
    core = p.price(c.id, 300)
    n = c.narrated
    print(f"  {c.judged.a.id} | {c.judged.b.id}: {c.judged.verdict.relation} -> core {core.verdict if core else c.error};"
          f" narrate {n.source if n else '-'} grounded={n.grounded if n else '-'}", flush=True)
out = cfg.replay_file
out.write_text(json.dumps(llm.recorded, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"wrote {len(llm.recorded)} recorded outputs to {out} in {time.time() - t0:.0f}s")
