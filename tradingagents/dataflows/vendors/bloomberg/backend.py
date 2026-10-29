"""Backend selection for the Bloomberg vendor.

One interface, several transports. A backend returns the same shapes the other
vendors produce (a CSV string of bars, formatted statements), so the router and
the analysts do not know which Bloomberg product served the request.

Implementing a backend means subclassing ``BloombergBackend`` and registering it
in ``_BACKENDS``. Entitlement checks belong in the backend: a capability the
terminal does not have must raise, not return empty data.
"""

from __future__ import annotations

import os
from typing import Protocol

from tradingagents.dataflows.errors import VendorNotConfiguredError

BACKEND_ENV = "TRADINGAGENTS_BLOOMBERG_BACKEND"


class BloombergBackend(Protocol):
    """What the vendor needs from a Bloomberg transport."""

    name: str

    def bars(self, symbol: str, start_date: str, end_date: str) -> str:
        """Daily OHLCV for ``symbol`` as CSV text, latest row last."""

    def statement(self, symbol: str, kind: str, freq: str, as_of: str) -> str:
        """A formatted statement: ``kind`` is balance_sheet, cashflow or income."""

    def overview(self, symbol: str, as_of: str) -> str:
        """Company overview, or raise if the backend has no such field group."""


_BACKENDS: dict[str, type] = {}


def register_backend(name: str):
    """Register a backend class under the name used in the environment."""

    def decorate(cls):
        _BACKENDS[name.lower()] = cls
        return cls

    return decorate


def get_backend() -> BloombergBackend:
    """The configured backend, or a typed error naming what is missing."""
    configured = os.environ.get(BACKEND_ENV, "").strip().lower()
    if not configured:
        raise VendorNotConfiguredError(
            f"Bloomberg is selected as a data vendor but {BACKEND_ENV} is unset. "
            "Set it to the backend you are entitled to (for example 'blpapi'), "
            "or remove 'bloomberg' from the vendor chain."
        )
    backend_cls = _BACKENDS.get(configured)
    if backend_cls is None:
        raise VendorNotConfiguredError(
            f"{BACKEND_ENV}={configured!r} is not implemented. Known backends: "
            f"{sorted(_BACKENDS) or 'none'}. Implement one in "
            "tradingagents/dataflows/vendors/bloomberg/backend.py."
        )
    try:
        return backend_cls()
    except VendorNotConfiguredError:
        raise
    except Exception as exc:  # noqa: BLE001 - a backend's own init failure
        raise VendorNotConfiguredError(
            f"The Bloomberg backend {configured!r} could not start: {exc}"
        ) from exc
