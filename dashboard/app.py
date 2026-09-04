from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import plotly.express as px
import streamlit as st

from pme.config import settings
from pme.database import Database

st.set_page_config(page_title="Prediction Market Efficiency", layout="wide")
st.title("Prediction Market Efficiency Lab")
st.caption("Research/paper-trading dashboard — no real-money order execution")

try:
    db = Database(settings.db_path)
    db.init()
except Exception as exc:
    st.error(f"Could not open database: {exc}")
    st.stop()

counts = db.conn.execute("""
SELECT
  (SELECT count(*) FROM markets) AS markets,
  (SELECT count(*) FROM mappings WHERE verified=TRUE) AS mappings,
  (SELECT count(*) FROM quotes) AS quotes,
  (SELECT count(*) FROM opportunities) AS opportunities,
  (SELECT count(*) FROM paper_trades) AS paper_trades
""").fetchone()

cols = st.columns(5)
for col, label, value in zip(
    cols,
    ["Markets", "Verified mappings", "Quotes", "Opportunities", "Paper trades"],
    counts,
    strict=True,
):
    col.metric(label, f"{value:,}")

st.subheader("Latest mapped quotes")
latest = db.conn.execute("""
SELECT m.canonical_event_id, q.venue, q.market_id, q.timestamp, q.bid, q.ask,
       (q.bid + q.ask)/2.0 AS midpoint, q.ask-q.bid AS spread
FROM (
  SELECT *, row_number() OVER (PARTITION BY venue, market_id ORDER BY timestamp DESC) rn
  FROM quotes
) q
JOIN mappings m ON q.venue=m.venue AND q.market_id=m.market_id
WHERE q.rn=1 AND m.verified=TRUE
ORDER BY m.canonical_event_id, q.venue
""").df()
st.dataframe(latest, use_container_width=True, hide_index=True)

st.subheader("Recent cross-venue opportunities")
opps = db.conn.execute("""
SELECT timestamp, canonical_event_id, buy_venue, hedge_venue, gross_edge, estimated_cost, net_edge, available_size
FROM opportunities ORDER BY timestamp DESC LIMIT 500
""").df()
if opps.empty:
    st.info("No stored opportunities yet. Run `pme scan` after collecting mapped quotes.")
else:
    display = opps.copy()
    display["gross_edge_pct"] = 100 * display["gross_edge"]
    display["net_edge_pct"] = 100 * display["net_edge"]
    st.dataframe(display.head(100), use_container_width=True, hide_index=True)
    fig = px.histogram(display, x="net_edge_pct", nbins=40, title="Net edge distribution (%)")
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Paper backtest")
trades = db.conn.execute("""
SELECT timestamp, canonical_event_id, quantity, net_edge, pnl
FROM paper_trades ORDER BY timestamp
""").df()
if trades.empty:
    st.info("Run `pme backtest` after storing opportunities.")
else:
    trades["cumulative_pnl"] = trades["pnl"].cumsum()
    st.plotly_chart(
        px.line(trades, x="timestamp", y="cumulative_pnl", title="Cumulative simulated P&L"),
        use_container_width=True,
    )
    st.dataframe(trades.tail(100), use_container_width=True, hide_index=True)

db.close()


st.subheader("Optional sportsbook consensus")
try:
    sb = Database(settings.db_path)
    sportsbook = sb.conn.execute("""
    SELECT timestamp, sport_key, event_id, home_team, away_team, bookmaker, market_key, outcome,
           american_odds, implied_probability, fair_probability
    FROM sportsbook_quotes ORDER BY timestamp DESC LIMIT 1000
    """).df()
    sb.close()
    if sportsbook.empty:
        st.caption(
            "No sportsbook data stored. Optional: set ODDS_API_KEY and run `pme collect-odds`."
        )
    else:
        st.dataframe(sportsbook.head(200), use_container_width=True, hide_index=True)
except Exception:
    pass
