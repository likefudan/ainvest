"""Human approval flows (Telegram paper, Passkey live) and approval state.

Approval binds a canonical order hash. It sits after risk and before
execution in the control flow.
"""

from ainvest.approval.order_hash import (
    CANCEL_HASH_FIELDS,
    ORDER_HASH_FIELDS,
    attach_order_hash,
    compute_cancel_hash,
    compute_order_hash,
    parse_order_proposal,
    verify_order_hash,
)

__all__ = [
    "CANCEL_HASH_FIELDS",
    "ORDER_HASH_FIELDS",
    "attach_order_hash",
    "compute_cancel_hash",
    "compute_order_hash",
    "parse_order_proposal",
    "verify_order_hash",
]
