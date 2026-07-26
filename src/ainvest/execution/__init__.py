"""Paper broker and Robinhood MCP write path.

Execution is the only package allowed to receive broker write-tool access.
Upstream research and strategy packages must not import this package.

P03-T13 exposes the domain broker port and error taxonomy. Paper fill
simulation (P03-T14) and Robinhood MCP clients land in later tasks.
"""

from ainvest.execution.broker import (
    FORBIDDEN_REPLACE_METHOD_NAMES,
    READ_METHOD_NAMES,
    WRITE_METHOD_NAMES,
    BrokerAuthError,
    BrokerError,
    BrokerInvalidOrderError,
    BrokerRateLimitError,
    BrokerReadPort,
    BrokerRejectedError,
    BrokerSubmitOutcome,
    BrokerSubmitRequest,
    BrokerSubmitResult,
    BrokerTimeoutError,
    BrokerUnknownOutcomeError,
    BrokerWritePort,
    assert_no_replace_operation,
    assert_read_port_has_no_write_methods,
    cancel_is_confirmed_rejection,
    cancel_is_unknown_outcome,
)

__all__ = [
    "FORBIDDEN_REPLACE_METHOD_NAMES",
    "READ_METHOD_NAMES",
    "WRITE_METHOD_NAMES",
    "BrokerAuthError",
    "BrokerError",
    "BrokerInvalidOrderError",
    "BrokerRateLimitError",
    "BrokerReadPort",
    "BrokerRejectedError",
    "BrokerSubmitOutcome",
    "BrokerSubmitRequest",
    "BrokerSubmitResult",
    "BrokerTimeoutError",
    "BrokerUnknownOutcomeError",
    "BrokerWritePort",
    "assert_no_replace_operation",
    "assert_read_port_has_no_write_methods",
    "cancel_is_confirmed_rejection",
    "cancel_is_unknown_outcome",
]
