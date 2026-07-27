"""Strategy conformance suite for third-party plugin CI (P03-T5).

Independent strategy teams should run this suite in their own CI before
publishing a plugin. The suite validates hooks, metadata, Strategy API range,
parameters, signal schemas, determinism, isolation boundaries, and a Paper
Trading example.

## Install

```bash
uv sync --locked
# or: pip install ainvest
```

## CLI

```bash
# Human-readable report on stdout (exit 0 = pass, 1 = fail, 2 = load error)
uv run ainvest-strategy-conformance --strategy moving_average

# Machine-readable JSON plus human report
uv run ainvest-strategy-conformance \
  --strategy moving_average \
  --plugin-id moving_average \
  --plugin-version 1.0.0 \
  --json-out conformance-report.json

# Equivalent module form
uv run python -m ainvest.strategy_conformance --strategy moving_average
```

## GitHub Actions example

```yaml
name: Strategy conformance
on:
  push:
  pull_request:

jobs:
  conformance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Install ainvest + your plugin
        run: |
          uv venv
          uv pip install "ainvest==0.1.0" .
      - name: Run strategy conformance
        run: |
          uv run ainvest-strategy-conformance \
            --strategy your_strategy_name \
            --plugin-id your_plugin_id \
            --plugin-version 1.0.0 \
            --json-out conformance-report.json
      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: strategy-conformance-report
          path: conformance-report.json
```

Replace `your_strategy_name` / `your_plugin_id` with the values declared by your
plugin entry point under the `ainvest.strategies` group.

## Stable failure codes

Failed checks emit stable `ConformanceCode` values in both the human report and
JSON (`code` field), for example:

| Code | Meaning |
| --- | --- |
| `CONFORMANCE_METADATA_INVALID` | Plugin / strategy metadata invalid |
| `CONFORMANCE_API_INCOMPATIBLE` | Strategy API range excludes the host |
| `CONFORMANCE_HOOK_INVALID` | Missing Protocol surface / hooks |
| `CONFORMANCE_PARAMS_INVALID` | Parameter model rejects defaults or allows extras |
| `CONFORMANCE_SIGNAL_INVALID` | Emitted signals fail schema / clock rules |
| `CONFORMANCE_NONDETERMINISTIC` | Repeat runs with fixed inputs diverge |
| `CONFORMANCE_FUTURE_DATA` | Wall-clock APIs found in strategy source |
| `CONFORMANCE_TIMEOUT` | Evaluation exceeded worker wall timeout |
| `CONFORMANCE_EXCEPTION` | Evaluation crashed or raised |
| `CONFORMANCE_PAPER_EXAMPLE` | Paper fixture evaluation failed |
| `CONFORMANCE_NETWORK_ACCESS` | Network imports or worker network denial |
| `CONFORMANCE_SECRET_ACCESS` | Credential environment access attempted |
| `CONFORMANCE_BROKER_IMPORT` | Broker / execution / approval imports |

## Programmatic API

```python
from ainvest.strategies import load_strategy_registry
from ainvest.strategy_conformance import run_conformance_suite, report_to_json

definition = load_strategy_registry().get("moving_average")
report = run_conformance_suite(definition)
print(report_to_json(report))
assert report.passed
```

Behavioral and isolation checks execute strategies through
`evaluate_in_worker` (see `docs/development.md` strategy worker isolation).
"""
