"""Senior management exposure summary.

The engine computes, the model only phrases. The LLM gets a short block
of finished figures and writes three paragraphs around them. Every
number in its reply is checked against the figures it was given; if it
invented one the reply is discarded for the deterministic template. No
API key means the template runs alone and nothing needs a network.
"""

import os
import re

PROMPT = """You are drafting a short exposure summary for senior management
of a securities lending business on a volatile day. Use ONLY the numbers
given below. Do not compute, estimate or invent any figure. Plain business
English, explain any term of art in a few words, three short paragraphs
maximum, no bullet points, no headers.

Style: write money exactly as given below, for example "$42.5 billion" or
"$133 million", never as raw millions like 42,481. Short sentences. Do not
use dashes of any kind; use commas or full stops instead.

Figures:
{facts}
"""


def money(x):
    """USD millions in, prose out: 42481 -> $42.5 billion, 133 -> $133 million."""
    x = float(x)
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1000:
        return f"{sign}${a / 1000:,.1f} billion"
    return f"{sign}${a:,.0f} million"


def pct(x):
    return f"{100 * float(x):.1f}%"


# dashes and curly quotes read as machine-written; swap them for plain
# punctuation before anything reaches the screen
_DASHES = str.maketrans({"\u2014": ",", "\u2013": ",", "\u2018": "'",
                         "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def humanize(text):
    text = text.translate(_DASHES)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s+([.;:!?])", r"\1", text)
    return text.strip()


def build_payload(summary, totals, bt_summary, drill, as_of):
    called = summary[summary.margin_call > 0].sort_values(
        "margin_call", ascending=False)
    over = summary[summary.utilization > 1.0]["name"].tolist()
    nonstd = summary[summary.nonstd_share > summary.nonstd_cap]["name"].tolist()
    return {
        "as_of": as_of.strftime("%B %d, %Y"),
        "on_loan": round(totals["on_loan"]),
        "collateral": round(totals["collateral"]),
        "coverage_pct": round(100 * totals["coverage"], 1),
        "n_margin_calls": totals["n_margin_calls"],
        "margin_call_total": round(totals["margin_call_total"]),
        "largest_call_name": called["name"].iloc[0] if len(called) else None,
        "largest_call": round(called["margin_call"].iloc[0]) if len(called) else 0,
        "over_limit": over,
        "nonstd_breaches": nonstd,
        "flat_exceed": bt_summary["flat_exceed"],
        "scaled_exceed": bt_summary["scaled_exceed"],
        "expected_exceed": round(bt_summary["expected_per_pair"]
                                 * bt_summary["n_pairs"]),
        "collateral_saved": round(bt_summary["flat_required"]
                                  - bt_summary["scaled_required"]),
        "drill_name": drill.name,
        "drill_loan": round(drill.loan_at_default),
        "drill_shortfall": round(drill.net_shortfall),
        "drill_window": drill.window_start.strftime("%B %d, %Y")
        if drill.window_start is not None else None,
        "drill_nonstd_pct": round(100 * drill.nonstd_share, 1),
    }


def _names(names):
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def template_narrative(p):
    """Deterministic fallback. Plain, and every number is the engine's."""
    calls = (f"{p['n_margin_calls']} borrowers are below their margin "
             f"requirement after today's move and margin calls totalling "
             f"{money(p['margin_call_total'])} go out this morning, the "
             f"largest to {p['largest_call_name']} at "
             f"{money(p['largest_call'])}."
             if p["n_margin_calls"] else
             "No borrower is below its margin requirement after today's move.")
    limits = ""
    if p["over_limit"]:
        verb = "is" if len(p["over_limit"]) == 1 else "are"
        limits += (f" {_names(p['over_limit'])} {verb} over the exposure "
                   "limit and needs a decision today.")
    if p["nonstd_breaches"]:
        verb = "holds" if len(p["nonstd_breaches"]) == 1 else "hold"
        limits += (f" {_names(p['nonstd_breaches'])} {verb} more "
                   "non-standard collateral than the cap allows.")

    para1 = (f"As of {p['as_of']} the book has {money(p['on_loan'])} on loan "
             f"against {money(p['collateral'])} of collateral, a coverage of "
             f"{p['coverage_pct']}%. {calls}{limits}")

    para2 = (f"On the haircut methodology, the flat house schedule was "
             f"beaten {p['flat_exceed']} times in the backtest where about "
             f"{p['expected_exceed']} would be expected at 99% confidence. The "
             f"volatility scaled haircut was beaten {p['scaled_exceed']} times "
             f"and needs {money(p['collateral_saved'])} less collateral on "
             "today's book. The cushion is in the wrong places, not too "
             "small overall.")

    if p["drill_shortfall"] > 0:
        para3 = (f"If {p['drill_name']} defaulted today with "
                 f"{money(p['drill_loan'])} on loan, closing out over the worst "
                 f"two days in the last three years (the window opening "
                 f"{p['drill_window']}) would leave a shortfall of "
                 f"{money(p['drill_shortfall'])} after netting, which the "
                 f"indemnity would pay. {p['drill_nonstd_pct']}% of its "
                 "collateral is non-standard.")
    else:
        para3 = (f"If {p['drill_name']} defaulted today with "
                 f"{money(p['drill_loan'])} on loan, the close out would leave "
                 "no shortfall after netting on this path.")
    return f"{para1}\n\n{para2}\n\n{para3}"


def _facts_block(p):
    lines = [
        f"as of date: {p['as_of']}",
        f"on loan: {money(p['on_loan'])}",
        f"collateral held: {money(p['collateral'])}",
        f"coverage (collateral over loans): {p['coverage_pct']}%",
        f"borrowers with a margin call today: {p['n_margin_calls']}",
        f"total margin calls: {money(p['margin_call_total'])}",
    ]
    if p["largest_call_name"]:
        lines.append(f"largest margin call: {p['largest_call_name']}, "
                     f"{money(p['largest_call'])}")
    if p["over_limit"]:
        lines.append(f"borrowers over exposure limit: {_names(p['over_limit'])}")
    if p["nonstd_breaches"]:
        lines.append("borrowers over their non-standard collateral cap: "
                     f"{_names(p['nonstd_breaches'])}")
    lines += [
        f"backtest, flat house haircut exceedances: {p['flat_exceed']}",
        f"backtest, volatility scaled haircut exceedances: {p['scaled_exceed']}",
        f"backtest, exceedances expected at 99% confidence: {p['expected_exceed']}",
        f"collateral the scaled method needs less of, on today's book: "
        f"{money(p['collateral_saved'])}",
        f"fire drill borrower: {p['drill_name']}, {money(p['drill_loan'])} on loan",
        f"fire drill shortfall after netting, worst two day window: "
        f"{money(p['drill_shortfall'])}",
        f"fire drill window opens: {p['drill_window']}",
        f"fire drill borrower non-standard collateral share: {p['drill_nonstd_pct']}%",
    ]
    return "\n".join(lines)


def _numbers_in(text):
    out = set()
    for m in re.findall(r"\d[\d,]*\.?\d*", text):
        try:
            out.add(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def _allowed_numbers(p):
    # anything we wrote in the facts block is fair game, plus billion
    # and million restatements of every money figure
    allowed = _numbers_in(_facts_block(p))
    for k in ("on_loan", "collateral", "margin_call_total", "largest_call",
              "collateral_saved", "drill_loan", "drill_shortfall"):
        v = float(p[k])
        allowed |= {v, round(v), round(v / 1000, 1), round(v / 1000, 2)}
    allowed |= {1.0, 2.0, 3.0, 99.0, 100.0}
    return allowed


def check_numbers(text, payload):
    """Every number in the text must match a figure we gave the model.
    Returns the offending numbers."""
    allowed = _allowed_numbers(payload)
    bad = []
    for n in _numbers_in(text):
        ok = any(abs(n - a) <= max(0.5, 0.005 * abs(a)) for a in allowed)
        if not ok:
            bad.append(n)
    return bad


def llm_narrative(payload, api_key):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1000,
        messages=[{"role": "user",
                   "content": PROMPT.format(facts=_facts_block(payload))}],
    )
    # newer models can return thinking blocks ahead of the text block
    parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
    return humanize("\n".join(parts))


def generate(payload, api_key=None):
    """Returns (text, source) where source is 'llm' or 'template'."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if key:
        try:
            text = llm_narrative(payload, key)
            if not check_numbers(text, payload):
                return text, "llm"
            # model invented a number: discard, use the safe path
        except Exception:
            pass
    return template_narrative(payload), "template"
