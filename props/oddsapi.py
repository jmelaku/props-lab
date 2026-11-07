"""Client for The Odds API (https://the-odds-api.com), v4.

Player props come from the per-event endpoint:

    GET /v4/sports/{sport}/events                       free, lists upcoming games
    GET /v4/sports/{sport}/events/{id}/odds?markets=... costs credits

Credit cost of an odds call = (markets returned) x (regions). The free plan
has 500 credits a month, so an NBA slate of 10 games x 3 markets x 1 region
costs about 30 credits per scan. Every response reports remaining credits in
headers, which the client records so the logger can stop before running dry.

The API key travels as a query parameter, so error messages here never
include the request URL or the underlying exception text (both can contain
the key).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

import requests

from .quotes import PropQuote, market_key, player_key

BASE_URL = "https://api.the-odds-api.com/v4"
RETRY_STATUS = {429, 500, 502, 503, 504}


class OddsApiError(RuntimeError):
    pass


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OddsApiClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        min_interval_s: float = 0.5,
        timeout_s: float = 20.0,
        max_retries: int = 4,
        session: Optional[requests.Session] = None,
        sleep=time.sleep,
    ):
        if not api_key:
            raise OddsApiError("no API key: set ODDS_API_KEY in .env (free key at the-odds-api.com)")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._sleep = sleep
        self._last_call = 0.0
        self.credits_remaining: Optional[int] = None
        self.credits_used: Optional[int] = None
        self.credits_spent_session = 0

    def _record_quota(self, headers: Mapping[str, str]) -> None:
        def num(name: str) -> Optional[int]:
            v = headers.get(name)
            try:
                return int(float(v)) if v is not None else None
            except ValueError:
                return None

        rem, used, last = num("x-requests-remaining"), num("x-requests-used"), num("x-requests-last")
        if rem is not None:
            self.credits_remaining = rem
        if used is not None:
            self.credits_used = used
        if last:
            self.credits_spent_session += last

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        query = {k: v for k, v in params.items() if v is not None}
        query["apiKey"] = self.api_key
        url = self.base_url + path
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval_s - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            try:
                r = self.session.get(url, params=query, timeout=self.timeout_s)
            except requests.RequestException as e:
                if attempt < self.max_retries:
                    self._sleep(min(2 ** attempt, 30))
                    continue
                raise OddsApiError(f"GET {path} failed: {type(e).__name__}") from None
            self._record_quota(r.headers)
            if r.status_code == 200:
                return r.json()
            if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                self._sleep(min(2 ** attempt, 30))
                continue
            detail = (r.text or "")[:200].replace(self.api_key, "***")
            raise OddsApiError(f"GET {path} -> HTTP {r.status_code}: {detail}")
        raise OddsApiError(f"GET {path} failed after retries")  # pragma: no cover

    def sports(self) -> list:
        return self._get("/sports", {})

    def events(self, sport: str, commence_from: Optional[datetime] = None,
               commence_to: Optional[datetime] = None) -> list:
        return self._get(
            f"/sports/{sport}/events",
            {
                "commenceTimeFrom": _iso(commence_from) if commence_from else None,
                "commenceTimeTo": _iso(commence_to) if commence_to else None,
                "dateFormat": "iso",
            },
        )

    def event_odds(self, sport: str, event_id: str, markets: Sequence[str], regions: str = "us",
                   bookmakers: Optional[Sequence[str]] = None) -> dict:
        params = {
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        # The API uses `bookmakers` instead of `regions` when both are given.
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)
        else:
            params["regions"] = regions
        return self._get(f"/sports/{sport}/events/{event_id}/odds", params)


def parse_event_odds(data: Mapping[str, Any], sport: str, ts: str,
                     aliases: Optional[Mapping[str, str]] = None) -> list:
    """Flatten one event-odds response into PropQuotes (over/under props with a line only)."""
    out = []
    event_id = str(data.get("id", ""))
    commence = str(data.get("commence_time", ""))
    home, away = str(data.get("home_team", "")), str(data.get("away_team", ""))
    for bk in data.get("bookmakers") or []:
        book = str(bk.get("key", ""))
        for m in bk.get("markets") or []:
            market = market_key(str(m.get("key", "")))
            for o in m.get("outcomes") or []:
                side = str(o.get("name", "")).lower()
                player, line, price = o.get("description"), o.get("point"), o.get("price")
                if side not in ("over", "under") or not player or line is None or price is None:
                    continue  # yes/no props (anytime TD etc.) have no line
                try:
                    price_i, line_f = int(round(float(price))), float(line)
                except (TypeError, ValueError):
                    continue
                if -100 < price_i < 100:
                    continue
                out.append(PropQuote(
                    ts=ts, sport=sport, event_id=event_id, commence_time=commence, home=home, away=away,
                    book=book, market=market, player=str(player), player_key=player_key(str(player), aliases),
                    side=side, line=line_f, price=price_i,
                ))
    return out


@dataclass
class FetchResult:
    ts: str
    events: list
    quotes: list
    errors: list = field(default_factory=list)


def fetch_props(client: OddsApiClient, sport: str, markets: Sequence[str], regions: str = "us",
                hours_ahead: float = 36, max_events: int = 15,
                bookmakers: Optional[Sequence[str]] = None,
                aliases: Optional[Mapping[str, str]] = None,
                now: Optional[datetime] = None) -> FetchResult:
    """Pull props for every game starting in the next `hours_ahead` hours (pregame only)."""
    now = now or datetime.now(timezone.utc)
    ts = now.isoformat(timespec="seconds")
    events = client.events(sport, commence_from=now, commence_to=now + timedelta(hours=hours_ahead))
    events = sorted(events, key=lambda e: e.get("commence_time", ""))[:max_events]
    res = FetchResult(ts=ts, events=events, quotes=[])
    for e in events:
        try:
            data = client.event_odds(sport, e["id"], markets, regions, bookmakers)
        except OddsApiError as ex:
            res.errors.append(f"{e.get('away_team')} @ {e.get('home_team')}: {ex}")
            continue
        res.quotes.extend(parse_event_odds(data, sport, ts, aliases))
    return res
