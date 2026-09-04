from __future__ import annotations

import csv
from pathlib import Path

from pme.models import MarketMapping, Venue


def load_mapping_csv(path: str | Path) -> list[MarketMapping]:
    rows: list[MarketMapping] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                MarketMapping(
                    canonical_event_id=row["canonical_event_id"].strip(),
                    canonical_outcome=row["canonical_outcome"].strip(),
                    venue=Venue(row["venue"].strip().lower()),
                    market_id=row["market_id"].strip(),
                    token_id=(row.get("token_id") or "").strip() or None,
                    question=(row.get("question") or "").strip() or None,
                    confidence=float(row.get("confidence") or 1.0),
                    verified=(row.get("verified") or "").strip().lower()
                    in {"1", "true", "yes", "y"},
                )
            )
    return rows
