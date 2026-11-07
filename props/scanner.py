"""Find arbitrage, middles, and +EV prices across books.

Quotes are grouped by (game, stat, player). Within a group we take the best
over price at each line and the best under price at each line, then pair
every over line Lo with every under line Lu where Lo <= Lu:

* Every outcome wins at least one leg (over wins above Lo, under wins below
  Lu). So with balanced stakes the worst case returns 1/margin - 1, where
  margin = 1/dec_over + 1/dec_under.
* margin < 1  -> ARB: profit no matter what.
* Lo < Lu     -> MIDDLE: outcomes strictly between the lines win BOTH legs
  (return 2/margin - 1). Worth a small worst-case cost when the window is
  wide. On whole-number lines, landing exactly on a line pushes that leg
  (stake refunded), which only helps.

+EV compares each price to a consensus fair probability: devig every other
book that quotes both sides at the same line, take the median, and leave the
book being evaluated out of its own consensus. This is market-vs-market, not
a model. It is the slot where the projection model plugs in later, and the
Kalshi lab's lesson applies: shrink toward the market before trusting it.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Tuple

from .odds import devig, expected_value, kelly
from .quotes import PropQuote

GroupKey = Tuple[str, str, str]


def group_quotes(quotes: Iterable[PropQuote]) -> Dict[GroupKey, List[PropQuote]]:
    g: Dict[GroupKey, List[PropQuote]] = defaultdict(list)
    for q in quotes:
        g[(q.event_id, q.market, q.player_key)].append(q)
    return g


def best_prices(qs: Iterable[PropQuote]) -> Dict[Tuple[str, float], PropQuote]:
    """Highest-paying quote for each (side, line)."""
    best: Dict[Tuple[str, float], PropQuote] = {}
    for q in qs:
        k = (q.side, q.line)
        if k not in best or q.decimal > best[k].decimal:
            best[k] = q
    return best


@dataclass(frozen=True)
class Pair:
    """An over at one book plus an under at another, with over.line <= under.line."""

    over: PropQuote
    under: PropQuote

    @property
    def margin(self) -> float:
        return 1.0 / self.over.decimal + 1.0 / self.under.decimal

    @property
    def worst_case(self) -> float:
        """Return on total stake when only one leg wins (positive = arb)."""
        return 1.0 / self.margin - 1.0

    @property
    def middle_return(self) -> float:
        """Return on total stake when both legs win."""
        return 2.0 / self.margin - 1.0

    @property
    def window(self) -> List[int]:
        """Whole-number results that win both legs."""
        lo, hi = self.over.line, self.under.line
        return [k for k in range(math.floor(lo) + 1, math.ceil(hi)) if lo < k < hi]

    @property
    def kind(self) -> str:
        if self.worst_case > 0:
            return "arb+middle" if self.window else "arb"
        return "middle"

    def stakes(self, total: float) -> Tuple[float, float]:
        """Split `total` so either leg winning pays the same."""
        po, pu = 1.0 / self.over.decimal, 1.0 / self.under.decimal
        return total * po / self.margin, total * pu / self.margin

    def rounded_stakes(self, total: float, step: float = 1.0) -> Tuple[float, float, float]:
        """Stakes rounded to `step` (books dislike odd amounts) and the guaranteed P&L after rounding."""
        so, su = self.stakes(total)
        best = None
        # Try rounding each leg down and up; keep the split with the best guaranteed result.
        for ro in (math.floor(so / step) * step, math.ceil(so / step) * step):
            for ru in (math.floor(su / step) * step, math.ceil(su / step) * step):
                if ro <= 0 or ru <= 0:
                    continue
                worst = min(ro * self.over.decimal, ru * self.under.decimal) - (ro + ru)
                if best is None or worst / (ro + ru) > best[2] / (best[0] + best[1]):
                    best = (ro, ru, worst)
        return best if best is not None else (so, su, self.worst_case * total)

    @property
    def player(self) -> str:
        return self.over.player

    @property
    def stat(self) -> str:
        return self.over.stat

    @property
    def game(self) -> str:
        return self.over.game


def candidate_pairs(qs: Iterable[PropQuote]) -> Iterator[Pair]:
    best = best_prices(qs)
    overs = [q for (side, _), q in best.items() if side == "over"]
    unders = [q for (side, _), q in best.items() if side == "under"]
    for o in overs:
        for u in unders:
            if o.line <= u.line:
                yield Pair(o, u)


def find_arbs(quotes: Iterable[PropQuote], min_profit: float = 0.0) -> List[Pair]:
    out = []
    for qs in group_quotes(quotes).values():
        out.extend(p for p in candidate_pairs(qs) if p.worst_case > min_profit)
    return sorted(out, key=lambda p: p.worst_case, reverse=True)


def find_middles(quotes: Iterable[PropQuote], max_cost: float = 0.03, min_width: int = 1) -> List[Pair]:
    """Middles that are not already arbs, costing at most `max_cost` of total stake if they miss."""
    out = []
    for qs in group_quotes(quotes).values():
        for p in candidate_pairs(qs):
            if -max_cost <= p.worst_case <= 0 and len(p.window) >= min_width:
                out.append(p)
    return sorted(out, key=lambda p: (len(p.window), p.worst_case), reverse=True)


@dataclass(frozen=True)
class EVBet:
    quote: PropQuote
    fair_p: float  # consensus no-vig probability that this side wins
    n_books: int  # books in the consensus (excluding this one)

    @property
    def ev(self) -> float:
        return expected_value(self.fair_p, self.quote.decimal)

    def stake(self, bankroll: float, kelly_fraction: float) -> float:
        return bankroll * kelly_fraction * kelly(self.fair_p, self.quote.decimal)


def find_ev(quotes: Iterable[PropQuote], min_ev: float = 0.03, min_books: int = 3,
            method: str = "multiplicative") -> List[EVBet]:
    out = []
    for qs in group_quotes(quotes).values():
        # Fair p_over for every book that quotes both sides at a line.
        sides: Dict[Tuple[str, float], Dict[str, PropQuote]] = defaultdict(dict)
        for q in qs:
            sides[(q.book, q.line)][q.side] = q
        fair_over: Dict[Tuple[str, float], float] = {}
        for k, s in sides.items():
            if "over" in s and "under" in s:
                fair_over[k] = devig(s["over"].implied, s["under"].implied, method)[0]
        for q in qs:
            others = [p for (book, line), p in fair_over.items() if line == q.line and book != q.book]
            if len(others) < min_books:
                continue
            p_over = statistics.median(others)
            bet = EVBet(q, p_over if q.side == "over" else 1.0 - p_over, len(others))
            if bet.ev >= min_ev:
                out.append(bet)
    return sorted(out, key=lambda b: b.ev, reverse=True)
