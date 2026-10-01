"""Shared deterministic evidence helpers for the P08-T13 fault matrix."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FaultEvidence:
    """The four observations every money-path fault test must pin."""

    final_state: str
    audit_or_result: tuple[str, ...]
    external_calls: int
    funds_effect: str


def assert_fault_evidence(
    evidence: FaultEvidence,
    *,
    state: str,
    calls: int,
    funds: str,
) -> None:
    assert evidence.final_state == state
    assert evidence.audit_or_result
    assert evidence.external_calls == calls
    assert evidence.funds_effect == funds
