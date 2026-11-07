from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from props.oddsapi import OddsApiClient, OddsApiError, fetch_props, parse_event_odds

KEY = "secret-key-123"

EVENT_ODDS = {
    "id": "ev1",
    "sport_key": "basketball_nba",
    "commence_time": "2026-11-08T00:10:00Z",
    "home_team": "Boston Celtics",
    "away_team": "New York Knicks",
    "bookmakers": [
        {"key": "draftkings", "markets": [
            {"key": "player_points", "outcomes": [
                {"name": "Over", "description": "Jalen Brunson", "price": -115, "point": 26.5},
                {"name": "Under", "description": "Jalen Brunson", "price": -105, "point": 26.5},
            ]},
            {"key": "player_points_alternate", "outcomes": [
                {"name": "Over", "description": "Jalen Brunson", "price": 140, "point": 29.5},
            ]},
        ]},
        {"key": "fanduel", "markets": [
            {"key": "player_points", "outcomes": [
                {"name": "Over", "description": "Jalen Brunson Jr.", "price": -108, "point": 26.5},
                {"name": "Under", "description": "Jalen Brunson", "price": "-112", "point": 26.5},
                {"name": "Yes", "description": "Someone", "price": 200},
                {"name": "Over", "description": "No Line", "price": -110},
            ]},
        ]},
    ],
}


class FakeResponse:
    def __init__(self, status, body=None, headers=None, text=""):
        self.status_code, self._body, self.headers, self.text = status, body, headers or {}, text

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(responses):
    s = FakeSession(responses)
    return OddsApiClient(KEY, session=s, min_interval_s=0, sleep=lambda _: None), s


def test_parse_flattens_and_normalizes():
    quotes = parse_event_odds(EVENT_ODDS, "basketball_nba", "ts")
    assert len(quotes) == 5  # yes/no prop and line-less outcome dropped
    assert {x.player_key for x in quotes} == {"jalen brunson"}
    assert {x.market for x in quotes} == {"player_points"}  # alternate folded in
    fd_under = [x for x in quotes if x.book == "fanduel" and x.side == "under"][0]
    assert fd_under.price == -112 and fd_under.line == 26.5
    assert quotes[0].game == "New York Knicks @ Boston Celtics"


def test_missing_key_is_clear():
    with pytest.raises(OddsApiError, match="ODDS_API_KEY"):
        OddsApiClient("")


def test_retries_then_records_quota():
    c, s = client([
        FakeResponse(429, text="slow down"),
        FakeResponse(200, EVENT_ODDS, {"x-requests-remaining": "470", "x-requests-used": "30", "x-requests-last": "3"}),
    ])
    data = c.event_odds("basketball_nba", "ev1", ["player_points"])
    assert data["id"] == "ev1"
    assert len(s.calls) == 2
    assert c.credits_remaining == 470 and c.credits_spent_session == 3
    url, params = s.calls[-1]
    assert url.endswith("/sports/basketball_nba/events/ev1/odds")
    assert params["markets"] == "player_points" and params["regions"] == "us" and params["oddsFormat"] == "american"


def test_bookmakers_replace_regions():
    c, s = client([FakeResponse(200, EVENT_ODDS)])
    c.event_odds("basketball_nba", "ev1", ["player_points"], bookmakers=["draftkings", "fanduel"])
    params = s.calls[0][1]
    assert params["bookmakers"] == "draftkings,fanduel" and "regions" not in params


def test_errors_never_leak_the_key():
    c, _ = client([FakeResponse(401, text=f"bad key {KEY}")])
    with pytest.raises(OddsApiError) as e:
        c.events("basketball_nba")
    assert KEY not in str(e.value) and "401" in str(e.value)

    boom = requests.ConnectionError(f"https://x/?apiKey={KEY}")
    c, _ = client([boom] * 5)
    with pytest.raises(OddsApiError) as e:
        c.events("basketball_nba")
    assert KEY not in str(e.value)


def test_fetch_props_skips_failed_events():
    events = [
        {"id": "ev1", "commence_time": "2026-11-08T00:10:00Z", "home_team": "Boston Celtics", "away_team": "New York Knicks"},
        {"id": "ev2", "commence_time": "2026-11-08T02:00:00Z", "home_team": "A", "away_team": "B"},
    ]
    c, s = client([FakeResponse(200, events), FakeResponse(200, EVENT_ODDS), FakeResponse(404, text="not found")])
    res = fetch_props(c, "basketball_nba", ["player_points"], now=datetime(2026, 11, 7, 18, tzinfo=timezone.utc))
    assert len(res.events) == 2 and len(res.quotes) == 5 and len(res.errors) == 1
    ev_params = s.calls[0][1]
    assert ev_params["commenceTimeFrom"] == "2026-11-07T18:00:00Z"
    assert ev_params["commenceTimeTo"] == "2026-11-09T06:00:00Z"
