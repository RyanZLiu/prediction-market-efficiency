# Prediction Market Efficiency Lab

A quantitative-research system for studying **cross-venue price discovery, probabilistic consistency, and executable-looking inefficiencies** in prediction markets.

The project ingests public market data from Kalshi and Polymarket, normalizes both venues into one schema, stores time-series snapshots in DuckDB, matches economically equivalent contracts, detects cross-venue discrepancies, evaluates logical probability constraints, repairs inconsistent probability vectors with constrained optimization, runs a paper backtest, and exposes the results in a Streamlit dashboard.

> **Important:** this repository is intentionally research/paper-trading only. It does not submit real-money orders.

## Architecture

```text
Kalshi REST / WS  ──┐
                    ├──> collectors ──> normalized models ──> DuckDB
Polymarket REST/WS ─┘                                      │
                                                           ├──> market matching
                                                           ├──> cross-venue signals
                                                           ├──> probability constraints
                                                           ├──> paper backtest
                                                           ├──> price-discovery research
                                                           └──> Streamlit dashboard
```

## Included functionality

- Public Kalshi market discovery and order-book/quote collection
- Public Polymarket Gamma market discovery and CLOB order books
- Polymarket public WebSocket market stream
- Optional authenticated Kalshi WebSocket ticker stream
- Common `Market`, `Quote`, `OrderBook`, `MarketMapping`, `Opportunity`, and `PaperTrade` models
- DuckDB schema and persistence layer
- Manual CSV mappings plus fuzzy cross-venue match suggestions
- Cross-venue executable-price discrepancy detection using best ask vs best bid
- Cost/slippage assumptions
- Probability constraints:
  - subset / implication
  - complement
  - mutually exclusive outcomes
  - exhaustive outcomes
  - monotone threshold chains
- CVXPY nearest coherent/arbitrage-free probability repair
- Paper-trading backtester with sizing and cooldown controls
- P&L, win rate, drawdown, per-trade Sharpe-like metric
- Lead/lag cross-correlation
- Convergence episode measurement
- Brier score, log loss, and reliability bins
- Streamlit dashboard
- CLI commands for database initialization, discovery, matching, collection, scanning, backtesting, and research
- Optional sportsbook ingestion from The Odds API with American-odds conversion and simple proportional de-vigging
- Synthetic offline demo dataset (`pme demo`)
- Unit tests for normalization, matching, cross-market signals, constraints, and metrics

## 1. Install

Recommended on macOS/Linux:

```bash
cd prediction-market-efficiency
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
cp .env.example .env
```

Or with `uv`:

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
```

## 2. Try the offline demo first

```bash
pme init-db
pme demo
pme backtest
streamlit run dashboard/app.py
```

This proves the local pipeline works before you connect live APIs.

## 3. Initialize the database

```bash
pme init-db
```

Default database: `data/pme.duckdb`.

## 4. Discover markets

```bash
pme discover kalshi --limit 300 --query "NBA"
pme discover polymarket --limit 300 --query "NBA"
```

With no query, the command stores the fetched open markets and prints a sample.

## 5. Generate match suggestions

```bash
pme suggest-matches --threshold 72 --limit 50
```

Review suggestions manually. High-quality research should not blindly trust fuzzy matching.

## 6. Create verified mappings

Edit `data/mappings/markets.csv` using the example rows. Each canonical event should have one Kalshi row and one Polymarket row referring to the same YES proposition.

```csv
canonical_event_id,canonical_outcome,venue,market_id,token_id,question,confidence,verified
nba_2026_example_bos_lal,BOS_WIN,kalshi,KXEXAMPLE,,Will Boston beat Los Angeles?,1.0,true
nba_2026_example_bos_lal,BOS_WIN,polymarket,12345,YES_TOKEN_ID,Will Boston beat Los Angeles?,1.0,true
```

Then import:

```bash
pme import-mappings data/mappings/markets.csv
```

## 7. Collect REST snapshots

One snapshot:

```bash
pme collect --once
```

Continuous polling every 60 seconds:

```bash
pme collect --interval 60
```

Collection is restricted to verified mappings by default, which keeps API usage and storage manageable.

## 8. Scan for cross-venue opportunities

```bash
pme scan --min-edge 0.005
```

The gross cross-venue edge is modeled as:

```text
buy YES on cheaper venue at YES ask
buy NO on expensive venue at (1 - YES bid)
locked gross edge = YES_bid_expensive - YES_ask_cheaper
```

This avoids pretending an unsupported naked short is available. The detector then subtracts configurable fee/slippage assumptions.

## 9. Probability constraints

Edit `data/constraints.example.yaml` or create your own file:

```yaml
variables:
  knicks_title: 0.31
  knicks_east: 0.29
  knicks_playoffs: 0.80
