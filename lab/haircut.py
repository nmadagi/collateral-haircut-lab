"""Haircut methodology and backtest.

A haircut is the cushion between what the borrower posted and what it
borrowed. The question it answers: if the borrower fails today, and it
takes two days to sell the collateral and buy the securities back, how
far can the lent basket rise against the collateral basket before the
cushion is gone?

Two methods sit side by side.

flat:   the house schedule, one number per collateral type (102 for cash
        and Treasuries, 105 for everything else), regardless of what is
        lent against it.
scaled: a 99% two-day move of the loan-over-collateral ratio, estimated
        from the trailing year of daily moves and refreshed every day,
        with a floor of one point. That is a VaR on the spread between
        the two baskets, which is what a haircut is underneath.

The backtest walks every day after the first year and asks whether the
next two days' move exceeded that day's haircut. Exceedances are counted
against the 1% expected rate and zoned green, amber, red the way the
Basel traffic light does it: green up to the 95th percentile of the
binomial count, amber to the 99.99th, red beyond.
"""

from math import exp, lgamma, log, sqrt

import numpy as np
import pandas as pd

from lab.book import (CONFIDENCE, FLAT_HAIRCUT, HAIRCUT_FLOOR, HORIZON_DAYS,
                      LOOKBACK_DAYS, pairs_in_book)
from lab.exposure import mark_positions

# one-sided normal quantiles; 99% is the only one the book uses
Z_SCORE = {0.99: 2.3263, 0.975: 1.9600, 0.95: 1.6449}


def ratio_returns(con):
    """Daily log change of (lent basket price / collateral basket price)
    for every pair in the book. Positive means the loan gained on the
    collateral, which is the direction that eats the cushion."""
    px = con.execute(
        "SELECT date, asset_class, side, price FROM prices ORDER BY date").df()
    wide = px.pivot_table(index="date", columns=["asset_class", "side"],
                          values="price")
    logp = np.log(wide)
    out = {}
    for loan_cls, coll_cls in pairs_in_book():
        spread = logp[(loan_cls, "loan")] - logp[(coll_cls, "collateral")]
        out[(loan_cls, coll_cls)] = spread.diff()
    return pd.DataFrame(out).iloc[1:]


def scaled_series(spread, lookback=LOOKBACK_DAYS, horizon=HORIZON_DAYS,
                  conf=CONFIDENCE, floor=HAIRCUT_FLOOR):
    """Haircut as a fraction above one (0.034 means 103.4%), one value
    per day, using only the trailing window through that day."""
    vol = spread.rolling(lookback).std()
    h = Z_SCORE[conf] * vol * sqrt(horizon)
    return h.clip(lower=floor - 1.0)


def forward_moves(spread, horizon=HORIZON_DAYS):
    """Ratio move over the next `horizon` days, as a fraction, aligned
    to the day the haircut was set."""
    fwd = spread[::-1].rolling(horizon).sum()[::-1].shift(-1)
    return np.exp(fwd) - 1.0


def exceedances(spread, haircut, horizon=HORIZON_DAYS, start=LOOKBACK_DAYS):
    """Count days after `start` where the forward move beat the haircut.
    `haircut` is a scalar fraction (flat) or a Series (scaled)."""
    fwd = forward_moves(spread, horizon).iloc[start:]
    fwd = fwd.dropna()
    if isinstance(haircut, pd.Series):
        h = haircut.reindex(fwd.index)
    else:
        h = pd.Series(haircut, index=fwd.index)
    hit = fwd > h
    return int(hit.sum()), int(len(fwd)), list(fwd.index[hit])


def binom_cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p), log-space so n in the hundreds
    is fine without scipy."""
    total = 0.0
    lp, lq = log(p), log(1 - p)
    for i in range(0, k + 1):
        lc = lgamma(n + 1) - lgamma(i + 1) - lgamma(n - i + 1)
        total += exp(lc + i * lp + (n - i) * lq)
    return min(total, 1.0)


def zone(count, n, p=1 - CONFIDENCE):
    """Basel traffic light: green while the count is still inside the
    95th percentile of what a correct model would produce, amber to the
    99.99th, red beyond."""
    cum = binom_cdf(count, n, p)
    if cum < 0.95:
        return "green"
    if cum < 0.9999:
        return "amber"
    return "red"


def backtest(con):
    """One row per (loan class, collateral class) pair in the book."""
    spreads = ratio_returns(con)
    marks = mark_positions(con)
    on_loan = marks.groupby(["loan_class", "coll_class"])["loan_asof"].sum()

    rows = []
    for pair in pairs_in_book():
        s = spreads[pair]
        flat = FLAT_HAIRCUT[pair[1]] - 1.0
        scaled = scaled_series(s)
        f_n, n, f_dates = exceedances(s, flat)
        s_n, _, s_dates = exceedances(s, scaled)
        loan = float(on_loan.get(pair, 0.0))
        latest = float(scaled.iloc[-1])
        rows.append({
            "loan_class": pair[0], "coll_class": pair[1],
            "on_loan": loan,
            "flat_haircut": flat, "flat_exceed": f_n, "flat_zone": zone(f_n, n),
            "scaled_haircut": latest, "scaled_exceed": s_n,
            "scaled_zone": zone(s_n, n),
            "n_windows": n, "expected": n * (1 - CONFIDENCE),
            "flat_required": loan * (1 + flat),
            "scaled_required": loan * (1 + latest),
            "flat_dates": f_dates, "scaled_dates": s_dates,
        })
    return pd.DataFrame(rows)


def summary(bt):
    return {
        "n_windows": int(bt["n_windows"].iloc[0]),
        "expected_per_pair": float(bt["expected"].iloc[0]),
        "flat_exceed": int(bt["flat_exceed"].sum()),
        "scaled_exceed": int(bt["scaled_exceed"].sum()),
        "flat_red": int((bt["flat_zone"] == "red").sum()),
        "scaled_red": int((bt["scaled_zone"] == "red").sum()),
        "flat_required": float(bt["flat_required"].sum()),
        "scaled_required": float(bt["scaled_required"].sum()),
        "on_loan": float(bt["on_loan"].sum()),
    }
