"""Collateral haircut lab - Streamlit front end.

Three tabs: exposure monitor, haircut backtest, fire drill. Every number
comes out of the validated DuckDB layer; the UI only formats.
"""

import json
import os
import tomllib

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lab import exposure, firedrill, haircut, narrative, report
from lab.book import (BORROWERS, CONFIDENCE, HORIZON_DAYS, LOOKBACK_DAYS,
                      label, short_label)
from lab.load import as_of_dates, build_db

st.set_page_config(page_title="collateral haircut lab", layout="wide")

ACCENT = "#1f5c8b"
RED = "#b03a2e"
GREY = "#7f8c8d"
DARK = "#2c3e50"
GREEN = "#27ae60"
AMBER = "#e67e22"
ZONE_COLOR = {"green": GREEN, "amber": AMBER, "red": RED}


def usd(x):
    # engine values are USD millions; show billions past 1,000
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1000:
        return f"{sign}${a / 1000:,.2f}B"
    return f"{sign}${a:,.0f}M"


def bn(x):
    """millions -> billions for chart axes."""
    return x / 1000


def card(col, title, value, note=None, alert=False):
    """A metric with a plain note under it. st.metric's delta draws an up
    arrow, which reads as "up since yesterday" on a label that is not a
    change at all, so the note is a caption instead."""
    col.metric(title, value)
    if note:
        # escape dollars, streamlit reads $...$ in markdown as latex
        text = note.replace("$", "\\$")
        col.caption(f":red[{text}]" if alert else text)


@st.cache_resource
def get_db():
    con, results = build_db()
    return con, results


@st.cache_data
def run_backtest(_con):
    return haircut.backtest(_con)


@st.cache_data
def stress_by_borrower(_con):
    return firedrill.all_borrowers(_con, "stressed")


@st.cache_data(show_spinner="drafting the summary")
def make_narrative(payload_json, api_key):
    return narrative.generate(json.loads(payload_json), api_key)


# one cached database, but a duckdb connection is not safe to share
# between threads and streamlit runs every browser session in its own.
# a cursor is a cheap per-session connection onto the same in-memory db.
_base_con, check_results = get_db()
con = _base_con.cursor()
as_of, prev_close = as_of_dates(con)

st.title("Collateral haircut lab")
st.caption(
    "Synthetic agency securities lending book, twelve borrowers, charts in "
    "USD billions. All data is generated: no real institution, no real "
    "client, no real price. Built to study where the margin cushion is "
    "too thin, where it is wasted, and what a borrower default costs.")
passed = sum(1 for r in check_results if r.passed)
st.caption(f"Data layer: {passed}/{len(check_results)} validation checks "
           "passed on this load (record counts, control totals, collateral "
           f"tied to requirement, price completeness). As of "
           f"{as_of.strftime('%d %b %Y')} close.")

summary = exposure.borrower_summary(con)
totals = exposure.book_totals(summary)
bt = run_backtest(con)
bt_sum = haircut.summary(bt)
stress = stress_by_borrower(con)

tab_exp, tab_bt, tab_drill = st.tabs([
    "Exposure monitor", "Haircut backtest", "Fire drill"])


