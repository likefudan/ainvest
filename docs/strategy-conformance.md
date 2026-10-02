# Strategy conformance reference

Independent strategy teams should run this suite in their own CI before
publishing a plugin. The suite validates hooks, metadata, Strategy API range,
parameters, signal schemas, determinism, isolation boundaries, and a Paper
Trading example.

For a complete standalone package, external wheel installation, YAML/state
contract and CI/release procedure, use the
[strategy plugin developer guide](strategy-plugin-guide.md).

## Install the reviewed host

```bash
./scripts/dev setup
```

Run this from a reviewed ainvest source checkout, with its committed lock.
Do not assume `pip install ainvest` selects this repository. The setup wrapper
also preserves the broker artifact verification required by the local merge gate.
No provider authorization is needed for conformance.

## CLI

```bash
# Human-readable report on stdout (exit 0 = pass, 1 = fail, 2 = load error)
uv run --locked ainvest-strategy-conformance --strategy moving_average \
  --plugin-id moving_average --plugin-version 1.0.0

# Machine-readable JSON plus human report
uv run --locked ainvest-strategy-conformance \
  --strategy moving_average \
  --plugin-id moving_average \
  --plugin-version 1.0.0 \
  --json-out conformance-report.json

# Equivalent module form
uv run --locked python -m ainvest.strategy_conformance --strategy moving_average \
  --plugin-id moving_average --plugin-version 1.0.0
```

## CI

Use the developer guide's offline external-wheel recipe after provisioning a
reviewed host checkout and build cache. Pin third-party actions to reviewed
commit SHAs as in [repository CI](../.github/workflows/ci.yml); do not inject
trading/API credentials into plugin builds or evaluation. Replace strategy,
plugin ID and exact version together, matching the installed entry point under
`ainvest.strategies`. Retain the non-secret JSON report and fail on nonzero exit.
Discovery imports trusted plugin code before evaluation isolation, so use an
isolated, secret-free build/test environment and a restricted allowlist.

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
| `CONFORMANCE_WORKER_FAILURE` | Isolated worker failed (including Paper example evaluation) |
| `CONFORMANCE_NETWORK_ACCESS` | Network imports or worker network denial |
| `CONFORMANCE_SECRET_ACCESS` | Credential environment access attempted |
| `CONFORMANCE_BROKER_IMPORT` | Broker / execution / approval imports |

## Programmatic API

```python
from ainvest.strategies import RegistryLoadConfig, load_strategy_registry
from ainvest.strategy_conformance import run_conformance_suite, report_to_json

definition = load_strategy_registry(
    RegistryLoadConfig(allowlist={"moving_average": "1.0.0"})
).get("moving_average")
report = run_conformance_suite(definition)
print(report_to_json(report))
assert report.passed
```

Behavioral and isolation checks execute strategies through
`evaluate_in_worker` (see `docs/development.md` strategy worker isolation).

Passing conformance neither enables a strategy instance nor grants any trading
authority. It does not prove profitability or replace semantic/security review.
