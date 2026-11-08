"""Command line entry point: ``polysim <command>`` (or ``python -m polysim``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .data import (
    PolymarketClient,
    SyntheticConfig,
    generate_binary_market,
    load_markets,
    read_event_files,
    read_events,
    record_books,
    save_markets,
    write_events,
)
from .approval import auto_approve, cli_approver
from .audit import verify_file
from .engine import Backtester, BookSynthesizer
from .fees import FeeModel
from .risk import RiskLimits
from .strategies import REGISTRY


def _strategy_kwargs(pairs: list[str]) -> dict:
    out = {}
    for p in pairs:
        k, _, v = p.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _fee_model(args: argparse.Namespace) -> FeeModel | None:
    """An explicit override, or None to use each market's own fee schedule."""
    if args.taker_rate is not None:
        return FeeModel(args.taker_rate, args.maker_rate)
    if args.fee_category:
        return FeeModel.for_category(args.fee_category, maker_rate=args.maker_rate)
    return None


def _risk_limits(args: argparse.Namespace) -> RiskLimits:
    return RiskLimits(
        max_order_notional=args.max_order_notional,
        max_position_cost=args.max_position_cost,
        max_gross_exposure=args.max_gross_exposure,
        max_orders_per_day=args.max_orders_per_day,
        max_daily_loss=args.max_daily_loss,
        max_drawdown_pct=args.max_drawdown_pct,
    )


def _approver(args: argparse.Namespace):
    return cli_approver(args.approver_name) if args.approve == "cli" else auto_approve


def _add_oversight_args(p: argparse.ArgumentParser) -> None:
    risk = p.add_argument_group("risk limits (enforced in code; off unless set)")
    risk.add_argument("--max-order-notional", type=float, help="USDC per buy order")
    risk.add_argument("--max-position-cost", type=float, help="USDC cost per token")
    risk.add_argument("--max-gross-exposure", type=float, help="USDC across positions + reserved cash")
    risk.add_argument("--max-orders-per-day", type=int)
    risk.add_argument("--max-daily-loss", type=float, help="USDC; trips the kill switch")
    risk.add_argument("--max-drawdown-pct", type=float, help="percent from peak; trips the kill switch")
    p.add_argument("--approve", choices=["auto", "cli"], default="auto",
                   help="auto = backtest auto-approval (logged as such); cli = ask a person for every order")
    p.add_argument("--approver-name", default="", help="your name, recorded with each approval (--approve cli)")
    p.add_argument("--audit", help="write the hash-chained audit log to this JSONL file")


def cmd_paper(args: argparse.Namespace) -> int:
    from .desk import format_report, live_snapshot, paper_trade
    from .tickets import load_tickets

    tickets, problems = load_tickets(args.tickets)
    if not tickets:
        print("no valid tickets in " + args.tickets + "".join(f"\n  - {p}" for p in problems), file=sys.stderr)
        return 2
    if args.live:
        markets, events = live_snapshot(PolymarketClient(), tickets)
        source = "LIVE Polymarket order books (read-only, one snapshot)"
    elif args.events:
        if not args.markets:
            print("--events requires --markets (the recording's markets.json)", file=sys.stderr)
            return 2
        markets, events = load_markets(args.markets), read_event_files(args.events)
        source = "recorded Polymarket data"
    else:
        print("give --events/--markets (a recording) or --live", file=sys.stderr)
        return 2
    rep = paper_trade(tickets, events, markets, cash=args.cash, latency=args.latency,
                      risk_limits=_risk_limits(args), approver=_approver(args), audit=args.audit,
                      max_delay=args.max_delay, max_book_age=args.max_book_age, source=source, problems=problems)
    print(format_report(rep))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rep.as_dict(), fh, indent=2)
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    if args.events:
        if not args.markets:
            print("--events requires --markets (a JSON file from `polysim fetch`)", file=sys.stderr)
            return 2
        markets = load_markets(args.markets)
        events = read_event_files(args.events)
    else:
        cfg = SyntheticConfig(n_steps=args.steps)
        market, events = generate_binary_market(cfg, seed=args.seed)
        markets = [market]

    strategy = REGISTRY[args.strategy](**_strategy_kwargs(args.param))
    bt = Backtester(
        events,
        markets,
        strategy,
        initial_cash=args.cash,
        fee_model=_fee_model(args),
        latency=args.latency,
        fill_mode=args.fill_mode,
        synthesizer=BookSynthesizer(spread=args.synth_spread, size_per_level=args.synth_depth),
        risk_limits=_risk_limits(args),
        approver=_approver(args),
        audit=args.audit,
        mark=args.mark,
    )
    result = bt.run()
    source = "recorded Polymarket data" if args.events else "SYNTHETIC data (invented market)"
    print(f"PAPER RESULT on {source}: simulated fills, not real-world performance")
    print(f"strategy: {args.strategy}  markets: {', '.join(m.question for m in markets)}")
    print(result.summary())
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(
                {
                    "metrics": result.metrics,
                    "equity_curve": result.equity_curve,
                    "final_portfolio": result.final_portfolio,
                    "risk": result.risk,
                    "baskets": [b.summary() for b in result.baskets],
                    "manual_settlements": result.manual_settlements,
                    "paper_trading": True,
                },
                fh,
                indent=2,
            )
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import run_demo

    res = run_demo(audit_dir=args.audit_dir, approver_name=args.approver_name)
    print(f"\naudit logs written to {res['audit_dir']}")
    return 0


