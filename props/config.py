"""Settings: config.json (optional, overrides defaults) and .env (API key)."""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict

# Prop market keys per sport (The Odds API names). Each market costs credits
# on every scan, so the defaults are a short list; add more in config.json.
SPORT_MARKETS = {
    "basketball_nba": ["player_points", "player_rebounds", "player_assists"],
    "basketball_wnba": ["player_points", "player_rebounds", "player_assists"],
    "basketball_ncaab": ["player_points", "player_rebounds", "player_assists"],
    "americanfootball_nfl": ["player_pass_yds", "player_rush_yds", "player_reception_yds", "player_receptions"],
    "americanfootball_ncaaf": ["player_pass_yds", "player_rush_yds", "player_reception_yds"],
    "baseball_mlb": ["pitcher_strikeouts", "batter_hits", "batter_total_bases"],
    "icehockey_nhl": ["player_points", "player_shots_on_goal"],
}

DEFAULTS: Dict[str, Any] = {
    "sport": "basketball_nba",
    "markets": None,  # null = SPORT_MARKETS[sport]
    "regions": "us",  # "us", "us2", "eu" (has Pinnacle), comma-separate for several; each multiplies cost
    "bookmakers": None,  # e.g. ["draftkings", "fanduel"]; overrides regions
    "hours_ahead": 36,
    "max_events": 15,
    "db_path": "data/props.db",
    "min_credits_remaining": 25,  # the logger stops below this
    "scan": {
        "min_arb_profit": 0.0,
        "max_middle_cost": 0.03,
        "min_middle_width": 1,
        "min_ev": 0.03,
        "min_consensus_books": 3,
        "devig": "multiplicative",
    },
    "bankroll_usd": 200.0,
    "arb_stake_usd": 100.0,
    "kelly_fraction": 0.1,
    "aliases": {},  # normalized name -> normalized name, e.g. {"nicolas claxton": "nic claxton"}
}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str = "config.json") -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            cfg = _merge(cfg, json.load(f))
    return cfg


def markets_for(cfg: Dict[str, Any]) -> list:
    if cfg.get("markets"):
        return list(cfg["markets"])
    try:
        return list(SPORT_MARKETS[cfg["sport"]])
    except KeyError:
        raise SystemExit(f"no default markets for sport '{cfg['sport']}': set \"markets\" in config.json")


def load_env(path: str = ".env") -> None:
    """Minimal .env reader (KEY=value lines). Existing environment variables win."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
