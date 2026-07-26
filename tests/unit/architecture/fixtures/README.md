# Architecture fixtures

These sources intentionally violate package dependency rules so unit tests can
prove the architecture checker detects forbidden imports. Some use a `.txt`
suffix because they are parsed as Python syntax but must not be discovered by
Ruff, mypy, pytest, or import tooling as real modules.

They are **not** part of `src/ainvest` and must never be imported by production
packages.
