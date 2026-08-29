"""Unit tests for the pinned `rh-mcp` read adapter (P06-T0).

Each test names the change it is supposed to break. Where that is not obvious
from the assertion, the docstring says it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from ainvest.execution.robinhood.errors import (
    GATEWAY_READ_ERROR_WIRE_NAMES,
    RETRYABLE_CODES,
    RH_MCP_ERROR_CODE_MAP,
    RH_MCP_ERROR_CODES,
    SANITIZED_MESSAGES,
    UNMAPPED_GATEWAY_FAILURE,
    GatewayReadError,
    GatewayReadErrorCode,
    translate_gateway_failure,
)
from ainvest.execution.robinhood.pins import (
    APPROVED_NON_TRADING_MUTATIONS,
    DENIED_TRADING_CAPABILITIES,
    EXPECTED_MANIFEST_DIGEST,
    MANIFEST_READ_CAPABILITIES,
    MAX_ENVELOPE_WARNINGS,
    MAX_LOGGED_DURATION_MS,
    MAX_PAYLOAD_DEPTH,
    MAX_PAYLOAD_STRING_LENGTH,
    MAX_WARNING_LENGTH,
    PINNED_MANIFEST_VERSION,
    REJECTED_CHANGELOG_MANIFEST_DIGEST,
    ReadCapability,
)
from ainvest.execution.robinhood.read_client import (
    READ_LOG_EVENT,
    READ_LOG_FIELDS_ERROR,
    READ_LOG_FIELDS_OK,
    READ_REJECTION_WIRE_NAMES,
    ReadRejection,
    RobinhoodReadClient,
    emit_read_log,
    verify_read_projection,
    verify_readiness,
)
from execution.robinhood.gateway_fakes import (
    INJECTED_PROSE,
    SAMPLE_RESULT_DIGEST,
    SAMPLE_SCHEMA_DIGEST,
    FakeCapability,
    FakeGateway,
    FakeGatewayError,
    PoisonPayload,
    RecordingSink,
    RenderedDocument,
    envelope_document,
    manifest_capabilities,
    readiness_document,
    run,
)

# ---------------------------------------------------------------------------
# Wire strings
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_error_and_rejection_wire_strings_are_pinned_by_literal_table() -> None:
    """Renaming a member or its value must fail here.

    ``str(member) == member.value`` would only prove ``StrEnum`` works. These
    tables are literal, so a rename on either side is a diff a reader sees.
    """
    assert {member.name: member.value for member in GatewayReadErrorCode} == (
        GATEWAY_READ_ERROR_WIRE_NAMES
    )
    assert {member.name: member.value for member in ReadRejection} == READ_REJECTION_WIRE_NAMES
    assert set(SANITIZED_MESSAGES) == set(GatewayReadErrorCode)
    assert frozenset({GatewayReadErrorCode.TIMEOUT}) == RETRYABLE_CODES


@pytest.mark.unit
def test_nine_pinned_gateway_error_codes_all_map_to_an_ainvest_code() -> None:
    """`rh-mcp` DESIGN.md §12.5 pins nine wire strings; all nine are handled."""
    assert len(RH_MCP_ERROR_CODES) == 9
    assert {
        "auth_required",
        "not_ready",
        "capability_denied",
        "input_invalid",
        "provider_error",
        "timeout",
        "response_too_large",
        "protocol_error",
        "configuration_error",
    } == RH_MCP_ERROR_CODES
    assert set(RH_MCP_ERROR_CODE_MAP) == RH_MCP_ERROR_CODES


@pytest.mark.unit
@pytest.mark.parametrize("gateway_code", sorted(RH_MCP_ERROR_CODES))
def test_each_gateway_code_translates_without_carrying_gateway_text(
    gateway_code: str,
) -> None:
    error = translate_gateway_failure(
        FakeGatewayError(gateway_code), capability="get_equity_quotes"
    )

    assert error.code is RH_MCP_ERROR_CODE_MAP[gateway_code]
    assert error.message == SANITIZED_MESSAGES[error.code]
    assert INJECTED_PROSE not in f"{error}{error!r}{error.args}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        FakeGatewayError("a_code_that_does_not_exist"),
        RuntimeError("provider blew up"),
        TimeoutError(),
    ],
)
def test_unrecognized_failure_fails_closed_and_is_not_retryable(exc: BaseException) -> None:
    """An unknown failure must never become 'retry' or 'try another provider'."""
    error = translate_gateway_failure(exc)

    assert error.code is UNMAPPED_GATEWAY_FAILURE
    assert error.retryable is False


# ---------------------------------------------------------------------------
# Read projection: the obligation `rh-mcp` does not carry for ainvest
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reviewed_listing_verifies_and_reports_the_36_11_8_split() -> None:
    verification = verify_read_projection(manifest_capabilities())

    assert verification.manifest_read_capabilities == MANIFEST_READ_CAPABILITIES
    assert verification.approved_non_trading_mutations == APPROVED_NON_TRADING_MUTATIONS
    assert verification.denied_trading_capabilities == DENIED_TRADING_CAPABILITIES
    assert len(verification.manifest_read_capabilities) == 36
    assert len(verification.approved_non_trading_mutations) == 11
    assert len(verification.denied_trading_capabilities) == 8


@pytest.mark.unit
def test_read_projection_is_a_strict_subset_of_the_36_and_touches_no_mutation() -> None:
    """The projection may only ever narrow, never widen."""
    projection = {member.value for member in ReadCapability}

    assert projection < MANIFEST_READ_CAPABILITIES
    assert len(projection) == 10
    assert projection.isdisjoint(APPROVED_NON_TRADING_MUTATIONS)
    assert projection.isdisjoint(DENIED_TRADING_CAPABILITIES)


@pytest.mark.unit
def test_limited_margin_upgrade_read_has_no_adapter_entry_point() -> None:
    """v0.4.1 reviews the provider tool without making it callable here."""
    capability = "get_limited_margin_upgrade_info"

    assert capability in MANIFEST_READ_CAPABILITIES
    assert capability not in {member.value for member in ReadCapability}
    assert not hasattr(RobinhoodReadClient, "read_limited_margin_upgrade_info")


@pytest.mark.unit
def test_equity_news_read_has_no_adapter_entry_point() -> None:
    """The sole v0.4.1 provider addition does not widen ainvest's projection."""
    capability = "get_equity_news"

    assert capability in MANIFEST_READ_CAPABILITIES
    assert capability not in {member.value for member in ReadCapability}
    assert not hasattr(RobinhoodReadClient, "read_equity_news")


