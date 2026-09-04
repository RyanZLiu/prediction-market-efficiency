from __future__ import annotations

import asyncio
import csv
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from pme.backtest import BacktestConfig, PaperBacktester, summarize_trades
from pme.collectors import KalshiClient, OddsApiClient, PolymarketClient
from pme.collectors.websockets import stream_kalshi, stream_polymarket
from pme.config import settings
from pme.database import Database
from pme.matching.auto import auto_match_markets
from pme.matching.mappings import load_mapping_csv
from pme.matching.matcher import suggest_matches
from pme.matching.normalize import canonical_slug
from pme.models import (
    ConstraintKind,
    MarketMapping,
    ProbabilityConstraint,
    Venue,
)
from pme.research.price_discovery import convergence_episodes, lead_lag_correlation
from pme.signals.constraints import evaluate_constraints, repair_prices
from pme.signals.cross_market import CostModel, detect_cross_venue

app = typer.Typer(no_args_is_help=True, help="Prediction Market Efficiency Lab CLI")
console = Console()


def _db() -> Database:
    db = Database()
    db.init()
    return db


@app.command("init-db")
def init_db() -> None:
    db = _db()
    db.close()
    console.print(f"Initialized [bold]{settings.db_path}[/bold]")


@app.command()
def discover(
    venue: Annotated[Venue, typer.Argument()],
    limit: int = typer.Option(200, min=1, max=5000),
    query: str = typer.Option("", help="Optional case-insensitive question/category filter."),
) -> None:
    async def run():
        if venue == Venue.KALSHI:
            async with KalshiClient() as client:
                return await client.list_markets(limit)
        if venue == Venue.POLYMARKET:
            async with PolymarketClient() as client:
                return await client.list_markets(limit)
        raise typer.BadParameter("Only kalshi and polymarket discovery are implemented.")

    markets = asyncio.run(run())
    if query:
        q = query.lower()
        markets = [m for m in markets if q in m.question.lower() or q in (m.category or "").lower()]
    db = _db()
    db.upsert_markets(markets)
    db.close()
    table = Table("Venue", "Market ID", "Token", "Question")
    for m in markets[:25]:
        table.add_row(m.venue.value, m.market_id, (m.token_id or "")[:18], m.question[:80])
    console.print(table)
    console.print(f"Stored {len(markets)} markets")

@app.command("discover-polymarket-sports")
def discover_polymarket_sports(
    limit: int = typer.Option(2000, min=1, max=5000),
) -> None:
    async def run():
        async with PolymarketClient() as client:
            return await client.list_sports_markets(limit)

    markets = asyncio.run(run())

    db = _db()
    db.upsert_markets(markets)
    db.close()

    table = Table(
        "Market ID",
        "Type",
        "Game ID",
        "Question",
    )

    for market in markets[:30]:
        metadata = market.metadata

        table.add_row(
            market.market_id,
            str(metadata.get("sportsMarketType") or ""),
            str(metadata.get("gameId") or ""),
            market.question[:80],
        )

    console.print(table)
    console.print(f"Stored {len(markets)} Polymarket sports markets")


@app.command("suggest-matches")
def suggest_matches_cmd(
    threshold: float = typer.Option(72.0),
    limit: int = typer.Option(50),
) -> None:
    db = _db()
    markets = db.markets()
    db.close()
    suggestions = suggest_matches(markets, threshold=threshold, limit=limit)
    table = Table("Score", "Kalshi", "Polymarket", "Kalshi question", "Polymarket question")
    for s in suggestions:
        table.add_row(
            f"{s.score:.1f}",
            s.kalshi_market_id,
            s.polymarket_market_id,
            s.kalshi_question[:55],
            s.polymarket_question[:55],
        )
    console.print(table)


