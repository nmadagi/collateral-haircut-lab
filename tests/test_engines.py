import numpy as np
import pandas as pd
import pytest

from lab import book, exposure, firedrill, haircut
from lab.load import build_db


@pytest.fixture(scope="module")
def con():
    c, _ = build_db()
    yield c
    c.close()


# ------------------------------------------------------------ exposure

def test_marks_apply_the_as_of_move(con):
    marks = exposure.mark_positions(con)
    # cash never moves, so cash collateral is unchanged from the prior close
    cash = marks[marks.coll_class == "cash"]
    assert np.allclose(cash["coll_asof"], cash["coll_prev"])
    # the as-of day is a rally, so lent equities are worth more
    eq = marks[marks.loan_class == "us_large_eq"]
    assert (eq["loan_asof"] > eq["loan_prev"]).all()


def test_summary_covers_every_borrower(con):
    s = exposure.borrower_summary(con)
    assert set(s["borrower_id"]) == set(book.BORROWERS)
    assert (s["on_loan"] > 0).all()
    assert (s["coverage"] > 1.0).all()


def test_margin_call_is_shortfall_against_requirement(con):
    s = exposure.borrower_summary(con)
    called = s[s.margin_call > 0]
    assert len(called) >= 1
    assert np.allclose(called["margin_call"], called["required"] - called["collateral"])
    assert (s.loc[s.margin_call == 0, "excess"] >= 0).all()


def test_status_flags(con):
    s = exposure.borrower_summary(con).set_index("name")
    assert "over limit" in s.loc["Dogwood", "status"]
    assert "non-standard cap breached" in s.loc["Dogwood", "status"]
    assert s.loc["Larch", "status"] == "ok"


def test_book_totals_reconcile(con):
    s = exposure.borrower_summary(con)
    t = exposure.book_totals(s)
    assert abs(t["on_loan"] - s["on_loan"].sum()) < 1e-6
    assert t["n_margin_calls"] == int((s.margin_call > 0).sum())
    assert t["margin_call_total"] == pytest.approx(s["margin_call"].sum())


# ------------------------------------------------------------- haircut

def test_binomial_cdf_matches_known_values():
    assert haircut.binom_cdf(0, 10, 0.5) == pytest.approx(1 / 1024)
    assert haircut.binom_cdf(10, 10, 0.5) == pytest.approx(1.0)
    assert haircut.binom_cdf(4, 250, 0.01) == pytest.approx(0.8922, abs=1e-3)


def test_zones_follow_the_traffic_light():
    # basel: 250 days, 99%: 0 to 4 green, 5 to 9 amber, 10 and up red
    assert haircut.zone(4, 250) == "green"
    assert haircut.zone(5, 250) == "amber"
    assert haircut.zone(9, 250) == "amber"
    assert haircut.zone(10, 250) == "red"
    assert haircut.zone(0, 503) == "green"


def test_scaled_haircut_respects_floor_and_scales_with_vol():
    calm = pd.Series(np.random.default_rng(0).normal(0, 0.0005, 600))
    wild = pd.Series(np.random.default_rng(0).normal(0, 0.02, 600))
    h_calm = haircut.scaled_series(calm).dropna()
    h_wild = haircut.scaled_series(wild).dropna()
    assert (h_calm == book.HAIRCUT_FLOOR - 1).all()
    assert h_wild.mean() > 0.05


def test_forward_move_is_the_next_two_days():
    s = pd.Series([0.0, 0.01, 0.02, -0.01, 0.0])
    fwd = haircut.forward_moves(s, horizon=2)
    assert fwd.iloc[0] == pytest.approx(np.exp(0.03) - 1)
    assert fwd.iloc[1] == pytest.approx(np.exp(0.01) - 1)
    assert np.isnan(fwd.iloc[-1])


def test_flat_exceedances_counted_against_a_constant():
    # windows overlap, so one spike day is seen by the two windows that
    # contain it; five spikes are ten exceedances, the same convention
    # the expected count uses
    s = pd.Series([0.0] * 300 + [0.05, 0.0, 0.0] * 5)
    n, windows, dates = haircut.exceedances(s, 0.02, horizon=2, start=250)
    assert n == 10
    assert len(dates) == 10
    n1, _, _ = haircut.exceedances(s, 0.02, horizon=1, start=250)
    assert n1 == 5


def test_backtest_headline(con):
    bt = haircut.backtest(con)
    sm = haircut.summary(bt)
    assert set(map(tuple, bt[["loan_class", "coll_class"]].values)) == set(
        book.pairs_in_book())
    # the thesis: flat fails far more often than the 1% it claims, the
    # scaled method lands near expectation and needs no more collateral
    assert sm["flat_exceed"] > 2 * sm["scaled_exceed"]
    assert sm["scaled_exceed"] < 1.5 * sm["expected_per_pair"] * len(bt)
    assert sm["scaled_required"] <= sm["flat_required"]
    assert sm["flat_red"] >= 1 and sm["scaled_red"] == 0


# ----------------------------------------------------------- fire drill

def test_orderly_path_only_costs_liquidation(con):
    r = firedrill.run(con, "B09", "orderly")   # Larch: treasuries vs treasuries
    assert r.window_start is None
    assert r.net_shortfall == 0.0
    assert r.proceeds < r.collateral_at_default


def test_stressed_path_is_a_real_window(con):
    r = firedrill.run(con, "B04", "stressed")
    assert r.window_start is not None
    assert r.net_shortfall > 0
    assert r.gross_shortfall >= r.net_shortfall - 1e-6
    assert r.buyback_cost > r.loan_at_default
    assert r.nonstd_share > r.by_pair.loc[~r.by_pair.nonstandard, "collateral"].sum() / r.collateral_at_default


def test_netting_never_exceeds_gross(con):
    df = firedrill.all_borrowers(con, "stressed")
    assert (df["gross_shortfall"] >= df["net_shortfall"] - 1e-9).all()
    assert len(df) == len(book.BORROWERS)


def test_unknown_borrower_raises(con):
    with pytest.raises(KeyError):
        firedrill.run(con, "B99")
