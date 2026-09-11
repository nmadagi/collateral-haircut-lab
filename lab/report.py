"""Exports: the morning exposure report as CSV, the senior management
summary as a one-page PDF. Plain on purpose."""

import io

from fpdf import FPDF

from lab.book import label

# core pdf fonts are latin-1 only; map the usual unicode suspects and
# replace anything else rather than crash on an odd character
_CHAR_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
})


def _latin1(s):
    return s.translate(_CHAR_MAP).encode("latin-1", "replace").decode("latin-1")


def _m(x):
    """USD millions in, short text out."""
    a = abs(float(x))
    sign = "-" if x < 0 else ""
    if a >= 1000:
        return f"{sign}${a / 1000:,.2f}B"
    return f"{sign}${a:,.0f}M"


EXPOSURE_COLUMNS = [
    "borrower_id", "name", "type", "grade", "on_loan", "collateral",
    "coverage", "required_coverage", "excess", "margin_call", "limit",
    "utilization", "nonstd_share", "nonstd_cap", "fee_income", "status"]


def exposure_csv(summary):
    return summary[EXPOSURE_COLUMNS].to_csv(index=False).encode()


def summary_pdf(payload, narrative_text, summary, drill):
    pdf = FPDF()
    pdf.set_margins(18, 16, 18)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 9, "Securities Lending Exposure Summary",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 5, f"As of {payload['as_of']}  |  synthetic book, "
                   "not a real institution", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_text_color(0, 0, 0)

    rows = [
        ("On loan", _m(payload["on_loan"])),
        ("Collateral held", _m(payload["collateral"])),
        ("Coverage", f"{payload['coverage_pct']}%"),
        ("Margin calls today",
         f"{payload['n_margin_calls']} borrowers, {_m(payload['margin_call_total'])}"),
        ("Flat haircut exceedances (backtest)", str(payload["flat_exceed"])),
        ("Scaled haircut exceedances (backtest)", str(payload["scaled_exceed"])),
        ("Collateral saved by scaled method", _m(payload["collateral_saved"])),
        (f"Fire drill, {drill.name}, net shortfall",
         _m(payload["drill_shortfall"])),
    ]
    for lab_, val in rows:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(110, 7, _latin1(lab_), border="B")
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, _latin1(val), border="B", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for para in narrative_text.split("\n\n"):
        pdf.multi_cell(0, 5.5, _latin1(para))
        pdf.ln(2)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Borrowers needing a decision today",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    flagged = summary[summary.status != "ok"]
    if flagged.empty:
        pdf.cell(0, 6, "None.", new_x="LMARGIN", new_y="NEXT")
    for _, r in flagged.iterrows():
        pdf.cell(40, 6, _latin1(r["name"]))
        pdf.cell(35, 6, _m(r["on_loan"]) + " on loan")
        pdf.cell(0, 6, _latin1(r["status"]), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, f"Fire drill, {drill.name}: where the cushion broke",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for _, r in drill.by_collateral.iterrows():
        pdf.cell(60, 6, _latin1(label(r["coll_class"])))
        pdf.cell(45, 6, "collateral " + _m(r["collateral"]))
        pdf.cell(0, 6, "shortfall " + _m(r["shortfall"]),
                 new_x="LMARGIN", new_y="NEXT")

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
