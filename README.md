# ainvest

AI-assisted stock research, strategy evaluation, risk control, human approval, and broker execution framework.

## Status

Batch B — Part 1 (B1) domain schemas are in review (`P02-T0`/`P02-T1`). Next
after merge: B2 (`P02-T2`). Real-money trading remains disabled.

Project coordination:

- [Decision register](docs/decisions/README.md)
- [Implementation task status](docs/tasks/status.md)
- [Threat model](docs/security/threat-model.md)
- [Development commands](docs/development.md)

## Intended workflow

1. Collect market, company, and portfolio data.
2. Produce a structured research packet.
3. Evaluate user-defined Python strategies.
4. Apply deterministic risk controls.
5. Request approval through Telegram.
6. Execute approved orders through Robinhood MCP.

Real-money trading will remain disabled until the approval, risk, and broker integration layers are explicitly configured and tested.