@pytest.mark.unit
def test_allowlisted_capability_reclassified_as_a_mutation_fails_closed() -> None:
    """The central assertion of `IMPLEMENTATION_TODO.md` rules 20 and 32.

    `rh-mcp` ships no read-only projection: ``invoke()`` accepts any *allowed*
    capability, mutating or not. A manifest that reclassified one of our reads
    as ``mutates=true`` would silently widen ainvest's surface unless this
    refuses.
    """
    listing = [
        FakeCapability(entry.capability, entry.read_allowed, True)
        if entry.capability == ReadCapability.GET_PORTFOLIO.value
        else entry
        for entry in manifest_capabilities()
    ]

    with pytest.raises(GatewayReadError) as caught:
        verify_read_projection(listing)

    assert caught.value.rejection == ReadRejection.PROJECTION_MUTATES.value
    assert caught.value.capability == "get_portfolio"


@pytest.mark.unit
def test_allowlisted_capability_flipped_to_denied_fails_closed() -> None:
    listing = [
        FakeCapability(entry.capability, False, True)
        if entry.capability == ReadCapability.GET_EQUITY_QUOTES.value
        else entry
        for entry in manifest_capabilities()
    ]

    with pytest.raises(GatewayReadError) as caught:
        verify_read_projection(listing)

    assert caught.value.rejection == ReadRejection.PROJECTION_NOT_ALLOWED.value
    assert caught.value.capability == "get_equity_quotes"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("listing_kwargs", "expected"),
    [
        (
            {"reads": MANIFEST_READ_CAPABILITIES - {"get_indexes"}},
            ReadRejection.ENTRY_COUNT_MISMATCH,
        ),
        (
            {"reads": MANIFEST_READ_CAPABILITIES | {"get_something_new"}},
            ReadRejection.ENTRY_COUNT_MISMATCH,
        ),
        (
            {"mutations": APPROVED_NON_TRADING_MUTATIONS - {"create_scan"}},
            ReadRejection.ENTRY_COUNT_MISMATCH,
        ),
        (
            {"denied": DENIED_TRADING_CAPABILITIES - {"place_equity_order"}},
            ReadRejection.ENTRY_COUNT_MISMATCH,
        ),
    ],
)
def test_manifest_count_drift_fails_closed(
    listing_kwargs: dict[str, frozenset[str]],
    expected: ReadRejection,
) -> None:
    """35 / 11 / 8 is executable here, not just recorded in prose."""
    with pytest.raises(GatewayReadError) as caught:
        verify_read_projection(manifest_capabilities(**listing_kwargs))

    assert caught.value.rejection == expected.value


@pytest.mark.unit
def test_capability_moved_between_dispositions_fails_closed() -> None:
    """Entry count stays 54, so only the name sets can catch this."""
    listing = manifest_capabilities(
        reads=MANIFEST_READ_CAPABILITIES - {"get_watchlists"},
        mutations=APPROVED_NON_TRADING_MUTATIONS | {"get_watchlists"},
    )

    with pytest.raises(GatewayReadError) as caught:
        verify_read_projection(listing)

    assert caught.value.rejection == ReadRejection.READ_SET_MISMATCH.value


