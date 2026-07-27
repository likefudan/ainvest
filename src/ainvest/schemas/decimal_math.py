"""Exact Decimal coefficient and tick-increment arithmetic.

Shared by order economics checks and the position sizer so both use the same
integer-scaled floor/ceil/coeff helpers without Decimal context rounding.
"""

from __future__ import annotations

from decimal import Decimal

from ainvest.schemas.common import canonicalize_decimal, parse_decimal

ZERO = Decimal("0")


def decimal_coeff_exp(value: Decimal) -> tuple[int, int]:
    """Return ``(coefficient, exponent)`` for exact integer-scaled arithmetic.

    Always re-canonicalizes so helpers never operate on extreme-exponent zeros
    or unstripped trailing zeros.
    """
    canonical = parse_decimal(value)
    sign, digits, exp = canonical.as_tuple()
    if not isinstance(exp, int):
        raise ValueError("NaN and Infinity are not allowed")
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    if sign:
        coefficient = -coefficient
    return coefficient, exp


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Largest non-negative multiple of ``increment`` that is ``<= value``."""
    value = parse_decimal(value)
    increment = parse_decimal(increment)
    if increment <= ZERO:
        raise ValueError("increment must be > 0")
    if value <= ZERO:
        return ZERO
    # Exact integer arithmetic avoids Decimal context rounding.
    value_coeff, value_exp = decimal_coeff_exp(value)
    inc_coeff, inc_exp = decimal_coeff_exp(increment)
    scale = min(value_exp, inc_exp)
    value_int = value_coeff * (10 ** (value_exp - scale))
    inc_int = inc_coeff * (10 ** (inc_exp - scale))
    multiples = value_int // inc_int
    result_exp = scale
    return canonicalize_decimal(Decimal((0, _digits(multiples * inc_int), result_exp)))


def ceil_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Smallest positive multiple of ``increment`` that is ``>= value``."""
    value = parse_decimal(value)
    increment = parse_decimal(increment)
    if increment <= ZERO:
        raise ValueError("increment must be > 0")
    if value <= ZERO:
        return increment
    floored = floor_to_increment(value, increment)
    if floored == value:
        return floored
    return canonicalize_decimal(floored + increment)


def _digits(value: int) -> tuple[int, ...]:
    if value < 0:
        raise ValueError("digit coefficient must be non-negative")
    if value == 0:
        return (0,)
    return tuple(int(ch) for ch in str(value))


__all__ = [
    "ceil_to_increment",
    "decimal_coeff_exp",
    "floor_to_increment",
]
