# ainvest

AI-assisted stock research, strategy evaluation, risk control, human approval, and broker execution framework.

## Status

Phase 01 foundation work is in progress. Real-money trading remains disabled.

Project coordination:

- [Decision register](docs/decisions/README.md)
- [Implementation task status](docs/tasks/status.md)

## Intended workflow

1. Collect market, company, and portfolio data.
2. Produce a structured research packet.
3. Evaluate user-defined Python strategies.
4. Apply deterministic risk controls.
5. Request approval through Telegram.
6. Execute approved orders through Robinhood MCP.

Real-money trading will remain disabled until the approval, risk, and broker integration layers are explicitly configured and tested.
