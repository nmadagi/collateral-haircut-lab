# build notes

Running notes to myself, roughly in order.

- Started from a number everyone in securities lending quotes and nobody
  backtests: 102 against cash, 105 against non-cash. A haircut is a VaR
  on the spread between what you lent and what you hold, so it should
  be testable like one. That is the whole repo.
- Same skeleton as my liquidity lab: seeded generator, DuckDB with
  gates, engine modules, three tabs. I know it deploys.
- First cut had one price series per asset class. Then a large cap
  loan against large cap collateral had exactly zero spread risk, which
  is not true of two different baskets of stocks. Every class now has
  a lent basket and a received basket, both the class index plus their
  own noise.
- Spent longer on the stress episode than on anything else. The first
  parameters gave equities down 58% in six weeks and down 70% over
  three years. Fat-tailed shocks plus a drift plus a volatility
  multiplier compound fast. Ended up scanning seeds against a
  plausibility rule (episode between 18 and 38% down, positive
  three-year equity return, Treasuries up in the episode) and fixing
  the one that passed. That is a choice and it is written down.
- The as-of day was originally just the last day. A calm last day gave
  a monitor with nothing to show. A lender is called when the lent
  securities rally, so the last day is now a rally by design.
- Horizon two days, not three or five. Default day plus one day to buy
  in under T+1. Five days is what the capital rules use for repo-style
  netting sets; two is what a liquid equity close-out actually takes.
  With five the flat schedule looks even worse, so two is the
  conservative choice for the thesis, not the lenient one.
- Traffic light zones: I first computed the cumulative probability of
  count minus one and got 5 exceedances on 250 days as green. The
  Basel table uses the cumulative probability of the count itself.
  Fixed, tested against the table.
- Overlapping two-day windows mean one bad day is two exceedances. I
  wrote a test expecting five and got ten before I remembered. The
  expected count uses the same convention so the comparison is fair.
- The first fire drill stress moved each class at its own worst day in
  history. Too easy to attack: those days never coincided. Now it is
  one joint two-day window, chosen per borrower as the one that hurts
  that mix most. Smaller loss, defensible loss.
- Non-standard collateral caps first flagged five borrowers. Three of
  them were A and BBB names holding 31% equity collateral against a
  25% cap I had set without thinking. Raised those caps to 35%. Two
  breaches remain, both hedge funds posting high yield, which is the
  story.
- Trailing 250-day standard deviation for the scaled haircut rather
  than an EWMA. A requirement that jumps every day is not something a
  client accepts. The cost is a lag at the onset of the stress, which
  is why two equity pairs sit amber. Left on screen on purpose.
- Found a 12.8% one-day gain in large caps outside the planted episode,
  a single Student t draw about ten standard deviations out. No real
  index has printed that. Shocks are now clipped at five sigma. The
  fire drill's worst window moved from that freak day to the rebound
  inside the planted crash, which is where it should have been.
- The LLM reply was cut off at 700 tokens once the facts block grew.
  Raised to 1000. The number check still passes on the first try.

## todo

- TODO: intraday marks, or at least a mid-day mark, for the exposure
  monitor
- TODO: cash collateral reinvestment book with its own liquidity and
  credit risk
- TODO: a stressed-volatility overlay on the scaled haircut so the
  amber pairs go green at the stress onset without an EWMA
- TODO: wrong-way risk: a borrower whose own credit is correlated with
  the collateral it posts
