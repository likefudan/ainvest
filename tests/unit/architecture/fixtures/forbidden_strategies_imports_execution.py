"""Intentionally invalid fixture: strategies must never import execution.

This module is parsed by architecture unit tests only. It is not part of
``src/ainvest`` and must never be imported by production packages.
"""

from ainvest import execution as _forbidden_execution_import

# Keep a reference so Ruff does not treat the import as unused.
_FIXTURE_SENTINEL = _forbidden_execution_import