@app.command("export-suggestions")
def export_suggestions(
    path: Path = typer.Argument(Path("data/mappings/suggestions.csv")),
    threshold: float = typer.Option(72.0),
    limit: int = typer.Option(100),
) -> None:
    db = _db()
    markets = db.markets()
    db.close()
    suggestions = suggest_matches(markets, threshold=threshold, limit=limit)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "score",
                "kalshi_market_id",
                "polymarket_market_id",
                "polymarket_token_id",
                "kalshi_question",
                "polymarket_question",
            ]
        )
        for s in suggestions:
            writer.writerow(
                [
                    s.score,
                    s.kalshi_market_id,
                    s.polymarket_market_id,
                    s.polymarket_token_id,
                    s.kalshi_question,
                    s.polymarket_question,
                ]
            )
    console.print(f"Wrote {len(suggestions)} suggestions to {path}")


@app.command("import-mappings")
def import_mappings(path: Path) -> None:
    mappings = load_mapping_csv(path)
    db = _db()
    count = db.upsert_mappings(mappings)
    db.close()
    console.print(f"Imported {count} mappings")

@app.command("auto-map")
def auto_map(
    min_score: float = typer.Option(
        93.0,
        help="Minimum overall match score.",
    ),
    min_text_score: float = typer.Option(
        90.0,
        help="Minimum fuzzy text similarity.",
    ),
    min_date_score: float = typer.Option(
        80.0,
        help="Minimum date similarity score.",
    ),
    min_margin: float = typer.Option(
        5.0,
        help="Required lead over the second-best candidate.",
    ),
    max_spread: float = typer.Option(
        0.15,
        help="Maximum allowed bid/ask spread on either venue.",
    ),
    min_size: float = typer.Option(
        1.0,
        help="Minimum known top-of-book size.",
    ),
    refresh: bool = typer.Option(
        True,
        "--refresh/--no-refresh",
        help="Fetch fresh markets from both exchanges first.",
    ),
    kalshi_limit: int = typer.Option(
        2000,
        min=1,
        max=5000,
    ),
    polymarket_limit: int = typer.Option(
        2000,
        min=1,
        max=2000,
        help=(
            "Keep at 2000 or below until "
            "Polymarket pagination is upgraded."
        ),
    ),
) -> None:
    """
    Automatically discover, filter, quality-check,
    and store high-confidence cross-venue mappings.
    """

    db = _db()

    if refresh:

        async def discover_both():
            async with (
                KalshiClient() as kalshi,
                PolymarketClient() as poly,
            ):
                return await asyncio.gather(
                    kalshi.list_markets(kalshi_limit),
                    poly.list_markets(polymarket_limit),
                )

        console.print(
            "[bold]Discovering fresh markets...[/bold]"
        )

        kalshi_markets, poly_markets = asyncio.run(
            discover_both()
        )

        db.upsert_markets(
            kalshi_markets + poly_markets
        )

        console.print(
            f"Discovered {len(kalshi_markets)} Kalshi "
            f"and {len(poly_markets)} Polymarket markets"
        )

    markets = db.markets()

    suggestions = auto_match_markets(
        markets,
        min_score=min_score,
        min_text_score=min_text_score,
        min_date_score=min_date_score,
        min_margin=min_margin,
    )

    market_lookup = {
        (market.venue, market.market_id): market
        for market in markets
    }

    async def quality_check():
        accepted = []

        async with (
            KalshiClient() as kalshi,
            PolymarketClient() as poly,
        ):
            semaphore = asyncio.Semaphore(12)

            async def check_one(suggestion):
                async with semaphore:
                    k_market = market_lookup.get(
                        (
                            Venue.KALSHI,
                            suggestion.kalshi_market_id,
                        )
                    )

                    p_market = market_lookup.get(
                        (
                            Venue.POLYMARKET,
                            suggestion.polymarket_market_id,
                        )
                    )

                    if k_market is None or p_market is None:
                        return None

                    if not p_market.token_id:
                        return None

                    try:
                        k_quote, p_quote = await asyncio.gather(
                            kalshi.get_quote(
                                k_market.market_id
                            ),
                            poly.get_quote(
                                p_market.market_id,
                                p_market.token_id,
                            ),
                        )
                    except Exception as exc:
                        console.print(
                            "[yellow]"
                            "quality check warning"
                            "[/yellow] "
                            f"{k_market.market_id}: {exc}"
                        )
                        return None

                    if (
                        k_quote.bid is None
                        or k_quote.ask is None
                        or p_quote.bid is None
                        or p_quote.ask is None
                    ):
                        return None

                    k_spread = (
                        k_quote.ask - k_quote.bid
                    )

                    p_spread = (
                        p_quote.ask - p_quote.bid
                    )

                    if (
                        k_spread > max_spread
                        or p_spread > max_spread
                    ):
                        return None

                    known_sizes = [
                        size
                        for size in (
                            k_quote.bid_size,
                            k_quote.ask_size,
                            p_quote.bid_size,
                            p_quote.ask_size,
                        )
                        if size is not None
                    ]

                    if (
                        known_sizes
                        and min(known_sizes) < min_size
                    ):
                        return None

                    return (
                        suggestion,
                        k_market,
                        p_market,
                        k_quote,
                        p_quote,
                    )

            results = await asyncio.gather(
                *(
                    check_one(suggestion)
                    for suggestion in suggestions
                )
            )

            accepted.extend(
                result
                for result in results
                if result is not None
            )

        return accepted

    console.print(
        f"High-confidence semantic candidates: "
        f"{len(suggestions)}"
    )

    accepted = asyncio.run(quality_check())

    mappings = []
    quotes = []

    table = Table(
        "Score",
        "Event",
        "Kalshi",
        "Polymarket",
        "K spread",
        "P spread",
    )

    for (
        suggestion,
        k_market,
        p_market,
        k_quote,
        p_quote,
    ) in accepted:

        event_id = (
            "auto_"
            + canonical_slug(k_market.market_id)
        )

        confidence = suggestion.score / 100.0

        mappings.extend(
            [
                MarketMapping(
                    canonical_event_id=event_id,
                    canonical_outcome="YES",
                    venue=Venue.KALSHI,
                    market_id=k_market.market_id,
                    question=k_market.question,
                    confidence=confidence,
                    verified=True,
                ),
                MarketMapping(
                    canonical_event_id=event_id,
                    canonical_outcome="YES",
                    venue=Venue.POLYMARKET,
                    market_id=p_market.market_id,
                    token_id=p_market.token_id,
                    question=p_market.question,
                    confidence=confidence,
                    verified=True,
                ),
            ]
        )

        quotes.extend(
            [
                k_quote,
                p_quote,
            ]
        )

        table.add_row(
            f"{suggestion.score:.1f}",
            event_id,
            k_market.question[:40],
            p_market.question[:40],
            f"{k_quote.spread:.3f}",
            f"{p_quote.spread:.3f}",
        )

    db.upsert_mappings(mappings)
    db.insert_quotes(quotes)

    console.print(table)

    console.print(
        f"[bold green]"
        f"Stored {len(accepted)} verified market pairs"
        f"[/bold green]"
    )

    db.close()