@pytest.mark.unit
def test_denied_entry_that_claims_not_to_mutate_fails_closed() -> None:
    listing = [
        FakeCapability(entry.capability, False, False)
        if entry.capability == "place_equity_order"
        else entry
        for entry in manifest_capabilities()
    ]

    with pytest.raises(GatewayReadError) as caught:
        verify_read_projection(listing)

    assert caught.value.rejection == ReadRejection.DENIED_ENTRY_NOT_MUTATING.value


@pytest.mark.unit
def test_duplicate_and_malformed_listing_entries_fail_closed() -> None:
    duplicated = [*manifest_capabilities(), FakeCapability("get_portfolio", True, False)]
    with pytest.raises(GatewayReadError) as duplicate:
        verify_read_projection(duplicated)
    assert duplicate.value.rejection == ReadRejection.DUPLICATE_CAPABILITY.value

    with pytest.raises(GatewayReadError) as malformed:
        verify_read_projection([FakeCapability("get_portfolio", True, None)])  # type: ignore[arg-type]
    assert malformed.value.rejection == ReadRejection.MALFORMED_LISTING.value


# ---------------------------------------------------------------------------
# Readiness: manifest version and full-manifest digest
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_readiness_verifies_the_pinned_version_and_digest() -> None:
    verification = verify_readiness(readiness_document())

    assert verification.manifest_version == PINNED_MANIFEST_VERSION
    assert verification.manifest_digest == EXPECTED_MANIFEST_DIGEST


@pytest.mark.unit
def test_readiness_accepts_a_report_with_no_package_version_field() -> None:
    """The envelope forbids requiring one; a conforming gateway sends none."""
    document = readiness_document()

    assert not any("package" in key or key == "version" for key in document)
    assert verify_readiness(document).manifest_digest == EXPECTED_MANIFEST_DIGEST


@pytest.mark.unit
def test_readiness_rejects_the_digest_rh_mcps_changelog_prints() -> None:
    """The specific documented hazard.

    `rh-mcp`'s CHANGELOG prints ``sha256:49b7218…`` beside manifest
    ``2026.08.03.1``; that digest belongs to manifest ``2026.08.05``. A
    consumer that pinned it would fail readiness at every startup — so this
    asserts the wrong value is refused *by this adapter's pin*, in both the
    direction a gateway could report it and the direction we could pin it.
    """
    # Widened to `str`: both are `Final` literals, so mypy folds the comparison
    # and reports `comparison-overlap`. It proves at type level what this
    # asserts at value level, and deleting the assertion to satisfy mypy would
    # remove the only guard against the two constants being made equal.
    rejected: str = REJECTED_CHANGELOG_MANIFEST_DIGEST
    assert rejected != EXPECTED_MANIFEST_DIGEST

    with pytest.raises(GatewayReadError) as caught:
        verify_readiness(
            readiness_document(
                manifest_digest=REJECTED_CHANGELOG_MANIFEST_DIGEST,
                expected_manifest_digest=REJECTED_CHANGELOG_MANIFEST_DIGEST,
            )
        )

    assert caught.value.rejection == ReadRejection.READINESS_MANIFEST_DIGEST_MISMATCH.value


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"manifest_version": "2026.08.05"},
            ReadRejection.READINESS_MANIFEST_VERSION_UNSUPPORTED,
        ),
        (
            {"manifest_digest": "sha256:" + "0" * 64},
            ReadRejection.READINESS_MANIFEST_DIGEST_MISMATCH,
        ),
        (
            {"expected_manifest_digest": "sha256:" + "0" * 64},
            ReadRejection.READINESS_EXPECTED_DIGEST_MISMATCH,
        ),
        ({"ready": False}, ReadRejection.GATEWAY_NOT_READY),
        ({"ready": "yes"}, ReadRejection.MALFORMED_READINESS),
    ],
)
def test_readiness_drift_fails_closed(
    overrides: dict[str, Any],
    expected: ReadRejection,
) -> None:
    with pytest.raises(GatewayReadError) as caught:
        verify_readiness(readiness_document(**overrides))

    assert caught.value.code is GatewayReadErrorCode.NOT_READY
    assert caught.value.rejection == expected.value


@pytest.mark.unit
def test_readiness_missing_a_required_field_fails_closed() -> None:
    document = readiness_document()
    document.pop("manifest_digest")

    with pytest.raises(GatewayReadError) as caught:
        verify_readiness(document)

    assert caught.value.rejection == ReadRejection.MALFORMED_READINESS.value