# ------------------------------------------------------------ exposure
with tab_exp:
    c1, c2, c3, c4 = st.columns(4)
    card(c1, "On loan", usd(totals["on_loan"]))
    card(c2, "Collateral held", usd(totals["collateral"]),
         f"{totals['coverage']:.1%} coverage")
    card(c3, "Margin calls this morning",
         f"{totals['n_margin_calls']} borrowers",
         f"{usd(totals['margin_call_total'])} to collect today",
         alert=totals["n_margin_calls"] > 0)
    card(c4, "Limit and cap breaches",
         f"{totals['n_limit_breaches'] + totals['n_nonstd_breaches']}",
         f"{totals['n_limit_breaches']} exposure limit, "
         f"{totals['n_nonstd_breaches']} non-standard collateral cap")

    st.subheader("On loan against exposure limit, by borrower")
    plot = summary.sort_values("on_loan")
    def _color(status):
        if status == "ok":
            return ACCENT
        if status.replace("near limit", "").replace(
                "non-standard near cap", "").strip(", ") == "":
            return AMBER
        return RED
    colors = [_color(s) for s in plot["status"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=bn(plot["on_loan"]), y=plot["name"], orientation="h",
        marker_color=colors, showlegend=False,
        hovertemplate="%{y}: $%{x:.2f}B on loan<extra></extra>"))
    # the bar colour is the status, so the legend names the three statuses
    for status, color in [("clean", ACCENT), ("near a limit or cap", AMBER),
                          ("action today", RED)]:
        fig.add_trace(go.Bar(x=[None], y=[None], orientation="h",
                             marker_color=color, name=status))
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                             line=dict(color=DARK, width=2),
                             name="exposure limit"))
    # a marker symbol drifted off its row, so each limit is a line drawn
    # inside that borrower's own category slot
    for i, lim in enumerate(plot["limit"]):
        fig.add_shape(type="line", xref="x", yref="y", x0=bn(lim),
                      x1=bn(lim), y0=i - 0.42, y1=i + 0.42,
                      line=dict(color=DARK, width=2))
    xmax = bn(max(plot["limit"].max(), plot["on_loan"].max())) * 1.05
    fig.update_layout(height=440, barmode="overlay",
                      margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(title="USD billions", tickprefix="$",
                                 ticksuffix="B", range=[0, xmax]),
                      yaxis=dict(categoryorder="array",
                                 categoryarray=list(plot["name"])),
                      legend=dict(orientation="h", y=-0.18))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Bar colour is today's status. Blue is clean. Amber is within "
               "10% of a limit or cap. Red needs a decision today: a margin "
               "call, a limit breach or too much non-standard collateral. "
               "The dark tick on each row is that borrower's exposure limit; "
               "a bar past its tick is over the limit.")

    st.subheader("Morning exposure report")
    rep = summary.merge(stress[["borrower_id", "net_shortfall"]],
                        on="borrower_id")
    table = pd.DataFrame({
        "Borrower": rep["name"] + " (" + rep["type"] + ", " + rep["grade"] + ")",
        "On loan": rep["on_loan"].map(usd),
        "Collateral": rep["collateral"].map(usd),
        "Coverage": rep["coverage"].map(lambda v: f"{v:.1%}"),
        "Required": rep["required_coverage"].map(lambda v: f"{v:.1%}"),
        "Excess / (call)": rep["excess"].map(
            lambda v: usd(v) if v >= 0 else f"({usd(-v)})"),
        "Limit used": rep["utilization"].map(lambda v: f"{v:.0%}"),
        "Non-standard": rep.apply(
            lambda r: f"{r['nonstd_share']:.0%} of {r['nonstd_cap']:.0%}",
            axis=1),
        "Fee / yr": rep["fee_income"].map(usd),
        "Stress shortfall": rep["net_shortfall"].map(usd),
        "Status": rep["status"],
    })
    st.dataframe(table, hide_index=True, use_container_width=True,
                 column_config={
                     "Borrower": st.column_config.TextColumn(width="medium"),
                     "Status": st.column_config.TextColumn(width="large")})
    st.caption(
        "Required is the flat house schedule weighted across the borrower's "
        "collateral. Excess is what it holds above that after today's move; "
        "a bracketed figure is the call going out. Non-standard is equities "
        "and high yield as collateral, against the cap for that credit "
        "grade. Stress shortfall is the fire drill loss for that borrower "
        "(tab three). Fee against stress shortfall is the risk and reward "
        "of each name in one row.")
    st.download_button("Download exposure report (CSV)",
                       report.exposure_csv(summary),
                       file_name=f"exposure_{as_of.strftime('%Y%m%d')}.csv")