async def _snapshot_once() -> int:
    db = _db()
    mappings = db.mappings(verified_only=True)
    quotes = []
    async with KalshiClient() as kalshi, PolymarketClient() as poly:
        semaphore = asyncio.Semaphore(12)

        async def fetch(m):
            async with semaphore:
                try:
                    if m.venue == Venue.KALSHI:
                        return await kalshi.get_quote(m.market_id)
                    if m.venue == Venue.POLYMARKET:
                        return await poly.get_quote(m.market_id, m.token_id)
                except (
                    Exception
                ) as exc:  # one bad market should not kill an entire collection cycle
                    console.print(
                        f"[yellow]collect warning[/yellow] {m.venue}:{m.market_id}: {exc}"
                    )
                return None

        results = await asyncio.gather(*(fetch(m) for m in mappings))
        quotes.extend(q for q in results if q is not None)
    count = db.insert_quotes(quotes)
    db.close()
    return count


@app.command()
def collect(
    once: bool = typer.Option(False, help="Collect one snapshot and exit."),
    interval: int = typer.Option(60, min=5),
) -> None:
    async def run() -> None:
        while True:
            count = await _snapshot_once()
            console.print(f"Collected {count} quotes")
            if once:
                return
            await asyncio.sleep(interval)

    asyncio.run(run())


