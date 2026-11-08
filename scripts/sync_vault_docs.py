"""Copy the repo's documentation into the Obsidian vault, so the vault always has every doc.

    python scripts/sync_vault_docs.py

Writes Obsidian/AgentVerse Research/Repo Docs/<name>.md, each with a header saying where it came from and when.
Re-run after any doc change; the copies are overwritten (edit the repo files, not the copies).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "Obsidian" / "AgentVerse Research" / "Repo Docs"
SOURCES = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), ROOT / "arbcheck-project" / "README.md"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    names = []
    for src in SOURCES:
        if not src.exists():
            continue
        rel = src.relative_to(ROOT).as_posix()
        name = "Core README" if rel.startswith("arbcheck-project/") else ("Repo README" if rel == "README.md" else src.stem)
        body = src.read_text(encoding="utf-8")
        head = (f"---\ntags: [agentverse, repo-doc]\nsource: {rel}\nsynced: {stamp}\n---\n"
                f"> Copy of `{rel}` from the repo (branch `integrated/llm-and-core`), synced {stamp}. "
                f"Edit the repo file, then re-run `python scripts/sync_vault_docs.py`. Index: [[Repo Docs Index]]\n\n")
        (OUT / f"{name}.md").write_text(head + body, encoding="utf-8")
        names.append((name, rel))
    index = ["---", "tags: [agentverse, repo-doc, moc]", f"synced: {stamp}", "---", "# Repo Docs Index", "",
             "Copies of the repository's documentation, synced by `scripts/sync_vault_docs.py`. "
             "The build notes are [[14 Built App — How It Works]] to [[19 Recording Study — 7–8 Nov]].", ""]
    index += [f"- [[{n}]]: `{r}`" for n, r in names]
    (OUT / "Repo Docs Index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"Synced {len(names)} docs into {OUT}")


if __name__ == "__main__":
    main()
