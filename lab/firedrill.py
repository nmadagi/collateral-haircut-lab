"""Counterparty fire drill: what happens the day a borrower fails.

The rehearsal is three steps. Freeze: no new loans, no collateral
returned. Mark: value every lent security and every piece of collateral
at today's close. Close out: sell the collateral, buy the lent securities
back in the market, and return them to the lending client. If the
buyback costs more than the collateral raised, the shortfall is the
loss, and under an indemnified program the agent lender pays it.

Two price paths for the close-out.

orderly:  today's prices, less each class's liquidation cost (the price
          concession for selling or buying a whole basket at once).
stressed: the worst two consecutive days in the three-year history for
          this borrower's particular mix: every class is moved together
          over the same two days, and the window chosen is the one that
          maximises buyback minus proceeds. Plus the same liquidation
          cost. One joint historical window, not each class at its own
          worst day.

Netting: under one master agreement the borrower's whole portfolio is
closed out together, so excess collateral on one loan offsets a
shortfall on another. The pair-level shortfalls show where the cushion
broke; the netted figure is what is actually lost.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from lab.book import ASSET_CLASSES, HORIZON_DAYS
from lab.exposure import mark_positions

STEPS = [
    "Freeze: stop new loans to the borrower, hold all collateral, notify "
    "Credit Risk, Legal and the lending clients' relationship owners.",
    "Mark: value every lent security and every collateral line at the "
    "close, confirm which lending clients are indemnified.",
    "Close out: sell collateral, buy the lent securities back over the "
    "liquidation horizon, return them to the clients, book the shortfall.",
]


def horizon_ratios(con, horizon=HORIZON_DAYS):
    """Price factor over the next `horizon` days for every (class, side),
    one row per window start date."""
    px = con.execute(
        "SELECT date, asset_class, side, price FROM prices ORDER BY date").df()
    wide = px.pivot_table(index="date", columns=["asset_class", "side"],
                          values="price")
    return (wide.shift(-horizon) / wide).dropna()


@dataclass
class DrillResult:
    borrower_id: str
    name: str
    path: str
    window_start: object         # date the stressed window opens, None if orderly
    by_pair: pd.DataFrame        # loan_class, coll_class, loan, collateral, buyback, proceeds, shortfall
    by_collateral: pd.DataFrame  # collateral class rollup
    collateral_at_default: float
    loan_at_default: float
    buyback_cost: float
    proceeds: float
    gross_shortfall: float       # sum of pair shortfalls, no netting
    net_shortfall: float         # what the indemnity actually pays
    nonstd_share: float


def _pair_book(con, borrower_id):
    marks = mark_positions(con)
    sub = marks[marks.borrower_id == borrower_id]
    if sub.empty:
        raise KeyError(borrower_id)
    pair = sub.groupby(["loan_class", "coll_class"], as_index=False).agg(
        loan=("loan_asof", "sum"), collateral=("coll_asof", "sum"))
    pair["loan_liq"] = pair["loan_class"].map(
        lambda c: 1 + ASSET_CLASSES[c]["liq_cost"])
    pair["coll_liq"] = pair["coll_class"].map(
        lambda c: 1 - ASSET_CLASSES[c]["liq_cost"])
    return pair


def _worst_window(pair, ratios):
    """The window start date that maximises buyback minus proceeds for
    this mix when every class moves over the same two days."""
    buyback = sum(
        r["loan"] * r["loan_liq"] * ratios[(r["loan_class"], "loan")]
        for _, r in pair.iterrows())
    proceeds = sum(
        r["collateral"] * r["coll_liq"] * ratios[(r["coll_class"], "collateral")]
        for _, r in pair.iterrows())
    loss = buyback - proceeds
    return loss.idxmax()


def run(con, borrower_id, path="stressed", ratios=None):
    pair = _pair_book(con, borrower_id)
    name = con.execute(
        "SELECT name FROM borrowers WHERE borrower_id = ?", [borrower_id]
    ).fetchone()[0]

    window = None
    if path == "stressed":
        if ratios is None:
            ratios = horizon_ratios(con)
        window = _worst_window(pair, ratios)
        move = ratios.loc[window]
    else:
        move = pd.Series(1.0, index=pd.MultiIndex.from_product(
            [list(ASSET_CLASSES), ["loan", "collateral"]]))

    pair["buyback"] = pair.apply(
        lambda r: r["loan"] * r["loan_liq"] * move[(r["loan_class"], "loan")],
        axis=1)
    pair["proceeds"] = pair.apply(
        lambda r: r["collateral"] * r["coll_liq"]
        * move[(r["coll_class"], "collateral")], axis=1)
    pair["shortfall"] = (pair["buyback"] - pair["proceeds"]).clip(lower=0.0)
    pair["nonstandard"] = pair["coll_class"].map(
        lambda c: ASSET_CLASSES[c]["nonstandard"])
    pair = pair.drop(columns=["loan_liq", "coll_liq"])

    by_coll = pair.groupby("coll_class", as_index=False).agg(
        collateral=("collateral", "sum"), proceeds=("proceeds", "sum"),
        buyback=("buyback", "sum"), shortfall=("shortfall", "sum"))
    by_coll["nonstandard"] = by_coll["coll_class"].map(
        lambda c: ASSET_CLASSES[c]["nonstandard"])

    buyback = float(pair["buyback"].sum())
    proceeds = float(pair["proceeds"].sum())
    coll = float(pair["collateral"].sum())
    return DrillResult(
        borrower_id=borrower_id, name=name, path=path, window_start=window,
        by_pair=pair, by_collateral=by_coll,
        collateral_at_default=coll,
        loan_at_default=float(pair["loan"].sum()),
        buyback_cost=buyback, proceeds=proceeds,
        gross_shortfall=float(pair["shortfall"].sum()),
        net_shortfall=max(0.0, buyback - proceeds),
        nonstd_share=float(pair.loc[pair.nonstandard, "collateral"].sum() / coll)
        if coll else 0.0,
    )


def all_borrowers(con, path="stressed"):
    """Net shortfall for every borrower under one path, for the risk and
    reward view: which names would actually cost money."""
    ratios = horizon_ratios(con) if path == "stressed" else None
    ids = [r[0] for r in con.execute(
        "SELECT borrower_id FROM borrowers ORDER BY borrower_id").fetchall()]
    rows = []
    for bid in ids:
        r = run(con, bid, path, ratios)
        rows.append({"borrower_id": bid, "name": r.name,
                     "net_shortfall": r.net_shortfall,
                     "gross_shortfall": r.gross_shortfall,
                     "window_start": r.window_start,
                     "nonstd_share": r.nonstd_share})
    return pd.DataFrame(rows)