@pytest.mark.unit
def test_readiness_is_read_through_to_json_dict_without_importing_the_sdk() -> None:
    assert (
        verify_readiness(RenderedDocument(readiness_document())).manifest_digest
        == EXPECTED_MANIFEST_DIGEST
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _verified_client(
    gateway: FakeGateway,
    sink: RecordingSink | None = None,
) -> RobinhoodReadClient:
    """A started client. A sink is always supplied so no test writes a log."""
    client = RobinhoodReadClient(
        gateway,
        log_sink=RecordingSink() if sink is None else sink,
        clock=_fixed_clock(),
    )
    run(client.verify_startup())
    if sink is not None:
        sink.records.clear()
    return client


def _fixed_clock() -> Any:
    ticks = iter([1.0, 1.25, 2.0, 2.25, 3.0, 3.25, 4.0, 4.25, 5.0, 5.25])

    def clock() -> float:
        return next(ticks)

    return clock


@pytest.mark.unit
def test_read_returns_a_validated_result_and_sends_only_our_own_wire_name() -> None:
    gateway = FakeGateway()
    client = _verified_client(gateway)

    result = run(client.read_equity_quotes({"symbols": ["ZZZZ"]}))

    assert gateway.invocations == [("get_equity_quotes", {"symbols": ["ZZZZ"]})]
    assert result.capability == "get_equity_quotes"
    assert result.manifest_digest == EXPECTED_MANIFEST_DIGEST
    assert result.schema_digest == SAMPLE_SCHEMA_DIGEST
    assert result.result_digest == SAMPLE_RESULT_DIGEST
    assert result.payload == {"results": [{"symbol": "ZZZZ", "last_trade_price": "1.00"}]}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "capability"),
    [
        ("read_accounts", ReadCapability.GET_ACCOUNTS),
        ("read_portfolio", ReadCapability.GET_PORTFOLIO),
        ("read_equity_positions", ReadCapability.GET_EQUITY_POSITIONS),
        ("read_equity_orders", ReadCapability.GET_EQUITY_ORDERS),
        ("read_equity_quotes", ReadCapability.GET_EQUITY_QUOTES),
        ("read_equity_price_book", ReadCapability.GET_EQUITY_PRICE_BOOK),
        ("read_equity_historicals", ReadCapability.GET_EQUITY_HISTORICALS),
        ("read_equity_fundamentals", ReadCapability.GET_EQUITY_FUNDAMENTALS),
        ("read_equity_tradability", ReadCapability.GET_EQUITY_TRADABILITY),
        ("read_financials", ReadCapability.GET_FINANCIALS),
    ],
)
def test_each_named_read_operation_sends_its_own_capability(
    method: str,
    capability: ReadCapability,
) -> None:
    """A named method wired to the wrong capability must fail here."""
    gateway = FakeGateway()
    gateway.envelope = envelope_document(capability.value)
    client = _verified_client(gateway)

    result = run(getattr(client, method)())

    assert gateway.invocations == [(capability.value, None)]
    assert result.capability == capability.value


@pytest.mark.unit
def test_adapter_exposes_named_reads_and_no_generic_invoke() -> None:
    """`design.md` §5.1: no generic ``invoke(capability, arguments)`` surface.

    Adding a public passthrough — the exact widening this forbids — fails here.
    """
    public = {name for name in dir(RobinhoodReadClient) if not name.startswith("_")}

    assert public == {
        "read_accounts",
        "read_equity_fundamentals",
        "read_equity_historicals",
        "read_equity_orders",
        "read_equity_positions",
        "read_equity_price_book",
        "read_equity_quotes",
        "read_equity_tradability",
        "read_financials",
        "read_portfolio",
        "startup",
        "verify_startup",
    }
    assert {name for name in public if name.startswith("read_")} == {
        f"read_{member.value.removeprefix('get_')}" for member in ReadCapability
    }


@pytest.mark.unit
def test_read_before_startup_verification_fails_closed() -> None:
    client = RobinhoodReadClient(FakeGateway())

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_portfolio())

    assert caught.value.code is GatewayReadErrorCode.NOT_READY
    assert caught.value.rejection == ReadRejection.STARTUP_NOT_VERIFIED.value


@pytest.mark.unit
def test_startup_failure_leaves_reads_refused() -> None:
    gateway = FakeGateway(readiness_result=readiness_document(ready=False))
    client = RobinhoodReadClient(gateway)

    with pytest.raises(GatewayReadError):
        run(client.verify_startup())

    assert client.startup is None
    with pytest.raises(GatewayReadError) as caught:
        run(client.read_portfolio())
    assert caught.value.rejection == ReadRejection.STARTUP_NOT_VERIFIED.value
    assert gateway.invocations == []


class _ExplodingDocument:
    def to_json_dict(self) -> dict[str, Any]:
        raise RuntimeError(INJECTED_PROSE)


