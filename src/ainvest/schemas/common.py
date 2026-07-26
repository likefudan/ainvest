"""Shared domain primitives for versioned ainvest schemas (P02-T0).

All money-related values use :class:`~decimal.Decimal`. JSON serialization uses
decimal strings and timezone-aware UTC ISO 8601 timestamps. Naive datetimes and
binary floats are rejected.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
    field_validator,
)

SCHEMA_VERSION_V1: Final[Literal["1.0"]] = "1.0"

# Canonical finite decimal string for JSON Schema / structured model output.
# Scientific notation is rejected; digit/exponent bounds close amplification paths.
DECIMAL_STRING_PATTERN: Final[str] = r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$"
_DECIMAL_STRING_RE: Final[re.Pattern[str]] = re.compile(DECIMAL_STRING_PATTERN)
MAX_DECIMAL_STRING_LENGTH: Final[int] = 64
MAX_DECIMAL_SIGNIFICAND_DIGITS: Final[int] = 40
MAX_DECIMAL_ABS_EXPONENT: Final[int] = 28
DECIMAL_JSON_SCHEMA: Final[dict[str, object]] = {
    "type": "string",
    "pattern": DECIMAL_STRING_PATTERN,
    "maxLength": MAX_DECIMAL_STRING_LENGTH,
    "description": (
        "Canonical decimal string without scientific notation. Binary JSON numbers are rejected."
    ),
}

UTC_DATETIME_JSON_SCHEMA: Final[dict[str, object]] = {
    "type": "string",
    "format": "date-time",
    "pattern": r"(?:Z|[+-]\d{2}:\d{2})$",
    "description": "Timezone-aware UTC timestamp serialized with a trailing Z.",
}

# A v1 model must not silently accept a document claiming a different wire
# contract. When a backward-compatible v1.1 model is implemented, expand this
# cumulatively to Literal["1.0", "1.1"] so the newer validator still accepts
# every older v1 payload it claims to support.
SchemaVersion = Literal["1.0"]

Symbol = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z][A-Z0-9.]{0,9}$", min_length=1, max_length=10),
]

CurrencyCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z]{3}$", min_length=3, max_length=3),
]

ExchangeMic = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z0-9]{4}$", min_length=4, max_length=4),
]

SourceId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_.:-]{1,63}$", min_length=2, max_length=64),
]

StableId = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]{1,12}_[A-Za-z0-9_-]{4,128}$", min_length=8, max_length=160
    ),
]


class AssetType(StrEnum):
    """First-release tradeable asset classes."""

    EQUITY = "EQUITY"
    ETF = "ETF"


class QualityFlag(StrEnum):
    """Machine-readable data-quality markers for provenance envelopes."""

    STALE = "STALE"
    DELAYED = "DELAYED"
    PARTIAL = "PARTIAL"
    ESTIMATED = "ESTIMATED"
    CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
    MISSING_FIELDS = "MISSING_FIELDS"
    UNVERIFIED = "UNVERIFIED"


def _reject_bool(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid numbers")
    return value


def _canonical_decimal_tuple(
    value: Decimal,
) -> tuple[int, tuple[int, ...], int]:
    """Return a trailing-zero-stripped ``(sign, digits, exp)`` without rounding.

    Does not call ``Decimal.normalize()`` (context-precision sensitive). All-zero
    coefficients collapse to the canonical zero tuple ``(0, (0,), 0)``.
    """
    if not value.is_finite():
        raise ValueError("NaN and Infinity are not allowed")
    sign, digits, exp = value.as_tuple()
    if not isinstance(exp, int):
        raise ValueError("NaN and Infinity are not allowed")
    digs = list(digits)
    while digs and digs[-1] == 0:
        digs.pop()
        exp += 1
    if not digs:
        return (0, (0,), 0)
    return (sign, tuple(digs), exp)


def _rendered_fixed_point_length(sign: int, digits: tuple[int, ...], exp: int) -> int:
    """Length of the fixed-point rendering of a canonical decimal tuple."""
    if digits == (0,):
        return 1
    coefficient_len = len(digits)
    if exp >= 0:
        rendered_len = coefficient_len + exp
    else:
        place = -exp
        # "0." + fractional zeros + digits, or insert a decimal point.
        rendered_len = 2 + place if place >= coefficient_len else coefficient_len + 1
    if sign:
        rendered_len += 1
    return rendered_len


def canonicalize_decimal(value: Decimal) -> Decimal:
    """Return the unique domain representation of a finite Decimal.

    Equivalent encodings (``2`` / ``2.0``, ``0e100`` / ``0.00``) collapse to the
    same object shape. Zero is always ``Decimal(0)`` so extreme zero exponents
    cannot survive into serialization or exact arithmetic.
    """
    sign, digits, exp = _canonical_decimal_tuple(value)
    if digits == (0,):
        return Decimal(0)
    return Decimal((sign, digits, exp))


def enforce_bounded_decimal(value: Decimal) -> Decimal:
    """Canonicalize then reject values that would amplify memory or CPU.

    Pipeline (single contract for schemas, hashing, and exact order checks):

    1. Strip insignificant trailing zeros without ``normalize()``.
    2. Collapse every zero encoding to ``Decimal(0)``.
    3. Bound significand digits, absolute exponent, and fixed-point render length
       on that canonical tuple only.
    4. Return the canonical Decimal so callers never keep a dangerous exponent.

    Raw string ``maxLength`` / scientific-notation rejection happen in
    :func:`parse_decimal` before this step.
    """
    sign, digits, exp = _canonical_decimal_tuple(value)
    if digits == (0,):
        return Decimal(0)

    if len(digits) > MAX_DECIMAL_SIGNIFICAND_DIGITS:
        raise ValueError("decimal significand exceeds maximum digits")
    if abs(exp) > MAX_DECIMAL_ABS_EXPONENT:
        raise ValueError("decimal exponent exceeds maximum magnitude")
    if _rendered_fixed_point_length(sign, digits, exp) > MAX_DECIMAL_STRING_LENGTH:
        raise ValueError("decimal exceeds maximum canonical length")
    return Decimal((sign, digits, exp))


def format_canonical_decimal(value: object) -> str:
    """Fixed-point string for hashing/serialization of a bounded decimal."""
    text = format(parse_decimal(value), "f")
    return "0" if text in {"-0", "-0.0"} else text


def parse_decimal(value: object) -> Decimal:
    """Parse a domain decimal into a bounded canonical Decimal."""
    return _parse_decimal(value)


def _parse_decimal(value: object) -> Decimal:
    value = _reject_bool(value)
    if isinstance(value, Decimal):
        return enforce_bounded_decimal(value)
    if isinstance(value, int):
        # Guard before constructing huge ints into Decimal.
        if value != 0 and len(str(abs(value))) > MAX_DECIMAL_SIGNIFICAND_DIGITS:
            raise ValueError("decimal significand exceeds maximum digits")
        return enforce_bounded_decimal(Decimal(value))
    if isinstance(value, float):
        raise ValueError("binary floats are not allowed; use decimal strings")
    if isinstance(value, str):
        text = value.strip()
        if not text or len(text) > MAX_DECIMAL_STRING_LENGTH:
            raise ValueError("invalid decimal string")
        if text.lower() in {
            "nan",
            "inf",
            "+inf",
            "-inf",
            "infinity",
            "+infinity",
            "-infinity",
        }:
            raise ValueError("invalid decimal string")
        if _DECIMAL_STRING_RE.fullmatch(text) is None:
            raise ValueError("invalid decimal string")
        try:
            parsed = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal string") from exc
        return enforce_bounded_decimal(parsed)
    raise ValueError(f"unsupported decimal input type: {type(value).__name__}")


def _require_non_negative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("value must be >= 0")
    return value


def _require_positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("value must be > 0")
    return value


def _require_unit_interval(value: Decimal) -> Decimal:
    if value < 0 or value > 1:
        raise ValueError("ratio/weight must be between 0 and 1 inclusive")
    return value


def _require_signed_unit_interval(value: Decimal) -> Decimal:
    if value < -1 or value > 1:
        raise ValueError("signed ratio must be between -1 and 1 inclusive")
    return value


def _serialize_decimal(value: Decimal) -> str:
    """Serialize via the canonical form so extreme exponents cannot expand."""
    return format_canonical_decimal(value)


def _parse_utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("invalid ISO 8601 datetime") from exc
    else:
        raise ValueError(f"unsupported datetime input type: {type(value).__name__}")

    if parsed.tzinfo is None:
        raise ValueError("naive datetimes are not allowed; use timezone-aware UTC")
    return parsed.astimezone(UTC)


def _serialize_utc_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


UtcDateTime = Annotated[
    datetime,
    BeforeValidator(_parse_utc_datetime),
    PlainSerializer(_serialize_utc_datetime, return_type=str),
    WithJsonSchema(UTC_DATETIME_JSON_SCHEMA),
]

DecimalString = Annotated[
    Decimal,
    BeforeValidator(_parse_decimal),
    PlainSerializer(_serialize_decimal, return_type=str),
    WithJsonSchema(DECIMAL_JSON_SCHEMA),
]

NonNegativeDecimal = Annotated[DecimalString, AfterValidator(_require_non_negative)]
PositiveDecimal = Annotated[DecimalString, AfterValidator(_require_positive)]
Money = Annotated[DecimalString, AfterValidator(_require_non_negative)]
Price = Annotated[DecimalString, AfterValidator(_require_positive)]
Quantity = Annotated[DecimalString, AfterValidator(_require_non_negative)]
Weight = Annotated[DecimalString, AfterValidator(_require_unit_interval)]
Ratio = Annotated[DecimalString, AfterValidator(_require_unit_interval)]
SignedRatio = Annotated[DecimalString, AfterValidator(_require_signed_unit_interval)]
PnL = DecimalString  # Gains and losses may be negative.


class DomainModel(BaseModel):
    """Base model for domain contracts: frozen, forbid extras, UTC/Decimal safe."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )


