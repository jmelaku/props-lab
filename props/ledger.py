"""SQLite store: every price change, every opportunity found, every scan.

This is the long-term payoff of the arb finder. Historical prop lines are the
hardest data to get for the over/under model, and this table builds them for
free as a side effect of scanning.

Tables
  events         one row per game
  quotes         a row only when a (game, book, stat, player, side, line)
                 price is new or has changed, so the table stays small while
                 keeping full line history (and closing lines for CLV later)
  opportunities  arbs, middles, and +EV prices as they were found
  scans          when we scanned and the API credits left afterwards
"""

from __future__ import annotations

import os
import sqlite3
from typing import Dict, Iterable, Optional, Tuple

from .quotes import PropQuote
from .scanner import EVBet, Pair

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY, sport TEXT, commence_time TEXT, home TEXT, away TEXT
);
CREATE TABLE IF NOT EXISTS quotes (
  ts TEXT, event_id TEXT, book TEXT, market TEXT, player TEXT, player_key TEXT,
  side TEXT, line REAL, price INTEGER
);
CREATE INDEX IF NOT EXISTS quotes_prop ON quotes(event_id, market, player_key);
CREATE TABLE IF NOT EXISTS opportunities (
  ts TEXT, kind TEXT, event_id TEXT, market TEXT, player TEXT,
  book_a TEXT, side_a TEXT, line_a REAL, price_a INTEGER,
  book_b TEXT, side_b TEXT, line_b REAL, price_b INTEGER,
  value REAL, fair_p REAL
);
CREATE TABLE IF NOT EXISTS scans (
  ts TEXT, sport TEXT, events INTEGER, quotes INTEGER, stored INTEGER,
  arbs INTEGER, middles INTEGER, ev INTEGER, credits_remaining INTEGER
);
"""

QuoteKey = Tuple[str, str, str, str, str, float]


def _key(q: PropQuote) -> QuoteKey:
    return (q.event_id, q.book, q.market, q.player_key, q.side, q.line)


class Ledger:
    def __init__(self, path: str = "data/props.db"):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._last: Dict[QuoteKey, int] = {}
        for r in self.db.execute(
            "SELECT event_id, book, market, player_key, side, line, price FROM quotes WHERE rowid IN "
            "(SELECT MAX(rowid) FROM quotes GROUP BY event_id, book, market, player_key, side, line)"
        ):
            self._last[(r[0], r[1], r[2], r[3], r[4], r[5])] = r[6]

    def close(self) -> None:
        self.db.close()

    def log_quotes(self, quotes: Iterable[PropQuote]) -> int:
        """Store new or changed prices only. Returns rows written."""
        rows, events = [], {}
        for q in quotes:
            events[q.event_id] = (q.event_id, q.sport, q.commence_time, q.home, q.away)
            k = _key(q)
            if self._last.get(k) != q.price:
                self._last[k] = q.price
                rows.append((q.ts, q.event_id, q.book, q.market, q.player, q.player_key, q.side, q.line, q.price))
        self.db.executemany("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?)", list(events.values()))
        self.db.executemany("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?)", rows)
        self.db.commit()
        return len(rows)

    def log_opportunities(self, ts: str, pairs: Iterable[Pair] = (), evs: Iterable[EVBet] = ()) -> int:
        rows = []
        for p in pairs:
            o, u = p.over, p.under
            rows.append((ts, p.kind, o.event_id, o.market, o.player,
                         o.book, "over", o.line, o.price, u.book, "under", u.line, u.price,
                         p.worst_case, None))
        for b in evs:
            q = b.quote
            rows.append((ts, "ev", q.event_id, q.market, q.player,
                         q.book, q.side, q.line, q.price, None, None, None, None, b.ev, b.fair_p))
        self.db.executemany("INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.db.commit()
        return len(rows)

    def log_scan(self, ts: str, sport: str, events: int, quotes: int, stored: int,
                 arbs: int, middles: int, ev: int, credits_remaining: Optional[int]) -> None:
        self.db.execute("INSERT INTO scans VALUES (?,?,?,?,?,?,?,?,?)",
                        (ts, sport, events, quotes, stored, arbs, middles, ev, credits_remaining))
        self.db.commit()

    def stats(self) -> dict:
        one = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        first, last = self.db.execute("SELECT MIN(ts), MAX(ts) FROM quotes").fetchone()
        return {
            "scans": one("SELECT COUNT(*) FROM scans"),
            "events": one("SELECT COUNT(*) FROM events"),
            "price_rows": one("SELECT COUNT(*) FROM quotes"),
            "players": one("SELECT COUNT(DISTINCT player_key) FROM quotes"),
            "books": one("SELECT COUNT(DISTINCT book) FROM quotes"),
            "first_seen": first,
            "last_seen": last,
            "opportunities": {r[0]: r[1] for r in self.db.execute(
                "SELECT kind, COUNT(*) FROM opportunities GROUP BY kind")},
            "credits_remaining": one("SELECT credits_remaining FROM scans ORDER BY rowid DESC LIMIT 1")
            if one("SELECT COUNT(*) FROM scans") else None,
        }
