from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import plotly.express as px
import streamlit as st

from pme.config import settings
from pme.database import Database
from pme.live import LiveArbitrageTracker

st.set_page_config(
    page_title="Kalshi ↔ Polymarket US Arbitrage",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ Live Kalshi ↔ Polymarket US Arbitrage")
st.caption(
    "Automatic shared-market discovery with executable-book verification. "
    "Green alerts require fresh synchronized quotes, actual YES/NO books, "
    "reviewed resolution rules, venue fee formulas, and size on both legs. "
    "Research/paper-trading only."
)

try:
    bootstrap = Database(settings.db_path)
    bootstrap.init()
    bootstrap.close()
except Exception as exc:
    st.error(f"Could not initialize database: {exc}")
    st.stop()


@st.cache_resource
def get_live_tracker() -> LiveArbitrageTracker:
    tracker = LiveArbitrageTracker(db_path=settings.db_path)
    tracker.start()
    return tracker


tracker = get_live_tracker()

with st.sidebar:
    st.header("Live tracker")
    st.caption("The tracker starts automatically with this dashboard.")
    if st.button("Restart tracker", width="stretch"):
        tracker.restart()
        st.rerun()

    st.divider()
    st.write("**Executable alert threshold**")
    st.code(f"{100 * tracker.config.min_net_edge:.3f}% net edge / contract")
    st.write("**Verified-book freshness**")
    st.code(f"≤ {tracker.config.max_quote_age_seconds:.1f} sec each")
    st.write("**Verified-book max skew**")
    st.code(f"≤ {tracker.config.max_pair_skew_seconds:.1f} sec")
    st.write("**Book verification latency**")
    st.code(f"≤ {tracker.config.max_verification_latency_seconds:.1f} sec")
    st.write("**Market refresh**")
    st.code(f"every {tracker.config.discovery_interval_seconds // 60} min")

    if not settings.kalshi_api_key_id or not settings.kalshi_private_key_path:
        st.warning(
            "Kalshi WebSocket credentials are not configured, so the tracker uses "
            "REST polling. With a 2-second freshness gate, WebSockets are strongly "
            "preferred for executable alerts."
        )


def _open_db() -> Database:
    db = Database(settings.db_path)
    db.init()
    return db


def _format_age(timestamp: datetime | None) -> str:
    if timestamp is None:
        return "—"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    seconds = max(0, int((datetime.now(UTC) - timestamp).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def _age_seconds(timestamp: datetime | None) -> float | None:
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - timestamp).total_seconds())


@st.fragment(run_every="1s")
def live_panel() -> None:
    status = tracker.status()
    current_event_ids = tracker.current_event_ids()

    db = _open_db()
    try:
        alert_count = db.conn.execute(
            "SELECT count(*) FROM arbitrage_alerts WHERE direction IS NOT NULL"
        ).fetchone()[0]

        latest_alert = db.conn.execute(
            """
            SELECT timestamp, status, canonical_event_id, question, direction,
                   net_edge, net_profit, quantity
            FROM arbitrage_alerts
            WHERE direction IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 1
            """
        ).fetchone()

        active = db.conn.execute(
            """
            SELECT * FROM executable_live
            ORDER BY net_profit DESC
            """
        ).df()

        board = db.conn.execute(
            """
            WITH latest_quotes AS (
                SELECT * EXCLUDE (rn)
                FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY venue, market_id, outcome
                        ORDER BY timestamp DESC
                    ) AS rn
                    FROM quotes
                )
                WHERE rn=1
            ), paired AS (
                SELECT
                    m.canonical_event_id,
                    min(m.confidence) AS confidence,
                    max(CASE WHEN m.venue='kalshi' THEN m.market_id END) AS kalshi_id,
                    max(CASE WHEN m.venue='polymarket' THEN m.market_id END) AS polymarket_id,
                    max(CASE WHEN m.venue='kalshi' THEN m.question END) AS question,
                    max(CASE WHEN m.venue='kalshi' THEN q.timestamp END) AS k_timestamp,
                    max(CASE WHEN m.venue='polymarket' THEN q.timestamp END) AS p_timestamp,
                    max(CASE WHEN m.venue='kalshi' THEN q.bid END) AS k_bid,
                    max(CASE WHEN m.venue='kalshi' THEN q.ask END) AS k_ask,
                    max(CASE WHEN m.venue='polymarket' THEN q.bid END) AS p_bid,
                    max(CASE WHEN m.venue='polymarket' THEN q.ask END) AS p_ask
                FROM mappings m
                LEFT JOIN latest_quotes q
                  ON q.venue=m.venue AND q.market_id=m.market_id
                WHERE m.verified=TRUE
                GROUP BY m.canonical_event_id
                HAVING count(DISTINCT m.venue)=2
            )
            SELECT p.*,
                   coalesce(r.verified, FALSE) AS rules_verified
            FROM paired p
            LEFT JOIN rule_reviews r
              ON r.kalshi_market_id=p.kalshi_id
             AND r.polymarket_market_id=p.polymarket_id
            """
        ).df()
    finally:
        db.close()

    # The mappings table is persistent, so it can contain pairs from older runs.
    # The main board should show only the pairs this tracker is subscribed to now.
    if board.empty:
        historical_board = board.copy()
    else:
        current_mask = board["canonical_event_id"].isin(current_event_ids)
        historical_board = board.loc[~current_mask].copy()
        board = board.loc[current_mask].copy()

    running = bool(status["running"])
    last_error = status["last_error"]
    mode = str(status["mode"])
    phase = str(status["phase"])

    if last_error and phase in {"error", "recovering"}:
        state_label = "ERROR"
    elif running and mode == "websocket":
        state_label = "LIVE WS"
    elif running and mode == "polling":
        state_label = "LIVE POLL"
    elif running and phase == "discovering shared markets":
        state_label = "MATCHING"
    elif running and phase == "waiting for shared markets":
        state_label = "NO PAIRS"
    elif running:
        state_label = "STARTING"
    else:
        state_label = "STOPPED"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Tracker", state_label)
    c2.metric("Shared pairs", f"{status['mapped_pairs']:,}")
    c3.metric("Executable arbs", f"{len(active):,}")
    c4.metric("Alert transitions", f"{alert_count:,}")
    c5.metric("Last feed update", _format_age(status["last_quote"]))

    st.caption(
        f"Status: {phase} · discovered {status['kalshi_markets']:,} Kalshi + "
        f"{status['polymarket_markets']:,} Polymarket US markets · map refreshed "
        f"{_format_age(status['last_market_refresh'])}"
    )
    if last_error:
        st.warning(f"Tracker warning: {last_error}")

    if latest_alert is not None:
        latest_ts = latest_alert[0]
        previous = st.session_state.get("last_toast_alert_ts")
        if latest_alert[1] == "open" and (previous is None or latest_ts > previous):
            st.toast(
                f"⚡ VERIFIED ARBITRAGE SNAPSHOT: {latest_alert[3] or latest_alert[2]} · "
                f"{latest_alert[4]} · {100 * latest_alert[5]:.3f}% net/contract · "
                f"${latest_alert[6]:.2f} at {latest_alert[7]:.1f} contracts",
                icon="⚡",
            )
        st.session_state["last_toast_alert_ts"] = latest_ts

    st.subheader("Verified arbitrage alerts")
    if active.empty:
        st.info(
            "No fully verified executable arbitrage is open right now. A green alert "
            "only appears after all six execution gates pass."
        )
    else:
        for row in active.head(5).itertuples(index=False):
            st.success(
                f"⚡ **VERIFIED ARBITRAGE SNAPSHOT — {row.question or row.canonical_event_id}**  \n"
                f"**BUY {row.quantity:.1f} {str(row.leg1_venue).title()} {row.leg1_side} "
                f"@ ${row.leg1_price:.4f}**  \n"
                f"**BUY {row.quantity:.1f} {str(row.leg2_venue).title()} {row.leg2_side} "
                f"@ ${row.leg2_price:.4f}**  \n\n"
                f"Locked entry cost: **${row.total_cost:.2f}** · "
                f"settlement value: **${row.payout:.2f}** · fees: **${row.total_fee:.2f}**  \n"
                f"Net profit: **${row.net_profit:.2f}** · "
                f"net edge: **{100 * row.net_edge:.3f}%/contract** · "
                f"net ROI: **{100 * row.net_roi:.2f}%**  \n"
                f"Freshness: K {row.kalshi_quote_age:.2f}s · P {row.polymarket_quote_age:.2f}s · "
                f"skew {row.quote_skew:.2f}s · verify {row.verification_latency:.2f}s  \n"
                f"Checks: ✅ actual YES/NO books · ✅ rules reviewed · ✅ venue fees · ✅ size both legs"
            )

    st.subheader("Live Kalshi / Polymarket US market board")
    st.caption(
        "K→P/P→K are indicative gaps from the latest WebSocket market-data changes. "
        "The age columns mean *time since that venue last changed*, not stale-book age. "
        "A quiet market may show a large change age while the connection is healthy. "
        "Before a green alert, the tracker independently re-fetches both executable books "
        f"and requires those snapshots to be ≤ {tracker.config.max_quote_age_seconds:.1f}s "
        f"old and ≤ {tracker.config.max_pair_skew_seconds:.1f}s apart."
    )

    st.caption(
        f"Showing {len(board):,} pair(s) in the current tracker subscription set. "
        f"{len(historical_board):,} older stored pair(s) are hidden from the live board."
    )

    def render_historical_pairs() -> None:
        if historical_board.empty:
            return

        with st.expander(
            f"Historical / stale stored pairs ({len(historical_board):,})",
            expanded=False,
        ):
            st.caption(
                "These mappings or quotes were stored by earlier tracker runs. "
                "They are excluded from the live board and cannot be mistaken for "
                "the current subscription set."
            )

            historical_display = historical_board.copy()
            historical_display["Event"] = historical_display["question"].fillna(
                historical_display["canonical_event_id"]
            )
            historical_display["K"] = historical_display.apply(
                lambda row: (
                    "—"
                    if row.k_bid is None or row.k_ask is None
                    else f"{row.k_bid:.3f} / {row.k_ask:.3f}"
                ),
                axis=1,
            )
            historical_display["P"] = historical_display.apply(
                lambda row: (
                    "—"
                    if row.p_bid is None or row.p_ask is None
                    else f"{row.p_bid:.3f} / {row.p_ask:.3f}"
                ),
                axis=1,
            )
            historical_display["Match"] = 100 * historical_display["confidence"]
            historical_display["Rules"] = historical_display["rules_verified"].map(
                {True: "✅ VERIFIED", False: "⚠️ REVIEW"}
            )

            st.dataframe(
                historical_display[
                    [
                        "Event",
                        "K",
                        "P",
                        "k_timestamp",
                        "p_timestamp",
                        "Rules",
                        "Match",
                    ]
                ].rename(
                    columns={
                        "k_timestamp": "Last K quote",
                        "p_timestamp": "Last P quote",
                    }
                ),
                width="stretch",
                hide_index=True,
                column_config={
                    "Match": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )

    required = {"k_bid", "k_ask", "p_bid", "p_ask"}
    if board.empty or not required.issubset(board.columns):
        st.info("Waiting for current bid/ask data from the current live pairs.")
        render_historical_pairs()
        return

    board = board.dropna(subset=["k_bid", "k_ask", "p_bid", "p_ask"]).copy()
    if board.empty:
        st.info("Waiting for current bid/ask data from the current live pairs.")
        render_historical_pairs()
        return

    now = datetime.now(UTC)

    def feed_metrics(
        row: object,
    ) -> tuple[float | None, float | None, float | None, str]:
        k_ts = row.k_timestamp
        p_ts = row.p_timestamp
        if k_ts is None or p_ts is None:
            return None, None, None, "⚪ NO DATA"
        if k_ts.tzinfo is None:
            k_ts = k_ts.replace(tzinfo=UTC)
        if p_ts.tzinfo is None:
            p_ts = p_ts.replace(tzinfo=UTC)
        k_age = max(0.0, (now - k_ts).total_seconds())
        p_age = max(0.0, (now - p_ts).total_seconds())
        skew = abs((k_ts - p_ts).total_seconds())

        recent_k = k_age <= tracker.config.max_quote_age_seconds
        recent_p = p_age <= tracker.config.max_quote_age_seconds
        if recent_k and recent_p and skew <= tracker.config.max_pair_skew_seconds:
            label = "🟢 RECENT CHANGES"
        elif not recent_k and not recent_p:
            label = "⚪ QUIET"
        elif recent_k:
            label = "🟡 K CHANGED"
        else:
            label = "🟡 P CHANGED"
        return k_age, p_age, skew, label

    board["k_to_p_gross"] = board["p_bid"] - board["k_ask"]
    board["p_to_k_gross"] = board["k_bid"] - board["p_ask"]
    board["best_gap"] = board[["k_to_p_gross", "p_to_k_gross"]].max(axis=1)
    board = board.sort_values("best_gap", ascending=False)

    metrics = [
        feed_metrics(row)
        for row in board.itertuples(index=False)
    ]

    board["k_age_s"] = [
        metric[0]
        for metric in metrics
    ]

    board["p_age_s"] = [
        metric[1]
        for metric in metrics
    ]

    board["quote_skew_s"] = [
        metric[2]
        for metric in metrics
    ]

    board["feed_activity"] = [
        metric[3]
        for metric in metrics
    ]

    display = board.copy()
    display["Event"] = display["question"].fillna(display["canonical_event_id"])
    display["K"] = display.apply(lambda r: f"{r.k_bid:.3f} / {r.k_ask:.3f}", axis=1)
    display["P"] = display.apply(lambda r: f"{r.p_bid:.3f} / {r.p_ask:.3f}", axis=1)
    display["K→P gross"] = 100 * display["k_to_p_gross"]
    display["P→K gross"] = 100 * display["p_to_k_gross"]
    display["K change age (s)"] = display["k_age_s"]
    display["P change age (s)"] = display["p_age_s"]
    display["Change skew (s)"] = display["quote_skew_s"]
    display["Feed activity"] = display["quote_gate"]
    display["Rules"] = display["rules_verified"].map(
        {
            True: "✅ VERIFIED",
            False: "⚠️ REVIEW",
        }
    )
    display["Match"] = 100 * display["confidence"]

    st.dataframe(
        display[
            [
                "Event",
                "K",
                "P",
                "K→P gross",
                "P→K gross",
                "K change age (s)",
                "P change age (s)",
                "Change skew (s)",
                "Feed activity",
                "Rules",
                "Match",
            ]
        ],
        width="stretch",
        hide_index=True,
        column_config={
            "K→P gross": st.column_config.NumberColumn(
                format="%+.3f%%"
            ),
            "P→K gross": st.column_config.NumberColumn(
                format="%+.3f%%"
            ),
            "K change age (s)": st.column_config.NumberColumn(
                format="%.2f",
                help="Seconds since Kalshi last emitted a market-data change. Informational only.",
            ),
            "P change age (s)": st.column_config.NumberColumn(
                format="%.2f",
                help="Seconds since Polymarket US last emitted a market-data change. Informational only.",
            ),
            "Change skew (s)": st.column_config.NumberColumn(
                format="%.2f",
                help=(
                    "Absolute timestamp difference between "
                    "the Kalshi and Polymarket US quotes."
                ),
            ),
            "Feed activity": st.column_config.TextColumn(
                help=(
                    "PASS requires both quote ages and their "
                    "timestamp skew to satisfy the sidebar limits."
                ),
            ),
            "Rules": st.column_config.TextColumn(
                help=(
                    "REVIEW means the settlement rules have "
                    "not been manually verified yet."
                ),
            ),
            "Match": st.column_config.NumberColumn(
                format="%.1f%%"
            ),
        },
    )

    top = board.head(15).copy()
    top["Event"] = top["question"].fillna(top["canonical_event_id"])
    chart = top[["Event", "k_to_p_gross", "p_to_k_gross"]].rename(
        columns={"k_to_p_gross": "K→P", "p_to_k_gross": "P→K"}
    )
    chart = chart.melt(id_vars="Event", var_name="Direction", value_name="Gross gap")
    chart["Gross gap (%)"] = 100 * chart["Gross gap"]
    fig = px.bar(
        chart,
        x="Event",
        y="Gross gap (%)",
        color="Direction",
        barmode="group",
        title="Top indicative cross-venue gaps",
    )
    fig.add_hline(y=0, line_dash="dash")
    fig.update_layout(xaxis_title=None, legend_title=None)
    st.plotly_chart(fig, width="stretch")

    render_historical_pairs()


live_panel()

st.divider()

history_tab, mapping_tab, rules_tab, research_tab = st.tabs(
    ["Alert history", "Shared-market map", "Rule verification", "Research"]
)

with history_tab:
    db = _open_db()
    try:
        history = db.conn.execute(
            """
            SELECT timestamp, status, question, direction, leg1_side, leg1_price,
                   leg2_side, leg2_price, quantity, net_profit, net_edge, net_roi,
                   kalshi_quote_age, polymarket_quote_age, quote_skew,
                   verification_latency, match_confidence
            FROM arbitrage_alerts
            WHERE direction IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 500
            """
        ).df()
    finally:
        db.close()
    if history.empty:
        st.info("No executable arbitrage transitions have been recorded yet.")
    else:
        history["net_edge_pct"] = 100 * history["net_edge"]
        history["net_roi_pct"] = 100 * history["net_roi"]
        st.dataframe(history, width="stretch", hide_index=True)

with mapping_tab:
    db = _open_db()
    try:
        mappings = db.conn.execute(
            """
            SELECT canonical_event_id,
                   min(confidence) AS confidence,
                   max(CASE WHEN venue='kalshi' THEN market_id END) AS kalshi_id,
                   max(CASE WHEN venue='kalshi' THEN question END) AS kalshi_question,
                   max(CASE WHEN venue='polymarket' THEN market_id END) AS polymarket_id,
                   max(CASE WHEN venue='polymarket' THEN question END) AS polymarket_question
            FROM mappings
            WHERE verified=TRUE
            GROUP BY canonical_event_id
            HAVING count(DISTINCT venue)=2
            ORDER BY confidence DESC
            """
        ).df()
    finally:
        db.close()
    st.caption("Automatically matched pairs. Rule verification is a separate safety gate.")
    st.dataframe(mappings, width="stretch", hide_index=True)

with rules_tab:
    db = _open_db()
    try:
        pairs = db.conn.execute(
            """
            WITH paired AS (
                SELECT canonical_event_id,
                       min(confidence) AS confidence,
                       max(CASE WHEN venue='kalshi' THEN market_id END) AS kalshi_id,
                       max(CASE WHEN venue='kalshi' THEN question END) AS kalshi_question,
                       max(CASE WHEN venue='polymarket' THEN market_id END) AS polymarket_id,
                       max(CASE WHEN venue='polymarket' THEN question END) AS polymarket_question
                FROM mappings
                WHERE verified=TRUE
                GROUP BY canonical_event_id
                HAVING count(DISTINCT venue)=2
            )
            SELECT p.*,
                   km.metadata_json AS kalshi_metadata,
                   pm.metadata_json AS polymarket_metadata,
                   coalesce(r.verified, FALSE) AS rules_verified,
                   r.reviewed_at,
                   r.notes
            FROM paired p
            LEFT JOIN markets km ON km.venue='kalshi' AND km.market_id=p.kalshi_id
            LEFT JOIN markets pm ON pm.venue='polymarket' AND pm.market_id=p.polymarket_id
            LEFT JOIN rule_reviews r
              ON r.kalshi_market_id=p.kalshi_id
             AND r.polymarket_market_id=p.polymarket_id
            ORDER BY p.confidence DESC
            """
        ).df()
    finally:
        db.close()

    st.warning(
        "Automatic text matching is not enough for an executable alert. Review the two "
        "contracts and only mark a pair verified when they resolve to the same proposition, "
        "with compatible timing, settlement criteria, and data source/rules."
    )
    if pairs.empty:
        st.info("No shared pairs are available for review yet.")
    else:
        labels = {
            row.canonical_event_id: (
                f"{row.kalshi_question[:70]}  |  match {100 * row.confidence:.1f}%"
            )
            for row in pairs.itertuples(index=False)
        }
        selected = st.selectbox(
            "Pair to review",
            list(labels),
            format_func=lambda value: labels[value],
        )
        row = pairs[pairs["canonical_event_id"] == selected].iloc[0]
        st.write(f"**Kalshi:** {row['kalshi_question']}")
        st.write(f"**Polymarket:** {row['polymarket_question']}")
        st.write(f"**Match score:** {100 * row['confidence']:.1f}%")

        def parse_meta(value: object) -> dict[str, object]:
            try:
                parsed = json.loads(value or "{}")
                return parsed if isinstance(parsed, dict) else {}
            except (TypeError, json.JSONDecodeError):
                return {}

        km = parse_meta(row["kalshi_metadata"])
        pm = parse_meta(row["polymarket_metadata"])
        k_rules = str(km.get("rules_primary") or km.get("rules") or "No rules text in market payload.")
        k_secondary = str(km.get("rules_secondary") or "")
        p_rules = str(pm.get("description") or pm.get("rules") or "No description/rules text in market payload.")
        p_source = str(pm.get("resolutionSource") or pm.get("resolution_source") or "")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### Kalshi resolution")
            st.text_area("Kalshi primary rules", k_rules, height=220, disabled=True)
            if k_secondary:
                st.text_area("Kalshi secondary rules", k_secondary, height=120, disabled=True)
        with c2:
            st.markdown("#### Polymarket resolution")
            st.text_area("Polymarket description/rules", p_rules, height=220, disabled=True)
            if p_source:
                st.text_input("Polymarket resolution source", p_source, disabled=True)

        notes = st.text_input(
            "Review notes",
            value=str(row["notes"] or ""),
            placeholder="e.g. Same player, map, event, settlement condition, and timing",
        )
        verified = bool(row["rules_verified"])
        if verified:
            st.success(f"Rules verified at {row['reviewed_at']}")
        else:
            st.error("Rules NOT verified — this pair cannot create a green executable alert.")

        b1, b2 = st.columns(2)
        if b1.button("✅ Mark rules verified", width="stretch"):
            db = _open_db()
            try:
                db.conn.execute(
                    """
                    INSERT OR REPLACE INTO rule_reviews VALUES (?, ?, TRUE, ?, ?)
                    """,
                    [row["kalshi_id"], row["polymarket_id"], datetime.now(UTC), notes],
                )
            finally:
                db.close()
            st.rerun()
        if b2.button("Revoke verification", width="stretch"):
            db = _open_db()
            try:
                db.conn.execute(
                    """
                    INSERT OR REPLACE INTO rule_reviews VALUES (?, ?, FALSE, ?, ?)
                    """,
                    [row["kalshi_id"], row["polymarket_id"], datetime.now(UTC), notes],
                )
            finally:
                db.close()
            st.rerun()

with research_tab:
    db = _open_db()
    try:
        trades = db.conn.execute(
            "SELECT timestamp, canonical_event_id, quantity, net_edge, pnl FROM paper_trades ORDER BY timestamp"
        ).df()
        sportsbook = db.conn.execute(
            """
            SELECT timestamp, sport_key, event_id, home_team, away_team, bookmaker,
                   market_key, outcome, american_odds, implied_probability, fair_probability
            FROM sportsbook_quotes ORDER BY timestamp DESC LIMIT 500
            """
        ).df()
    finally:
        db.close()

    st.subheader("Paper backtest")
    if trades.empty:
        st.info("Run `pme backtest` after opportunities have been stored.")
    else:
        trades["cumulative_pnl"] = trades["pnl"].cumsum()
        st.plotly_chart(
            px.line(trades, x="timestamp", y="cumulative_pnl", title="Cumulative simulated P&L"),
            width="stretch",
        )
        st.dataframe(trades.tail(100), width="stretch", hide_index=True)

    st.subheader("Optional sportsbook consensus")
    if sportsbook.empty:
        st.caption("No sportsbook data stored. Optional: set ODDS_API_KEY and run `pme collect-odds`.")
    else:
        st.dataframe(sportsbook, width="stretch", hide_index=True)