constraints:
  - kind: subset
    variables: [knicks_title, knicks_east]
  - kind: subset
    variables: [knicks_east, knicks_playoffs]
```

Evaluate and repair:

```bash
pme constraints data/constraints.example.yaml
```

The repair step solves a weighted least-squares problem subject to probability coherence constraints.

## 10. Backtest

First run `scan` repeatedly during collection to persist opportunities, then:

```bash
pme backtest --min-net-edge 0.005 --max-contracts 100 --cooldown 300
```

The backtester is deliberately conservative about duplicate snapshots by imposing a per-canonical-market cooldown.

## 11. Real-time WebSockets

### Polymarket

Public market-channel stream:

```bash
pme stream-polymarket --seconds 120
```

The command subscribes to token IDs from verified mappings and writes best bid/ask updates into DuckDB.

### Kalshi

Kalshi's WebSocket connection requires API credentials even for public-market channels. Put the API key ID and private-key path in `.env`, then:

```bash
pme stream-kalshi --seconds 120
```

No trading endpoint is implemented.

## 12. Dashboard

```bash
streamlit run dashboard/app.py
```

The dashboard shows:

- tracked markets and quote counts
- latest mapped quotes
- recent opportunities
- edge distribution
- cumulative paper P&L
- top discrepancies

## 13. Research commands

Cross-venue lead/lag for one canonical event:

```bash
pme lead-lag nba_2026_example_bos_lal --max-lag 10
```

Gap-convergence episodes:

```bash
pme convergence nba_2026_example_bos_lal --entry 0.03 --exit 0.01
```

## Repository layout

```text
src/pme/
  collectors/      API + WebSocket adapters
  database/        DuckDB persistence
  matching/        normalization + fuzzy match suggestions
  signals/         cross-market and probability-constraint logic
  backtest/        paper execution and metrics
  research/        lead/lag, convergence, calibration
  cli.py            Typer command line interface

dashboard/          Streamlit UI
notebooks/          lightweight research notebooks
tests/              unit tests
data/mappings/       manually verified mapping files
```

## Research principles

1. **Use executable prices.** Midpoints can manufacture fake arbitrage.
2. **Treat matching as a first-class problem.** Similar wording does not imply identical settlement rules.
3. **Do not hard-code current fee claims.** Fees can vary by venue/market; set cost assumptions explicitly.
4. **Separate data from strategy.** Collectors know APIs; signals only consume normalized data.
5. **Log failures and missing liquidity.** Data quality is part of the research result.
6. **Prefer falsifiable conclusions.** Discovering that most apparent edges vanish after spreads/costs is a useful result.

## Suggested project milestones

- V1: verified pair of equivalent contracts + REST snapshots
- V2: 20–50 mappings + cross-venue signal history
- V3: fuzzy match suggestions + manual review
- V4: probability-constraint engine + CVXPY repair
- V5: paper backtest + price-discovery analysis
- V6: WebSockets + dashboard + polished research write-up

## Tests

```bash
pytest -q
ruff check .
```

## Disclaimer

This is an educational/research project. Prediction-market access, fees, products, and legal availability vary by jurisdiction and can change. Verify venue rules and current documentation yourself before using market services.


## Optional sportsbook layer

Set `ODDS_API_KEY` in `.env`, then for NBA:

```bash
pme collect-odds --sport-key basketball_nba --regions us --markets h2h,spreads,totals
```

This stores individual bookmaker lines together with raw implied probabilities and a simple proportional no-vig probability within each returned bookmaker market. Sportsbook data is intentionally kept separate from prediction-market quotes until you create a canonical event/outcome mapping.

## API-change note

External APIs evolve. The adapters isolate venue-specific JSON parsing so endpoint or field changes should be fixed in `src/pme/collectors/` rather than throughout the research code. If an API response changes, inspect the raw response and update only the relevant adapter.