class InstrumentIdentity(DomainModel):
    """Canonical tradeable instrument identity.

    A display symbol alone is never sufficient to identify a broker instrument.
    """

    instrument_id: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    symbol: Symbol
    exchange: ExchangeMic
    currency: CurrencyCode
    asset_type: AssetType
    identity_as_of: UtcDateTime
    provider: SourceId | None = None

    @field_validator("instrument_id")
    @classmethod
    def _require_non_empty_instrument_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("instrument_id must be non-empty")
        return normalized


ProvenanceTimezone = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64),
]


class Provenance(DomainModel):
    """Freshness and source metadata required on every external datum."""

    source: SourceId
    observed_at: UtcDateTime
    received_at: UtcDateTime
    timezone: ProvenanceTimezone = "UTC"
    is_delayed: bool = False
    quality_flags: tuple[QualityFlag, ...] = ()

    @field_validator("quality_flags", mode="before")
    @classmethod
    def _coerce_flags(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, (str, QualityFlag)):
            return (value,)
        return value

    @field_validator("received_at")
    @classmethod
    def _received_not_before_observed(cls, value: datetime, info: Any) -> datetime:
        observed = info.data.get("observed_at")
        if isinstance(observed, datetime) and value < observed:
            raise ValueError("received_at must be >= observed_at")
        return value


