"""Tests for portfolio-level risk limits (app/risk_check.py's
check_portfolio_limits) - daily loss and concentration checks, distinct
from check_pretrade's per-symbol checks, both opt-in via Settings."""
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import risk_check
from app.config import Settings
from app.models import DailyPnlBaseline, Position


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_disabled_by_default_never_blocks(session):
    result = risk_check.check_portfolio_limits(1, "AAPL", "buy", 10, 100.0, session)
    assert result.block_reason is None


def test_concentration_limit_blocks_an_over_concentrated_new_buy(session):
    session.add(Position(tenant_id=1, symbol="AAPL", qty=100, avg_price=100.0))
    session.add(Position(tenant_id=1, symbol="MSFT", qty=10, avg_price=100.0))
    session.commit()
    settings = Settings(risk_max_symbol_concentration_pct=0.5)
    # Adding 1000 more AAPL @ 100 -> AAPL notional dominates the book
    result = risk_check.check_portfolio_limits(1, "AAPL", "buy", 1000, 100.0, session, settings)
    assert result.block_reason is not None
    assert "concentration" in result.block_reason


def test_concentration_limit_allows_a_balanced_buy(session):
    session.add(Position(tenant_id=1, symbol="AAPL", qty=10, avg_price=100.0))
    session.add(Position(tenant_id=1, symbol="MSFT", qty=10, avg_price=100.0))
    session.commit()
    settings = Settings(risk_max_symbol_concentration_pct=0.9)
    result = risk_check.check_portfolio_limits(1, "AAPL", "buy", 1, 100.0, session, settings)
    assert result.block_reason is None


def test_daily_loss_limit_blocks_a_new_position_after_breach(session):
    pos = Position(tenant_id=1, symbol="AAPL", qty=10, avg_price=100.0, realized_pnl=-500.0)
    session.add(pos)
    session.commit()
    session.refresh(pos)
    settings = Settings(risk_max_daily_loss=100.0)
    # no baseline yet -> first check captures -500 as today's baseline, so
    # today's delta is 0 and this one should NOT block
    result = risk_check.check_portfolio_limits(1, "MSFT", "buy", 1, 100.0, session, settings)
    assert result.block_reason is None

    # simulate more loss happening AFTER the baseline was captured
    pos.realized_pnl = -800.0
    session.add(pos)
    session.commit()

    result2 = risk_check.check_portfolio_limits(1, "MSFT", "buy", 1, 100.0, session, settings)
    assert result2.block_reason is not None
    assert "daily loss" in result2.block_reason


def test_daily_loss_limit_never_blocks_a_reducing_trade(session):
    session.add(Position(tenant_id=1, symbol="AAPL", qty=10, avg_price=100.0, realized_pnl=-500.0))
    session.add(DailyPnlBaseline(tenant_id=1, trading_date=date.today(),
                                 baseline_realized_pnl=0.0))
    session.commit()
    settings = Settings(risk_max_daily_loss=100.0)
    # SELLING an existing long is reducing risk, even though daily loss is
    # already breached (-500 < -100) - must never be blocked.
    result = risk_check.check_portfolio_limits(1, "AAPL", "sell", 5, 100.0, session, settings)
    assert result.block_reason is None
