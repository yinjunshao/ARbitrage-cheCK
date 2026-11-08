"""Helper for run.bat (stdlib only, so it works before anything is installed).

    launcher.py status [--short]      what of ours is running: mode, port, pid, age
    launcher.py stop [--apps]         stop our servers (all, or only the app/monitor copies); frees their ports
    launcher.py port [start]          print the first free local port at or above `start` (default 8080)
    launcher.py open <port> [path]    wait until the server answers, then open the browser (run in the background)
    launcher.py needs-tests           exit 0 if the code changed since the last passing test run, else 1
    launcher.py mark-tests-ok         remember that the tests just passed
    launcher.py has-key               exit 0 if GEMINI_API_KEY is set (in .env or the environment)

"Ours" = Python processes started with -m app_frame.main | frame.monitor | frame.testdata | arbcheck serve, plus the
run.bat windows that launched them. Nothing else is ever touched, and never this process or its own parents."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ROOT / ".venv" / ".tests-ok"
OURS = re.compile(r"-m\s+(app_frame\.main|frame\.monitor|frame\.testdata|arbcheck\s+serve)", re.I)
APPS = re.compile(r"-m\s+(app_frame\.main|frame\.monitor)", re.I)
WATCHED = ("frame", "app_frame", "tests", "polysim", "arbcheck-project/arbcheck")


def _processes() -> list[dict]:
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | "
          "Select-Object ProcessId,ParentProcessId,Name,CommandLine,CreationDate | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout
    data = json.loads(out or "[]")
    return [data] if isinstance(data, dict) else data


def _ports() -> dict[int, int]:
    """pid -> a listening local port."""
    out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=30).stdout
    found: dict[int, int] = {}
    for line in out.splitlines():
        m = re.search(r"(?:127\.0\.0\.1|0\.0\.0\.0|\[::1?\]):(\d+)\s+\S+\s+LISTENING\s+(\d+)", line)
        if m:
            found[int(m.group(2))] = int(m.group(1))
    return found


def _ancestors(procs: list[dict], pid: int) -> set[int]:
    by = {p["ProcessId"]: p for p in procs}
    out = set()
    while pid in by and pid not in out:
        out.add(pid)
        pid = by[pid]["ParentProcessId"]
    return out


def _ours(procs: list[dict], only_apps: bool = False) -> list[dict]:
    me = _ancestors(procs, os.getpid())
    pat = APPS if only_apps else OURS
    mine = [p for p in procs if p["ProcessId"] not in me and "python" in p["Name"].lower() and pat.search(p["CommandLine"])]
    ids = {p["ProcessId"] for p in mine}
    # a run.bat window whose server is one of ours (and that is not the window running this command)
    wins = [p for p in procs if p["ProcessId"] not in me and p["Name"].lower() == "cmd.exe" and "run.bat" in p["CommandLine"].lower()
            and any(c["ParentProcessId"] == p["ProcessId"] for c in procs if c["ProcessId"] in ids)]
    return mine + wins


def _age(created: str | None) -> str:
    try:
        ms = int(re.search(r"(\d+)", created or "").group(1))
        s = max(0, int(time.time() - ms / 1000))
        return f"{s // 3600}h{(s % 3600) // 60:02d}m" if s >= 3600 else f"{s // 60}m" if s >= 60 else f"{s}s"
    except (AttributeError, ValueError, TypeError):
        return "?"


def cmd_status(short: bool = False) -> int:
    procs = _processes()
    ports = _ports()
    seen, rows = set(), []
    for p in _ours(procs):
        if p["Name"].lower() == "cmd.exe":
            continue
        # report each server once: the venv launcher and its child share a command line; the child owns the port
        port = ports.get(p["ProcessId"])
        key = (p["CommandLine"].split("-m", 1)[-1].strip(), port)
        mode = (OURS.search(p["CommandLine"]) or [None, "?"])[1]
        if port is None and any(q["ParentProcessId"] == p["ProcessId"] for q in procs):
            continue
        if key in seen:
            continue
        seen.add(key)
        rows.append((mode, port, p["ProcessId"], _age(p.get("CreationDate"))))
    if not rows:
        if not short:
            print("No servers of ours are running.")
        return 0
    for mode, port, pid, age in rows:
        where = f"http://127.0.0.1:{port}" if port else "(no port)"
        print(f"  running: {mode:<14} {where:<26} pid {pid}  up {age}")
    return 0


def cmd_stop(only_apps: bool = False) -> int:
    procs = _processes()
    victims = _ours(procs, only_apps)
    if not victims:
        print("  nothing of ours to stop")
        return 0
    for p in sorted(victims, key=lambda v: v["Name"].lower() != "cmd.exe"):
        subprocess.run(["taskkill", "/PID", str(p["ProcessId"]), "/T", "/F"], capture_output=True, text=True)
    time.sleep(1.0)
    left = {p["ProcessId"] for p in _processes()} & {v["ProcessId"] for v in victims}
    print(f"  stopped {len(victims) - len(left)} process(es)" + (f"; {len(left)} could not be stopped" if left else ""))
    return 1 if left else 0


def _free(port: int) -> bool:
    for host in ("127.0.0.1", "::1"):
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex((host, port)) == 0:
                    return False
        except OSError:
            pass
    return True


def cmd_port(start: int = 8080) -> int:
    for port in range(start, start + 50):
        if _free(port):
            print(port)
            return 0
    print(start)
    return 1


def cmd_open(port: int, path: str = "/") -> int:
    for _ in range(240):                                  # up to ~2 minutes for the app to come up
        if not _free(port):
            time.sleep(0.8)
            webbrowser.open(f"http://127.0.0.1:{port}{path}")
            return 0
        time.sleep(0.5)
    return 1


def _newest() -> float:
    best = 0.0
    for d in WATCHED:
        for f in (ROOT / d).rglob("*.py"):
            if "__pycache__" not in f.parts:
                best = max(best, f.stat().st_mtime)
    for f in ("requirements-frame.txt", "run.bat"):
        if (ROOT / f).exists():
            best = max(best, (ROOT / f).stat().st_mtime)
    return best


def cmd_needs_tests() -> int:
    if not MARKER.exists():
        return 0
    return 0 if _newest() > MARKER.stat().st_mtime else 1


def cmd_mark_ok() -> int:
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    return 0


def cmd_has_key() -> int:
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return 0
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*(GEMINI_API_KEY|GOOGLE_API_KEY)\s*=\s*(\S+)", line)
            if m:
                return 0
    return 1


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    c, a = argv[0], argv[1:]
    if c == "status":
        return cmd_status("--short" in a)
    if c == "stop":
        return cmd_stop("--apps" in a)
    if c == "port":
        return cmd_port(int(a[0]) if a else 8080)
    if c == "open":
        return cmd_open(int(a[0]), a[1] if len(a) > 1 else "/")
    if c == "needs-tests":
        return cmd_needs_tests()
    if c == "mark-tests-ok":
        return cmd_mark_ok()
    if c == "has-key":
        return cmd_has_key()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
