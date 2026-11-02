"""Yahoo-style ticker → Bloomberg yellow-key mapping.

One instrument, two naming worlds: the system speaks Yahoo symbols
(``5803.T``, ``NVDA``) because that is what the original vendors and the
company-identity resolver use, and Bloomberg wants ``5803 JP Equity``. The map
lives here so one table decides it, instead of every backend guessing.

A suffix the table does not know raises ``VendorNotConfiguredError`` rather
than guessing: a wrong key is a wrong instrument, and the configured chain can
then fall back to the next vendor (``"bloomberg,yfinance"``). Override or add
entries with::

    TRADINGAGENTS_BLOOMBERG_TICKERS='{"BTC-USD": "XBTUSD Curncy"}'
"""

from __future__ import annotations

import json
import os

from tradingagents.dataflows.errors import VendorNotConfiguredError
from tradingagents.dataflows.symbols import canonical_ticker

# Exchange suffix → Bloomberg country/exchange key. Empty key = US listing.
_SUFFIX_KEYS: dict[str, str] = {
    "": "US",
    ".T": "JP",
    ".HK": "HK",
    ".SS": "CH",
    ".SZ": "CH",
    ".L": "LN",
    ".NS": "NS",
    ".BO": "BO",
    ".TO": "CN",
    ".AX": "AU",
    ".PA": "FP",
    ".DE": "GR",
    ".MI": "MI",
    ".AS": "AS",
    ".SW": "SW",
    ".ST": "SS",
    ".IS": "IR",
    ".KS": "KS",
    ".KQ": "KQ",
    ".TW": "TT",
    ".SI": "SP",
    ".SA": "BZ",
    ".MX": "MM",
}

# Symbols already in Bloomberg form pass through untouched.
_BBG_FORMS = (" Equity", " Index", " Curncy", " Govt", " Comdty")


def _overrides() -> dict[str, str]:
    raw = os.environ.get("TRADINGAGENTS_BLOOMBERG_TICKERS", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VendorNotConfiguredError(
            f"Invalid TRADINGAGENTS_BLOOMBERG_TICKERS: not a JSON object ({exc})"
        ) from exc
    if not isinstance(value, dict):
        raise VendorNotConfiguredError(
            "TRADINGAGENTS_BLOOMBERG_TICKERS must be a JSON object of "
            '"<symbol>": "<Bloomberg key>".'
        )
    return {str(k): str(v) for k, v in value.items()}


def to_bloomberg(symbol: str) -> str:
    """The Bloomberg security key for a Yahoo-style symbol.

    Raises ``VendorNotConfiguredError`` when no mapping exists, so the caller
    either names the key in ``TRADINGAGENTS_BLOOMBERG_TICKERS`` or lets the
    configured vendor chain try the next link.
    """
    raw = str(symbol or "").strip()
    if not raw:
        raise VendorNotConfiguredError("Empty symbol: no Bloomberg key to build.")

    if any(raw.endswith(form) for form in _BBG_FORMS):
        return raw

    override = _overrides()
    if raw in override:
        return override[raw]

    # 3711.TT and 3711.TW are one instrument: take the suffix table's side of
    # the mapping (TT is Bloomberg's own code and is not in it).
    raw = canonical_ticker(raw)
    upper = raw.upper()
    # Yahoo index notation (^N225), crypto and FX pairs (BTC-USD, EURUSD=X) are
    # not exchange-suffixed equities; refuse rather than send a wrong key. The
    # configured chain can still fall back to a vendor that knows them, or the
    # symbol can be added to TRADINGAGENTS_BLOOMBERG_TICKERS.
    if raw.startswith("^") or "-" in raw or upper.endswith("=X"):
        raise _unmapped(raw)

    suffix = ""
    if "." in raw:
        head, _, tail = raw.rpartition(".")
        suffix = "." + tail
        if head:
            upper = head.upper()

    key = _SUFFIX_KEYS.get(suffix.upper() if suffix else "")
    if key is None:
        raise _unmapped(raw)
    if not upper:
        raise _unmapped(raw)
    return f"{upper} {key} Equity"


def _unmapped(symbol: str) -> VendorNotConfiguredError:
    return VendorNotConfiguredError(
        f"No Bloomberg mapping for {symbol!r}. Add it with "
        'TRADINGAGENTS_BLOOMBERG_TICKERS=\'{"' + str(symbol) + '": "<Bloomberg key>"}\', '
        "or list another vendor after bloomberg in the chain "
        '(e.g. "bloomberg,yfinance").'
    )
