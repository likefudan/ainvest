# Strategy plugin developer guide

This guide targets the checked-out ainvest Strategy API **1.0.0**. A plugin is
trusted Python code that declares strategies; each evaluation runs in an
isolated worker and returns a versioned `StrategyResult`. It must never obtain
credentials, decide final order quantity, approve a proposal, or call a broker.

The [standalone starter](../examples/strategy-plugin) is a complete installable
package. It emits **HOLD only**, keeps explicit state, and ships with a disabled
YAML instance. It is a teaching artifact, not an investment recommendation or
an approved strategy. Real strategy/universe/schedule/parameter selection and
risk limits remain owner decisions DEC-011/DEC-012.

## Build and run a genuinely external package

Use Python 3.12 and `uv` from a reviewed ainvest checkout. Do not assume that a
package named `ainvest` on a public index is this repository. Run the following
in one POSIX shell from the repository root:

```bash
./scripts/dev setup
AINVEST_ROOT=$PWD
AINVEST_PLUGIN_WORK=$(mktemp -d)
cp -R "$AINVEST_ROOT/examples/strategy-plugin" "$AINVEST_PLUGIN_WORK/plugin"
uv build --offline --wheel --out-dir "$AINVEST_PLUGIN_WORK/wheels" "$AINVEST_PLUGIN_WORK/plugin"
uv pip install --offline --no-deps --python "$AINVEST_ROOT/.venv/bin/python" \
  --target "$AINVEST_PLUGIN_WORK/installed" \
  "$AINVEST_PLUGIN_WORK/wheels/ainvest_starter_hold-0.1.0-py3-none-any.whl"
PYTHONPATH="$AINVEST_PLUGIN_WORK/installed" "$AINVEST_ROOT/.venv/bin/python" \
  -m ainvest.strategy_conformance --strategy starter_hold \
  --plugin-id starter_hold --plugin-version 0.1.0 \
  --json-out "$AINVEST_PLUGIN_WORK/conformance.json"
```

Expect exit 0 and a `PASSED` report with `passed: true`. Setup downloads the
locked host and primes its Hatchling build-backend cache; later build/install
commands are offline. If the build cache is missing, restore it via the reviewed
setup on the same machine before retrying; do not silently enable index access
inside tests. `--no-deps` is safe here **only because the compatible host and
its locked dependencies are already installed**. The template declares its
host range but this demo never resolves an arbitrary public host package.

The wheel is installed into a new temporary target, not ainvest's source tree
or active `.venv`. Only these explicit child commands get its `PYTHONPATH`.
Workers inherit that installed target. No tokens, Bot, broker session, persistent
database, live flags or enabled strategy are needed. The temporary files remain
available for inspection; nothing in this recipe changes your staging service.
Do not put an unreviewed package on PYTHONPATH or use an untrusted target directory.

After copying into your own repository, choose unique names and replace example
provenance before publishing. Keep the disabled instance until owner approval.
Do not publish `ainvest-starter-hold` unchanged as a production strategy.

## Package and API contract

The template contains:

```text
pyproject.toml                  wheel metadata and entry point
src/ainvest_starter/plugin.py   PluginMetadata + declaration hook
src/ainvest_starter/strategy.py params model + evaluate(context)
strategies.yaml                disabled synthetic instance
```

| Field | Starter value | Contract |
| --- | --- | --- |
| Distribution name | `ainvest-starter-hold` | Packaging identity; not the strategy name. |
| Entry-point group | `ainvest.strategies` | Registry discovery group. |
| Entry-point name / plugin ID | `starter_hold` | Must agree so allowlist filtering happens before import. |
| Entry-point target | `ainvest_starter.plugin:plugin` | Object with metadata and `@hookimpl strategy_definitions()`. |
| Plugin version | `0.1.0` | Exact reviewed package-version pin; keep wheel and metadata aligned. |
| Strategy name / version | `starter_hold` / `0.1.0` | Stable signal/state identity, independent of host version. |
| Strategy API range | `>=1.0.0,<2.0.0` | Host validates compatibility; not the host distribution version. |

