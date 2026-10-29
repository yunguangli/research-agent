"""Bloomberg data vendor: the seam, not the feed.

Bloomberg is not one API. A deployment uses whichever backend it is entitled to —
BLPAPI on a desktop terminal, B-PIPE on a server, or Data License in batch — and
each has different capabilities. This package defines what the rest of the system
needs (bars, statements, overview) and refuses to guess when no backend is
configured.

Nothing here imports ``blpapi`` at module import: it is Windows/terminal-only.
Select a backend with::

    TRADINGAGENTS_BLOOMBERG_BACKEND=blpapi

and register the vendor per category::

    TRADINGAGENTS_DATA_VENDORS={"core_stock_apis":"bloomberg","fundamental_data":"bloomberg"}

Until a backend is implemented, every call raises ``VendorNotConfiguredError``
with the missing piece named, so a misconfigured deployment fails at the first
data request instead of returning nothing.
"""

from .backend import BloombergBackend, get_backend, register_backend
from .fundamentals import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
)
from .market import get_stock_data, get_stock_indicators

__all__ = [
    "BloombergBackend",
    "get_backend",
    "register_backend",
    "get_balance_sheet",
    "get_cashflow",
    "get_fundamentals",
    "get_income_statement",
    "get_stock_data",
    "get_stock_indicators",
]
