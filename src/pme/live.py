from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pme.collectors import KalshiClient, PolymarketClient
from pme.collectors.websockets import stream_kalshi, stream_polymarket
from pme.config import settings
from pme.database import Database
from pme.executable import (
    ExecutableArbitrage,
    ExecutableLeg,
    kalshi_taker_fee,
    market_pair,
    polymarket_taker_fee,
    quote_gate,
)
from pme.matching.auto import auto_match_markets
from pme.matching.normalize import canonical_slug
from pme.models import Market, MarketMapping, Quote, Venue


@dataclass(frozen=True)
class LiveTrackerConfig:
    min_net_edge: float = 0.001
    discovery_interval_seconds: int = 300
    polling_interval_seconds: float = 2.0
    kalshi_limit: int = 10000
    polymarket_limit: int = 20000
    min_match_score: float = 86.0
    min_text_score: float = 84.0
    min_date_score: float = 0.0
    min_match_margin: float = 1.5
    candidate_limit: int = 50
    max_quote_age_seconds: float = 2.0
    max_pair_skew_seconds: float = 1.0
    max_verification_latency_seconds: float = 2.0
    verification_cooldown_seconds: float = 0.75


@dataclass
class LiveTrackerStatus:
    running: bool = False
    phase: str = "stopped"
    mode: str = "idle"
    last_error: str | None = None
    last_market_refresh: datetime | None = None
    last_quote: datetime | None = None
    kalshi_markets: int = 0
    polymarket_markets: int = 0
    mapped_pairs: int = 0
    active_alerts: int = 0