def ensure_utc(value: datetime | str) -> datetime:
    """Public helper used by tests and downstream packages."""
    return _parse_utc_datetime(value)


def decimal_json_schema() -> dict[str, object]:
    """Return the canonical JSON Schema fragment for money-like fields."""
    return dict(DECIMAL_JSON_SCHEMA)


__all__ = [
    "DECIMAL_JSON_SCHEMA",
    "DECIMAL_STRING_PATTERN",
    "MAX_DECIMAL_ABS_EXPONENT",
    "MAX_DECIMAL_SIGNIFICAND_DIGITS",
    "MAX_DECIMAL_STRING_LENGTH",
    "SCHEMA_VERSION_V1",
    "AssetType",
    "CurrencyCode",
    "DecimalString",
    "DomainModel",
    "ExchangeMic",
    "InstrumentIdentity",
    "Money",
    "NonNegativeDecimal",
    "PnL",
    "PositiveDecimal",
    "Price",
    "Provenance",
    "ProvenanceTimezone",
    "QualityFlag",
    "Quantity",
    "Ratio",
    "SchemaVersion",
    "SignedRatio",
    "SourceId",
    "StableId",
    "Symbol",
    "UtcDateTime",
    "Weight",
    "canonicalize_decimal",
    "decimal_json_schema",
    "enforce_bounded_decimal",
    "ensure_utc",
    "format_canonical_decimal",
    "parse_decimal",
]
