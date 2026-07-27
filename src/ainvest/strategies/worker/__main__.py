"""``python -m ainvest.strategies.worker`` child entrypoint."""

from __future__ import annotations

from ainvest.strategies.worker.child import main

if __name__ == "__main__":
    raise SystemExit(main())
