SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS markets (
    venue VARCHAR NOT NULL,
    market_id VARCHAR NOT NULL,
    event_id VARCHAR,
    question VARCHAR NOT NULL,
    outcome VARCHAR NOT NULL,
    token_id VARCHAR,
    category VARCHAR,
    market_type VARCHAR,
    start_time TIMESTAMPTZ,
    close_time TIMESTAMPTZ,
    active BOOLEAN,
    volume DOUBLE,
    liquidity DOUBLE,
    metadata_json VARCHAR,
    updated_at TIMESTAMPTZ,
    PRIMARY KEY (venue, market_id, outcome)
);

CREATE TABLE IF NOT EXISTS quotes (
    timestamp TIMESTAMPTZ NOT NULL,
    venue VARCHAR NOT NULL,
    market_id VARCHAR NOT NULL,
    token_id VARCHAR,
    outcome VARCHAR NOT NULL,
    bid DOUBLE,
    ask DOUBLE,
    bid_size DOUBLE,
    ask_size DOUBLE,
    last DOUBLE,
    volume DOUBLE,
    raw_json VARCHAR
);

CREATE TABLE IF NOT EXISTS mappings (
    canonical_event_id VARCHAR NOT NULL,
    canonical_outcome VARCHAR NOT NULL,
    venue VARCHAR NOT NULL,
    market_id VARCHAR NOT NULL,
    token_id VARCHAR,
    question VARCHAR,
    confidence DOUBLE,
    verified BOOLEAN,
    PRIMARY KEY (canonical_event_id, canonical_outcome, venue, market_id)
);

CREATE TABLE IF NOT EXISTS opportunities (
    timestamp TIMESTAMPTZ NOT NULL,
    canonical_event_id VARCHAR NOT NULL,
    canonical_outcome VARCHAR NOT NULL,
    buy_venue VARCHAR NOT NULL,
    buy_market_id VARCHAR NOT NULL,
    buy_price DOUBLE,
    buy_size DOUBLE,
    hedge_venue VARCHAR NOT NULL,
    hedge_market_id VARCHAR NOT NULL,
    hedge_yes_bid DOUBLE,
    hedge_no_price DOUBLE,
    hedge_size DOUBLE,
    gross_edge DOUBLE,
    estimated_cost DOUBLE,
    net_edge DOUBLE,
    available_size DOUBLE,
    metadata_json VARCHAR
);

CREATE TABLE IF NOT EXISTS paper_trades (
    timestamp TIMESTAMPTZ NOT NULL,
    canonical_event_id VARCHAR NOT NULL,
    canonical_outcome VARCHAR NOT NULL,
    quantity DOUBLE,
    gross_edge DOUBLE,
    net_edge DOUBLE,
    locked_cost_per_contract DOUBLE,
    pnl DOUBLE,
    buy_venue VARCHAR,
    hedge_venue VARCHAR
);

CREATE TABLE IF NOT EXISTS sportsbook_quotes (
    timestamp TIMESTAMPTZ NOT NULL,
    sport_key VARCHAR,
    event_id VARCHAR,
    commence_time TIMESTAMPTZ,
    home_team VARCHAR,
    away_team VARCHAR,
    bookmaker VARCHAR,
    market_key VARCHAR,
    outcome VARCHAR,
    american_odds DOUBLE,
    point DOUBLE,
    implied_probability DOUBLE,
    fair_probability DOUBLE,
    metadata_json VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_sportsbook_event_time ON sportsbook_quotes (event_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_quotes_market_time ON quotes (venue, market_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_opp_event_time ON opportunities (canonical_event_id, timestamp);
"""