class LiveArbitrageTracker:
    """Discover shared markets and emit only executable, fully-gated alerts."""

    def __init__(
        self,
        config: LiveTrackerConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or LiveTrackerConfig()
        self.db_path = Path(db_path or settings.db_path)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._status = LiveTrackerStatus()
        self._status_lock = threading.Lock()

        self._latest: dict[tuple[Venue, str], Quote] = {}
        self._mappings: list[MarketMapping] = []
        self._mapping_by_market: dict[tuple[Venue, str], MarketMapping] = {}
        self._event_mappings: dict[tuple[str, str], list[MarketMapping]] = {}
        self._market_by_key: dict[tuple[Venue, str], Market] = {}
        self._active: dict[tuple[str, str, str], ExecutableArbitrage] = {}
        self._last_verification: dict[tuple[str, str, str], float] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="pme-live-arbitrage",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def restart(self) -> None:
        self.stop()
        self.start()

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            return asdict(self._status)

    def _set_status(self, **values: Any) -> None:
        with self._status_lock:
            for key, value in values.items():
                setattr(self._status, key, value)

    def _thread_main(self) -> None:
        self._set_status(running=True, phase="starting", last_error=None)
        try:
            asyncio.run(self._run())
        except Exception as exc:  # pragma: no cover
            self._set_status(last_error=str(exc), phase="error")
        finally:
            self._set_status(running=False)

    async def _run(self) -> None:
        db = Database(self.db_path)
        db.init()
        self._close_persisted_open_alerts(db)
        db.conn.execute("DELETE FROM executable_live")
        try:
            while not self._stop_event.is_set():
                try:
                    mappings = await self._refresh_markets_and_mappings(db)
                    self._apply_mappings(mappings)
                    self._close_removed_alerts(db)
                    await self._scan_seeded_events(db)

                    if not mappings:
                        self._set_status(
                            phase="waiting for shared markets",
                            mode="idle",
                            active_alerts=len(self._active),
                        )
                        await self._sleep_or_stop(self.config.discovery_interval_seconds)
                        continue

                    ws_ready = bool(
                        settings.kalshi_api_key_id
                        and settings.kalshi_private_key_path
                        and settings.polymarket_us_key_id
                        and settings.polymarket_us_secret_key
                    )
                    if ws_ready:
                        self._set_status(
                            phase="streaming Kalshi + Polymarket US live quotes",
                            mode="websocket",
                        )
                        await self._websocket_cycle(db)
                    else:
                        self._set_status(
                            phase=(
                                "polling live quotes; add Polymarket US API credentials "
                                "for sub-second WebSocket data"
                            ),
                            mode="polling",
                        )
                        await self._polling_cycle(db)
                except Exception as exc:
                    self._set_status(last_error=str(exc), phase="recovering")
                    await self._sleep_or_stop(3.0)
        finally:
            db.conn.execute("DELETE FROM executable_live")
            db.close()
            self._set_status(phase="stopped", mode="idle")

    async def _refresh_markets_and_mappings(self, db: Database) -> list[MarketMapping]:
        self._set_status(phase="discovering shared markets", last_error=None)

        async with KalshiClient() as kalshi, PolymarketClient() as poly:
            kalshi_markets, poly_markets = await asyncio.gather(
                kalshi.list_markets(self.config.kalshi_limit),
                poly.list_markets(self.config.polymarket_limit),
            )
            all_markets = kalshi_markets + poly_markets
            self._market_by_key = {(m.venue, m.market_id): m for m in all_markets}
            db.upsert_markets(all_markets)

            suggestions = auto_match_markets(
                all_markets,
                min_score=self.config.min_match_score,
                min_text_score=self.config.min_text_score,
                min_date_score=self.config.min_date_score,
                min_margin=self.config.min_match_margin,
                candidate_limit=self.config.candidate_limit,
            )

            auto_mappings: list[MarketMapping] = []
            for suggestion in suggestions:
                event_id = "auto_" + canonical_slug(suggestion.kalshi_market_id)
                confidence = suggestion.score / 100.0
                auto_mappings.extend(
                    [
                        MarketMapping(
                            canonical_event_id=event_id,
                            canonical_outcome="YES",
                            venue=Venue.KALSHI,
                            market_id=suggestion.kalshi_market_id,
                            question=suggestion.kalshi_question,
                            confidence=confidence,
                            verified=True,
                        ),
                        MarketMapping(
                            canonical_event_id=event_id,
                            canonical_outcome="YES",
                            venue=Venue.POLYMARKET,
                            market_id=suggestion.polymarket_market_id,
                            token_id=suggestion.polymarket_token_id,
                            question=suggestion.polymarket_question,
                            confidence=confidence,
                            verified=True,
                        ),
                    ]
                )
            db.replace_auto_mappings(auto_mappings)

            active_keys = {(m.venue, m.market_id) for m in all_markets if m.active}
            mappings = [
                mapping
                for mapping in db.mappings(verified_only=True)
                if (mapping.venue, mapping.market_id) in active_keys
            ]

            semaphore = asyncio.Semaphore(20)

            async def fetch(mapping: MarketMapping) -> Quote | None:
                async with semaphore:
                    try:
                        if mapping.venue == Venue.KALSHI:
                            return await kalshi.get_quote(mapping.market_id)
                        if mapping.venue == Venue.POLYMARKET and mapping.token_id:
                            return await poly.get_quote(mapping.market_id, mapping.token_id)
                    except Exception:
                        return None
                    return None

            seed_quotes = await asyncio.gather(*(fetch(mapping) for mapping in mappings))
            quotes = [quote for quote in seed_quotes if quote is not None]
            if quotes:
                db.insert_quotes(quotes)
                for quote in quotes:
                    self._latest[(quote.venue, quote.market_id)] = quote

        self._set_status(
            last_market_refresh=datetime.now(UTC),
            kalshi_markets=len(kalshi_markets),
            polymarket_markets=len(poly_markets),
            mapped_pairs=len({m.canonical_event_id for m in mappings}),
        )
        return mappings

    def _apply_mappings(self, mappings: list[MarketMapping]) -> None:
        self._mappings = mappings
        self._mapping_by_market = {(m.venue, m.market_id): m for m in mappings}
        grouped: dict[tuple[str, str], list[MarketMapping]] = {}
        for mapping in mappings:
            grouped.setdefault((mapping.canonical_event_id, mapping.canonical_outcome), []).append(
                mapping
            )
        self._event_mappings = grouped

    async def _scan_seeded_events(self, db: Database) -> None:
        for event_key in self._event_mappings:
            await self._evaluate_event(db, event_key)

    async def _websocket_cycle(self, db: Database) -> None:
        kalshi_tickers = [
            mapping.market_id for mapping in self._mappings if mapping.venue == Venue.KALSHI
        ]
        poly_slug_to_market = {
            mapping.token_id: mapping.market_id
            for mapping in self._mappings
            if mapping.venue == Venue.POLYMARKET and mapping.token_id
        }
        if not kalshi_tickers or not poly_slug_to_market:
            await self._sleep_or_stop(5.0)
            return

        async def handler(quote: Quote) -> None:
            await self._handle_quote(db, quote, persist=True)

        async def stream_both() -> None:
            await asyncio.gather(
                stream_kalshi(kalshi_tickers, handler, seconds=None),
                stream_polymarket(poly_slug_to_market, handler, seconds=None),
            )

        stream_task = asyncio.create_task(stream_both())
        timer_task = asyncio.create_task(asyncio.sleep(self.config.discovery_interval_seconds))
        stop_task = asyncio.create_task(self._wait_for_stop())
        stale_task = asyncio.create_task(self._stale_watchdog(db))

        done, pending = await asyncio.wait(
            {stream_task, timer_task, stop_task, stale_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        if stream_task in done:
            exc = stream_task.exception()
            if exc is not None and not self._stop_event.is_set():
                raise exc

    async def _polling_cycle(self, db: Database) -> None:
        deadline = time.monotonic() + self.config.discovery_interval_seconds
        async with KalshiClient() as kalshi, PolymarketClient() as poly:
            semaphore = asyncio.Semaphore(20)

            async def fetch(mapping: MarketMapping) -> Quote | None:
                async with semaphore:
                    try:
                        if mapping.venue == Venue.KALSHI:
                            return await kalshi.get_quote(mapping.market_id)
                        if mapping.venue == Venue.POLYMARKET and mapping.token_id:
                            return await poly.get_quote(mapping.market_id, mapping.token_id)
                    except Exception:
                        return None
                    return None

            while time.monotonic() < deadline and not self._stop_event.is_set():
                results = await asyncio.gather(*(fetch(m) for m in self._mappings))
                quotes = [quote for quote in results if quote is not None]
                if quotes:
                    db.insert_quotes(quotes)
                    for quote in quotes:
                        await self._handle_quote(db, quote, persist=False)
                await self._expire_stale_alerts(db)
                await self._sleep_or_stop(self.config.polling_interval_seconds)

    async def _handle_quote(self, db: Database, quote: Quote, persist: bool) -> None:
        self._latest[(quote.venue, quote.market_id)] = quote
        if persist:
            db.insert_quotes([quote])
        self._set_status(last_quote=quote.timestamp)

        mapping = self._mapping_by_market.get((quote.venue, quote.market_id))
        if mapping is None:
            return
        await self._evaluate_event(db, (mapping.canonical_event_id, mapping.canonical_outcome))

    async def _evaluate_event(self, db: Database, event_key: tuple[str, str]) -> None:
        mappings = self._event_mappings.get(event_key, [])
        pair = market_pair(mappings, self._market_by_key)
        if pair is None:
            await self._close_event_alerts(db, event_key)
            return
        k_mapping, p_mapping, _k_market, _p_market = pair
        k_quote = self._latest.get((Venue.KALSHI, k_mapping.market_id))
        p_quote = self._latest.get((Venue.POLYMARKET, p_mapping.market_id))
        if k_quote is None or p_quote is None:
            await self._close_event_alerts(db, event_key)
            return

        gate = quote_gate(
            k_quote,
            p_quote,
            max_age_seconds=self.config.max_quote_age_seconds,
            max_skew_seconds=self.config.max_pair_skew_seconds,
        )
        if not gate.passed:
            await self._close_event_alerts(db, event_key)
            return

        if not self._rules_verified(db, k_mapping.market_id, p_mapping.market_id):
            await self._close_event_alerts(db, event_key)
            return

        directions: list[str] = []
        if k_quote.ask is not None and p_quote.bid is not None:
            if p_quote.bid - k_quote.ask >= self.config.min_net_edge:
                directions.append("K→P")
        if p_quote.ask is not None and k_quote.bid is not None:
            if k_quote.bid - p_quote.ask >= self.config.min_net_edge:
                directions.append("P→K")

        if not directions:
            await self._close_event_alerts(db, event_key)
            return

        current_keys: set[tuple[str, str, str]] = set()
        for direction in directions:
            key = (event_key[0], event_key[1], direction)
            now_mono = time.monotonic()
            last = self._last_verification.get(key, 0.0)
            if now_mono - last < self.config.verification_cooldown_seconds:
                if key in self._active:
                    current_keys.add(key)
                continue
            self._last_verification[key] = now_mono

            verified = await self._verify_direction(
                db,
                mappings,
                k_quote,
                p_quote,
                direction,
            )
            if verified is None:
                continue
            current_keys.add(key)
            was_open = key in self._active
            self._active[key] = verified
            self._upsert_live(db, verified)
            if not was_open:
                self._insert_alert(db, "open", verified)

        event_active_keys = {key for key in self._active if key[:2] == event_key}
        for key in event_active_keys - current_keys:
            opportunity = self._active.pop(key)
            self._delete_live(db, key)
            self._insert_alert(db, "closed", opportunity)
        self._set_status(active_alerts=len(self._active))

    async def _verify_direction(
        self,
        db: Database,
        mappings: list[MarketMapping],
        k_quote: Quote,
        p_quote: Quote,
        direction: str,
    ) -> ExecutableArbitrage | None:
        pair = market_pair(mappings, self._market_by_key)
        if pair is None:
            return None
        k_mapping, p_mapping, k_market, p_market = pair

        gate = quote_gate(
            k_quote,
            p_quote,
            max_age_seconds=self.config.max_quote_age_seconds,
            max_skew_seconds=self.config.max_pair_skew_seconds,
        )
        if not gate.passed:
            return None
        if not self._rules_verified(db, k_mapping.market_id, p_mapping.market_id):
            return None

        series_ticker = str(k_market.metadata.get("series_ticker") or "")
        event_ticker = str(k_market.event_id or "")
        if not series_ticker or not event_ticker:
            return None

        started = time.monotonic()
        try:
            async with KalshiClient() as kalshi, PolymarketClient() as poly:
                k_book, p_book, series, event = await asyncio.gather(
                    kalshi.get_executable_book(k_market.market_id),
                    poly.get_binary_executable_book(p_market),
                    kalshi.get_series(series_ticker),
                    kalshi.get_event(event_ticker),
                )
        except Exception:
            return None
        latency = time.monotonic() - started
        if latency > self.config.max_verification_latency_seconds:
            return None

        k_book_ts = k_book.get("timestamp")
        p_book_ts = p_book.get("timestamp")
        if not isinstance(k_book_ts, datetime) or not isinstance(p_book_ts, datetime):
            return None
        book_skew = abs((k_book_ts - p_book_ts).total_seconds())
        if book_skew > self.config.max_pair_skew_seconds:
            return None

        if direction == "K→P":
            leg1_venue = Venue.KALSHI
            leg1_market = k_market.market_id
            leg1_side = "YES"
            leg1_price = k_book.get("yes_ask")
            leg1_size = k_book.get("yes_ask_size")
            leg2_venue = Venue.POLYMARKET
            leg2_market = p_market.market_id
            leg2_side = "NO"
            leg2_price = p_book.get("no_ask")
            leg2_size = p_book.get("no_ask_size")
        else:
            leg1_venue = Venue.POLYMARKET
            leg1_market = p_market.market_id
            leg1_side = "YES"
            leg1_price = p_book.get("yes_ask")
            leg1_size = p_book.get("yes_ask_size")
            leg2_venue = Venue.KALSHI
            leg2_market = k_market.market_id
            leg2_side = "NO"
            leg2_price = k_book.get("no_ask")
            leg2_size = k_book.get("no_ask_size")

        values = (leg1_price, leg1_size, leg2_price, leg2_size)
        if any(value is None for value in values):
            return None
        price1 = float(leg1_price)
        size1 = float(leg1_size)
        price2 = float(leg2_price)
        size2 = float(leg2_size)
        quantity = min(size1, size2)
        minimum_order_size = p_book.get("minimum_order_size")
        if quantity <= 0:
            return None
        if minimum_order_size is not None and quantity < float(minimum_order_size):
            return None

        # Event-level fee overrides take precedence over series defaults.
        # A numeric override of 0 is meaningful (fee-free), so do not use `or`
        # when selecting the multiplier.
        fee_type = str(event.get("fee_type_override") or series.get("fee_type") or "")
        multiplier_override = event.get("fee_multiplier_override")
        if multiplier_override is None:
            multiplier_raw = series.get("fee_multiplier")
        else:
            multiplier_raw = multiplier_override
        fee_multiplier = float(1.0 if multiplier_raw is None else multiplier_raw)
        poly_fee_rate = float(p_book.get("fee_rate") or 0.0)
        poly_fee_exponent = p_book.get("fee_exponent")

        if leg1_venue == Venue.KALSHI:
            fee1 = kalshi_taker_fee(
                price=price1,
                quantity=quantity,
                fee_type=fee_type,
                fee_multiplier=fee_multiplier,
            )
            fee2 = polymarket_taker_fee(
                price=price2,
                quantity=quantity,
                fee_rate=poly_fee_rate,
                fee_exponent=poly_fee_exponent,
            )
        else:
            fee1 = polymarket_taker_fee(
                price=price1,
                quantity=quantity,
                fee_rate=poly_fee_rate,
                fee_exponent=poly_fee_exponent,
            )
            fee2 = kalshi_taker_fee(
                price=price2,
                quantity=quantity,
                fee_type=fee_type,
                fee_multiplier=fee_multiplier,
            )
        if fee1 is None or fee2 is None:
            return None

        gross_profit = quantity * (1.0 - price1 - price2)
        total_fee = fee1 + fee2
        net_profit = gross_profit - total_fee
        net_edge = net_profit / quantity
        total_cost = quantity * (price1 + price2) + total_fee
        payout = quantity
        net_roi = net_profit / total_cost if total_cost > 0 else 0.0
        if net_edge < self.config.min_net_edge:
            return None

        confidence = min((m.confidence for m in mappings), default=0.0)
        question = k_mapping.question or p_mapping.question
        return ExecutableArbitrage(
            timestamp=datetime.now(UTC),
            canonical_event_id=k_mapping.canonical_event_id,
            canonical_outcome=k_mapping.canonical_outcome,
            question=question,
            direction=direction,
            leg1=ExecutableLeg(
                venue=leg1_venue,
                market_id=leg1_market,
                side=leg1_side,
                price=price1,
                available_size=size1,
                fee=fee1,
            ),
            leg2=ExecutableLeg(
                venue=leg2_venue,
                market_id=leg2_market,
                side=leg2_side,
                price=price2,
                available_size=size2,
                fee=fee2,
            ),
            quantity=quantity,
            gross_profit=gross_profit,
            total_fee=total_fee,
            net_profit=net_profit,
            net_edge=net_edge,
            total_cost=total_cost,
            payout=payout,
            net_roi=net_roi,
            kalshi_quote_age_seconds=gate.kalshi_age_seconds,
            polymarket_quote_age_seconds=gate.polymarket_age_seconds,
            quote_skew_seconds=gate.skew_seconds,
            verification_latency_seconds=latency,
            match_confidence=confidence,
            rules_verified=True,
            books_verified=True,
            fees_verified=True,
            metadata={
                "kalshi_fee_type": fee_type,
                "kalshi_fee_multiplier": fee_multiplier,
                "polymarket_us_fee_coefficient": poly_fee_rate,
                "polymarket_fee_exponent": poly_fee_exponent,
                "book_skew_seconds": book_skew,
                "series_ticker": series_ticker,
                "event_ticker": event_ticker,
                "kalshi_fee_override": multiplier_override is not None
                or bool(event.get("fee_type_override")),
            },
        )

    def _rules_verified(self, db: Database, kalshi_id: str, polymarket_id: str) -> bool:
        row = db.conn.execute(
            """
            SELECT verified
            FROM rule_reviews
            WHERE kalshi_market_id=? AND polymarket_market_id=?
            """,
            [kalshi_id, polymarket_id],
        ).fetchone()
        return bool(row and row[0])

    async def _stale_watchdog(self, db: Database) -> None:
        while not self._stop_event.is_set():
            await self._expire_stale_alerts(db)
            await asyncio.sleep(0.5)

    async def _expire_stale_alerts(self, db: Database) -> None:
        for key, opportunity in list(self._active.items()):
            mappings = self._event_mappings.get(key[:2], [])
            pair = market_pair(mappings, self._market_by_key)
            if pair is None:
                self._active.pop(key, None)
                self._delete_live(db, key)
                self._insert_alert(db, "closed", opportunity)
                continue
            k_mapping, p_mapping, _k_market, _p_market = pair
            k_quote = self._latest.get((Venue.KALSHI, k_mapping.market_id))
            p_quote = self._latest.get((Venue.POLYMARKET, p_mapping.market_id))
            rules_ok = self._rules_verified(db, k_mapping.market_id, p_mapping.market_id)
            if k_quote is None or p_quote is None or not rules_ok:
                passed = False
            else:
                passed = quote_gate(
                    k_quote,
                    p_quote,
                    max_age_seconds=self.config.max_quote_age_seconds,
                    max_skew_seconds=self.config.max_pair_skew_seconds,
                ).passed
            if not passed:
                self._active.pop(key, None)
                self._delete_live(db, key)
                self._insert_alert(db, "closed", opportunity)
        self._set_status(active_alerts=len(self._active))

    async def _close_event_alerts(self, db: Database, event_key: tuple[str, str]) -> None:
        for key, opportunity in list(self._active.items()):
            if key[:2] != event_key:
                continue
            self._active.pop(key, None)
            self._delete_live(db, key)
            self._insert_alert(db, "closed", opportunity)
        self._set_status(active_alerts=len(self._active))

    def _close_removed_alerts(self, db: Database) -> None:
        valid_events = set(self._event_mappings)
        for key, opportunity in list(self._active.items()):
            if key[:2] in valid_events:
                continue
            self._active.pop(key, None)
            self._delete_live(db, key)
            self._insert_alert(db, "closed", opportunity)
        self._set_status(active_alerts=len(self._active))

    def _upsert_live(self, db: Database, opportunity: ExecutableArbitrage) -> None:
        db.conn.execute(
            """
            INSERT OR REPLACE INTO executable_live VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                opportunity.canonical_event_id,
                opportunity.canonical_outcome,
                opportunity.direction,
                opportunity.timestamp,
                opportunity.question,
                opportunity.leg1.venue.value,
                opportunity.leg1.side,
                opportunity.leg1.market_id,
                opportunity.leg1.price,
                opportunity.leg1.available_size,
                opportunity.leg1.fee,
                opportunity.leg2.venue.value,
                opportunity.leg2.side,
                opportunity.leg2.market_id,
                opportunity.leg2.price,
                opportunity.leg2.available_size,
                opportunity.leg2.fee,
                opportunity.quantity,
                opportunity.gross_profit,
                opportunity.total_fee,
                opportunity.net_profit,
                opportunity.net_edge,
                opportunity.total_cost,
                opportunity.payout,
                opportunity.net_roi,
                opportunity.kalshi_quote_age_seconds,
                opportunity.polymarket_quote_age_seconds,
                opportunity.quote_skew_seconds,
                opportunity.verification_latency_seconds,
                opportunity.match_confidence,
                opportunity.rules_verified,
                opportunity.books_verified,
                opportunity.fees_verified,
                json.dumps(opportunity.metadata, default=str),
            ],
        )

    def _delete_live(self, db: Database, key: tuple[str, str, str]) -> None:
        db.conn.execute(
            """
            DELETE FROM executable_live
            WHERE canonical_event_id=? AND canonical_outcome=? AND direction=?
            """,
            list(key),
        )

    def _close_persisted_open_alerts(self, db: Database) -> None:
        # arbitrage_alerts is an append-only transition history.  Current
        # actionable state lives in executable_live, so a process restart
        # should clear only that materialized current-state table rather than
        # rewriting historical OPEN transitions.
        db.conn.execute("DELETE FROM executable_live")

    def _insert_alert(
        self,
        db: Database,
        status: str,
        opportunity: ExecutableArbitrage,
    ) -> None:
        db.conn.execute(
            """
            INSERT INTO arbitrage_alerts (
                timestamp, status, canonical_event_id, canonical_outcome, question,
                buy_venue, hedge_venue, buy_market_id, hedge_market_id, buy_price,
                hedge_yes_bid, gross_edge, estimated_cost, net_edge, available_size,
                direction, leg1_side, leg2_side, leg1_price, leg2_price, leg1_size,
                leg2_size, quantity, leg1_fee, leg2_fee, total_cost, payout, net_profit,
                net_roi, kalshi_quote_age, polymarket_quote_age, quote_skew,
                verification_latency, match_confidence, rules_verified, books_verified,
                fees_verified, metadata_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                datetime.now(UTC),
                status,
                opportunity.canonical_event_id,
                opportunity.canonical_outcome,
                opportunity.question,
                opportunity.leg1.venue.value,
                opportunity.leg2.venue.value,
                opportunity.leg1.market_id,
                opportunity.leg2.market_id,
                opportunity.leg1.price,
                None,
                opportunity.gross_profit / opportunity.quantity,
                opportunity.total_fee / opportunity.quantity,
                opportunity.net_edge,
                opportunity.quantity,
                opportunity.direction,
                opportunity.leg1.side,
                opportunity.leg2.side,
                opportunity.leg1.price,
                opportunity.leg2.price,
                opportunity.leg1.available_size,
                opportunity.leg2.available_size,
                opportunity.quantity,
                opportunity.leg1.fee,
                opportunity.leg2.fee,
                opportunity.total_cost,
                opportunity.payout,
                opportunity.net_profit,
                opportunity.net_roi,
                opportunity.kalshi_quote_age_seconds,
                opportunity.polymarket_quote_age_seconds,
                opportunity.quote_skew_seconds,
                opportunity.verification_latency_seconds,
                opportunity.match_confidence,
                opportunity.rules_verified,
                opportunity.books_verified,
                opportunity.fees_verified,
                json.dumps(opportunity.metadata, default=str),
            ],
        )

    async def _wait_for_stop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(0.25)

    async def _sleep_or_stop(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._stop_event.is_set():
            await asyncio.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
