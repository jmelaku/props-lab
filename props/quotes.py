"""The one data type everything shares: a single book's price on one side of one prop."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Optional

from .odds import american_to_decimal

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def player_key(name: str, aliases: Optional[Mapping[str, str]] = None) -> str:
    """Normalize a player name so different books' spellings match.

    "Luka Dončić" / "Luka Doncic", "P.J. Washington" / "PJ Washington",
    "Jaren Jackson Jr." / "Jaren Jackson" all map to one key. For names that
    still differ (nicknames, "Nic" vs "Nicolas"), add an entry to `aliases`
    in config.json, keyed by the normalized form.
    """
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = s.replace("'", "").replace("’", "").replace(".", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    key = " ".join(t for t in s.split() if t not in SUFFIXES)
    if aliases:
        key = aliases.get(key, key)
    return key


def market_key(raw: str) -> str:
    """Fold alternate-line markets into the main one so their lines can be compared."""
    return raw[: -len("_alternate")] if raw.endswith("_alternate") else raw


@dataclass(frozen=True)
class PropQuote:
    ts: str  # when we saw it (UTC ISO)
    sport: str
    event_id: str
    commence_time: str
    home: str
    away: str
    book: str
    market: str  # e.g. player_points
    player: str  # as the book spells it
    player_key: str  # normalized, used for matching
    side: str  # "over" or "under"
    line: float
    price: int  # American odds

    @property
    def decimal(self) -> float:
        return american_to_decimal(self.price)

    @property
    def implied(self) -> float:
        return 1.0 / self.decimal

    @property
    def game(self) -> str:
        return f"{self.away} @ {self.home}"

    @property
    def stat(self) -> str:
        m = self.market
        for prefix in ("player_", "batter_", "pitcher_"):
            if m.startswith(prefix):
                return m[len(prefix):]
        return m
