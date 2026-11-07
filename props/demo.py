"""Synthetic prop board for offline runs and tests.

Each player has a true mean and spread per stat. Books set a line near the
mean, then price it off their own noisy estimate plus vig. "Sharp" books are
accurate with tight margins; "soft" books are noisier, sometimes hang a
stale line half a point or a point off, and charge more vig. That is where
real arbs and middles come from, so the demo finds them the same way.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .odds import normal_cdf, prob_to_american, round_american
from .quotes import PropQuote, player_key

BOOKS = {  # name: (estimate noise in prob, vig, chance of an off-market line)
    "sharpbook": (0.008, 0.040, 0.00),
    "bigbook_a": (0.012, 0.048, 0.08),
    "bigbook_b": (0.012, 0.048, 0.08),
    "softbook_a": (0.020, 0.060, 0.20),
    "softbook_b": (0.020, 0.060, 0.20),
}

GAMES = [
    ("Harbor City Gulls", "Ridgeview Foxes", ["Marcus Bell", "Tomás Ortega", "Dre Whitfield", "Kenji Arai"]),
    ("Lakeside Pilots", "Granite Bears", ["Jalen Moss Jr.", "Ivan Petrov", "Cole Ramsey", "D'Andre Pike"]),
    ("Sunset Comets", "Northgate Owls", ["Luis Navarro", "Theo Grant", "Sam O'Neil", "Bryce Holloway"]),
]

STATS = {  # market: (mean range, sd as a fraction of mean, min sd)
    "player_points": ((10, 32), 0.28, 3.0),
    "player_rebounds": ((3, 12), 0.35, 1.5),
    "player_assists": ((2, 10), 0.40, 1.3),
}


def demo_quotes(seed: int = 7, now: Optional[datetime] = None) -> List[PropQuote]:
    rng = random.Random(seed)
    now = now or datetime.now(timezone.utc)
    ts = now.isoformat(timespec="seconds")
    quotes: List[PropQuote] = []
    for gi, (home, away, players) in enumerate(GAMES):
        event_id = f"demo{gi}"
        commence = (now + timedelta(hours=3 + gi)).isoformat(timespec="seconds")
        for player in players:
            for market, ((lo, hi), cv, min_sd) in STATS.items():
                mean = rng.uniform(lo, hi)
                sd = max(min_sd, cv * mean)
                base_line = round(mean * 2) / 2
                if base_line == int(base_line):
                    base_line += 0.5  # most props use half-point lines
                for book, (noise, vig, off_chance) in BOOKS.items():
                    line = base_line
                    p_true = 1.0 - normal_cdf((line - mean) / sd)
                    if rng.random() < off_chance:
                        # Stale line: the book moved the number but kept pricing it like the old one.
                        line += rng.choice([-1.0, -0.5, 0.5, 1.0])
                    p_hat = min(0.9, max(0.1, p_true + rng.gauss(0, noise)))
                    shade = rng.uniform(-0.2, 0.2) * vig  # books split the vig unevenly
                    p_over = p_hat + vig / 2 + shade
                    p_under = (1 - p_hat) + vig / 2 - shade
                    for side, p in (("over", p_over), ("under", p_under)):
                        price = round_american(prob_to_american(min(0.95, max(0.05, p))))
                        quotes.append(PropQuote(
                            ts=ts, sport="demo", event_id=event_id, commence_time=commence,
                            home=home, away=away, book=book, market=market, player=player,
                            player_key=player_key(player), side=side, line=line, price=price,
                        ))
    return quotes
