"""Pure in-memory budget admission; no billing account or provider activation."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, localcontext
from threading import Lock
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from ainvest.agents.research_models import ResearchRunRecord
from ainvest.schemas.common import DomainModel, MachineCode, Money, StableId

Version = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
Tokens = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]


class ResearchCostRates(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    version: Version
    basis: Literal["synthetic"] = "synthetic"
    input_usd_per_million: Annotated[Money, Field(gt=0, le=100_000)]
    output_usd_per_million: Annotated[Money, Field(gt=0, le=100_000)]

    def estimate(self, input_tokens: int, output_tokens: int) -> Decimal:
        # Reuse strict token validation; reject floats/bools/negative/huge counts.
        usage = BudgetUsage(input_tokens=input_tokens, output_tokens=output_tokens)
        with localcontext() as context:
            context.prec = 50
            context.rounding = ROUND_CEILING
            return (
                (
                    usage.input_tokens * self.input_usd_per_million
                    + usage.output_tokens * self.output_usd_per_million
                )
                / Decimal(1_000_000)
            ).quantize(Decimal("0.00000001"))


class BudgetUsage(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    input_tokens: Tokens
    output_tokens: Tokens
    known: bool = True


class ResearchBudgetLimit(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    ceiling_usd: Annotated[Money, Field(gt=0, le=10_000_000)]
    rates: ResearchCostRates
    max_runs: Annotated[int, Field(strict=True, ge=1, le=128)] = 128


class BudgetAlert(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    code: MachineCode
    run_id: StableId
    action: Literal["pause_new_research"] = "pause_new_research"


class BudgetSnapshot(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    ceiling_usd: Money
    spent_usd: Money
    reserved_usd: Money
    remaining_usd: Money
    paused: bool
    rate_version: Version
    alerts: Annotated[tuple[BudgetAlert, ...], Field(max_length=128)]


class BudgetAdmission(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: StableId
    allowed: bool
    reservation_usd: Money
    snapshot: BudgetSnapshot


@dataclass
class _Reservation:
    maximum: BudgetUsage
    amount: Decimal
    settled: BudgetUsage | None = None


class ResearchBudgetGuard:
    """Explicit synthetic ceiling, atomic reservation, sticky pause, no reset.

    Alerts are returned domain events for a future trusted notifier, not Telegram
    sends. This process-local guard is neither a durable monthly ledger nor a
    provider billing guarantee. A reviewed operational composition must supply
    accepted real rates, durable coordination and owner authorization separately.
    """

    def __init__(self, limit: ResearchBudgetLimit) -> None:
        self._limit = ResearchBudgetLimit.model_validate_json(limit.model_dump_json())
        self._lock = Lock()
        self._reservations: dict[str, _Reservation] = {}
        self._spent = Decimal(0)
        self._paused = False
        self._alerts: list[BudgetAlert] = []

    def _snapshot(self) -> BudgetSnapshot:
        with localcontext() as context:
            context.prec = 50
            held = sum(
                (
                    entry.amount
                    for entry in self._reservations.values()
                    if entry.settled is None or not entry.settled.known
                ),
                Decimal(0),
            )
            remaining = max(Decimal(0), self._limit.ceiling_usd - self._spent - held)
        return BudgetSnapshot(
            ceiling_usd=self._limit.ceiling_usd,
            spent_usd=self._spent,
            reserved_usd=held,
            remaining_usd=remaining,
            paused=self._paused,
            rate_version=self._limit.rates.version,
            alerts=tuple(self._alerts),
        )

    @property
    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return self._snapshot()

    def _pause(self, run_id: str, code: str) -> None:
        if not self._paused:
            self._alerts.append(BudgetAlert(run_id=run_id, code=code))
        self._paused = True

    def reserve(self, run_id: str, *, input_tokens: int, output_tokens: int) -> BudgetAdmission:
        maximum = BudgetUsage(input_tokens=input_tokens, output_tokens=output_tokens)
        amount = self._limit.rates.estimate(input_tokens, output_tokens)
        # Validate identifiers before any mutation even on rejection.
        BudgetAlert(run_id=run_id, code="BUDGET_EXHAUSTED")
        with self._lock:
            if run_id in self._reservations:
                raise ValueError("BUDGET_RUN_REUSED")
            if not self._paused:
                if len(self._reservations) >= self._limit.max_runs:
                    self._pause(run_id, "BUDGET_RUN_LIMIT")
                elif amount > self._snapshot().remaining_usd:
                    self._pause(run_id, "BUDGET_EXHAUSTED")
                else:
                    self._reservations[run_id] = _Reservation(maximum=maximum, amount=amount)
                    return BudgetAdmission(
                        run_id=run_id,
                        allowed=True,
                        reservation_usd=amount,
                        snapshot=self._snapshot(),
                    )
            return BudgetAdmission(
                run_id=run_id,
                allowed=False,
                reservation_usd=Decimal(0),
                snapshot=self._snapshot(),
            )

    def settle(self, record: ResearchRunRecord) -> BudgetSnapshot:
        record = ResearchRunRecord.model_validate_json(record.model_dump_json())
        usage = BudgetUsage(
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            known=record.usage_complete,
        )
        with self._lock:
            entry = self._reservations.get(record.run_id)
            if entry is None:
                raise ValueError("BUDGET_RESERVATION_MISSING")
            if entry.settled is not None:
                if entry.settled != usage:
                    raise ValueError("BUDGET_SETTLEMENT_CONFLICT")
                return self._snapshot()
            entry.settled = usage
            if not usage.known:
                # Do not turn unknown consumption into zero or release its hold.
                entry.amount = max(
                    entry.amount,
                    self._limit.rates.estimate(usage.input_tokens, usage.output_tokens),
                )
                self._pause(record.run_id, "BUDGET_USAGE_UNKNOWN")
            else:
                with localcontext() as context:
                    context.prec = 50
                    self._spent += self._limit.rates.estimate(
                        usage.input_tokens, usage.output_tokens
                    )
                if (
                    usage.input_tokens > entry.maximum.input_tokens
                    or usage.output_tokens > entry.maximum.output_tokens
                ):
                    self._pause(record.run_id, "BUDGET_RESERVATION_EXCEEDED")
                elif self._snapshot().remaining_usd == 0:
                    self._pause(record.run_id, "BUDGET_EXHAUSTED")
            return self._snapshot()
