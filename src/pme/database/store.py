from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from pme.config import settings
from pme.database.schema import SCHEMA_SQL
from pme.models import Market, MarketMapping, Opportunity, PaperTrade, Quote, SportsbookQuote, Venue


class Database:
    def __init__(self, path: str | Path | None = None):
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("DuckDB is required. Install project dependencies first.") from exc
        self.path = Path(path or settings.db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))

    def close(self) -> None:
        self.conn.close()

    def init(self) -> None:
        self.conn.execute(SCHEMA_SQL)

    def upsert_markets(self, markets: Iterable[Market]) -> int:
        rows = list(markets)
        for m in rows:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO markets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    m.venue.value,
                    m.market_id,
                    m.event_id,
                    m.question,
                    m.outcome,
                    m.token_id,
                    m.category,
                    m.market_type.value,
                    m.start_time,
                    m.close_time,
                    m.active,
                    m.volume,
                    m.liquidity,
                    json.dumps(m.metadata, default=str),
                    m.updated_at,
                ],
            )
        return len(rows)

    def insert_quotes(self, quotes: Iterable[Quote]) -> int:
        rows = list(quotes)
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    q.timestamp,
                    q.venue.value,
                    q.market_id,
                    q.token_id,
                    q.outcome,
                    q.bid,
                    q.ask,
                    q.bid_size,
                    q.ask_size,
                    q.last,
                    q.volume,
                    json.dumps(q.raw, default=str),
                ]
                for q in rows
            ],
        )
        return len(rows)

    def insert_sportsbook_quotes(self, quotes: Iterable[SportsbookQuote]) -> int:
        rows = list(quotes)
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO sportsbook_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    q.timestamp,
                    q.sport_key,
                    q.event_id,
                    q.commence_time,
                    q.home_team,
                    q.away_team,
                    q.bookmaker,
                    q.market_key,
                    q.outcome,
                    q.american_odds,
                    q.point,
                    q.implied_probability,
                    q.fair_probability,
                    json.dumps(q.metadata, default=str),
                ]
                for q in rows
            ],
        )
        return len(rows)

    def upsert_mappings(self, mappings: Iterable[MarketMapping]) -> int:
        rows = list(mappings)
        for m in rows:
            self.conn.execute(
                "INSERT OR REPLACE INTO mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    m.canonical_event_id,
                    m.canonical_outcome,
                    m.venue.value,
                    m.market_id,
                    m.token_id,
                    m.question,
                    m.confidence,
                    m.verified,
                ],
            )
        return len(rows)

    def insert_opportunities(self, opportunities: Iterable[Opportunity]) -> int:
        rows = list(opportunities)
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO opportunities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    o.timestamp,
                    o.canonical_event_id,
                    o.canonical_outcome,
                    o.buy_venue.value,
                    o.buy_market_id,
                    o.buy_price,
                    o.buy_size,
                    o.hedge_venue.value,
                    o.hedge_market_id,
                    o.hedge_yes_bid,
                    o.hedge_no_price,
                    o.hedge_size,
                    o.gross_edge,
                    o.estimated_cost,
                    o.net_edge,
                    o.available_size,
                    json.dumps(o.metadata, default=str),
                ]
                for o in rows
            ],
        )
        return len(rows)

    def insert_paper_trades(self, trades: Iterable[PaperTrade]) -> int:
        rows = list(trades)
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    t.timestamp,
                    t.canonical_event_id,
                    t.canonical_outcome,
                    t.quantity,
                    t.gross_edge,
                    t.net_edge,
                    t.locked_cost_per_contract,
                    t.pnl,
                    t.buy_venue.value,
                    t.hedge_venue.value,
                ]
                for t in rows
            ],
        )
        return len(rows)

    def mappings(self, verified_only: bool = True) -> list[MarketMapping]:
        sql = "SELECT canonical_event_id, canonical_outcome, venue, market_id, token_id, question, confidence, verified FROM mappings"
        if verified_only:
            sql += " WHERE verified = TRUE"
        rows = self.conn.execute(sql).fetchall()
        return [
            MarketMapping(
                canonical_event_id=r[0],
                canonical_outcome=r[1],
                venue=Venue(r[2]),
                market_id=r[3],
                token_id=r[4],
                question=r[5],
                confidence=r[6],
                verified=r[7],
            )
            for r in rows
        ]

    def markets(self, venue: Venue | None = None) -> list[Market]:
        sql = "SELECT venue, market_id, event_id, question, outcome, token_id, category, market_type, start_time, close_time, active, volume, liquidity, metadata_json, updated_at FROM markets"
        params: list[object] = []
        if venue:
            sql += " WHERE venue = ?"
            params.append(venue.value)
        rows = self.conn.execute(sql, params).fetchall()
        return [
            Market(
                venue=Venue(r[0]),
                market_id=r[1],
                event_id=r[2],
                question=r[3],
                outcome=r[4],
                token_id=r[5],
                category=r[6],
                market_type=r[7],
                start_time=r[8],
                close_time=r[9],
                active=r[10],
                volume=r[11],
                liquidity=r[12],
                metadata=json.loads(r[13] or "{}"),
                updated_at=r[14],
            )
            for r in rows
        ]

    def latest_quotes(self) -> list[Quote]:
        rows = self.conn.execute(
            """
            SELECT timestamp, venue, market_id, token_id, outcome, bid, ask, bid_size, ask_size, last, volume, raw_json
            FROM (
                SELECT *, row_number() OVER (PARTITION BY venue, market_id, outcome ORDER BY timestamp DESC) AS rn
                FROM quotes
            ) q WHERE rn = 1
            """
        ).fetchall()
        return [
            Quote(
                timestamp=r[0],
                venue=Venue(r[1]),
                market_id=r[2],
                token_id=r[3],
                outcome=r[4],
                bid=r[5],
                ask=r[6],
                bid_size=r[7],
                ask_size=r[8],
                last=r[9],
                volume=r[10],
                raw=json.loads(r[11] or "{}"),
            )
            for r in rows
        ]

    def opportunities(self, limit: int | None = None) -> list[Opportunity]:
        sql = "SELECT timestamp, canonical_event_id, canonical_outcome, buy_venue, buy_market_id, buy_price, buy_size, hedge_venue, hedge_market_id, hedge_yes_bid, hedge_no_price, hedge_size, gross_edge, estimated_cost, net_edge, available_size, metadata_json FROM opportunities ORDER BY timestamp"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self.conn.execute(sql).fetchall()
        return [
            Opportunity(
                timestamp=r[0],
                canonical_event_id=r[1],
                canonical_outcome=r[2],
                buy_venue=Venue(r[3]),
                buy_market_id=r[4],
                buy_price=r[5],
                buy_size=r[6],
                hedge_venue=Venue(r[7]),
                hedge_market_id=r[8],
                hedge_yes_bid=r[9],
                hedge_no_price=r[10],
                hedge_size=r[11],
                gross_edge=r[12],
                estimated_cost=r[13],
                net_edge=r[14],
                available_size=r[15],
                metadata=json.loads(r[16] or "{}"),
            )
            for r in rows
        ]

    def quote_series(self, canonical_event_id: str) -> list[tuple]:
        return self.conn.execute(
            """
            SELECT q.timestamp, q.venue, q.bid, q.ask, (q.bid + q.ask) / 2.0 AS mid
            FROM quotes q
            JOIN mappings m ON q.venue=m.venue AND q.market_id=m.market_id
            WHERE m.canonical_event_id=? AND m.verified=TRUE AND q.bid IS NOT NULL AND q.ask IS NOT NULL
            ORDER BY q.timestamp
            """,
            [canonical_event_id],
        ).fetchall()
