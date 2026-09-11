# Assumptions and calibration

Everything here is a choice I made, with the reason. Nothing is a real
institution, client or price.

## The book

Twelve borrowers, $42.0B on loan at the previous close. Four lent
classes: US Treasuries, IG corporate bonds, US large cap equities, US
small cap equities. Five collateral classes: cash, US Treasuries, IG
corporate bonds, US large cap equities, high yield bonds. Equities and
high yield as collateral are tagged non-standard.

The mix is fixed-income heavy on purpose. Agency lending programs lend
far more government bonds by value than equities, and a large share of
that is against non-cash collateral. That mix is also what makes the
thesis work: the flat schedule over-collateralises Treasury loans, so
scaling the haircut to volatility frees collateral there.

Each borrower has a habitual buffer of 0 to 3% above the requirement.
Some borrowers post exactly what is asked and get called every time the
market moves; some keep a cushion. Dogwood and Rowan keep none, which is
why they are called on the as-of day.

Position count per borrower is on loan divided by 60, clipped to 8 and
150, so about 700 loans in total. Sizes are lognormal within each pair
and scaled to the borrower's mix exactly.

## Prices

Three years of business days ending 31 December 2025. Three common
factors (equity, rates, credit) with Student t shocks (5 degrees of
freedom, scaled to unit variance) so tails are fat, clipped at five
standard deviations so no single draw prints a one-day move larger than
any real market has. The largest one-day equity move in the history is
about 7% for large caps and 9% for small caps. Each class index
loads on the factors:

| class | equity | rates | credit | own noise |
|---|---|---|---|---|
| US Treasuries | | 1.0 | | |
| IG corporate | | 0.9 | 0.9 | 0.05% |
| High yield | 0.25 | 0.3 | 1.5 | 0.10% |
| Large cap equity | 1.0 | | | 0.40% |
| Small cap equity | 1.2 | | | 0.60% |

Daily factor volatility: equity 1.0%, rates 0.25%, credit 0.30%.

Each class then has two baskets, lent and received, each the index plus
its own basket noise (Treasuries 0.05%, IG 0.15%, large cap 0.50%,
small cap 0.70%, high yield 0.30%). Without that, a large cap loan
against large cap collateral would have zero spread risk, which is not
true of two different baskets of stocks.

A stress episode is planted from day 330 to day 372 (six weeks in the
second year): factor volatility doubles, equities drift down 0.6% a day,
credit 0.2% a day, Treasuries drift up 0.1% a day. On the seed used,
large cap equities fall about 25 to 30% over the episode, high yield
about 14 to 17%, Treasuries gain about 3%. The seed was chosen from a scan so
the episode reads like 2020, not 1929.

The as-of day (the last day) carries an added rally shock: equity factor
up 2.5%, rates up 0.3%, credit up 0.4%. A lender is exposed when the
securities it lent out rise, so a rally is the day the margin calls go
out. Without it the monitor would show a quiet day.

Cash is 100 every day. Interest on cash collateral is ignored.

## Flat house schedule

102% for cash and Treasuries, 105% for everything else. This is the
convention in US agency lending: 102% for same-currency cash, 105% for
non-cash or cross-currency. The point of the backtest is that it is a
convention, not a risk number.

## Volatility scaled haircut

Haircut = 1 + z(99%) x trailing 250-day standard deviation of the daily
log change in (lent basket price / collateral basket price) x sqrt(2),
floored at 101%. Normal quantile, 2.3263. Two-day horizon: the default
day plus one day to buy the securities back under T+1 settlement. A
trailing simple standard deviation rather than an EWMA because a
requirement that jumps every day is not something a client will accept;
the cost is that it lags the onset of a stress, which the backtest shows
honestly as two amber pairs.

The floor at 101% is a judgement: no loan is lent with less than one
point of cushion whatever the volatility says.

## Backtest

Every day after the first 250 is a window start. The forward move is
the ratio change over the next two days. An exceedance is a forward
move larger than that day's haircut. Windows overlap, so one bad day is
seen by two windows; the expected count uses the same convention.

Zones use the Basel traffic light on the binomial count: green while
the cumulative probability of the observed count is below 95%, amber
below 99.99%, red above. On 250 days at 99% that gives the familiar
0 to 4 green, 5 to 9 amber, 10 and up red (BCBS, Supervisory framework
for the use of backtesting, 1996).

## Fire drill

Orderly path: today's prices less a liquidation cost per class
(Treasuries 0.05%, IG 0.4%, large cap 0.2%, small cap 1.0%, high
yield 1.0%) on both the sale of collateral and the buyback of lent
securities.

Stressed path: the worst two consecutive days in the three-year history
for that borrower's mix, with every class moved together over the same
two days, the window chosen to maximise buyback minus proceeds. One
joint historical window, not each class at its own worst day.

Netting: one master agreement per borrower, so the loss is total
buyback minus total proceeds, floored at zero. Pair-level shortfalls
show where the cushion broke.

## Limits

Exposure limit per borrower by credit grade. Non-standard collateral
cap by grade: 35% for A names and the BBB names that post equity
collateral, 25% for the HY-heavy BBB fund, 20% for BB. Near means
within 10% of the limit or cap.

## Fees

Annual lending fee by lent class: Treasuries 5bp, IG 15bp, large cap
35bp, small cap 150bp. Shown per borrower per year so fee can sit next
to stress shortfall as risk and reward.

## Simplifications I would name first

1. Daily marks, not intraday. The monitor runs at the close.
2. One price series per asset class and side, not per security. A
   basket, not a stock.
3. No cash collateral reinvestment. In a real program the cash is
   reinvested and that carries its own risk; here cash is cash.

Also: no CCAR capital calculation, no FX, no term loans, no
rehypothecation, no wrong-way risk between borrower and collateral.
