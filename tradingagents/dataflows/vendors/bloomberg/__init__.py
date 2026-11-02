"""Bloomberg data vendor: the seam, not the feed.

Bloomberg is not one API. A deployment uses whichever backend it is entitled to —
BLPAPI on a desktop terminal (implemented: prices and the company overview),
B-PIPE on a server, or Data License in batch — and each has different
capabilities. This package defines what the rest of the system needs (bars,
statements, overview) and refuses to guess when no backend is configured.

Nothing here imports ``blpapi`` at module import: it is Windows/terminal-only,
and a vanilla or Linux install must never load it. Select a backend with::

    TRADINGAGENTS_BLOOMBERG_BACKEND=blpapi

and register the vendor per category — ``"bloomberg,yfinance"`` makes Bloomberg
the primary source with Yahoo as the opt-in fallback::

    TRADINGAGENTS_DATA_VENDORS={"core_stock_apis":"bloomberg,yfinance",
                                "technical_indicators":"bloomberg,yfinance"}

A misconfigured deployment fails at the first data request with the missing
piece named, never with empty data.
"""

from . import blpapi_backend as _blpapi_backend  # noqa: F401 - registers the backend
from .backend import BloombergBackend, get_backend, register_backend
from .fundamentals import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
)
from .market import bars_frame, get_stock_data, get_stock_indicators
from .symbols import to_bloomberg

__all__ = [
    "BloombergBackend",
    "bars_frame",
    "get_backend",
    "register_backend",
    "get_balance_sheet",
    "get_cashflow",
    "get_fundamentals",
    "get_income_statement",
    "get_stock_data",
    "get_stock_indicators",
    "to_bloomberg",
]