def _parse_ts(v: str | None) -> float | None:
    """A unix timestamp, or an ISO date/time such as 2026-11-15 (UTC)."""
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(v)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def cmd_evaluate(args: argparse.Namespace) -> int:
    from .evaluation import EvalConfig, MarketData, evaluate, format_report, load_candidates, parse_candidates

    if args.demo:
        from .eval_demo import make_eval_dataset

        markets, events, raw, split = make_eval_dataset(seed=args.seed)
        cands, bad = parse_candidates(raw)
        label = "SYNTHETIC demo data (invented markets, books and results)"
    else:
        if not (args.candidates and args.events and args.markets):
            print("evaluate needs --candidates, --events and --markets (or --demo)", file=sys.stderr)
            return 2
        markets, events = load_markets(args.markets), read_event_files(args.events)
        cands, bad = load_candidates(args.candidates)
        split = None
        label = "recorded Polymarket data"
    split = _parse_ts(args.split_ts) if args.split_ts else split
    cfg = EvalConfig(size=args.size, min_edge=args.min_edge, min_shares=args.min_shares,
                     latency=args.latency, bootstrap=args.bootstrap, seed=args.seed, fee_model=_fee_model(args))
    rep = evaluate(cands, MarketData(events, markets), cfg, split_ts=split, label=label, rejected_inputs=bad)
    print(format_report(rep))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rep.as_dict(), fh, indent=2, default=str)
        print(f"\nfull results written to {args.json}")
    return 0


def cmd_audit_verify(args: argparse.Namespace) -> int:
    ok, bad = verify_file(args.path)
    print(f"{args.path}: hash chain {'verified' if ok else f'BROKEN at record {bad}'}")
    return 0 if ok else 1