# ------------------------------------------------------------ backtest
with tab_bt:
    saved = bt_sum["flat_required"] - bt_sum["scaled_required"]
    c1, c2, c3, c4 = st.columns(4)
    card(c1, "Flat schedule exceedances", bt_sum["flat_exceed"],
         f"{bt_sum['flat_red']} pairs red", alert=bt_sum["flat_red"] > 0)
    card(c2, "Scaled haircut exceedances", bt_sum["scaled_exceed"],
         f"{bt_sum['scaled_red']} pairs red", alert=bt_sum["scaled_red"] > 0)
    card(c3, "Expected at 99%",
         f"{round(bt_sum['expected_per_pair'] * bt_sum['n_pairs'])}",
         f"{bt_sum['n_windows']} windows x {bt_sum['n_pairs']} pairs")
    card(c4, "Collateral saved by scaling", usd(saved),
         f"{usd(bt_sum['flat_required'])} flat, "
         f"{usd(bt_sum['scaled_required'])} scaled")

    st.subheader("Exceedances per pair, flat schedule vs volatility scaled")
    names = [f"{short_label(l)}<br>vs {short_label(c)}" for l, c in
             zip(bt["loan_class"], bt["coll_class"])]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=names, y=bt["flat_exceed"], name="flat schedule",
                         marker_color=GREY))
    fig.add_trace(go.Bar(x=names, y=bt["scaled_exceed"],
                         name="volatility scaled", marker_color=ACCENT))
    fig.add_hline(y=bt_sum["expected_per_pair"], line_color=RED,
                  line_dash="dash",
                  annotation_text=f"expected {bt_sum['expected_per_pair']:.0f}",
                  annotation_position="top left")
    fig.update_layout(barmode="group", height=420,
                      margin=dict(l=10, r=10, t=10, b=10),
                      yaxis=dict(title="exceedances", rangemode="tozero"),
                      xaxis=dict(tickangle=0, title="lent vs held"),
                      legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"An exceedance is a {HORIZON_DAYS}-day window where the lent basket "
        "rose against the collateral basket by more than the haircut. "
        f"Tested on every day after the first {LOOKBACK_DAYS}, so "
        f"{bt_sum['n_windows']} windows per pair, about "
        f"{bt_sum['expected_per_pair']:.0f} exceedances expected at "
        f"{CONFIDENCE:.0%}. The flat schedule is beaten constantly on "
        "equity loans against cash and never on Treasuries: the cushion is "
        "in the wrong places.")

    st.subheader("Haircut by pair")
    tbl = pd.DataFrame({
        "Lent": bt["loan_class"].map(label),
        "Collateral": bt["coll_class"].map(label),
        "On loan": bt["on_loan"].map(usd),
        "Flat haircut": bt["flat_haircut"].map(lambda v: f"{100 * (1 + v):.1f}%"),
        "Flat exceedances": bt["flat_exceed"],
        "Flat zone": bt["flat_zone"],
        "Scaled haircut today": bt["scaled_haircut"].map(
            lambda v: f"{100 * (1 + v):.1f}%"),
        "Scaled exceedances": bt["scaled_exceed"],
        "Scaled zone": bt["scaled_zone"],
    })
    st.dataframe(tbl, hide_index=True, use_container_width=True)
    st.caption(
        "Scaled haircut is a 99% two-day move of the loan-over-collateral "
        "ratio from the trailing year of daily moves, refreshed daily, "
        "floored at 101%. That is a VaR on the spread between the two "
        "baskets. Zones follow the Basel traffic light: green inside the "
        "95th percentile of the expected count, amber to the 99.99th, red "
        "beyond. The scaled method still runs amber on two equity pairs "
        "because a trailing-year volatility lags the onset of a stress.")