@app.command()
def scan(
    min_edge: float = typer.Option(0.0, help="Minimum net edge per contract."),
    max_pair_skew: float = typer.Option(
        90.0, help="Reject venue quotes farther apart than this many seconds."
    ),
    max_quote_age: float = typer.Option(
        180.0, help="Reject latest quotes older than this many seconds."
    ),
) -> None:
    db = _db()
    quotes = db.latest_quotes()
    mappings = db.mappings(verified_only=True)
    cost = CostModel(
        fee_bps={
            Venue.KALSHI: settings.kalshi_fee_bps,
            Venue.POLYMARKET: settings.polymarket_fee_bps,
        },
        slippage_bps=settings.slippage_bps,
    )
    opportunities = detect_cross_venue(
        quotes,
        mappings,
        cost_model=cost,
        min_net_edge=min_edge,
        max_pair_skew_seconds=max_pair_skew,
        max_quote_age_seconds=max_quote_age,
    )
    db.insert_opportunities(opportunities)
    db.close()
    table = Table("Event", "Buy", "Hedge", "Gross", "Cost", "Net", "Size")
    for o in opportunities[:30]:
        table.add_row(
            o.canonical_event_id,
            o.buy_venue.value,
            o.hedge_venue.value,
            f"{100 * o.gross_edge:.2f}%",
            f"{100 * o.estimated_cost:.2f}%",
            f"{100 * o.net_edge:.2f}%",
            "?" if o.available_size is None else f"{o.available_size:.1f}",
        )
    console.print(table)
    console.print(f"Stored {len(opportunities)} opportunities")


@app.command()
def backtest(
    min_net_edge: float = typer.Option(0.005),
    max_contracts: float = typer.Option(100.0),
    cooldown: int = typer.Option(300),
) -> None:
    db = _db()
    opportunities = db.opportunities()
    engine = PaperBacktester(
        BacktestConfig(
            min_net_edge=min_net_edge, max_contracts=max_contracts, cooldown_seconds=cooldown
        )
    )
    trades = engine.run(opportunities)
    db.conn.execute("DELETE FROM paper_trades")
    db.insert_paper_trades(trades)
    db.close()
    stats = summarize_trades(trades)
    for key, value in stats.items():
        console.print(f"{key:>18}: {value:.6f}" if key != "trades" else f"{key:>18}: {int(value)}")


@app.command("collect-odds")
def collect_odds(
    sport_key: str = typer.Option("basketball_nba"),
    regions: str = typer.Option("us"),
    markets: str = typer.Option("h2h,spreads,totals"),
    bookmakers: str | None = typer.Option(None),
) -> None:
    async def run():
        async with OddsApiClient() as client:
            return await client.odds(
                sport_key=sport_key, regions=regions, markets=markets, bookmakers=bookmakers
            )

    quotes = asyncio.run(run())
    db = _db()
    count = db.insert_sportsbook_quotes(quotes)
    db.close()
    table = Table("Book", "Market", "Outcome", "Odds", "Implied", "De-vig")
    for q in quotes[:30]:
        table.add_row(
            q.bookmaker,
            q.market_key,
            q.outcome,
            f"{q.american_odds:+.0f}",
            f"{100 * q.implied_probability:.2f}%",
            f"{100 * (q.fair_probability or 0):.2f}%",
        )
    console.print(table)
    console.print(f"Stored {count} sportsbook quotes")


