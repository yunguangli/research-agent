"""Vendor-neutral daily bars: one set of OHLCV rules for every price source.

The market analyst's tools, the verification snapshot and the indicators all
need the same thing — daily bars on or before the run date, with look-ahead
removed and stale frames rejected. That handling grew inside the Yahoo vendor,
which made the snapshot Yahoo-only whatever the vendor chain said. It lives here
now: ``load_bars`` walks the configured ``core_stock_apis`` chain (Bloomberg
first, Yahoo as the opt-in fallback, e.g. ``"bloomberg,yfinance"``) and every
vendor's frame is held to the same date, gap and staleness rules.

Vendor SDKs are imported lazily: importing this module never imports ``yfinance``
or ``blpapi``.
"""

from __future__ import annotations

import logging
from io import StringIO

import pandas as pd

from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorError,
    VendorNotConfiguredError,
    VendorUnavailableError,
)

logger = logging.getLogger(__name__)

# A vendor's latest OHLCV row this many calendar days before the requested date
# is treated as stale. Generous enough to span long holiday weekends, tight
# enough to catch the year-old frames yfinance occasionally returns (#1021).
MAX_OHLCV_STALE_DAYS = 10

_DATE_COLUMNS = ("Date", "Datetime", "date", "index")
_PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def _local_midnight(value) -> pd.Timestamp:
    """A single timestamp as its naive, midnight-normalized local date (or NaT)."""
    if pd.isna(value):
        return pd.NaT
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return pd.NaT
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)  # drop tz, keep the local wall-clock date
    return ts.normalize()


