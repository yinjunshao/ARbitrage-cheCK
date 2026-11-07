"""Command line entry point: ``polysim <command>`` (or ``python -m polysim``)."""

from __future__ import annotations

import argparse
import json
import sys

from .data import (
    PolymarketClient,
    SyntheticConfig,
    generate_binary_market,
    load_markets,
    read_events,
    record_books,
    save_markets,
    write_events,
)
from .engine import Backtester, BookSynthesizer
from .fees import FeeModel
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


def _fee_model(args: argparse.Namespace) -> FeeModel:
    if args.fee_category:
        fm = FeeModel.for_category(args.fee_category, maker_rate=args.maker_rate)
        return fm if args.taker_rate is None else FeeModel(args.taker_rate, args.maker_rate)
    return FeeModel(taker_rate=args.taker_rate or 0.0, maker_rate=args.maker_rate)


def cmd_backtest(args: argparse.Namespace) -> int:
    if args.events:
        if not args.markets:
            print("--events requires --markets (a JSON file from `polysim fetch`)", file=sys.stderr)
            return 2
        markets = load_markets(args.markets)
        events = read_events(args.events)
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
                },
                fh,
                indent=2,
            )
    return 0


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
    bt.add_argument("--events", help="JSONL event file (from `fetch` or `record`)")
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
    bt.add_argument("--json", help="write metrics + equity curve to this file")
    bt.set_defaults(func=cmd_backtest)

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
