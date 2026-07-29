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
from ainvest.approval.service import (
    DEFAULT_APPROVAL_TTL,
    MAX_APPROVAL_TTL,
    MIN_APPROVAL_TTL,
    ApprovalService,
    ApprovalServiceError,
    IssuedApprovalChallenge,
)
from ainvest.approval.tokens import (
    APPROVAL_TOKEN_BYTES,
    APPROVAL_TOKEN_HASH_DOMAIN,
    OpaqueApprovalToken,
    generate_approval_token,
    hash_approval_token,
)

__all__ = [
    "APPROVAL_TOKEN_BYTES",
    "APPROVAL_TOKEN_HASH_DOMAIN",
    "CANCEL_HASH_FIELDS",
    "DEFAULT_APPROVAL_TTL",
    "MAX_APPROVAL_TTL",
    "MIN_APPROVAL_TTL",
    "ORDER_HASH_FIELDS",
    "ApprovalService",
    "ApprovalServiceError",
    "IssuedApprovalChallenge",
    "OpaqueApprovalToken",
    "attach_order_hash",
    "compute_cancel_hash",
    "compute_order_hash",
    "generate_approval_token",
    "hash_approval_token",
    "parse_order_proposal",
    "verify_order_hash",
]