@app.command("constraints")
def constraints_cmd(path: Path) -> None:
    payload = yaml.safe_load(path.read_text())
    prices = {str(k): float(v) for k, v in payload["variables"].items()}
    constraints = [
        ProbabilityConstraint(
            kind=ConstraintKind(row["kind"]), variables=list(row["variables"]), name=row.get("name")
        )
        for row in payload["constraints"]
    ]
    violations = evaluate_constraints(prices, constraints)
    if violations:
        console.print("[bold red]Violations[/bold red]")
        for v in violations:
            console.print(f"- {v.message}: {v.magnitude:.6f}")
    else:
        console.print("No constraint violations.")
    repaired = repair_prices(prices, constraints)
    table = Table("Variable", "Observed", "Repaired", "Difference")
    for name, observed in prices.items():
        table.add_row(
            name, f"{observed:.4f}", f"{repaired[name]:.4f}", f"{repaired[name] - observed:+.4f}"
        )
    console.print(table)


@app.command()
def demo() -> None:
    """Populate a tiny synthetic example so every analysis component can be tried offline."""
    from datetime import timedelta

    from pme.models import MarketMapping, Quote, utcnow

    ts = utcnow()
    mappings = [
        MarketMapping(
            canonical_event_id="demo_event",
            canonical_outcome="YES",
            venue=Venue.KALSHI,
            market_id="DEMO-K",
            question="Demo proposition",
            verified=True,
        ),
        MarketMapping(
            canonical_event_id="demo_event",
            canonical_outcome="YES",
            venue=Venue.POLYMARKET,
            market_id="DEMO-P",
            token_id="DEMO-TOKEN",
            question="Demo proposition",
            verified=True,
        ),
    ]
    db = _db()
    db.upsert_mappings(mappings)
    for i in range(12):
        t = ts + timedelta(seconds=60 * i)
        k = Quote(
            timestamp=t,
            venue=Venue.KALSHI,
            market_id="DEMO-K",
            bid=0.50 + i * 0.001,
            ask=0.52 + i * 0.001,
            bid_size=100,
            ask_size=100,
        )
        p = Quote(
            timestamp=t,
            venue=Venue.POLYMARKET,
            market_id="DEMO-P",
            token_id="DEMO-TOKEN",
            bid=0.57 - i * 0.001,
            ask=0.59 - i * 0.001,
            bid_size=80,
            ask_size=80,
        )
        db.insert_quotes([k, p])
        opps = detect_cross_venue(
            [k, p], mappings, CostModel(slippage_bps=5), min_net_edge=0, max_quote_age_seconds=None
        )
        db.insert_opportunities(opps)
    engine = PaperBacktester(
        BacktestConfig(min_net_edge=0.005, max_contracts=25, cooldown_seconds=120)
    )
    trades = engine.run(db.opportunities())
    db.conn.execute("DELETE FROM paper_trades")
    db.insert_paper_trades(trades)
    db.close()
    console.print("Synthetic demo data inserted. Run `pme backtest` or launch the dashboard.")


@app.command("stream-polymarket")
def stream_polymarket_cmd(seconds: int = typer.Option(120, min=1)) -> None:
    db = _db()
    mappings = [m for m in db.mappings(True) if m.venue == Venue.POLYMARKET and m.token_id]
    token_to_market = {m.token_id: m.market_id for m in mappings if m.token_id}

    async def handler(q):
        db.insert_quotes([q])

    try:
        asyncio.run(stream_polymarket(token_to_market, handler, seconds=seconds))
    finally:
        db.close()


@app.command("stream-kalshi")
def stream_kalshi_cmd(seconds: int = typer.Option(120, min=1)) -> None:
    db = _db()
    tickers = [m.market_id for m in db.mappings(True) if m.venue == Venue.KALSHI]

    async def handler(q):
        db.insert_quotes([q])

    try:
        asyncio.run(stream_kalshi(tickers, handler, seconds=seconds))
    finally:
        db.close()

