"""Paper orchestration composition root (P03-T16).

Wires strategy → sizing → risk → explicit approval stub → paper submit →
fill → reconciliation without rewriting D1-D3 modules. Never auto-approves.
"""

from ainvest.orchestrator.approval_stub import (
    ApprovalStubStore,
    consume_challenge,
    create_challenge,
)
from ainvest.orchestrator.fixtures import make_paper_flow_config
from ainvest.orchestrator.paper_loop import PaperFlowConfig, run_paper_flow
from ainvest.orchestrator.types import (
    DEFAULT_AS_OF,
    PaperFlowResult,
    PaperFlowTerminal,
    StepRecord,
)

__all__ = [
    "DEFAULT_AS_OF",
    "ApprovalStubStore",
    "PaperFlowConfig",
    "PaperFlowResult",
    "PaperFlowTerminal",
    "StepRecord",
    "consume_challenge",
    "create_challenge",
    "make_paper_flow_config",
    "run_paper_flow",
]
