from __future__ import annotations

import math

import pytest

from props import odds as om
from props.demo import demo_quotes
from props.ledger import Ledger
from props.quotes import PropQuote, market_key, player_key
from props.scanner import Pair, find_arbs, find_ev, find_middles


def q(book, side, line, price, player="LeBron James", market="player_points", event="e1", ts="2026-11-07T00:00:00+00:00"):
    return PropQuote(ts=ts, sport="basketball_nba", event_id=event, commence_time="2026-11-08T00:00:00Z",
                     home="Home", away="Away", book=book, market=market, player=player,
                     player_key=player_key(player), side=side, line=line, price=price)


# ---- odds math ----

def test_conversions():
    assert om.american_to_decimal(-110) == pytest.approx(1.9090909)
    assert om.american_to_decimal(150) == pytest.approx(2.5)
    assert om.decimal_to_american(2.5) == pytest.approx(150)
    assert om.decimal_to_american(1.5) == pytest.approx(-200)
    assert om.implied_prob(-110) == pytest.approx(0.5238095)
    with pytest.raises(ValueError):
        om.american_to_decimal(50)


@pytest.mark.parametrize("method", ["multiplicative", "power"])
def test_devig_sums_to_one(method):
    fo, fu = om.devig(om.implied_prob(-150), om.implied_prob(120), method)
    assert fo + fu == pytest.approx(1.0)
    assert fo > 0.5
    # symmetric market devigs to 50/50
    assert om.devig(om.implied_prob(-110), om.implied_prob(-110), method)[0] == pytest.approx(0.5)


def test_power_devig_takes_more_from_longshot():
    po, pu = om.implied_prob(-400), om.implied_prob(280)
    mult = om.devig(po, pu, "multiplicative")[1]
    power = om.devig(po, pu, "power")[1]
    assert power < mult


def test_ev_and_kelly():
    assert om.expected_value(0.55, 2.0) == pytest.approx(0.10)
    assert om.kelly(0.55, 2.0) == pytest.approx(0.10)
    assert om.kelly(0.40, 2.0) == 0.0


def test_round_american_avoids_invalid_gap():
    assert om.round_american(-102) == -100
    assert om.round_american(102) == 100
    assert om.round_american(103) == 105
    assert om.round_american(-112.4) == -110


# ---- names ----

def test_player_key_matches_book_spellings():
    assert player_key("Luka Dončić") == player_key("Luka Doncic") == "luka doncic"
    assert player_key("P.J. Washington") == player_key("PJ Washington")
    assert player_key("Jaren Jackson Jr.") == "jaren jackson"
    assert player_key("D'Angelo Russell") == "dangelo russell"
    assert player_key("Shai Gilgeous-Alexander") == "shai gilgeous alexander"
    assert player_key("Nicolas Claxton", {"nicolas claxton": "nic claxton"}) == "nic claxton"


def test_market_key_folds_alternates():
    assert market_key("player_points_alternate") == "player_points"
    assert market_key("player_points") == "player_points"


# ---- scanner ----

def test_no_arb_in_normal_vig_market():
    quotes = [q("dk", "over", 25.5, -110), q("dk", "under", 25.5, -110),
              q("fd", "over", 25.5, -112), q("fd", "under", 25.5, -108)]
    assert find_arbs(quotes) == []


def test_arb_across_books_and_equal_payouts():
    quotes = [q("dk", "over", 25.5, 110), q("dk", "under", 25.5, -135),
              q("fd", "over", 25.5, -140), q("fd", "under", 25.5, 105)]
    arbs = find_arbs(quotes)
    assert len(arbs) == 1
    a = arbs[0]
    assert (a.over.book, a.under.book) == ("dk", "fd")
    assert a.kind == "arb"
    assert a.worst_case == pytest.approx(1 / (1 / 2.10 + 1 / 2.05) - 1)
    so, su = a.stakes(100)
    assert so + su == pytest.approx(100)
    assert so * a.over.decimal == pytest.approx(su * a.under.decimal)
    rso, rsu, worst = a.rounded_stakes(100)
    assert rso == round(rso) and worst > 0


def test_rounding_keeps_thin_arb_profitable():
    # +0.7% arb whose nearest-dollar split (37/63) would lose 10 cents
    a = Pair(q("soft", "over", 4.5, 170), q("big", "under", 4.5, -165))
    assert a.worst_case > 0
    so, su, worst = a.rounded_stakes(100)
    assert worst > 0 and so == round(so) and su == round(su)