@app.command("live-scan")
def live_scan(
    min_edge: float = typer.Option(
        0.001,
        help="Minimum net edge per contract. 0.001 = 0.1 cent.",
    ),
    seconds: int = typer.Option(
        0,
        min=0,
        help="How long to run. 0 = run until Ctrl+C.",
    ),
) -> None:
    """
    Stream Kalshi and Polymarket simultaneously and scan for
    cross-venue arbitrage on every incoming quote update.
    """

    asyncio.run(_snapshot_once())

    db = _db()
    mappings = db.mappings(True)

    kalshi_tickers = [
        m.market_id
        for m in mappings
        if m.venue == Venue.KALSHI
    ]

    polymarket_token_to_market = {
        m.token_id: m.market_id
        for m in mappings
        if m.venue == Venue.POLYMARKET and m.token_id
    }

    latest = {
        (q.venue, q.market_id): q
        for q in db.latest_quotes()
    }

    mapping_by_market = {
        (m.venue, m.market_id): m
        for m in mappings
    }

    cost_model = CostModel(
        fee_bps={
            Venue.KALSHI: settings.kalshi_fee_bps,
            Venue.POLYMARKET: settings.polymarket_fee_bps,
        },
        slippage_bps=settings.slippage_bps,
    )

    active: set[tuple] = set()

    async def handler(q):
        latest[(q.venue, q.market_id)] = q
        db.insert_quotes([q])

        mapping = mapping_by_market.get(
            (q.venue, q.market_id)
        )

        if mapping is None:
            return

    
        event_quotes = []

        for m in mappings:
            if (
                m.canonical_event_id
                == mapping.canonical_event_id
                and m.canonical_outcome
                == mapping.canonical_outcome
            ):
                quote = latest.get(
                    (m.venue, m.market_id)
                )

                if quote is not None:
                    event_quotes.append(quote)

        if len(event_quotes) >= 2:
            kalshi_quote = next(
                (
                    x
                    for x in event_quotes
                    if x.venue == Venue.KALSHI
                ),
                None,
            )

            poly_quote = next(
                (
                    x
                    for x in event_quotes
                    if x.venue == Venue.POLYMARKET
                ),
                None,
            )

            if kalshi_quote and poly_quote:
                k_bid = kalshi_quote.bid
                k_ask = kalshi_quote.ask
                p_bid = poly_quote.bid
                p_ask = poly_quote.ask

                if None not in (
                    k_bid,
                    k_ask,
                    p_bid,
                    p_ask,
                ):
                    buy_kalshi_edge = (
                        p_bid - k_ask
                    )

                    buy_poly_edge = (
                        k_bid - p_ask
                    )

                    console.print(
                        f"{mapping.canonical_event_id} | "
                        f"K {k_bid:.3f}/{k_ask:.3f} | "
                        f"P {p_bid:.3f}/{p_ask:.3f} | "
                        f"K→P "
                        f"{100 * buy_kalshi_edge:+.2f}% | "
                        f"P→K "
                        f"{100 * buy_poly_edge:+.2f}%"
                    )

        opportunities = detect_cross_venue(
            list(latest.values()),
            mappings,
            cost_model=cost_model,
            min_net_edge=min_edge,
            max_pair_skew_seconds=None,
            max_quote_age_seconds=None,
        )

        opportunities = [
            o
            for o in opportunities
            if (
                o.canonical_event_id
                == mapping.canonical_event_id
                and o.canonical_outcome
                == mapping.canonical_outcome
            )
        ]

        current = {}

        for opportunity in opportunities:
            key = (
                opportunity.canonical_event_id,
                opportunity.canonical_outcome,
                opportunity.buy_venue,
                opportunity.hedge_venue,
            )

            current[key] = opportunity

            db.insert_opportunities([opportunity])

        event_active = {
            key
            for key in active
            if (
                key[0] == mapping.canonical_event_id
                and key[1]
                == mapping.canonical_outcome
            )
        }

        current_keys = set(current)

        for key in current_keys - event_active:
            opportunity = current[key]

            size = (
                "?"
                if opportunity.available_size is None
                else f"{opportunity.available_size:.1f}"
            )

            console.print()
            console.print(
                "[bold green]"
                "⚡ ARBITRAGE OPEN"
                "[/bold green]"
            )

            console.print(
                "Event: "
                f"{opportunity.canonical_event_id}"
            )

            console.print(
                "Buy YES: "
                f"{opportunity.buy_venue.value} "
                f"@ {opportunity.buy_price:.4f}"
            )

            console.print(
                "Hedge: "
                f"{opportunity.hedge_venue.value}"
            )

            console.print(
                "Gross edge: "
                f"{100 * opportunity.gross_edge:.3f}%"
            )

            console.print(
                "Net edge:   "
                f"{100 * opportunity.net_edge:.3f}%"
            )

            console.print(
                f"Available size: {size}"
            )

            console.print()

        for key in event_active - current_keys:
            console.print(
                "[red]✕ ARBITRAGE CLOSED[/red] "
                f"{key[0]}"
            )

        active.difference_update(event_active)
        active.update(current_keys)

    async def run():
        console.print(
            "[bold]"
            "Starting live arbitrage scanner..."
            "[/bold]"
        )

        console.print(
            f"Kalshi markets: {len(kalshi_tickers)}"
        )

        console.print(
            "Polymarket markets: "
            f"{len(polymarket_token_to_market)}"
        )

        console.print(
            "Minimum net edge: "
            f"{100 * min_edge:.3f}%"
        )

        duration = (
            None
            if seconds == 0
            else seconds
        )

        await asyncio.gather(
            stream_kalshi(
                kalshi_tickers,
                handler,
                seconds=duration,
            ),
            stream_polymarket(
                polymarket_token_to_market,
                handler,
                seconds=duration,
            ),
        )

    try:
        asyncio.run(run())
    finally:
        db.close()

