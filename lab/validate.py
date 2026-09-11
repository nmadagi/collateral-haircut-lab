"""Validation gates that run at every load.

Three families:
  record counts   - every row the generator wrote is in the table
  control totals  - on loan per borrower and collateral per borrower in
                    the database match the figures computed at generation,
                    and the book total matches the static config
  completeness    - no nulls, no negative values, no price at or below
                    zero, every series present on every date, every
                    borrower has positions and a limit

Every check returns a CheckResult; the loader blocks if any failed.
"""

from dataclasses import dataclass

from lab.book import BORROWERS, COLLATERAL_CLASSES, LOAN_CLASSES, total_on_loan

TOL = 1e-6


class ValidationError(Exception):
    pass


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def _check(name, passed, detail):
    return CheckResult(name=name, passed=bool(passed), detail=detail)


def run_checks(con, expected):
    results = []

    # record counts
    n_prices = con.execute("SELECT count(*) FROM prices").fetchone()[0]
    results.append(_check(
        "price_record_count", n_prices == expected["n_prices"],
        f"db has {n_prices}, generator wrote {expected['n_prices']}"))

    n_pos = con.execute("SELECT count(*) FROM positions").fetchone()[0]
    results.append(_check(
        "position_record_count", n_pos == expected["n_positions"],
        f"db has {n_pos}, generator wrote {expected['n_positions']}"))

    n_bor = con.execute("SELECT count(*) FROM borrowers").fetchone()[0]
    results.append(_check(
        "borrower_record_count", n_bor == len(BORROWERS),
        f"db has {n_bor}, config has {len(BORROWERS)}"))

    n_dates = con.execute("SELECT count(DISTINCT date) FROM prices").fetchone()[0]
    results.append(_check(
        "price_day_count", n_dates == expected["n_dates"] and n_dates >= 700,
        f"{n_dates} distinct dates"))

    # control totals
    on_loan = con.execute("SELECT sum(loan_prev) FROM positions").fetchone()[0]
    results.append(_check(
        "on_loan_control_total", abs(on_loan - expected["on_loan_total"]) < TOL,
        f"db {on_loan:.6f} vs generator {expected['on_loan_total']:.6f}"))
    results.append(_check(
        "on_loan_matches_config", abs(on_loan - total_on_loan()) < 1e-3,
        f"db {on_loan:.3f} vs config {total_on_loan()}"))

    by_b = dict(con.execute(
        "SELECT borrower_id, sum(loan_prev) FROM positions GROUP BY 1").fetchall())
    worst = max(abs(by_b.get(k, 0.0) - v)
                for k, v in expected["on_loan_by_borrower"].items())
    results.append(_check(
        "on_loan_by_borrower", worst < TOL,
        f"largest gap to generator {worst:.9f}"))

    coll_b = dict(con.execute(
        "SELECT borrower_id, sum(coll_prev) FROM positions GROUP BY 1").fetchall())
    worst = max(abs(coll_b.get(k, 0.0) - v)
                for k, v in expected["coll_by_borrower"].items())
    results.append(_check(
        "collateral_ties_to_requirement", worst < TOL,
        f"collateral vs loan x haircut x (1 + buffer), largest gap {worst:.9f}"))

    # completeness
    nulls = con.execute("""
        SELECT count(*) FROM positions
        WHERE loan_prev IS NULL OR coll_prev IS NULL OR loan_class IS NULL
           OR coll_class IS NULL OR borrower_id IS NULL
    """).fetchone()[0]
    results.append(_check("position_nulls", nulls == 0, f"{nulls} null rows"))

    neg = con.execute(
        "SELECT count(*) FROM positions WHERE loan_prev <= 0 OR coll_prev <= 0"
    ).fetchone()[0]
    results.append(_check("position_negatives", neg == 0,
                          f"{neg} non-positive values"))

    bad_px = con.execute(
        "SELECT count(*) FROM prices WHERE price IS NULL OR price <= 0"
    ).fetchone()[0]
    results.append(_check("price_positive", bad_px == 0,
                          f"{bad_px} null or non-positive prices"))

    gaps = con.execute("""
        SELECT count(*) FROM (
            SELECT date, count(*) AS n FROM prices GROUP BY date
        ) WHERE n <> ?
    """, [expected["n_series"]]).fetchone()[0]
    results.append(_check("price_series_complete", gaps == 0,
                          f"{gaps} dates missing at least one series"))

    # a 40% move in one day in any class is a data error, not a market
    jumps = con.execute("""
        SELECT count(*) FROM (
            SELECT price / lag(price) OVER (
                PARTITION BY asset_class, side ORDER BY date) AS ratio
            FROM prices
        ) WHERE ratio > 1.4 OR ratio < 0.6
    """).fetchone()[0]
    results.append(_check("price_jump_sanity", jumps == 0,
                          f"{jumps} one-day moves beyond 40%"))

    classes = set(r[0] for r in con.execute(
        "SELECT DISTINCT loan_class FROM positions").fetchall())
    bad = classes - set(LOAN_CLASSES)
    results.append(_check("loan_classes_known", not bad,
                          f"unknown loan classes {sorted(bad)}" if bad else "all known"))
    classes = set(r[0] for r in con.execute(
        "SELECT DISTINCT coll_class FROM positions").fetchall())
    bad = classes - set(COLLATERAL_CLASSES)
    results.append(_check("collateral_classes_known", not bad,
                          f"unknown collateral classes {sorted(bad)}" if bad else "all known"))

    orphan = con.execute("""
        SELECT count(*) FROM borrowers b
        LEFT JOIN positions p USING (borrower_id)
        WHERE p.position_id IS NULL
    """).fetchone()[0]
    results.append(_check("every_borrower_has_positions", orphan == 0,
                          f"{orphan} borrowers with no positions"))

    no_limit = con.execute(
        "SELECT count(*) FROM borrowers WHERE \"limit\" IS NULL OR \"limit\" <= 0"
    ).fetchone()[0]
    results.append(_check("every_borrower_has_limit", no_limit == 0,
                          f"{no_limit} borrowers without a limit"))

    return results