def _ensure_date_column(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize the date column to ``Date``.

    Some downloads leave the index unnamed (so ``reset_index()`` yields
    ``index``) or use ``Datetime`` for intraday data. Rename the first
    date-like column so indicators don't silently drop when it isn't ``Date``.
    """
    if "Date" in data.columns:
        return data
    for candidate in ("index", "Datetime", "date"):
        if candidate in data.columns:
            return data.rename(columns={candidate: "Date"})
    # An unnamed index column (``to_csv`` of an index without a name) arrives
    # as "Unnamed: 0"; its values are the dates.
    for candidate in data.columns:
        if str(candidate).startswith("Unnamed"):
            return data.rename(columns={candidate: "Date"})
    return data


def _normalize_dates(dates) -> pd.Series:
    """Parse to naive, midnight-normalized dates so tz-aware or intraday
    timestamps compare correctly against the naive ``as_of_date`` cutoff (#1201).

    Normalized per element: 5 years of bars span daylight-saving changes (and
    cache CSVs round-trip the offsets as strings), so the series can carry mixed
    UTC offsets that ``pd.to_datetime`` cannot unify without ``utc=True`` — which
    would shift non-US (positive-offset) markets to the previous day. Keeping
    each bar's own local date avoids both.
    """
    return pd.to_datetime(pd.Series(dates).map(_local_midnight))


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a bar frame for stockstats: parse/normalize dates and coerce
    prices to numeric (NaN where invalid). Dropping incomplete rows and filling
    gaps is left to ``_fill_price_gaps`` so a caller can first inspect the
    latest in-range bar (#1201)."""
    data = _ensure_date_column(data)
    data["Date"] = _normalize_dates(data["Date"])
    data = data.dropna(subset=["Date"]).copy()

    price_cols = [c for c in _PRICE_COLUMNS if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    return data


def _fill_price_gaps(data: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with no close and forward/back-fill remaining price gaps so
    indicators compute on a continuous series."""
    price_cols = [c for c in _PRICE_COLUMNS if c in data.columns]
    # copy() so a filtered (sliced) input is written to safely, not via a view.
    data = data.dropna(subset=["Close"]).copy()
    data[price_cols] = data[price_cols].ffill().bfill()
    return data


def _coerce_ohlcv_dates(data: pd.DataFrame) -> pd.Series:
    """Return parsed dates from an OHLCV frame, whether Date is a column or the index."""
    if "Date" in data.columns:
        return pd.to_datetime(data["Date"], errors="coerce").dropna()
    if isinstance(data.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(data.index, errors="coerce")).dropna()
    df = data.reset_index()
    for col in ("Date", "Datetime", "date", "index"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce").dropna()
            if not parsed.empty:
                return parsed
    return pd.Series(dtype="datetime64[ns]")


def _assert_ohlcv_not_stale(
    data: pd.DataFrame,
    as_of_date: str,
    symbol: str,
    canonical: str | None = None,
    *,
    max_stale_days: int = MAX_OHLCV_STALE_DAYS,
) -> None:
    """Reject OHLCV whose latest row is far older than as_of_date.

    Raises NoMarketDataError (with a stale-specific detail) so the router treats
    it like any other "no usable data from this vendor" — try the next vendor,
    then emit one clear unavailable signal. Empty frames are left to the
    caller's existing no-data handling; this guards only the dangerous case of
    present-but-stale rows (a vendor returning a year-old frame that would
    otherwise feed wrong prices to the agent, #1021).
    """
    if data is None or data.empty:
        return
    requested = pd.to_datetime(as_of_date, errors="coerce")
    if pd.isna(requested):
        return
    requested = requested.normalize()
    dates = _coerce_ohlcv_dates(data)
    if dates.empty:
        return
    latest = dates.max().normalize()
    stale_days = (requested - latest).days
    if stale_days > max_stale_days:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"latest row is {latest.date()}, {stale_days} days before the "
            f"requested {requested.date()} (stale) — refusing to use it",
        )


def parse_bars_csv(text: str) -> pd.DataFrame:
    """The bars half of a vendor payload: ``#`` comment lines dropped, the CSV
    below parsed and date/price-normalized.

    Every vendor backend returns the same shape — a few ``#`` header lines the
    agent reads, then CSV — so the snapshot and the indicators can consume any
    of them through one parser.
    """
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    frame = pd.read_csv(StringIO("\n".join(lines)))
    if frame.empty:
        return frame
    return _clean_dataframe(_ensure_date_column(frame))


def prepare_bars(
    frame: pd.DataFrame,
    symbol: str,
    as_of_date: str,
    fill_gaps: bool = True,
    canonical: str | None = None,
) -> pd.DataFrame:
    """Hold a bar frame to the run's rules: no look-ahead, no closeless tail,
    gaps handled as asked, staleness rejected.

    This is the part every vendor shares; fetching is the part that differs.
    """
    if frame is None or frame.empty or "Close" not in frame.columns:
        raise NoMarketDataError(symbol, canonical, "no price rows")

    data = _clean_dataframe(frame)
    as_of_dt = pd.to_datetime(as_of_date).normalize()

    # Filter to as_of_date to prevent look-ahead bias in backtesting.
    data = data[data["Date"] <= as_of_dt]
    if data.empty:
        raise NoMarketDataError(symbol, canonical, f"no price rows on or before {as_of_date}")

    # A closeless newest bar is an unsettled session, not a symbol without data.
    # _fill_price_gaps below drops it, here and mid-series alike, so the frame
    # ends at the last settled bar (#1201, #1289).
    if pd.isna(data["Close"].iloc[-1]):
        settled = data["Close"].notna().to_numpy().nonzero()[0]
        if settled.size == 0:
            raise NoMarketDataError(symbol, canonical, "no bar in range has a closing price")
        logger.warning(
            "%s: %d trailing bar(s) through %s have no closing price; using %s as the latest close.",
            canonical or symbol, len(data) - settled[-1] - 1,
            data["Date"].iloc[-1].date(), data["Date"].iloc[settled[-1]].date(),
        )

    # Indicators need a continuous series, so gaps are carried forward. A caller
    # that reports the numbers themselves asks for the frame as it was reported:
    # a filled cell is the previous session's price under this session's date.
    data = _fill_price_gaps(data) if fill_gaps else data.dropna(subset=["Close"]).copy()
    if data.empty:
        raise NoMarketDataError(symbol, canonical, "no bar in range has a closing price")

    _assert_ohlcv_not_stale(data, as_of_date, symbol, canonical)
    return data


def _yfinance_bars(symbol: str, as_of_date: str, fill_gaps: bool) -> pd.DataFrame:
    from tradingagents.dataflows.vendors.yahoo.ohlcv import load_ohlcv

    return load_ohlcv(symbol, as_of_date, fill_gaps=fill_gaps)


def _bloomberg_bars(symbol: str, as_of_date: str, fill_gaps: bool) -> pd.DataFrame:
    from tradingagents.dataflows.vendors.bloomberg.market import bars_frame

    return bars_frame(symbol, as_of_date, fill_gaps=fill_gaps)


# Vendors that can serve a DataFrame of bars. A configured vendor without a
# fetcher is skipped with a typed error, so a chain like "alpha_vantage,yfinance"
# still resolves to the link that can answer.
_FETCHERS = {
    "yfinance": _yfinance_bars,
    "bloomberg": _bloomberg_bars,
}


def _bar_chain() -> list[str]:
    """The configured ``core_stock_apis`` chain, in order."""
    from tradingagents.dataflows.router import VENDOR_METHODS, get_vendor

    configured = get_vendor("core_stock_apis", "get_stock_data") or "default"
    explicit = [v.strip() for v in configured.split(",") if v.strip() and v.strip() != "default"]
    registered = list(VENDOR_METHODS["get_stock_data"])
    if not explicit:
        return registered
    unknown = [v for v in explicit if v not in registered]
    if unknown:
        raise ValueError(
            f"Configured vendor(s) {unknown} not available for 'get_stock_data'. "
            f"Available: {registered}."
        )
    return explicit


def load_bars(symbol: str, as_of_date: str, fill_gaps: bool = True) -> pd.DataFrame:
    """Daily bars on or before ``as_of_date`` from the configured price chain.

    The chain is walked in order (``"bloomberg,yfinance"`` = Bloomberg primary,
    Yahoo as the opt-in fallback). Each link either returns the frame or fails
    with a typed error; the final error keeps the router's precedence — a vendor
    that could not answer never lets another vendor's "no data" speak for it.
    """
    chain = _bar_chain()
    unavailable: VendorError | None = None
    no_data: NoMarketDataError | None = None
    not_configured: list[VendorNotConfiguredError] = []
    failed: Exception | None = None

    for vendor in chain:
        fetch = _FETCHERS.get(vendor)
        if fetch is None:
            not_configured.append(VendorNotConfiguredError(
                f"Vendor {vendor!r} cannot serve daily bars for the snapshot and "
                "indicators; use a price vendor that can (yfinance, bloomberg) or "
                "add one after it in core_stock_apis."
            ))
            continue
        try:
            return fetch(symbol, as_of_date, fill_gaps)
        except NoMarketDataError as exc:
            no_data = exc
        except VendorUnavailableError as exc:
            logger.warning("Price vendor %r unavailable for %s: %s", vendor, symbol, exc)
            unavailable = exc
        except VendorNotConfiguredError as exc:
            not_configured.append(exc)
        except Exception as exc:  # noqa: BLE001 - one broken link must not end the call
            logger.warning("Price vendor %r failed for %s: %s", vendor, symbol, exc)
            failed = exc

    if unavailable is not None:
        raise unavailable
    if no_data is not None:
        if failed is not None:
            logger.warning("Returning no-data for %s, but a vendor errored: %s", symbol, failed)
        raise no_data
    if not_configured:
        raise not_configured[0]
    if failed is not None:
        raise VendorUnavailableError(
            f"No price vendor could answer for {symbol!r} ({failed})"
        ) from failed
    raise VendorNotConfiguredError(
        "No configured vendor in core_stock_apis can serve daily bars. "
        "Set TRADINGAGENTS_DATA_VENDORS, e.g. "
        '{"core_stock_apis":"bloomberg,yfinance"}.'
    )