class _ExplodingReadiness(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        raise RuntimeError(INJECTED_PROSE)

    def __iter__(self) -> Any:
        raise RuntimeError(INJECTED_PROSE)

    def __len__(self) -> int:
        raise RuntimeError(INJECTED_PROSE)


@pytest.mark.unit
@pytest.mark.parametrize(
    "gateway",
    [
        FakeGateway(capabilities_raises=RuntimeError(INJECTED_PROSE)),
        FakeGateway(readiness_raises=RuntimeError(INJECTED_PROSE)),
        FakeGateway(readiness_result=_ExplodingDocument()),
        FakeGateway(readiness_result=_ExplodingReadiness()),
    ],
    ids=["capabilities", "readiness", "readiness-rendering", "readiness-validation"],
)
def test_unexpected_startup_exceptions_are_sanitized_and_leave_startup_unset(
    gateway: FakeGateway,
) -> None:
    client = RobinhoodReadClient(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client.verify_startup())

    assert INJECTED_PROSE not in f"{caught.value}{caught.value!r}{caught.value.args}"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert client.startup is None


@pytest.mark.unit
def test_existing_sanitized_startup_error_is_preserved() -> None:
    expected = GatewayReadError(GatewayReadErrorCode.NOT_READY)
    client = RobinhoodReadClient(FakeGateway(capabilities_raises=expected))

    with pytest.raises(GatewayReadError) as caught:
        run(client.verify_startup())

    assert caught.value is expected
    assert client.startup is None


@pytest.mark.unit
def test_failed_reverification_clears_a_previous_startup_verification() -> None:
    gateway = FakeGateway()
    client = RobinhoodReadClient(gateway)
    run(client.verify_startup())
    gateway.readiness_raises = RuntimeError(INJECTED_PROSE)

    with pytest.raises(GatewayReadError):
        run(client.verify_startup())

    assert client.startup is None


@pytest.mark.unit
def test_a_non_projection_capability_never_reaches_the_gateway() -> None:
    """`_read` refuses a raw string rather than forwarding it."""
    gateway = FakeGateway()
    client = _verified_client(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client._read("place_equity_order", None))  # type: ignore[arg-type]

    assert caught.value.code is GatewayReadErrorCode.CAPABILITY_DENIED
    assert caught.value.rejection == ReadRejection.CAPABILITY_NOT_IN_PROJECTION.value
    assert gateway.invocations == []


# ---------------------------------------------------------------------------
# Envelope validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_versions_are_verified_before_the_payload_is_consumed() -> None:
    """The ordering requirement, made executable.

    ``PoisonPayload`` raises ``AssertionError`` on any access. If a bounds
    walk, a prose scrub, or anything else moves ahead of the version check,
    that ``AssertionError`` escapes and this test fails instead of seeing a
    sanitized ``GatewayReadError``.
    """
    gateway = FakeGateway(
        envelope=envelope_document(
            "get_equity_quotes",
            envelope_version="2.0",
            data=PoisonPayload(),
        )
    )
    client = _verified_client(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_equity_quotes())

    assert caught.value.rejection == ReadRejection.ENVELOPE_VERSION_UNSUPPORTED.value


@pytest.mark.unit
@pytest.mark.parametrize(
    "override",
    [
        {"manifest_version": "2026.08.05"},
        {"manifest_digest": REJECTED_CHANGELOG_MANIFEST_DIGEST},
    ],
)
def test_manifest_drift_in_a_result_is_caught_before_the_payload(
    override: dict[str, Any],
) -> None:
    gateway = FakeGateway(
        envelope=envelope_document("get_equity_quotes", data=PoisonPayload(), **override)
    )
    client = _verified_client(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_equity_quotes())

    assert caught.value.code is GatewayReadErrorCode.ENVELOPE_INVALID


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"drop": "warnings"}, ReadRejection.ENVELOPE_KEYS_MISMATCH),
        ({"extra_field": 1}, ReadRejection.ENVELOPE_KEYS_MISMATCH),
        ({"envelope_version": "1.1"}, ReadRejection.ENVELOPE_VERSION_UNSUPPORTED),
        (
            {"manifest_version": "2026.08.05"},
            ReadRejection.ENVELOPE_MANIFEST_VERSION_UNSUPPORTED,
        ),
        (
            {"manifest_digest": "sha256:" + "0" * 64},
            ReadRejection.ENVELOPE_MANIFEST_DIGEST_MISMATCH,
        ),
        ({"capability": "get_portfolio"}, ReadRejection.ENVELOPE_CAPABILITY_MISMATCH),
        ({"schema_digest": "sha256:nope"}, ReadRejection.ENVELOPE_DIGEST_MALFORMED),
        ({"result_digest": "not-a-digest"}, ReadRejection.ENVELOPE_DIGEST_MALFORMED),
        ({"observed_at": "  "}, ReadRejection.ENVELOPE_OBSERVED_AT_MALFORMED),
        ({"observed_at": "not-a-timestamp"}, ReadRejection.ENVELOPE_OBSERVED_AT_MALFORMED),
        ({"observed_at": "2026-08-06T12:00:00"}, ReadRejection.ENVELOPE_OBSERVED_AT_MALFORMED),
        (
            {"observed_at": "2026-13-06T12:00:00Z"},
            ReadRejection.ENVELOPE_OBSERVED_AT_MALFORMED,
        ),
        (
            {"observed_at": "2026-08-06T12:00:00+00:60"},
            ReadRejection.ENVELOPE_OBSERVED_AT_MALFORMED,
        ),
        (
            {"observed_at": "2026-08-06T12:00:00+01:99"},
            ReadRejection.ENVELOPE_OBSERVED_AT_MALFORMED,
        ),
        ({"warnings": "a string"}, ReadRejection.ENVELOPE_WARNINGS_INVALID),
        ({"warnings": [1]}, ReadRejection.ENVELOPE_WARNINGS_INVALID),
        (
            {"warnings": ["w"] * (MAX_ENVELOPE_WARNINGS + 1)},
            ReadRejection.ENVELOPE_WARNINGS_INVALID,
        ),
        (
            {"warnings": ["w" * (MAX_WARNING_LENGTH + 1)]},
            ReadRejection.ENVELOPE_WARNINGS_INVALID,
        ),
        ({"data": [1, 2, 3]}, ReadRejection.PAYLOAD_NOT_OBJECT),
    ],
)
def test_malformed_envelope_fails_closed_with_its_own_reason(
    kwargs: dict[str, Any],
    expected: ReadRejection,
) -> None:
    gateway = FakeGateway(envelope=envelope_document("get_equity_quotes", **kwargs))
    client = _verified_client(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_equity_quotes())

    assert caught.value.rejection == expected.value


