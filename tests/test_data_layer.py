import duckdb
import pytest

from lab import book, generate
from lab.load import build_db, as_of_dates
from lab.validate import ValidationError, run_checks


@pytest.fixture(scope="module")
def db():
    con, results = build_db()
    yield con, results
    con.close()


def test_generator_is_deterministic():
    a = generate.generate_prices()
    b = generate.generate_prices()
    assert a.equals(b)
    assert generate.generate_positions().equals(generate.generate_positions())


def test_positions_sum_to_config():
    pos = generate.generate_positions()
    assert abs(pos["loan_prev"].sum() - book.total_on_loan()) < 1e-6
    for bid, cfg in book.BORROWERS.items():
        got = pos.loc[pos.borrower_id == bid, "loan_prev"].sum()
        assert abs(got - cfg["on_loan"]) < 1e-6


def test_every_pair_in_mix_has_positions():
    pos = generate.generate_positions()
    for bid, cfg in book.BORROWERS.items():
        sub = pos[pos.borrower_id == bid]
        pairs = set(zip(sub.loan_class, sub.coll_class))
        assert pairs == set(cfg["mix"])


def test_prices_have_two_sides_per_class():
    px = generate.generate_prices()
    per_class = px.groupby("asset_class")["side"].nunique()
    assert (per_class == 2).all()
    assert set(per_class.index) == set(book.ASSET_CLASSES)


def test_cash_never_moves():
    px = generate.generate_prices()
    assert (px.loc[px.asset_class == "cash", "price"] == 100.0).all()


def test_all_checks_pass(db):
    _, results = db
    assert all(r.passed for r in results), [r for r in results if not r.passed]
    assert len(results) >= 15


def test_as_of_dates_are_last_two(db):
    con, _ = db
    asof, prev = as_of_dates(con)
    assert asof > prev


def test_load_blocks_on_tampered_totals():
    prices = generate.generate_prices()
    borrowers = generate.generate_borrowers()
    positions = generate.generate_positions()
    expected = generate.control_totals(prices, borrowers, positions)
    expected["on_loan_total"] += 1.0
    con = duckdb.connect(":memory:")
    con.register("p", prices)
    con.register("b", borrowers)
    con.register("q", positions)
    con.execute("CREATE TABLE prices AS SELECT * FROM p")
    con.execute("CREATE TABLE borrowers AS SELECT * FROM b")
    con.execute("CREATE TABLE positions AS SELECT * FROM q")
    results = run_checks(con, expected)
    failed = [r.name for r in results if not r.passed]
    assert failed == ["on_loan_control_total"]
    con.close()


def test_missing_series_is_caught():
    prices = generate.generate_prices()
    borrowers = generate.generate_borrowers()
    positions = generate.generate_positions()
    expected = generate.control_totals(prices, borrowers, positions)
    drop = prices.index[(prices.asset_class == "ust") & (prices.side == "loan")][:3]
    broken = prices.drop(drop)
    con = duckdb.connect(":memory:")
    con.register("p", broken)
    con.register("b", borrowers)
    con.register("q", positions)
    con.execute("CREATE TABLE prices AS SELECT * FROM p")
    con.execute("CREATE TABLE borrowers AS SELECT * FROM b")
    con.execute("CREATE TABLE positions AS SELECT * FROM q")
    results = run_checks(con, expected)
    failed = {r.name for r in results if not r.passed}
    assert "price_series_complete" in failed
    assert "price_record_count" in failed
    con.close()


def test_validation_error_type_exists():
    assert issubclass(ValidationError, Exception)