# ------------------------------------------------------------ fire drill
with tab_drill:
    left, right = st.columns([2, 1])
    names_by_id = {bid: f"{cfg['name']} ({cfg['type']}, {cfg['grade']})"
                   for bid, cfg in BORROWERS.items()}
    with left:
        pick = st.selectbox("Borrower that fails today", list(names_by_id),
                            format_func=names_by_id.get,
                            index=list(names_by_id).index("B04"))
    with right:
        path = st.radio("Close-out path", ["stressed", "orderly"],
                        horizontal=True,
                        help="stressed: the worst two consecutive days in "
                             "the three-year history for this borrower's "
                             "mix. orderly: today's prices less liquidation "
                             "cost.")
    drill = firedrill.run(con, pick, path)

    c1, c2, c3, c4 = st.columns(4)
    cap = BORROWERS[pick]["nonstd_cap"]
    card(c1, "On loan at default", usd(drill.loan_at_default))
    card(c2, "Collateral at default", usd(drill.collateral_at_default))
    card(c3, "Net shortfall (indemnity pays)", usd(drill.net_shortfall),
         f"gross {usd(drill.gross_shortfall)} before netting")
    card(c4, "Non-standard collateral", f"{drill.nonstd_share:.0%}",
         f"cap {cap:.0%}", alert=drill.nonstd_share > cap)
    def _moves(side):
        return ", ".join(f"{label(c)} {v:+.1%}" if v else f"{label(c)} 0.0%"
                         for c, v in drill.class_moves[side].items())
    gap = (f"a gap of {usd(drill.net_shortfall)}" if drill.net_shortfall > 0
           else "no gap after netting")
    if drill.window_start is not None:
        why = (f"Stressed window opens "
               f"{drill.window_start.strftime('%d %b %Y')}, the worst two "
               "days in the history for this borrower's mix, every class "
               f"moved together. Over those two days what {drill.name} "
               f"borrowed moved {_moves('lent')}, and what it posted moved "
               f"{_moves('held')}. ")
    else:
        why = ("Orderly close out: today's prices, less the cost of selling "
               "or buying a whole basket at once. ")
    st.caption((why + f"Buying back costs {usd(drill.buyback_cost)}, "
                f"selling the collateral raises {usd(drill.proceeds)} after "
                f"selling costs, {gap}.").replace("$", "\\$"))

    st.subheader("Close out by collateral class")
    bc = drill.by_collateral
    xs = bc["coll_class"].map(label)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=xs, y=bn(bc["collateral"]), name="collateral held",
                         marker_color=GREY))
    fig.add_trace(go.Bar(x=xs, y=bn(bc["proceeds"]), name="sale proceeds",
                         marker_color=ACCENT))
    fig.add_trace(go.Bar(x=xs, y=bn(bc["buyback"]),
                         name="cost to buy the lent securities back",
                         marker_color=RED))
    fig.update_layout(barmode="group", height=380,
                      margin=dict(l=10, r=10, t=10, b=10),
                      yaxis=dict(title="USD billions", tickprefix="$",
                                 ticksuffix="B", rangemode="tozero"),
                      legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Grey is what the borrower had posted. Blue is what selling "
               "it raises. Red is what buying back the securities it "
               "secures costs. Red above blue is the shortfall on that "
               "collateral. Under one master agreement the whole portfolio "
               "nets, so the loss is the total red minus the total blue.")

    st.subheader("The drill")
    for i, step in enumerate(firedrill.STEPS, 1):
        st.write(f"{i}. {step}")

    st.subheader("Senior management summary")
    # streamlit prints a scary red box if you touch st.secrets with no
    # secrets.toml where it looks (working dir or home, not the app dir).
    # only call it when one of those exists; if the app was launched from
    # elsewhere, read the app-dir file directly.
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    st_paths = [os.path.join(os.getcwd(), ".streamlit", "secrets.toml"),
                os.path.expanduser("~/.streamlit/secrets.toml")]
    app_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".streamlit", "secrets.toml")
    if not api_key and any(os.path.exists(p) for p in st_paths):
        api_key = st.secrets.get("ANTHROPIC_API_KEY", None)
    elif not api_key and os.path.exists(app_path):
        with open(app_path, "rb") as f:
            api_key = tomllib.load(f).get("ANTHROPIC_API_KEY")
    payload = narrative.build_payload(summary, totals, bt_sum, drill, as_of)
    text, source = make_narrative(json.dumps(payload), api_key)
    # streamlit treats $...$ as latex, which mangles dollar amounts
    st.write(text.replace("$", "\\$"))
    st.caption(
        "Drafted by the LLM from computed figures only, then verified "
        "number-by-number against the engine output."
        if source == "llm" else
        "Deterministic template (no API key configured; the LLM path, "
        "when enabled, is verified number-by-number against the engine "
        "output).")
    st.download_button("Download summary (PDF)",
                       report.summary_pdf(payload, text, summary, drill),
                       file_name=f"exposure_summary_{pick}_{path}.pdf")