def _aligned_mid_series(rows: list[tuple]) -> tuple[list[float], list[float]]:
    # Simple snapshot-index alignment: suitable for regular REST polling. For irregular WS data,
    # resample in a notebook before using lead/lag analysis.
    by_venue: dict[str, list[float]] = defaultdict(list)
    for _, venue, _, _, mid in rows:
        by_venue[venue].append(float(mid))
    if "kalshi" not in by_venue or "polymarket" not in by_venue:
        raise typer.BadParameter("Need quote history from both venues.")
    n = min(len(by_venue["kalshi"]), len(by_venue["polymarket"]))
    return by_venue["kalshi"][-n:], by_venue["polymarket"][-n:]


@app.command("lead-lag")
def lead_lag(event_id: str, max_lag: int = typer.Option(10)) -> None:
    db = _db()
    rows = db.quote_series(event_id)
    db.close()
    a, b = _aligned_mid_series(rows)
    table = Table("Lag", "Correlation")
    for result in lead_lag_correlation(a, b, max_lag=max_lag):
        table.add_row(str(result.lag), f"{result.correlation:.4f}")
    console.print(table)


@app.command()
def convergence(
    event_id: str,
    entry: float = typer.Option(0.03),
    exit: float = typer.Option(0.01),
) -> None:
    db = _db()
    rows = db.quote_series(event_id)
    db.close()
    # Pair regular REST snapshots by index.
    grouped: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        grouped[row[1]].append(row)
    n = min(len(grouped.get("kalshi", [])), len(grouped.get("polymarket", [])))
    if n == 0:
        raise typer.BadParameter("Need both venue histories.")
    k = grouped["kalshi"][-n:]
    p = grouped["polymarket"][-n:]
    ts = [max(k[i][0], p[i][0]) for i in range(n)]
    gaps = [float(k[i][4]) - float(p[i][4]) for i in range(n)]
    episodes = convergence_episodes(ts, gaps, entry_threshold=entry, exit_threshold=exit)
    table = Table("Start", "End", "Duration(s)", "Max gap")
    for e in episodes:
        table.add_row(
            str(e.start), str(e.end), f"{e.duration_seconds:.1f}", f"{100 * e.max_gap:.2f}%"
        )
    console.print(table)


if __name__ == "__main__":
    app()
