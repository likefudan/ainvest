# Architecture fixtures

These modules intentionally violate package dependency rules so unit tests can
prove the architecture checker detects forbidden imports.

They are **not** part of `src/ainvest` and must never be imported by production
packages.