def test_names_spelled_differently_still_pair():
    quotes = [q("dk", "over", 25.5, 110, player="Luka Dončić"), q("fd", "under", 25.5, 105, player="Luka Doncic")]
    assert len(find_arbs(quotes)) == 1


def test_different_players_or_games_never_pair():
    quotes = [q("dk", "over", 25.5, 110, player="A Player"), q("fd", "under", 25.5, 105, player="B Player"),
              q("dk", "over", 20.5, 110, event="e1"), q("fd", "under", 20.5, 105, event="e2")]
    assert find_arbs(quotes) == []


def test_over_above_under_is_not_an_arb():
    # over 26.5 and under 24.5 both lose on 25 or 26: never pair them
    quotes = [q("dk", "over", 26.5, 150), q("fd", "under", 24.5, 150)]
    assert find_arbs(quotes) == [] and find_middles(quotes, max_cost=1.0) == []


def test_middle_window_and_returns():
    quotes = [q("dk", "over", 24.5, -110), q("fd", "under", 26.5, -110)]
    mids = find_middles(quotes, max_cost=0.05)
    assert len(mids) == 1
    m = mids[0]
    assert m.window == [25, 26]
    assert m.kind == "middle"
    assert m.worst_case == pytest.approx(1 / (2 / om.american_to_decimal(-110)) - 1)
    assert m.middle_return > 0.8


def test_whole_number_lines_window_excludes_pushes():
    p = Pair(q("dk", "over", 25.0, -110), q("fd", "under", 27.0, -110))
    assert p.window == [26]


def test_arb_middle_kind():
    quotes = [q("dk", "over", 24.5, 105), q("fd", "under", 25.5, 105)]
    arbs = find_arbs(quotes)
    assert arbs and arbs[0].kind == "arb+middle"


def test_ev_uses_leave_one_out_consensus():
    fair = [("b1", -110, -110), ("b2", -112, -108), ("b3", -108, -112)]
    quotes = []
    for book, o, u in fair:
        quotes += [q(book, "over", 25.5, o), q(book, "under", 25.5, u)]
    quotes += [q("soft", "over", 25.5, 115), q("soft", "under", 25.5, -150)]
    bets = find_ev(quotes, min_ev=0.03, min_books=3)
    assert len(bets) == 1
    b = bets[0]
    assert (b.quote.book, b.quote.side) == ("soft", "over")
    assert b.n_books == 3
    assert b.fair_p == pytest.approx(0.5, abs=0.01)
    assert b.ev == pytest.approx(b.fair_p * 2.15 - 1)
    assert b.stake(200, 0.1) > 0


def test_ev_needs_enough_books():
    quotes = [q("b1", "over", 25.5, -110), q("b1", "under", 25.5, -110), q("soft", "over", 25.5, 130)]
    assert find_ev(quotes, min_books=3) == []


def test_demo_board_finds_opportunities_deterministically():
    quotes = demo_quotes(seed=7)
    assert len(quotes) == 3 * 4 * 3 * 5 * 2
    arbs, mids = find_arbs(quotes), find_middles(quotes)
    assert arbs or mids
    assert len(find_arbs(demo_quotes(seed=7))) == len(arbs)
    for a in arbs:
        assert a.over.line <= a.under.line and a.worst_case > 0


# ---- ledger ----

def test_ledger_stores_only_changes_and_survives_reopen(tmp_path):
    path = str(tmp_path / "t.db")
    led = Ledger(path)
    first = [q("dk", "over", 25.5, -110), q("dk", "under", 25.5, -110)]
    assert led.log_quotes(first) == 2
    assert led.log_quotes(first) == 0
    moved = [q("dk", "over", 25.5, -120, ts="2026-11-07T01:00:00+00:00"), q("dk", "under", 25.5, -110)]
    assert led.log_quotes(moved) == 1
    arbs = find_arbs([q("dk", "over", 25.5, 110), q("fd", "under", 25.5, 105)])
    assert led.log_opportunities("t", arbs) == 1
    led.log_scan("t", "basketball_nba", 1, 2, 1, 1, 0, 0, 480)
    led.close()

    led2 = Ledger(path)
    assert led2.log_quotes(moved) == 0  # last prices reloaded from disk
    s = led2.stats()
    assert s["price_rows"] == 3 and s["events"] == 1 and s["opportunities"] == {"arb": 1}
    assert s["credits_remaining"] == 480