`PluginMetadata` also requires `source_commit`, `owner` and `repository`.
The starter uses explicit local/example placeholders; replace them with reviewed
provenance for a release. Metadata is a claim, not an authenticity guarantee:
record the immutable source commit and wheel hash in your release process.
The current registry validates declared metadata and pins; it is not an artifact
signature verifier. Review the wheel and transitive dependencies separately.

`strategy_definitions()` returns a list of `StrategyDefinition.from_type(...)`
declarations. Do not evaluate, start threads, read secrets, open files/sockets,
or contact external services on import or during discovery. Duplicate strategy
names/plugin IDs, invalid hooks and incompatible APIs fail discovery.

The strategy class declares `name`, `version`, and `params_model`; its constructor
accepts validated parameters. `evaluate(context)` returns `StrategyResult`
with `signals`, `next_state`, and non-secret `diagnostics`. Read the full
[reference moving-average implementation](../src/ainvest/strategies/reference/moving_average/strategy.py)
for BUY/SELL/HOLD intent handling; final quantity and approval never belong here.

## Parameters, YAML and allowlists

Subclass `StrategyParams` (frozen, extra keys forbidden). The starter's
`ttl_seconds` is a strict integer between 1 and 3600, default 1800; strings,
booleans, unknown keys and out-of-range values are rejected. Use Decimal/domain
types for prices, weights and money; serialize them as decimal strings, not
floating-point JSON numbers. Supply useful defaults for the conformance fixture.

YAML describes an **instance**, not executable code: ID, plugin/strategy names,
version pin, disabled/enabled state, universe, parameters, named schedule and
constraints. The example uses config `schema_version: "1"`; this is distinct
from domain envelopes' `schema_version: "1.0"`. Unknown fields, executable YAML,
duplicate instances and invalid parameters fail closed. Loading/binding the
file does not start a scheduler or approve its synthetic values.

To bind the installed starter without evaluating it, reuse the shell variables
above:

```bash
PYTHONPATH="$AINVEST_PLUGIN_WORK/installed" "$AINVEST_ROOT/.venv/bin/python" - \
  "$AINVEST_PLUGIN_WORK/plugin/strategies.yaml" <<'PY'
import sys
from pathlib import Path
from ainvest.strategies import (
    RegistryLoadConfig, StrategyRegistry, load_and_bind_strategy_instances,
)
registry = StrategyRegistry.load(
    RegistryLoadConfig(allowlist={"starter_hold": "0.1.0"})
)
instances = load_and_bind_strategy_instances(Path(sys.argv[1]), registry)
assert len(instances) == 1 and not instances[0].enabled
print("starter_hold: validated, disabled")
PY
```

Always use an explicit reviewed allowlist, even for Paper development. Paper
technically permits no allowlist; then all installed entry points can be imported.
The CLI `--plugin-id` plus `--plugin-version` pair creates an exact allowlist.
Live additionally requires pinned allowlists and instance versions, but neither
is Live authorization; current production Live is unavailable. A plugin that
passed conformance is not automatically added to any operational allowlist.

## Determinism and state

- Use only the immutable `StrategyContext`: research, portfolio, optional
  strategy state and the **`context.as_of`** UTC evaluation clock. Do not call
  `datetime.now()`, `time.time()`, random UUIDs or nondeterministic `hash()`.
- Identical context, parameters and code version must yield identical output.
  Signal IDs must be deterministic; the starter hashes its complete context,
  parameters and strategy identity. Signals bind `research_id`, symbol, strategy
  name/version and `generated_at=context.as_of`; expiry must be later than that
  clock. HOLD has zero strength and no target weight in the starter.
- Never consume data observed/published after `as_of`. The shared context/parser
  checks future inputs and the worker validates output against context. Static
  clock scanning cannot prove absence of look-ahead bias in custom calculations;
  write explicit historical-cutoff, missing-data and boundary tests yourself.
