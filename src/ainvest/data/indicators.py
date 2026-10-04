"""TA-Lib technical calculations only; never order sizing or money arithmetic."""

import hashlib
import math
from decimal import Decimal, InvalidOperation, localcontext
from importlib import import_module
from typing import Annotated, Literal

from pydantic import StringConstraints

from ainvest.data.models import OhlcvPage
from ainvest.data.ports import (
    DataConflictError,
    DataIncompleteError,
    DataOperation,
    DataSchemaError,
)
from ainvest.data.quality import BarExpectation, assess_bars
from ainvest.schemas.common import DomainModel, Provenance, QualityFlag, SchemaVersion
from ainvest.schemas.market import TechnicalIndicators

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]
Version = Annotated[str, StringConstraints(min_length=1, max_length=256)]


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class IndicatorParameters(DomainModel):
    schema_version: SchemaVersion = "1.0"
    algorithm: Literal["talib-sma-rsi-atr-v1"] = "talib-sma-rsi-atr-v1"
    sma_short: Literal[20] = 20
    sma_long: Literal[50] = 50
    rsi_period: Literal[14] = 14
    atr_period: Literal[14] = 14
    numeric_mode: Literal["binary64-to-decimal-8dp"] = "binary64-to-decimal-8dp"


class IndicatorRun(DomainModel):
    schema_version: SchemaVersion = "1.0"
    parameters: IndicatorParameters
    library_version: Version
    numpy_version: Version
    c_library_version: Version
    input_digest: Digest
    technical: TechnicalIndicators


DEFAULT_PARAMETERS = IndicatorParameters()


def _decimal(value: float) -> Decimal:
    if not math.isfinite(value):
        raise DataSchemaError(
            "Non-finite indicator",
            operation=DataOperation.DATASET,
            reason_code="INDICATOR_NONFINITE",
        )
    try:
        with localcontext() as ctx:
            ctx.prec = 50
            return Decimal(str(value)).quantize(Decimal("0.00000001"))
    except InvalidOperation:
        raise DataSchemaError(
            "Unrepresentable indicator output",
            operation=DataOperation.DATASET,
            reason_code="INDICATOR_NONFINITE",
        ) from None


def compute_indicators(
    page: OhlcvPage,
    expected: BarExpectation,
    parameters: IndicatorParameters = DEFAULT_PARAMETERS,
) -> IndicatorRun:
    """Known warm-up values are None; any bad source quality rejects calculation."""
    quality = assess_bars(page, expected)
    if quality.issues:
        raise DataIncompleteError(
            "Bars fail quality checks",
            operation=DataOperation.DATASET,
            reason_code="INDICATOR_INPUT_INVALID",
        )
    talib = import_module("talib")
    numpy = import_module("numpy")
    if (
        talib.get_compatibility() != 0
        or talib.get_unstable_period("RSI") != 0
        or talib.get_unstable_period("ATR") != 0
    ):
        raise DataConflictError(
            "TA-Lib global settings changed",
            operation=DataOperation.DATASET,
            reason_code="INDICATOR_SETTINGS_CHANGED",
        )
    close = numpy.asarray([str(bar.close) for bar in page.items], dtype=numpy.float64)
    high = numpy.asarray([str(bar.high) for bar in page.items], dtype=numpy.float64)
    low = numpy.asarray([str(bar.low) for bar in page.items], dtype=numpy.float64)
    if not all(numpy.isfinite(values).all() for values in (close, high, low)):
        raise DataSchemaError(
            "Unrepresentable indicator input",
            operation=DataOperation.DATASET,
            reason_code="INDICATOR_NONFINITE",
        )
    count = len(page.items)
    short = (
        _decimal(float(talib.SMA(close, timeperiod=parameters.sma_short)[-1]))
        if count >= 20
        else None
    )
    long = (
        _decimal(float(talib.SMA(close, timeperiod=parameters.sma_long)[-1]))
        if count >= 50
        else None
    )
    rsi = (
        _decimal(float(talib.RSI(close, timeperiod=parameters.rsi_period)[-1]))
        if count >= 15
        else None
    )
    atr = (
        _decimal(float(talib.ATR(high, low, close, timeperiod=parameters.atr_period)[-1]))
        if count >= 15
        else None
    )
    flags = (QualityFlag.PARTIAL, QualityFlag.MISSING_FIELDS) if count < 50 else ()
    technical = TechnicalIndicators(
        symbol=expected.instrument.symbol,
        sma_20=short,
        sma_50=long,
        rsi_14=rsi,
        atr_14=atr,
        provenance=Provenance(
            source="talib.indicators.v1",
            observed_at=page.provenance.observed_at,
            received_at=expected.as_of,
            quality_flags=flags,
        ),
    )
    return IndicatorRun(
        parameters=parameters,
        library_version=str(talib.__version__),
        numpy_version=str(numpy.__version__),
        c_library_version=talib.__ta_version__.decode("ascii"),
        input_digest=digest_bytes(
            (
                page.model_dump_json() + expected.model_dump_json() + parameters.model_dump_json()
            ).encode()
        ),
        technical=technical,
    )
