"""Canonical extreme-exponent / trailing-zero decimal fixtures for unit tests.

Layer-specific suites (orders, order hash) should import these constants for
fail-closed proofs rather than re-listing the full Money/parse_decimal matrix.
"""

from __future__ import annotations

from decimal import Decimal

# Compact scientific encodings rejected by the string pattern / parse_decimal.
SCIENTIFIC_NOTATION_STRINGS: tuple[str, ...] = ("1e10", "1E+6", "1e1000000")

EXTREME_HUGE: Decimal = Decimal("1e1000000")
EXTREME_TINY: Decimal = Decimal("1e-1000000")

# Zero encodings that must collapse to Decimal(0) before serialize / power ops.
EXTREME_ZERO_ENCODINGS: tuple[Decimal, ...] = (
    Decimal("0e1000000"),
    Decimal("0e-1000000"),
    Decimal("-0e999999"),
)

# Trailing-zero equivalents that remain inside digit/length bounds.
PADDED_ONE: str = "1." + ("0" * 40)
TINY_FIXED: str = "0." + ("0" * 27) + "1"  # 1e-28 fixed-point
TINY_PADDED: str = TINY_FIXED + "00"