- Do not use class/module globals or local files as cross-evaluation state.
  Read `context.strategy_state` and return a new `StrategyState` with matching
  strategy/version, `updated_at=context.as_of`, unique typed scalar entries.
  The host owns persistence and replay; the plugin must not write the database.
- The starter reads a BOOLEAN `initialized` marker and returns it as true. The
  next call with that state produces `STARTER_ALREADY_INITIALIZED`, still HOLD.
  Unexpected marker type or state identity/version fails. The sample ignores
  unrelated keys; a real strategy must define and test its full state contract.
- Bumping a strategy version requires an explicit reviewed state migration or
  reset/reseed policy. Never silently reinterpret state from a different version.
  An exception is a failed evaluation, not permission to keep using an old signal.

## Isolation and prohibited behavior

Evaluation uses `evaluate_in_worker`, versioned JSON only, a scrubbed environment,
bounded execution/resources and a restricted working directory. Do not evaluate
arbitrary plugins directly in a privileged service. Never import broker/approval
code, read environment credentials, access networks, mutate portfolios, approve
orders or submit/cancel trades. Keep diagnostics free of sensitive input dumps.

**Discovery and build are trusted-code execution, not sandboxing.** Build backends
and entry-point imports run before evaluation isolation. Use isolated CI/build
workers with no secrets and review dependencies before installation. In-process
socket blocking is best effort, not a security boundary against malicious Python;
production/CI must add OS/container egress denial and resource/filesystem policy.
See [worker isolation](development.md#strategy-worker-isolation) and
[security controls](security/control-matrix.md).

Common invalid examples:

| Mistake | Expected handling |
| --- | --- |
| Unknown YAML parameter or `ttl_seconds: true` | Parameter validation rejects it. |
| `generated_at=datetime.now()` or random signal ID | Clock/determinism checks fail; use `as_of` and deterministic inputs. |
| Wrong symbol/research ID or future/expired signal | Context/schema validation fails. |
| Reusing a previous strategy version's state | Starter rejects it; review an explicit migration. |
| Importing `requests`, `socket`, broker or approval modules | Conformance/isolation rejects prohibited access. |
| Reading an API key from the environment | Secret access is prohibited and blocked/tested. |
| Claiming a different plugin version than the allowlist | Discovery exits with a load failure before evaluation. |
| Returning an order, ORM object or arbitrary dictionary | Return the declared versioned `StrategyResult`, not a write capability. |

## CI, version upgrades and release review

CI should provision a reviewed immutable ainvest checkout with its committed
lock, run `./scripts/dev setup`, and execute the build/install/conformance recipe
above on an isolated disposable worker. Substitute the plugin checkout for the
copied template. Never assume a public index package is the trusted host, give
pull-request code trading/API secrets, or use floating action revisions. Use
the [repository CI](../.github/workflows/ci.yml) as the pinned-action reference;
cross-repository read access for private source must be provisioned separately
with least privilege. No new GitHub secret or provider is selected by this guide.

Fail CI on any nonzero conformance exit: 1 means failed checks, 2 means a load
error. Retain only the non-secret JSON report and reviewed wheel/digest, not
environment dumps or credentials. Run your own semantic/no-look-ahead tests too.
Conformance covers metadata, parameters, signal contracts, determinism, clock
use, timeouts, exceptions, a Paper fixture and isolation checks; passing proves
neither profitability nor completeness of security controls. See
[conformance reference](strategy-conformance.md) for stable error codes.

For upgrades: review source/dependency changes; bump package/plugin version and
strategy version when behavior/state changes; check the API compatibility range;
test old-state migration and historical replay; rebuild and hash the wheel;
rerun conformance/semantic tests; then separately review exact operational
allowlist/instance pins. Preserve the prior approved artifact for controlled
rollback. Code publication never enables an instance or grants trading approval.

The host's canonical `./scripts/dev verify` includes a test that copies this
template to a temporary external directory, builds/installs its wheel offline,
proves installed entry-point discovery and disabled YAML binding, checks state
and invalid parameters, runs full conformance and rejects a wrong version pin.
