"""
asset_metadata.py - persists the canonical (asset_class, market, currency)
classification symbols.classify() derives, keyed by symbol, so it survives
a container restart and is queryable/joinable like any other control-plane
table - not just an in-memory dict (symbols.py's _live_symbols) that starts
empty every time the process restarts.

app/symbol_discovery.py's poll loop calls record_seen() with every symbol
its universe scan just saw; each is upserted here (first_seen_at set once,
on genuine first sighting; last_seen_at bumped every pass after that).
Classification itself is symbols.classify's job - this module is only the
persistence layer over it.
"""
from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from . import symbols as symref
from .models import AssetMetadata


def record_seen(syms, session: Session) -> int:
    """Upsert AssetMetadata for every symbol in `syms` (the full live
    universe scan, not just newly-added ones - existing rows still need
    last_seen_at bumped). Returns the count of genuinely new symbols - a
    symbol's classification is derived once and never re-computed, since it
    doesn't change once known."""
    now = datetime.utcnow()
    new_count = 0
    for sym in syms:
        row = session.get(AssetMetadata, sym)
        if row is None:
            market, currency, asset_class = symref.classify(sym)
            session.add(AssetMetadata(symbol=sym, name=sym, asset_class=asset_class,
                                      market=market, currency=currency, source="live",
                                      first_seen_at=now, last_seen_at=now))
            new_count += 1
        else:
            row.last_seen_at = now
            session.add(row)
    session.commit()
    return new_count


def class_breakdown(session: Session) -> list[dict]:
    """Distinct asset classes with counts among symbols persisted here (the
    live-discovered universe), most-populous first - for the Autoverse /
    asset-universe view. Does not include symbols.py's static equity seed;
    those are always classified "equity" and never lose that no matter what
    a live feed says, so counting them here would just double-count."""
    rows = session.exec(select(AssetMetadata)).all()
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r.asset_class] = by_class.get(r.asset_class, 0) + 1
    return sorted(({"asset_class": k, "count": v} for k, v in by_class.items()),
                  key=lambda x: -x["count"])
