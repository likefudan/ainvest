"""ainvest package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ainvest")
except PackageNotFoundError:  # pragma: no cover - supports direct source-tree imports
    __version__ = "0.0.0+uninstalled"

__all__ = ["__version__"]
