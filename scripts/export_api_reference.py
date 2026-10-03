"""Deterministically publish internal integration contracts, never HTTP routes."""

from __future__ import annotations

import argparse
import ast
import inspect
import json
from pathlib import Path
from typing import Any, get_args

from pydantic import TypeAdapter

from ainvest.approval import service as approval_service
from ainvest.approval.handoff import ApprovalHandoffCode
from ainvest.approval.telegram_approval import TelegramApprovalCode
from ainvest.execution.broker import BrokerErrorCode
from ainvest.execution.state_machine import (
    CANCEL_EDGES,
    CANCEL_RECOVERY_ONLY,
    CANCEL_TERMINAL,
    ORDER_EDGES,
    ORDER_RECOVERY_ONLY,
    ORDER_TERMINAL,
    CancelCommandState,
    OrderLifecycleState,
)
from ainvest.workflow.commands import COMMAND_TYPE_TO_MODEL, WorkflowCommand
from ainvest.workflow.events import COMMAND_TO_EVENT_TYPE, CommandOutcome, WorkflowEvent
from ainvest.workflow.semantics import COMMAND_RETRY_SEMANTICS, CommandType

TARGET = Path(__file__).resolve().parents[1] / "docs/api/generated"


def approval_error_codes() -> list[str]:
    """Snapshot literal service codes; fail if that source contract becomes dynamic."""
    codes: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(approval_service))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ApprovalServiceError"
        ):
            if not node.args or not isinstance(node.args[0], ast.Constant):
                raise ValueError("approval error code is no longer a literal")
            code = node.args[0].value
            if not isinstance(code, str):
                raise ValueError("approval error code is not text")
            codes.add(code)
    return sorted(codes)


def render_artifacts() -> dict[str, str]:
    """Return stable artifact names/content; no timestamps or environment state."""
    command = TypeAdapter(WorkflowCommand)
    event = TypeAdapter(WorkflowEvent)
    command_example = command.validate_python(
        {
            "schema_version": "1.0",
            "command_type": "SIZE_POSITION",
            "command_id": "cmd_example0001",
            "correlation_id": "corr_example0001",
            "idempotency_id": "idem_example0001",
            "issued_at": "2026-10-01T12:00:00Z",
            "signal_id": "sig_example0001",
        }
    )
    event_example = event.validate_python(
        {
            "schema_version": "1.0",
            "event_type": "POSITION_SIZED",
            "event_id": "evt_example0001",
            "correlation_id": command_example.correlation_id,
            "causation_id": command_example.command_id,
            "idempotency_id": command_example.idempotency_id,
            "occurred_at": "2026-10-01T12:00:01Z",
            "outcome": "SUCCEEDED",
            "signal_id": "sig_example0001",
            "candidate_id": "cand_example0001",
        }
    )
    documents: dict[str, Any] = {
        "WorkflowCommand.json": command.json_schema(mode="validation"),
        "WorkflowEvent.json": event.json_schema(mode="validation"),
        "examples.json": {
            "WorkflowCommand": command_example.model_dump(mode="json"),
            "WorkflowEvent": event_example.model_dump(mode="json"),
        },
        "catalog.json": {
            "purpose": "internal data contracts; not HTTP endpoints or execution authorization",
            "commands": {
                kind.value: {
                    "model": COMMAND_TYPE_TO_MODEL[kind].__name__,
                    "event": COMMAND_TO_EVENT_TYPE[kind].value,
                    "retry": COMMAND_RETRY_SEMANTICS[kind].value,
                }
                for kind in CommandType
            },
            "order": {
                "states": sorted(OrderLifecycleState),
                "edges": sorted(ORDER_EDGES),
                "terminal": sorted(ORDER_TERMINAL),
                "recovery_only": {key: sorted(value) for key, value in ORDER_RECOVERY_ONLY.items()},
            },
            "cancel": {
                "states": sorted(CancelCommandState),
                "edges": sorted(CANCEL_EDGES),
                "terminal": sorted(CANCEL_TERMINAL),
                "recovery_only": {
                    key: sorted(value) for key, value in CANCEL_RECOVERY_ONLY.items()
                },
            },
            "codes": {
                "broker": sorted(get_args(BrokerErrorCode)),
                "approval_service": approval_error_codes(),
                "telegram_approval": sorted(TelegramApprovalCode),
                "approval_handoff": sorted(ApprovalHandoffCode),
                "workflow_outcome": sorted(CommandOutcome),
            },
        },
    }
    for name in ("WorkflowCommand.json", "WorkflowEvent.json"):
        documents[name]["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return {
        name: json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        for name, document in documents.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    artifacts = render_artifacts()
    if args.check:
        valid = TARGET.is_dir() and {path.name for path in TARGET.iterdir()} == set(artifacts)
        valid = valid and all(
            (TARGET / name).is_file() and (TARGET / name).read_text(encoding="utf-8") == content
            for name, content in artifacts.items()
        )
        print(
            "API reference matches code." if valid else "API reference drift: regenerate artifacts."
        )
        return 0 if valid else 1
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts.items():
        (TARGET / name).write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