@pytest.mark.unit
def test_an_envelope_that_is_not_a_json_document_fails_closed() -> None:
    gateway = FakeGateway(envelope=object())
    client = _verified_client(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_equity_quotes())

    assert caught.value.rejection == ReadRejection.MALFORMED_ENVELOPE.value


@pytest.mark.unit
def test_an_envelope_reached_through_to_json_dict_validates() -> None:
    gateway = FakeGateway(envelope=RenderedDocument(envelope_document("get_portfolio")))
    client = _verified_client(gateway)

    assert run(client.read_portfolio()).capability == "get_portfolio"


@pytest.mark.unit
@pytest.mark.parametrize(
    "observed_at",
    [
        "2026-08-06T12:00:00Z",
        "2026-08-06T12:00:00.123456Z",
        "2026-08-06T12:00:00-07:00",
        "2026-08-06T12:00:00+23:59",
    ],
)
def test_timezone_aware_rfc3339_observed_at_is_accepted(observed_at: str) -> None:
    gateway = FakeGateway(envelope=envelope_document("get_portfolio", observed_at=observed_at))
    client = _verified_client(gateway)

    assert run(client.read_portfolio()).observed_at == observed_at


def _payload_nested(levels: int) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    node = payload
    for _ in range(levels):
        child: dict[str, Any] = {}
        node["nested"] = child
        node = child
    return payload


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (_payload_nested(MAX_PAYLOAD_DEPTH + 2), ReadRejection.PAYLOAD_TOO_DEEP),
        (
            {"s": "x" * (MAX_PAYLOAD_STRING_LENGTH + 1)},
            ReadRejection.PAYLOAD_STRING_TOO_LONG,
        ),
        (
            {"x" * (MAX_PAYLOAD_STRING_LENGTH + 1): "s"},
            ReadRejection.PAYLOAD_STRING_TOO_LONG,
        ),
    ],
)
def test_unbounded_payloads_fail_closed(
    payload: dict[str, Any],
    expected: ReadRejection,
) -> None:
    gateway = FakeGateway(envelope=envelope_document("get_portfolio", data=payload))
    client = _verified_client(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_portfolio())

    assert caught.value.code is GatewayReadErrorCode.RESPONSE_TOO_LARGE
    assert caught.value.rejection == expected.value


@pytest.mark.unit
def test_a_payload_at_the_depth_bound_is_accepted() -> None:
    """The bound rejects past it, not at it."""
    gateway = FakeGateway(
        envelope=envelope_document("get_portfolio", data=_payload_nested(MAX_PAYLOAD_DEPTH - 1))
    )
    client = _verified_client(gateway)

    assert run(client.read_portfolio()).capability == "get_portfolio"


