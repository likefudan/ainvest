"""Conformance check package."""

from __future__ import annotations

from ainvest.strategy_conformance.checks._util import CheckFn
from ainvest.strategy_conformance.checks.behavior import (
    check_determinism,
    check_exception_handling,
    check_no_future_data,
    check_paper_example,
    check_timeout_behavior,
)
from ainvest.strategy_conformance.checks.isolation import (
    check_broker_imports,
    check_network_isolation,
    check_secret_access,
)
from ainvest.strategy_conformance.checks.metadata import (
    check_api_range,
    check_hooks,
    check_metadata,
)
from ainvest.strategy_conformance.checks.schemas import check_parameters, check_signal_schemas

# Ordered for human reports: static first, then behavioral / isolation.
DEFAULT_CHECKS: tuple[tuple[str, CheckFn], ...] = (
    ("metadata", check_metadata),
    ("api_range", check_api_range),
    ("hooks", check_hooks),
    ("parameters", check_parameters),
    ("signal_schemas", check_signal_schemas),
    ("determinism", check_determinism),
    ("no_future_data", check_no_future_data),
    ("timeout", check_timeout_behavior),
    ("exceptions", check_exception_handling),
    ("paper_example", check_paper_example),
    ("broker_imports", check_broker_imports),
    ("network", check_network_isolation),
    ("secret_access", check_secret_access),
)

__all__ = [
    "DEFAULT_CHECKS",
    "check_api_range",
    "check_broker_imports",
    "check_determinism",
    "check_exception_handling",
    "check_hooks",
    "check_metadata",
    "check_network_isolation",
    "check_no_future_data",
    "check_paper_example",
    "check_parameters",
    "check_secret_access",
    "check_signal_schemas",
    "check_timeout_behavior",
]
