"""Build/install the copied starter outside the repo; never pollute host discovery."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[3] / "examples" / "strategy-plugin"
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def external_plugin(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path_factory.mktemp("external-plugin")
    source = root / "plugin"
    shutil.copytree(TEMPLATE, source, ignore=shutil.ignore_patterns("__pycache__", "dist", ".venv"))
    uv = shutil.which("uv")
    assert uv is not None, "canonical development setup requires uv"
    env = {"PATH": os.environ.get("PATH", os.defpath)}
    build = subprocess.run(
        [uv, "build", "--offline", "--wheel", "--out-dir", str(root / "wheels"), str(source)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    [wheel] = (root / "wheels").glob("*.whl")
    installed = root / "installed"
    install = subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            sys.executable,
            "--target",
            str(installed),
            str(wheel),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    # The worker inherits this installed target, NOT the template source tree.
    env["PYTHONPATH"] = str(installed)
    return source, installed, env


def test_external_wheel_discovery_binding_and_state(
    external_plugin: tuple[Path, Path, dict[str, str]],
) -> None:
    source, installed, env = external_plugin
    probe = r"""
import inspect
import sys
from importlib.metadata import version
from pathlib import Path
from ainvest.strategies import (
    RegistryLoadConfig, StrategyRegistry, load_and_bind_strategy_instances,
)
from ainvest.strategies.worker import evaluate_in_worker, WorkerStatus
from ainvest.strategy_conformance.fixtures import make_paper_context
from ainvest.schemas.strategy import SignalIntent, StrategyStateItem, StrategyStateValueKind

registry = StrategyRegistry.load(RegistryLoadConfig(allowlist={"starter_hold": "0.1.0"}))
definition = registry.get("starter_hold")
assert version("ainvest-starter-hold") == definition.metadata.plugin_version == "0.1.0"
assert Path(inspect.getfile(definition.strategy_type)).is_relative_to(Path(sys.argv[2]))
[instance] = load_and_bind_strategy_instances(Path(sys.argv[1]) / "strategies.yaml", registry)
assert not instance.enabled
assert instance.params.ttl_seconds == 1800
context = make_paper_context(strategy_name="starter_hold", strategy_version="0.1.0")
first = evaluate_in_worker(definition, params={}, context=context)
assert first.status is WorkerStatus.SUCCESS and first.result is not None
assert len(first.result.signals) == 1
signal = first.result.signals[0]
assert signal.intent is SignalIntent.HOLD and not signal.may_become_order()
assert signal.target_weight is None and signal.generated_at == context.as_of
assert (signal.expires_at - context.as_of).total_seconds() == 1800
state = first.result.next_state
assert state is not None and state.updated_at == context.as_of
assert state.entries[0].boolean_value is True
assert context.strategy_state != state  # no in-place mutation
second_context = context.model_copy(update={"strategy_state": state})
second = evaluate_in_worker(definition, params={}, context=second_context)
assert second.status is WorkerStatus.SUCCESS and second.result is not None
assert second.result.signals[0].reason_codes == ("STARTER_ALREADY_INITIALIZED",)
assert second.result.next_state == state
for invalid in ({"ttl_seconds": 0}, {"ttl_seconds": True}, {"ttl_seconds": "30"},
                {"ttl_seconds": 3601}, {"unknown": 1}):
    try:
        definition.validate_params(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid parameters were accepted")
wrong = state.model_copy(update={"strategy_version": "9.0.0"})
failed = evaluate_in_worker(
    definition, params={}, context=context.model_copy(update={"strategy_state": wrong})
)
assert failed.status is WorkerStatus.FAILED and failed.result is None
bad_marker = StrategyStateItem(
    key="initialized", kind=StrategyStateValueKind.TEXT, text_value="true"
)
wrong = state.model_copy(update={"entries": (bad_marker,)})
failed = evaluate_in_worker(
    definition, params={}, context=context.model_copy(update={"strategy_state": wrong})
)
assert failed.status is WorkerStatus.FAILED and failed.result is None
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(source), str(installed)],
        cwd=source.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_external_wheel_passes_full_conformance(
    external_plugin: tuple[Path, Path, dict[str, str]],
) -> None:
    source, _, env = external_plugin
    report = source.parent / "conformance.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ainvest.strategy_conformance",
            "--strategy",
            "starter_hold",
            "--plugin-id",
            "starter_hold",
            "--plugin-version",
            "0.1.0",
            "--json-out",
            str(report),
        ],
        cwd=source.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text())
    assert data["passed"] is True
    assert data["plugin_id"] == "starter_hold"
    assert all(item["status"] == "PASSED" for item in data["checks"])


def test_external_wheel_rejects_wrong_allowlist_pin(
    external_plugin: tuple[Path, Path, dict[str, str]],
) -> None:
    source, _, env = external_plugin
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ainvest.strategy_conformance",
            "--strategy",
            "starter_hold",
            "--plugin-id",
            "starter_hold",
            "--plugin-version",
            "9.0.0",
        ],
        cwd=source.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "does not match pinned allowlist version" in result.stderr