def cmd_markets(args: argparse.Namespace) -> int:
    client = PolymarketClient()
    for m in client.list_markets(limit=args.limit, offset=args.offset):
        outcomes = " / ".join(f"{t.outcome}:{t.token_id[:10]}…" for t in m.tokens)
        print(f"{m.slug:60.60}  tick={m.tick_size:<6} {outcomes}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Download a market's price history (+ resolution if closed) for offline backtests."""
    from .data.feeds import merge_feeds
    from .data.polymarket import parse_gamma_market, parse_resolution
    from .events import ResolutionEvent

    client = PolymarketClient()
    raw = client.get_market_raw(args.slug)
    market = parse_gamma_market(raw)
    feeds = [
        client.get_price_history(t.token_id, interval=args.interval, fidelity=args.fidelity)
        for t in market.tokens
    ]
    events = list(merge_feeds(*feeds))
    res = parse_resolution(raw)
    if res is not None:
        # resolution must come after the last price point to keep the feed ordered
        res_ts = max(res.ts, events[-1].ts if events else res.ts)
        events.append(ResolutionEvent(res_ts, res.market_id, res.payouts))
    n = write_events(args.out, events)
    save_markets(args.markets_out, [market])
    print(f"wrote {n} events to {args.out}, market metadata to {args.markets_out}")
    return 0


def cmd_record_events(args: argparse.Namespace) -> int:
    from .data.recorder import EventRecorder

    rec = EventRecorder(PolymarketClient(), args.slugs, args.out_dir, interval=args.interval, depth=args.depth,
                        rate=args.rate, refresh=args.refresh, log=print)
    rec.run(args.duration)
    return 0


def cmd_resolutions(args: argparse.Namespace) -> int:
    """Look up which recorded markets have resolved and append their results."""
    from .data.polymarket import parse_resolution
    from .events import event_from_dict, event_to_dict

    markets = load_markets(args.markets)
    out = Path(args.out)
    done = set()
    if out.exists():
        done = {event_from_dict(json.loads(l)).market_id for l in out.read_text().splitlines() if l.strip()}
    client = PolymarketClient()
    new = 0
    with out.open("a", encoding="utf-8") as fh:
        for m in markets:
            if m.market_id in done or not m.slug:
                continue
            try:
                ev = parse_resolution(client.get_market_raw(m.slug))
            except Exception as exc:
                print(f"warn: {m.slug}: {exc}", file=sys.stderr)
                continue
            if ev is not None:
                fh.write(json.dumps(event_to_dict(ev)) + "\n")
                new += 1
    print(f"{new} new resolution(s) written to {out} ({len(done) + new} of {len(markets)} markets resolved)")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    client = PolymarketClient()
    market = client.get_market(args.slug)
    save_markets(args.markets_out, [market])
    n = record_books(
        client,
        list(market.token_ids),
        args.out,
        interval=args.interval,
        duration=args.duration,
        on_error=lambda tid, e: print(f"warn: {tid}: {e}", file=sys.stderr),
    )
    print(f"recorded {n} book snapshots to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="polysim", description="Polymarket trade simulator")
    sub = p.add_subparsers(dest="cmd", required=True)

    bt = sub.add_parser("backtest", help="run a strategy on synthetic or recorded data")
    bt.add_argument("--strategy", choices=sorted(REGISTRY), default="buy_and_hold")
    bt.add_argument("--param", action="append", default=[], metavar="K=V", help="strategy kwarg (repeatable)")
    bt.add_argument("--events", nargs="+", help="event files or recorder folders (from `fetch`/`record`)")
    bt.add_argument("--markets", help="markets JSON file matching --events")
    bt.add_argument("--seed", type=int, default=0, help="synthetic market seed")
    bt.add_argument("--steps", type=int, default=500, help="synthetic market length")
    bt.add_argument("--cash", type=float, default=1000.0)
    bt.add_argument("--fee-category", help="use the verified taker fee rate for a category (e.g. crypto)")
    bt.add_argument("--taker-rate", type=float, default=None, help="taker feeRate (fraction), overrides category")
    bt.add_argument("--maker-rate", type=float, default=0.0, help="maker feeRate (fraction)")
    bt.add_argument("--latency", type=float, default=0.0, help="order latency in seconds")
    bt.add_argument("--fill-mode", choices=["touch", "through"], default="touch")
    bt.add_argument("--synth-spread", type=float, default=0.02, help="spread for books built from prices")
    bt.add_argument("--synth-depth", type=float, default=500.0, help="size per level for synthetic books")
    bt.add_argument("--mark", choices=["bid", "mid"], default="bid", help="price open positions are valued at")
    _add_oversight_args(bt)
    bt.add_argument("--json", help="write metrics + equity curve to this file")
    bt.set_defaults(func=cmd_backtest)

    pp = sub.add_parser("paper", help="paper-execute tickets the checker approved, on a recording or live books")
    pp.add_argument("--tickets", required=True, help="JSON list or JSONL of tickets (see polysim/tickets.py)")
    pp.add_argument("--events", nargs="+", help="recorder folders or event files to trade on")
    pp.add_argument("--markets", help="markets JSON matching --events")
    pp.add_argument("--live", action="store_true", help="trade on the current live books (read-only GETs)")
    pp.add_argument("--cash", type=float, default=1000.0)
    pp.add_argument("--latency", type=float, default=2.0, help="seconds between sending a leg and it reaching the book")
    pp.add_argument("--max-delay", type=float, default=300.0, help="give up on a ticket this many seconds after its time")
    pp.add_argument("--max-book-age", type=float, default=120.0, help="only trade a leg whose book is this fresh")
    _add_oversight_args(pp)
    pp.add_argument("--json", help="write the per-ticket report to this file")
    pp.set_defaults(func=cmd_paper)

    dm = sub.add_parser("demo", help="show every risk control triggering, on invented data (offline)")
    dm.add_argument("--audit-dir", help="where to write the demo's audit logs (default: a temp folder)")
    dm.add_argument("--approver-name", default="demo user")
    dm.set_defaults(func=cmd_demo)

    ev = sub.add_parser("evaluate", help="our checker vs a naive price-only checker, paper-executed to resolution")
    ev.add_argument("--demo", action="store_true", help="run on a built-in SYNTHETIC dataset (offline)")
    ev.add_argument("--candidates", help="JSONL of claimed arbitrages (id, ts, token_ids, floor, rule_check)")
    ev.add_argument("--events", nargs="+", help="recorded books + resolutions: files or recorder folders")
    ev.add_argument("--markets", help="markets JSON matching --events")
    ev.add_argument("--split-ts", help="tune before this time, test after (unix ts or ISO date)")
    ev.add_argument("--size", type=float, default=100.0, help="baskets the naive checker buys")
    ev.add_argument("--min-edge", type=float, default=0.01, help="USDC/share after fees (used if no split)")
    ev.add_argument("--min-shares", type=float, default=20.0, help="depth our checker needs")
    ev.add_argument("--latency", type=float, default=2.0, help="seconds per order")
    ev.add_argument("--bootstrap", type=int, default=2000, help="resamples for the 95%% intervals")
    ev.add_argument("--seed", type=int, default=0)
    ev.add_argument("--fee-category", help="override every market's fee with a category rate")
    ev.add_argument("--taker-rate", type=float, default=None, help="override every market's taker feeRate")
    ev.add_argument("--maker-rate", type=float, default=0.0)
    ev.add_argument("--json", help="write the full report (every candidate) to this file")
    ev.set_defaults(func=cmd_evaluate)

    av = sub.add_parser("audit-verify", help="check an audit log's hash chain")
    av.add_argument("path")
    av.set_defaults(func=cmd_audit_verify)

    mk = sub.add_parser("markets", help="list active Polymarket markets")
    mk.add_argument("--limit", type=int, default=20)
    mk.add_argument("--offset", type=int, default=0)
    mk.set_defaults(func=cmd_markets)

    fe = sub.add_parser("fetch", help="download price history for a market slug")
    fe.add_argument("slug")
    fe.add_argument("--interval", default="max")
    fe.add_argument("--fidelity", type=int, default=60, help="minutes per point")
    fe.add_argument("--out", default="events.jsonl")
    fe.add_argument("--markets-out", default="markets.json")
    fe.set_defaults(func=cmd_fetch)

    re_ = sub.add_parser("record-events", help="record every open market of one or more events (e.g. ladders)")
    re_.add_argument("slugs", nargs="+", help="Gamma event slugs")
    re_.add_argument("--out-dir", default="recordings", help="kept out of git (see .gitignore)")
    re_.add_argument("--interval", type=float, default=60.0, help="seconds between rounds")
    re_.add_argument("--depth", type=int, default=20, help="book levels kept per side")
    re_.add_argument("--rate", type=float, default=5.0, help="max requests per second")
    re_.add_argument("--refresh", type=float, default=1800.0, help="seconds between event refreshes")
    re_.add_argument("--duration", type=float, default=14 * 86400.0, help="seconds to run (Ctrl-C to stop)")
    re_.set_defaults(func=cmd_record_events)

    rs = sub.add_parser("resolutions", help="append results for recorded markets that have resolved")
    rs.add_argument("--markets", default="recordings/markets.json")
    rs.add_argument("--out", default="recordings/resolutions.jsonl")
    rs.set_defaults(func=cmd_resolutions)

    rc = sub.add_parser("record", help="poll live order books for a market slug")
    rc.add_argument("slug")
    rc.add_argument("--interval", type=float, default=5.0)
    rc.add_argument("--duration", type=float, default=300.0)
    rc.add_argument("--out", default="books.jsonl")
    rc.add_argument("--markets-out", default="markets.json")
    rc.set_defaults(func=cmd_record)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