@pytest.mark.unit
def test_json_compatible_payload_values_are_accepted_and_detached() -> None:
    payload = {
        "null": None,
        "boolean": True,
        "integer": 1,
        "number": -1.25,
        "string": "value",
        "array": [None, False, 2, 3.5, "nested"],
        "tuple": ({"key": "value"},),
    }
    gateway = FakeGateway(envelope=envelope_document("get_portfolio", data=payload))
    client = _verified_client(gateway)

    result = run(client.read_portfolio())

    assert result.payload == {**payload, "tuple": [{"key": "value"}]}
    assert result.payload is not payload


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_value",
    [
        b"bytes",
        object(),
        {"set-item"},
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
    ids=["bytes", "object", "set", "nan", "positive-infinity", "negative-infinity"],
)
def test_non_json_payload_values_fail_closed(invalid_value: object) -> None:
    gateway = FakeGateway(
        envelope=envelope_document("get_portfolio", data={"value": invalid_value})
    )
    client = _verified_client(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_portfolio())

    assert caught.value.code is GatewayReadErrorCode.ENVELOPE_INVALID
    assert caught.value.rejection == ReadRejection.PAYLOAD_NOT_JSON.value


@pytest.mark.unit
def test_non_string_mapping_key_fails_closed() -> None:
    gateway = FakeGateway(envelope=envelope_document("get_portfolio", data={1: "value"}))
    client = _verified_client(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_portfolio())

    assert caught.value.rejection == ReadRejection.PAYLOAD_NOT_JSON.value


@pytest.mark.unit
@pytest.mark.parametrize("container_kind", ["mapping", "list"])
def test_cyclic_payload_fails_closed(container_kind: str) -> None:
    if container_kind == "mapping":
        payload: Any = {}
        payload["self"] = payload
    else:
        cyclic_list: list[Any] = []
        cyclic_list.append(cyclic_list)
        payload = {"value": cyclic_list}
    gateway = FakeGateway(envelope=envelope_document("get_portfolio", data=payload))
    client = _verified_client(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_portfolio())

    assert caught.value.code is GatewayReadErrorCode.ENVELOPE_INVALID
    assert caught.value.rejection == ReadRejection.PAYLOAD_NOT_JSON.value


# ---------------------------------------------------------------------------
# Provider prose (consumer requirement 5 / P-GATEWAY-PROSE)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_provider_prose_is_discarded_from_a_result_and_from_its_log() -> None:
    """The delivery path the independent gateway reviews name, end to end.

    Every reviewed capability's output schema requires a top-level ``guide``
    sibling of ``data``, so this shape is what a real read returns. The
    injected sentence must survive nowhere: not in the payload, not in its
    JSON form, not in any log record.
    """
    sink = RecordingSink()
    gateway = FakeGateway(
        envelope=envelope_document(
            "get_equity_quotes",
            data={
                "guide": INJECTED_PROSE,
                "results": [
                    {
                        "symbol": "ZZZZ",
                        "description": INJECTED_PROSE,
                        "last_trade_price": "1.00",
                    }
                ],
            },
        )
    )
    client = _verified_client(gateway, sink)

    result = run(client.read_equity_quotes())
    serialized = json.dumps(result.payload)

    assert "guide" not in result.payload
    assert INJECTED_PROSE not in serialized
    assert "description" not in serialized
    # ...and the data that is not prose survived, so the scrubber is not
    # simply returning an empty payload.
    assert result.payload == {"results": [{"symbol": "ZZZZ", "last_trade_price": "1.00"}]}
    assert INJECTED_PROSE not in json.dumps(sink.records)


@pytest.mark.unit
def test_gateway_error_text_never_reaches_the_ainvest_error_or_the_log() -> None:
    sink = RecordingSink()
    gateway = FakeGateway(raises=FakeGatewayError("provider_error"))
    client = _verified_client(gateway, sink)

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_equity_quotes())

    rendered = f"{caught.value}{caught.value!r}{caught.value.args}{json.dumps(sink.records)}"
    assert INJECTED_PROSE not in rendered
    assert caught.value.__cause__ is None


@pytest.mark.unit
def test_startup_verification_retains_no_manifest_prose() -> None:
    """A capability listing's own ``description``/``rationale`` never lands."""
    gateway = FakeGateway()
    client = _verified_client(gateway)
    startup = client.startup

    assert startup is not None
    rendered = json.dumps(
        {
            "reads": sorted(startup.projection.manifest_read_capabilities),
            "mutations": sorted(startup.projection.approved_non_trading_mutations),
            "denied": sorted(startup.projection.denied_trading_capabilities),
            "projection": sorted(startup.projection.ainvest_read_projection),
            "readiness": [
                startup.readiness.manifest_version,
                startup.readiness.manifest_digest,
            ],
        }
    )
    assert "description" not in rendered
    assert "rationale" not in rendered


