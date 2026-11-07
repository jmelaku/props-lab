# Props Lab

A player-prop scanner for sportsbooks. It pulls over/under lines for every player from many books (via [The Odds API](https://the-odds-api.com)) and finds three things:

- **Arbs:** an over at one book and an under at another whose prices guarantee a profit whatever the player does.
- **Middles:** over a low line at one book, under a higher line at another. It costs a little if the result lands outside the gap, and both bets win if it lands inside.
- **+EV prices:** a book paying more than the other books' consensus fair (no-vig) probability says it should.

Every price change is saved to SQLite. That's the second purpose: over time it builds the **historical prop-line dataset** an over/under projection model needs, and that data is hard to get any other way.

It's a sister project of `kalshi-btc-lab` and follows the same idea: measure edge against the market honestly before trusting it.

```
 The Odds API ──► /events (free) ──► /events/{id}/odds (credits) ──► PropQuote list
                                                                        │ normalize player names,
                                                                        │ fold alternate lines in
                                ┌───────────────────────────────────────┤
                                ▼                                       ▼
          group by (game, stat, player)                       SQLite ledger: quotes stored
          best over / under at each line                      only when a price changes
                                │                             (line history, closing lines)
          ┌─────────────────────┼──────────────────────┐
          ▼                     ▼                      ▼
   ARBS: 1/d_o + 1/d_u < 1   MIDDLES: Lo < Lu,      +EV: price vs. median devigged
   stakes so either leg      small worst case,      fair prob of the OTHER books
   pays the same             window wins both
```

## Quick start

```bash
cd ~/Downloads/props-lab
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest                          # 27 tests, offline
python -m props demo                      # synthetic board, no key needed
python -m props odds --over 110 --under 105
```

Live data:

1. Get a free key at [the-odds-api.com](https://the-odds-api.com) (500 credits a month).
2. `cp .env.example .env` and paste the key in.
3. `cp config.example.json config.json` (optional; defaults are NBA points/rebounds/assists).
4. `python -m props scan`

## Commands

| Command | What it does |
|---|---|
| `python -m props demo` | Synthetic board with sharp and soft books. Exercises the whole scanner offline. |
| `python -m props odds --over -110 --under +105` | Implied probabilities, vig, fair odds, and whether the pair is an arb. |
| `python -m props scan` | One live pull: prints arbs, middles, and +EV, and saves everything to `data/props.db`. Add `--no-save` to skip the DB. |
| `python -m props log --interval 900` | Scans every 15 minutes, one summary line per scan, and stops automatically before running out of credits. |
| `python -m props stats` | What's in the database: price rows, players, books, opportunities found, credits left. |
| `--sport americanfootball_nfl` | Works on any command. Also `baseball_mlb`, `icehockey_nhl`, `basketball_wnba`, `basketball_ncaab`, `americanfootball_ncaaf`. |

## API credits (read this before running `log`)

Listing games is free. Each game's props cost **(markets returned) × (regions)** credits.

| Setup | Cost per scan | Free plan (500/mo) lasts |
|---|---|---|
| NBA, 10 games, 3 markets, `us` | ~30 | ~16 scans |
| NBA, 10 games, 3 markets, `us,us2` | ~60 | ~8 scans |

Free is enough to try it. Continuous logging (the dataset) needs a paid tier. To stretch credits, lower `max_events`, use fewer `markets`, or list only the `bookmakers` you can actually bet at. Adding the `eu` region brings in Pinnacle, the sharpest consensus anchor, but costs another region.

## How the math works

All prices are converted to decimal odds `d` (payout per $1, stake included). Implied probability is `1/d`.

**Arbs and middles.** For one player and stat, take the best over price at each line and the best under price at each line, and pair over line `Lo` with under line `Lu` whenever `Lo ≤ Lu`. Every result then wins at least one leg. With stakes split as `stake_i ∝ 1/d_i`, either leg alone returns `1/margin − 1` of the total stake, where `margin = 1/d_o + 1/d_u`.

- `margin < 1`: **arb**, profit no matter what.
- `Lo < Lu`: results strictly between the lines win **both** legs (return `2/margin − 1`). The scanner lists middles whose worst case costs at most `max_middle_cost` (3% by default). On whole-number lines, landing exactly on a line pushes that leg, which only helps.
- Stakes are rounded to whole dollars (books flag odd amounts), and the output shows the profit locked in *after* rounding.

**+EV.** For each book that quotes both sides of a line, remove the vig (multiplicative by default; `power` takes more off longshots). The consensus is the median of the *other* books. The book being judged is left out so it can't vote for itself. `EV = p_fair × d − 1`, and the suggested stake is 10% Kelly. This is market vs. market, not a model. It's the slot where the projection model plugs in later.

## Reality check

- **Real arbs are rarer and shorter-lived than the demo.** They're usually 1–3% and gone in minutes. A 15-minute scan will find some that have already moved. Always confirm both prices in the books before betting.
- **Leg risk:** place the harder-to-get leg first. If the second price moves, you hold a one-sided bet.
- **Player doesn't play:** books handle this differently (void vs. loss). Check both books' rules for that prop.
- **Books limit arbers.** Accounts that only bet off-market prices get limited, sometimes within weeks.
- **Name matching:** names are normalized (accents, periods, Jr./III), but nicknames can still miss. Add them to `aliases` in `config.json`, e.g. `{"nicolas claxton": "nic claxton"}`.
- Sports betting legality varies by state. This is software, not betting advice.

## What carries over from kalshi-btc-lab

| Kalshi lab | Here |
|---|---|
| `fees.py` | `odds.py`: vig removal plays the role of fees |
| `scanner.py` (edge after costs) | `scanner.py`: arbs, middles, +EV |
| `ledger.py` (snapshots → honest report) | `ledger.py`: change-only line history |
| `kalshi.py` (spacing, backoff) | `oddsapi.py`: same pattern, plus credit tracking and an API key that never shows up in errors |
| `calibrate.py`, `risk.py`, `report` | **Next milestone** (below) |

## Roadmap

1. **Let `log` run for a few weeks** to build line history.
2. **Closing line value:** for each +EV flag, compare its price to that book's last price before tip-off. Consistently beating the close is the earliest real evidence of edge.
3. **Results:** pull box scores (`nba_api`) to settle every logged prop.
4. **Projection model:** projected minutes × per-minute rates → negative binomial / gradient boosting → P(over) ± sd. Port the Kalshi lab's **shrinkage toward the market** (`fair = market + β·(model − market)`, β learned from settled props) and its **expected vs. realized P&L report with a 95% band**.
5. **Risk:** port `risk.py`, with exposure caps per game and per player (a player's points, rebounds, and assists are one correlated bet).
6. Alerts (Discord/Telegram) for arbs above a threshold.

## Layout

```
props/
  odds.py      American/decimal conversions, devig (multiplicative, power), EV, Kelly
  quotes.py    PropQuote, player-name normalization, alternate-market folding
  oddsapi.py   The Odds API client: spacing, retries, credit tracking, parsing, fetch_props
  scanner.py   arbs, middles, +EV (leave-one-out consensus)
  ledger.py    SQLite: events, change-only quotes, opportunities, scans
  config.py    defaults, per-sport markets, config.json / .env loading
  demo.py      synthetic board: sharp vs. soft books, stale lines
  cli.py       demo / odds / scan / log / stats
tests/         odds math, names, scanner edge cases, ledger, API client (no network)
```

Python 3.9-compatible (macOS system Python). The only dependency is `requests`.
