"""Odds math: conversions, vig removal, expected value, Kelly sizing.

Sportsbooks quote American odds. -110 means "risk $110 to win $100";
+120 means "risk $100 to win $120". Everything here works in decimal odds
(total payout per $1 staked, stake included), which makes the math simple:

    implied probability = 1 / decimal
    EV per $1 staked    = p * decimal - 1

A two-way market (over/under) has implied probabilities that sum to more
than 1. The excess is the book's margin (the "vig"). Removing it ("devig")
gives the book's fair probability, the props version of the Kalshi lab's
mid price.
"""

from __future__ import annotations

import math


def american_to_decimal(american: float) -> float:
    if -100 < american < 100:
        raise ValueError(f"American odds must be <= -100 or >= +100, got {american}")
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / -american


def decimal_to_american(decimal: float) -> float:
    if decimal <= 1.0:
        raise ValueError(f"decimal odds must be > 1, got {decimal}")
    if decimal >= 2.0:
        return (decimal - 1.0) * 100.0
    return -100.0 / (decimal - 1.0)


def implied_prob(american: float) -> float:
    """Raw implied probability, vig included."""
    return 1.0 / american_to_decimal(american)


def overround(p_over: float, p_under: float) -> float:
    """Book margin: how far the two implied probabilities sum above 1."""
    return p_over + p_under - 1.0


def devig(p_over: float, p_under: float, method: str = "multiplicative") -> tuple[float, float]:
    """Remove the vig from a two-way market. Returns fair (p_over, p_under), summing to 1.

    multiplicative: scale both sides by the same factor. Simple and standard.
    power:          find k with p_over**k + p_under**k = 1. Takes more margin
                    off longshots, which matches how books shade lopsided props.
    """
    if not (0 < p_over < 1 and 0 < p_under < 1):
        raise ValueError("implied probabilities must be in (0, 1)")
    if method == "multiplicative":
        total = p_over + p_under
        return p_over / total, p_under / total
    if method == "power":
        # f(k) = p_over**k + p_under**k - 1 decreases in k; bisect.
        lo, hi = 0.01, 100.0
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            if p_over ** mid + p_under ** mid > 1.0:
                lo = mid
            else:
                hi = mid
        k = 0.5 * (lo + hi)
        a, b = p_over ** k, p_under ** k
        return a / (a + b), b / (a + b)
    raise ValueError(f"unknown devig method: {method}")


def expected_value(p: float, decimal: float) -> float:
    """EV per $1 staked when the bet wins with probability p."""
    return p * decimal - 1.0


def kelly(p: float, decimal: float) -> float:
    """Full-Kelly fraction of bankroll. Use a small fraction of this in practice."""
    b = decimal - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (p * b - (1.0 - p)) / b)


def round_american(american: float, step: int = 5) -> int:
    """Round to the nearest `step`, keeping away from the invalid (-100, +100) gap."""
    r = int(step * round(american / step))
    if -100 < r < 100:
        r = 100 if american >= 0 else -100
    return r


def prob_to_american(p: float) -> float:
    if not 0 < p < 1:
        raise ValueError("probability must be in (0, 1)")
    return decimal_to_american(1.0 / p)


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