# ---------------------------------------------------------------------------
# Logging: approved metadata only, on both branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_successful_read_logs_exactly_the_approved_fields() -> None:
    sink = RecordingSink()
    gateway = FakeGateway()
    client = _verified_client(gateway, sink)

    result = run(client.read_equity_quotes({"symbols": ["ZZZZ"]}))
    event, fields = sink.only

    assert event == READ_LOG_EVENT
    assert set(fields) == READ_LOG_FIELDS_OK
    assert fields == {
        "capability": "get_equity_quotes",
        "status": "ok",
        "duration_ms": 250,
        "manifest_digest": EXPECTED_MANIFEST_DIGEST,
        "result_digest": result.result_digest,
    }


@pytest.mark.unit
def test_failed_read_logs_exactly_the_approved_fields() -> None:
    """The error branch is pinned as tightly as the success branch."""
    sink = RecordingSink()
    gateway = FakeGateway(raises=FakeGatewayError("timeout", retryable=True))
    client = _verified_client(gateway, sink)

    with pytest.raises(GatewayReadError):
        run(client.read_equity_quotes({"symbols": ["ZZZZ"]}))
    event, fields = sink.only

    assert event == READ_LOG_EVENT
    assert set(fields) == READ_LOG_FIELDS_ERROR
    assert fields == {
        "capability": "get_equity_quotes",
        "status": "error",
        "duration_ms": 250,
        "manifest_digest": EXPECTED_MANIFEST_DIGEST,
        "error_code": "timeout",
    }


@pytest.mark.unit
def test_log_carries_no_argument_value_token_or_account_payload() -> None:
    sink = RecordingSink()
    gateway = FakeGateway(
        envelope=envelope_document(
            "get_accounts",
            data={"account_number": "SYNTHETIC-1", "buying_power": "1.00"},
        )
    )
    client = _verified_client(gateway, sink)

    run(client.read_accounts({"account_number": "SYNTHETIC-1"}))
    _, fields = sink.only

    rendered = json.dumps(fields)
    assert "SYNTHETIC-1" not in rendered
    assert "buying_power" not in rendered
    assert "account_number" not in rendered


@pytest.mark.unit
def test_logged_duration_is_bounded() -> None:
    sink = RecordingSink()
    ticks = iter([0.0, 1_000_000.0])
    client = RobinhoodReadClient(FakeGateway(), log_sink=sink, clock=lambda: next(ticks, 0.0))
    run(client.verify_startup())
    sink.records.clear()

    run(client.read_portfolio())
    _, fields = sink.only

    assert fields["duration_ms"] == MAX_LOGGED_DURATION_MS


@pytest.mark.unit
def test_default_sink_writes_through_the_repository_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default path is wired to the redacted structured logger."""
    captured: list[tuple[str, str, dict[str, object]]] = []

    class _Logger:
        def __init__(self, component: str) -> None:
            self.component = component

        def info(self, event: str, **fields: object) -> None:
            captured.append((self.component, event, fields))

    monkeypatch.setattr(
        "ainvest.execution.robinhood.read_client.get_logger",
        lambda component=None: _Logger(str(component)),
    )
    emit_read_log(READ_LOG_EVENT, {"capability": "get_portfolio", "status": "ok"})

    assert captured == [
        (
            "execution.robinhood",
            READ_LOG_EVENT,
            {"capability": "get_portfolio", "status": "ok"},
        )
    ]


@pytest.mark.unit
def test_client_uses_the_default_sink_when_none_is_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, object]] = []
    monkeypatch.setattr(
        "ainvest.execution.robinhood.read_client.emit_read_log",
        lambda event, fields: captured.append(fields),
    )
    client = RobinhoodReadClient(FakeGateway())
    run(client.verify_startup())

    run(client.read_portfolio())

    assert [set(fields) for fields in captured] == [READ_LOG_FIELDS_OK]


# ---------------------------------------------------------------------------
# No fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_failed_read_calls_the_gateway_once_and_reaches_no_other_provider() -> None:
    """DEC-003 / rule 19: failure is an error, never a different source."""
    gateway = FakeGateway(raises=FakeGatewayError("provider_error"))
    client = _verified_client(gateway)

    with pytest.raises(GatewayReadError) as caught:
        run(client.read_equity_quotes())

    assert len(gateway.invocations) == 1
    assert caught.value.code is GatewayReadErrorCode.PROVIDER_UNAVAILABLE
    assert caught.value.retryable is False
    assert client._gateway is gateway


@pytest.mark.unit
def test_readiness_is_verified_once_per_startup_not_per_read() -> None:
    gateway = FakeGateway()
    client = _verified_client(gateway)

    run(client.read_portfolio())
    run(client.read_equity_quotes())

    assert gateway.readiness_calls == 1
