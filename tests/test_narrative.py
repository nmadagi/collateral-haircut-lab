import pytest

from lab import exposure, firedrill, haircut, narrative, report
from lab.load import build_db, as_of_dates


@pytest.fixture(scope="module")
def parts():
    con, _ = build_db()
    summary = exposure.borrower_summary(con)
    totals = exposure.book_totals(summary)
    bt = haircut.summary(haircut.backtest(con))
    drill = firedrill.run(con, "B04", "stressed")
    as_of, _ = as_of_dates(con)
    payload = narrative.build_payload(summary, totals, bt, drill, as_of)
    con.close()
    return payload, summary, drill


def test_payload_has_the_headline_numbers(parts):
    p, _, _ = parts
    assert p["n_margin_calls"] >= 1
    assert p["flat_exceed"] > p["scaled_exceed"]
    assert p["collateral_saved"] > 0
    assert p["drill_shortfall"] > 0
    assert "Dogwood" in p["over_limit"]


def test_template_passes_its_own_number_check(parts):
    p, _, _ = parts
    text = narrative.template_narrative(p)
    assert narrative.check_numbers(text, p) == []
    assert p["largest_call_name"] in text


def test_check_numbers_catches_invented_figures(parts):
    p, _, _ = parts
    bad = narrative.check_numbers("Exposure reached 77,777 and coverage was 88.8%.", p)
    assert 77777.0 in bad
    assert 88.8 in bad


def test_check_numbers_allows_restatement_in_billions(parts):
    p, _, _ = parts
    bn = round(p["on_loan"] / 1000, 1)
    assert narrative.check_numbers(f"The book has {bn} billion on loan.", p) == []


def test_generate_falls_back_without_key(parts, monkeypatch):
    p, _, _ = parts
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    text, source = narrative.generate(p)
    assert source == "template"
    assert len(text) > 200


def test_humanize_strips_dashes():
    assert "—" not in narrative.humanize("a — b – c")
    assert narrative.humanize("x ’y’") == "x 'y'"


def test_money_formats():
    assert narrative.money(42481) == "$42.5 billion"
    assert narrative.money(133) == "$133 million"
    assert narrative.money(-50) == "-$50 million"


def test_exports_produce_bytes(parts):
    p, summary, drill = parts
    csv = report.exposure_csv(summary)
    assert csv.startswith(b"borrower_id,name")
    assert csv.count(b"\n") == len(summary) + 1
    pdf = report.summary_pdf(p, narrative.template_narrative(p), summary, drill)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1500
