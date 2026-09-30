"""Pinned identity of the external `rh-mcp` Non-Trading Gateway (P06-T0).

Every current value here is transcribed from **one** authority: the
``P06-T0 rh-mcp v0.4.4 pin refresh`` execution envelope in
`docs/tasks/status.md`. Nothing here is inferred from a package version or
release prose: the tagged source, public artifacts, and artifact-shipped
manifest were independently verified. The historical
:data:`REJECTED_CHANGELOG_MANIFEST_DIGEST` remains a regression for an earlier
``v0.2.0`` documentation mismatch; it is not a candidate ``v0.4.4`` value.

Two pins answer different questions and move independently: the package
version says *which code*, the full-manifest digest says *which permission
set*. Neither is inferred from the other, and moving either is a deliberate
human decision recorded in `docs/tasks/status.md` first.

The capability name sets below are not decoration. `rh-mcp` ships **no
read-only projection** — ``RobinhoodGateway.invoke()`` accepts any *allowed*
capability, including the 11 approved non-trading mutations — so narrowing to
reads is ainvest adapter code (`IMPLEMENTATION_TODO.md` rules 20 and 32).
``tests/contract/execution/test_rh_mcp_manifest_contract.py`` recomputes the
full-manifest digest from the committed ``v0.4.4`` manifest with an
independent ``rh-canon-1`` implementation and compares every name set below
against that manifest, so these are checked values rather than prose.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

# ---------------------------------------------------------------------------
# Release and artifact identity (verified at deployment composition / startup)
# ---------------------------------------------------------------------------

#: Distribution name as published on the release, for `importlib.metadata`.
RH_MCP_DISTRIBUTION: Final = "rh-mcp"

#: Annotated tag of the reviewed release. Never a branch or a mutable tag.
PINNED_RELEASE_TAG: Final = "v0.4.4"

#: ``Version:`` recorded in the wheel's ``METADATA``.
PINNED_PACKAGE_VERSION: Final = "0.4.4"

#: Source provenance only. A source commit is evidence, never the consumable
#: dependency — the consumable dependency is the release artifact below.
PINNED_SOURCE_COMMIT: Final = "e5fdac4e71da916d86b50c1d50efc66096a46242"

PINNED_WHEEL_FILENAME: Final = "rh_mcp-0.4.4-py3-none-any.whl"
PINNED_WHEEL_SHA256: Final = "357851cefd2d181cf45ddcfd07b8b0b166c62c110f0df6647bf2ae09a5680c13"
PINNED_WHEEL_SIZE_BYTES: Final = 267_148

PINNED_SDIST_FILENAME: Final = "rh_mcp-0.4.4.tar.gz"
PINNED_SDIST_SHA256: Final = "b14dce333c3737d1007cc0877a6967f19f7f9ec16fc8f5b2586f23627c4239a7"
PINNED_SDIST_SIZE_BYTES: Final = 572_873

# ---------------------------------------------------------------------------
# Reviewed capability manifest (verified at readiness) and result envelope
# ---------------------------------------------------------------------------

PINNED_MANIFEST_FORMAT_VERSION: Final = "1.2"
PINNED_CANONICALIZATION_VERSION: Final = "rh-canon-1"
PINNED_DIGEST_ALGORITHM: Final = "sha256"

PINNED_MANIFEST_VERSION: Final = "2026.09.30"
SUPPORTED_MANIFEST_VERSIONS: Final[frozenset[str]] = frozenset({PINNED_MANIFEST_VERSION})

EXPECTED_MANIFEST_DIGEST: Final = (
    "sha256:b73100b38148174065064f6eced9040833900162de344a6d0eaa4c3bbbfff68d"
)
PINNED_PROVIDER_SURFACE_DIGEST: Final = (
    "sha256:d28615cb6b6889a747d1501bd7ed44af068e05cbd624a7a2a999c7c369704aa3"
)

#: The digest `rh-mcp`'s changelog prints beside manifest ``2026.08.03.1``.
#: It belongs to manifest ``2026.08.05`` and must never be accepted here.
REJECTED_CHANGELOG_MANIFEST_DIGEST: Final = (
    "sha256:49b7218278fc2aebb1a040c89b8c94f60750afe142d6b728e88771944a88093a"
)

PINNED_ENVELOPE_VERSION: Final = "1.0"
SUPPORTED_ENVELOPE_VERSIONS: Final[frozenset[str]] = frozenset({PINNED_ENVELOPE_VERSION})

#: The nine result-envelope keys `rh-mcp` ``DESIGN.md`` §12.5 pins for
#: ``envelope_version`` ``1.x``. Verified as an exact set, so a key appearing
#: or disappearing is drift rather than a silently absorbed change.
RESULT_ENVELOPE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "envelope_version",
        "manifest_version",
        "manifest_digest",
        "capability",
        "schema_digest",
        "result_digest",
        "observed_at",
        "data",
        "warnings",
    }
)

#: The four readiness fields this adapter consumes. Deliberately a subset:
#: ``rh-mcp`` also reports ``findings``, whose safe-tool labels are
#: provider-derived, and this adapter never retains them. Equally deliberately
#: there is **no** package-version field here — neither the readiness report
#: nor the result envelope carries one, and requiring one would fail closed
#: against a conforming gateway.
READINESS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "ready",
        "manifest_version",
        "manifest_digest",
        "expected_manifest_digest",
    }
)

# ---------------------------------------------------------------------------
# The reviewed manifest's six exact sets (rule 32: 35 / 11 / 10 / 4 / 4 / 12)
# ---------------------------------------------------------------------------

#: The 35 ``allowed`` capabilities with ``mutates=false``.
MANIFEST_READ_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "get_accounts",
        "get_earnings_calendar",
        "get_earnings_results",
        "get_equity_fundamentals",
        "get_equity_historicals",
        "get_equity_orders",
        "get_equity_positions",
        "get_equity_price_book",
        "get_equity_quotes",
        "get_equity_tax_lots",
        "get_equity_technical_indicators",
        "get_equity_tradability",
        "get_financials",
        "get_index_historicals",
        "get_index_quotes",
        "get_indexes",
        "get_limited_margin_upgrade_info",
        "get_option_chains",
        "get_option_historicals",
        "get_option_instruments",
        "get_option_level_upgrade_info",
        "get_option_orders",
        "get_option_positions",
        "get_option_quotes",
        "get_option_watchlist",
        "get_pnl_trade_history",
        "get_popular_watchlists",
        "get_portfolio",
        "get_realized_pnl",
        "get_scanner_filter_specs",
        "get_scans",
        "get_watchlist_items",
        "get_watchlists",
        "run_scan",
        "search",
    }
)

#: The 11 ``allowed`` capabilities with ``mutates=true``. Reviewed and
#: non-trading, and still forbidden to P06-T0: the boundary `rh-mcp` enforces
#: is "no trading", not "no writes", so excluding these is ainvest's job.
APPROVED_NON_TRADING_MUTATIONS: Final[frozenset[str]] = frozenset(
    {
        "add_option_to_watchlist",
        "add_to_watchlist",
        "create_scan",
        "create_watchlist",
        "follow_watchlist",
        "remove_from_watchlist",
        "remove_option_from_watchlist",
        "unfollow_watchlist",
        "update_scan_config",
        "update_scan_filters",
        "update_watchlist",
    }
)

#: The 10 ``denied`` trading capabilities. Every one is ``mutates=true``.
DENIED_TRADING_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "cancel_crypto_order",
        "cancel_equity_order",
        "cancel_option_exercise",
        "cancel_option_order",
        "exercise_option",
        "place_equity_order",
        "place_crypto_order",
        "place_option_order",
        "review_equity_order",
        "review_option_order",
    }
)

#: Four denied alert-management mutations. These are not trading operations,
#: but they remain outside ainvest's read-only projection.
DENIED_ALERT_MUTATIONS: Final[frozenset[str]] = frozenset(
    {
        "create_alert",
        "delete_alert",
        "mark_alerts_read",
        "update_alert",
    }
)

#: The 4 provider reads recorded for drift accounting but denied to every
#: ainvest caller. They are non-mutating; denial is a permission decision, not
#: an assertion that the provider operation is a trade. A future SEC feature
#: requires its own explicit permission review and a new named projection.
DENIED_SEC_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "get_sec_filing",
        "get_sec_filing_facts",
        "get_sec_filing_facts_catalog",
        "get_sec_filing_index",
    }
)

#: Twelve non-mutating crypto/alert/scanner/reference capabilities. Preview
#: operations are trade- or write-adjacent and all twelve remain denied; recording them grants no
#: adapter method and does not widen :class:`ReadCapability`.
DENIED_NON_MUTATING_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "get_alert_log",
        "get_alerts",
        "get_crypto_account_onboarding_info",
        "get_crypto_orders",
        "get_crypto_positions",
        "get_crypto_quotes",
        "get_currency_pairs",
        "get_equity_analyst_ratings",
        "get_politician_trades",
        "get_scanner_datapoints",
        "preview_crypto_order",
        "preview_scan",
    }
)

EXPECTED_READ_CAPABILITY_COUNT: Final = 35
EXPECTED_APPROVED_MUTATION_COUNT: Final = 11
EXPECTED_DENIED_CAPABILITY_COUNT: Final = 10
EXPECTED_DENIED_ALERT_MUTATION_COUNT: Final = 4
EXPECTED_DENIED_SEC_CAPABILITY_COUNT: Final = 4
EXPECTED_DENIED_NON_MUTATING_CAPABILITY_COUNT: Final = 12
EXPECTED_MANIFEST_ENTRY_COUNT: Final = 76


class ReadCapability(StrEnum):
    """The ainvest read projection: the only capabilities this adapter invokes.

    A strict subset of :data:`MANIFEST_READ_CAPABILITIES` — the intersection of
    the manifest's 35 ``mutates=false`` entries with what ainvest actually
    needs for the Non-Trading Preview (`design.md` §5.1: quotes, price book,
    historicals, fundamentals, tradability, and the account/portfolio/position/
    order reads). Membership is closed at import time and each member is
    reachable only through its own named ``read_*`` method on the adapter, so
    no caller-supplied capability name ever reaches the gateway.
    """

    GET_ACCOUNTS = "get_accounts"
    GET_EQUITY_FUNDAMENTALS = "get_equity_fundamentals"
    GET_EQUITY_HISTORICALS = "get_equity_historicals"
    GET_EQUITY_ORDERS = "get_equity_orders"
    GET_EQUITY_POSITIONS = "get_equity_positions"
    GET_EQUITY_PRICE_BOOK = "get_equity_price_book"
    GET_EQUITY_QUOTES = "get_equity_quotes"
    GET_EQUITY_TRADABILITY = "get_equity_tradability"
    GET_FINANCIALS = "get_financials"
    GET_PORTFOLIO = "get_portfolio"


#: Exactly the wire strings :class:`ReadCapability` is allowed to carry.
#: Held separately from the enum on purpose: comparing an enum against itself
#: proves only that ``StrEnum`` works, so this literal table is what a rename
#: has to survive.
READ_CAPABILITY_WIRE_NAMES: Final[dict[str, str]] = {
    "GET_ACCOUNTS": "get_accounts",
    "GET_EQUITY_FUNDAMENTALS": "get_equity_fundamentals",
    "GET_EQUITY_HISTORICALS": "get_equity_historicals",
    "GET_EQUITY_ORDERS": "get_equity_orders",
    "GET_EQUITY_POSITIONS": "get_equity_positions",
    "GET_EQUITY_PRICE_BOOK": "get_equity_price_book",
    "GET_EQUITY_QUOTES": "get_equity_quotes",
    "GET_EQUITY_TRADABILITY": "get_equity_tradability",
    "GET_FINANCIALS": "get_financials",
    "GET_PORTFOLIO": "get_portfolio",
}

# ---------------------------------------------------------------------------
# ainvest-owned bounds. Defence in depth: `rh-mcp` bounds its own responses
# under its §8 budgets, but this adapter is the trust boundary and re-checks
# what it accepts rather than inheriting a remote guarantee.
# ---------------------------------------------------------------------------

MAX_PAYLOAD_DEPTH: Final = 16
MAX_PAYLOAD_NODES: Final = 100_000
MAX_PAYLOAD_STRING_LENGTH: Final = 262_144
MAX_ENVELOPE_WARNINGS: Final = 8
MAX_WARNING_LENGTH: Final = 512
MAX_LOGGED_DURATION_MS: Final = 600_000

__all__ = [
    "APPROVED_NON_TRADING_MUTATIONS",
    "DENIED_ALERT_MUTATIONS",
    "DENIED_NON_MUTATING_CAPABILITIES",
    "DENIED_SEC_CAPABILITIES",
    "DENIED_TRADING_CAPABILITIES",
    "EXPECTED_APPROVED_MUTATION_COUNT",
    "EXPECTED_DENIED_ALERT_MUTATION_COUNT",
    "EXPECTED_DENIED_CAPABILITY_COUNT",
    "EXPECTED_DENIED_NON_MUTATING_CAPABILITY_COUNT",
    "EXPECTED_DENIED_SEC_CAPABILITY_COUNT",
    "EXPECTED_MANIFEST_DIGEST",
    "EXPECTED_MANIFEST_ENTRY_COUNT",
    "EXPECTED_READ_CAPABILITY_COUNT",
    "MANIFEST_READ_CAPABILITIES",
    "MAX_ENVELOPE_WARNINGS",
    "MAX_LOGGED_DURATION_MS",
    "MAX_PAYLOAD_DEPTH",
    "MAX_PAYLOAD_NODES",
    "MAX_PAYLOAD_STRING_LENGTH",
    "MAX_WARNING_LENGTH",
    "PINNED_CANONICALIZATION_VERSION",
    "PINNED_DIGEST_ALGORITHM",
    "PINNED_ENVELOPE_VERSION",
    "PINNED_MANIFEST_FORMAT_VERSION",
    "PINNED_MANIFEST_VERSION",
    "PINNED_PACKAGE_VERSION",
    "PINNED_PROVIDER_SURFACE_DIGEST",
    "PINNED_RELEASE_TAG",
    "PINNED_SDIST_FILENAME",
    "PINNED_SDIST_SHA256",
    "PINNED_SDIST_SIZE_BYTES",
    "PINNED_SOURCE_COMMIT",
    "PINNED_WHEEL_FILENAME",
    "PINNED_WHEEL_SHA256",
    "PINNED_WHEEL_SIZE_BYTES",
    "READINESS_KEYS",
    "READ_CAPABILITY_WIRE_NAMES",
    "REJECTED_CHANGELOG_MANIFEST_DIGEST",
    "RESULT_ENVELOPE_KEYS",
    "RH_MCP_DISTRIBUTION",
    "SUPPORTED_ENVELOPE_VERSIONS",
    "SUPPORTED_MANIFEST_VERSIONS",
    "ReadCapability",
]
