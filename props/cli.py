"""Command line: python -m props <command>.

  demo    offline: synthetic board -> arbs, middles, +EV (no API key needed)
  odds    calculator: implied probabilities, vig, fair odds, arb check
  scan    one live pull from The Odds API, print opportunities, save to the DB
  log     scan every N seconds, building the line-history dataset
  stats   what's in the database
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from . import odds as om
from .config import load_config, load_env, markets_for
from .ledger import Ledger
from .scanner import EVBet, Pair, find_arbs, find_ev, find_middles


def _fmt_price(p: int) -> str:
    return f"+{p}" if p > 0 else str(p)


def _leg(q) -> str:
    return f"{q.side[0]}{q.line:g} {_fmt_price(q.price):>5} {q.book}"


def _section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def print_pairs(title: str, pairs: List[Pair], stake: float, limit: int) -> None:
    _section(f"{title} ({len(pairs)})")
    if not pairs:
        print("  none")
        return
    print(f"  {'worst':>6} {'if mid':>6}  {'player':<20} {'stat':<9} {'over leg':<26} {'under leg':<26} stakes on ~${stake:g}")
    for p in pairs[:limit]:
        so, su, worst = p.rounded_stakes(stake)
        mid = f"{p.middle_return:+.0%}" if p.window else ""
        print(f"  {p.worst_case:>+6.1%} {mid:>6}  {p.player[:20]:<20} {p.stat[:9]:<9} "
              f"{_leg(p.over):<26} {_leg(p.under):<26} ${so:g} / ${su:g} (locks {worst:+.2f})")
    if len(pairs) > limit:
        print(f"  ... {len(pairs) - limit} more")


def print_ev(bets: List[EVBet], bankroll: float, kelly_fraction: float, limit: int) -> None:
    _section(f"+EV vs. consensus ({len(bets)})")
    if not bets:
        print("  none")
        return
    print(f"  {'EV':>6} {'fair':>5} {'books':>5}  {'player':<20} {'stat':<9} {'price':<26} stake")
    for b in bets[:limit]:
        print(f"  {b.ev:>+6.1%} {b.fair_p:>5.1%} {b.n_books:>5}  {b.quote.player[:20]:<20} {b.quote.stat[:9]:<9} "
              f"{_leg(b.quote):<26} ${b.stake(bankroll, kelly_fraction):.2f}")
    if len(bets) > limit:
        print(f"  ... {len(bets) - limit} more")
    print("  Note: consensus +EV is market vs. market, not a model. Track closing line value before trusting it.")


def analyze(quotes, cfg, limit: int):
    s = cfg["scan"]
    arbs = find_arbs(quotes, s["min_arb_profit"])
    middles = find_middles(quotes, s["max_middle_cost"], s["min_middle_width"])
    evs = find_ev(quotes, s["min_ev"], s["min_consensus_books"], s["devig"])
    print_pairs("ARBS: profit whatever happens", arbs, cfg["arb_stake_usd"], limit)
    print_pairs("MIDDLES: small worst case, both legs win in the window", middles, cfg["arb_stake_usd"], limit)
    print_ev(evs, cfg["bankroll_usd"], cfg["kelly_fraction"], limit)
    return arbs, middles, evs


def cmd_demo(args, cfg) -> int:
    from .demo import demo_quotes

    quotes = demo_quotes(seed=args.seed)
    books = sorted({q.book for q in quotes})
    print(f"Synthetic board: {len({q.event_id for q in quotes})} games, "
          f"{len({q.player_key for q in quotes})} players, {len(books)} books, {len(quotes)} prices")
    analyze(quotes, cfg, args.limit)
    return 0


def cmd_odds(args, cfg) -> int:
    po, pu = om.implied_prob(args.over), om.implied_prob(args.under)
    fo, fu = om.devig(po, pu, args.method)
    print(f"over  {_fmt_price(args.over):>5}  implied {po:.2%}  fair {fo:.2%}  fair odds {om.round_american(om.prob_to_american(fo), 1):+d}")
    print(f"under {_fmt_price(args.under):>5}  implied {pu:.2%}  fair {fu:.2%}  fair odds {om.round_american(om.prob_to_american(fu), 1):+d}")
    margin = po + pu
    if margin < 1:
        print(f"ARB: implied sum {margin:.2%}, locks {1 / margin - 1:+.2%} of total stake")
    else:
        print(f"vig {margin - 1:.2%} (implied sum {margin:.2%})")
    return 0


def _client(cfg):
    from .oddsapi import OddsApiClient

    return OddsApiClient(os.environ.get("ODDS_API_KEY", ""))


def _scan_once(client, cfg, ledger: Optional[Ledger], limit: int, quiet: bool = False):
    from .oddsapi import fetch_props

    markets = markets_for(cfg)
    res = fetch_props(client, cfg["sport"], markets, cfg["regions"], cfg["hours_ahead"], cfg["max_events"],
                      cfg.get("bookmakers"), cfg.get("aliases"))
    for e in res.errors[:3]:
        print(f"  warning: {e}", file=sys.stderr)
    if quiet:
        s = cfg["scan"]
        arbs = find_arbs(res.quotes, s["min_arb_profit"])
        middles = find_middles(res.quotes, s["max_middle_cost"], s["min_middle_width"])
        evs = find_ev(res.quotes, s["min_ev"], s["min_consensus_books"], s["devig"])
    else:
        print(f"{cfg['sport']}: {len(res.events)} games, {len(res.quotes)} prices, "
              f"{len({q.book for q in res.quotes})} books")
        arbs, middles, evs = analyze(res.quotes, cfg, limit)
    stored = 0
    if ledger is not None:
        stored = ledger.log_quotes(res.quotes)
        ledger.log_opportunities(res.ts, arbs + middles, evs)
        ledger.log_scan(res.ts, cfg["sport"], len(res.events), len(res.quotes), stored,
                        len(arbs), len(middles), len(evs), client.credits_remaining)
    return res, arbs, middles, evs, stored


def cmd_scan(args, cfg) -> int:
    client = _client(cfg)
    ledger = None if args.no_save else Ledger(cfg["db_path"])
    _scan_once(client, cfg, ledger, args.limit)
    print(f"\nAPI credits: {client.credits_spent_session} spent this run, {client.credits_remaining} remaining")
    return 0


def cmd_log(args, cfg) -> int:
    client = _client(cfg)
    ledger = Ledger(cfg["db_path"])
    floor = cfg["min_credits_remaining"]
    est = len(markets_for(cfg)) * len(str(cfg["regions"]).split(",")) * cfg["max_events"]
    print(f"Logging {cfg['sport']} every {args.interval}s to {cfg['db_path']} "
          f"(up to ~{est} credits per scan; stops below {floor} remaining). Ctrl-C to stop.")
    try:
        while True:
            res, arbs, middles, evs, stored = _scan_once(client, cfg, ledger, args.limit, quiet=True)
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")
            top = f"  best arb {arbs[0].worst_case:+.1%} {arbs[0].player} {arbs[0].stat}" if arbs else ""
            print(f"[{now}] {len(res.events)} games, {len(res.quotes)} prices, {stored} changed | "
                  f"{len(arbs)} arbs, {len(middles)} middles, {len(evs)} +EV | "
                  f"credits left {client.credits_remaining}{top}", flush=True)
            if client.credits_remaining is not None and client.credits_remaining < floor:
                print("Credit floor reached; stopping.")
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


def cmd_stats(args, cfg) -> int:
    if not os.path.exists(cfg["db_path"]):
        print(f"no database yet at {cfg['db_path']}: run `python -m props scan` first")
        return 0
    for k, v in Ledger(cfg["db_path"]).stats().items():
        print(f"{k:>18}: {v}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="props", description="Player-prop arbitrage, middles, and +EV finder")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--sport", help="override config sport, e.g. basketball_nba, americanfootball_nfl")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("demo", help="offline run on a synthetic board")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("odds", help="odds calculator")
    p.add_argument("--over", type=int, required=True, help="American odds, e.g. -110")
    p.add_argument("--under", type=int, required=True)
    p.add_argument("--method", default="multiplicative", choices=["multiplicative", "power"])

    p = sub.add_parser("scan", help="one live scan")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--no-save", action="store_true")

    p = sub.add_parser("log", help="scan repeatedly and store line history")
    p.add_argument("--interval", type=int, default=900, help="seconds between scans (default 15 min)")
    p.add_argument("--limit", type=int, default=5)

    sub.add_parser("stats", help="database summary")

    args = ap.parse_args(argv)
    load_env()
    cfg = load_config(args.config)
    if args.sport and args.sport != cfg["sport"]:
        cfg["sport"] = args.sport
        cfg["markets"] = None  # config markets belong to the config's sport; use this sport's defaults
    handlers = {"demo": cmd_demo, "odds": cmd_odds, "scan": cmd_scan, "log": cmd_log, "stats": cmd_stats}
    try:
        return handlers[args.cmd](args, cfg)
    except Exception as e:  # show API problems as one line, not a traceback
        from .oddsapi import OddsApiError

        if isinstance(e, OddsApiError):
            print(f"error: {e}", file=sys.stderr)
            return 1
        raise
