"""Paper broker and Robinhood MCP write path.

Execution is the only package allowed to receive broker write-tool access.
Upstream research and strategy packages must not import this package.
"""

__all__: list[str] = []
