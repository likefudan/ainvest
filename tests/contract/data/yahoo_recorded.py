"""Recorded no-network Yahoo provider factory for the shared P04-T0 contracts."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import ainvest.data.providers.yahoo as yahoo_module
from ainvest.config import TradingMode
from ainvest.data import fixture_dataset
from ainvest.data.providers.yahoo import YahooDevelopmentAdapter, YahooInstrumentConfig

_RECORDING = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "data" / "yahoo_recording.json").read_text(
        encoding="utf-8"
    )
)
_NOW = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)


class _RecordedYahooBoundary:
    """Immutable recording that implements only Yahoo's private transport seam."""

    def __init__(self) -> None:
        self._quote = yahoo_module._YahooQuote(
            observed_at=datetime.fromisoformat(_RECORDING["quote"]["observed_at"]),
            last_price=_RECORDING["quote"]["last_price"],
        )
        self._bars = tuple(
            yahoo_module._YahooBar(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
            for row in _RECORDING["bars"]
        )
        self._actions = tuple(
            yahoo_module._YahooAction(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                dividend=row["dividend"],
                split=row["split"],
            )
            for row in _RECORDING["actions"]
        )

    def quote(self, symbol: str, *, timeout_seconds: float) -> yahoo_module._YahooQuote:
        del symbol, timeout_seconds
        return self._quote

    def history(
        self,
        symbol: str,
        *,
        start_at: datetime,
        end_at: datetime,
        interval: str,
        auto_adjust: bool,
        timeout_seconds: float,
    ) -> tuple[yahoo_module._YahooBar, ...]:
        del symbol, interval, auto_adjust, timeout_seconds
        return tuple(
            bar for bar in self._bars if start_at <= bar.timestamp.astimezone(UTC) < end_at
        )

    def actions(
        self,
        symbol: str,
        *,
        effective_from: date,
        effective_to: date,
        timeout_seconds: float,
    ) -> tuple[yahoo_module._YahooAction, ...]:
        del timeout_seconds
        actions = self._actions
        if symbol == "SPY":
            actions = tuple(action for action in actions if Decimal(str(action.dividend)) > 0)
        return tuple(
            action for action in actions if effective_from <= action.timestamp.date() < effective_to
        )


def recorded_yahoo_provider() -> YahooDevelopmentAdapter:
    """Return a fresh deterministic adapter for shared quote/OHLCV/action contracts."""
    identities = {
        observation.instrument.instrument_id: observation.instrument
        for observation in fixture_dataset().instrument_metadata
        if observation.instrument.instrument_id in {"rh_inst_aapl_xnas", "rh_inst_spy_arcx"}
    }
    instruments = tuple(
        YahooInstrumentConfig(
            instrument=identities[instrument_id],
            exchange_timezone="America/New_York",
        )
        for instrument_id in ("rh_inst_aapl_xnas", "rh_inst_spy_arcx")
    )
    return YahooDevelopmentAdapter(
        mode=TradingMode.RESEARCH,
        instruments=instruments,
        clock=lambda: _NOW,
        monotonic_clock=lambda: 0.0,
        boundary=_RecordedYahooBoundary(),
    )


__all__ = ["recorded_yahoo_provider"]
