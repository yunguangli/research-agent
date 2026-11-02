"""The BLPAPI desktop backend: prices and the company overview from a terminal.

Selected with ``TRADINGAGENTS_BLOOMBERG_BACKEND=blpapi``. ``blpapi`` is imported
only when a request is made, so a Linux CI box or a vanilla install never loads
it (the same rule the Outlook/pywin32 adapters follow).

Session rules:

- one session per process, started on first use and reused after;
- a terminal that is closed or not logged in raises ``VendorUnavailableError``,
  which the routing layer treats as "this vendor could not answer" — so a
  configured chain like ``"bloomberg,yfinance"`` falls back to Yahoo exactly as
  intended, and ``"bloomberg"`` alone reports the outage instead of inventing
  prices.

Every failure is typed: no empty strings, no fabricated bars.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from datetime import date, timedelta

import pandas as pd

from tradingagents.dataflows.bars import _assert_ohlcv_not_stale
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorUnavailableError,
)
from tradingagents.dataflows.vendors.bloomberg.symbols import to_bloomberg

from .backend import register_backend

logger = logging.getLogger(__name__)

REFDATA = "//blp/refdata"
SERVICE_TIMEOUT_S = 10.0
RESPONSE_TIMEOUT_MS = 20_000

# What the market analyst and the overview ask for, and nothing more: fields
# verified against a live terminal. A field the entitlement does not cover comes
# back as a fieldException, which is reported, not guessed around.
HISTORICAL_FIELDS = ("OPEN", "HIGH", "LOW", "PX_LAST", "VOLUME")

# The income statement, all four lines verified live on 4062 JP Equity as a
# QUARTERLY history dated on fiscal period ends. The header labels below are
# exactly what research/vendor_facts.parse_annual_statement matches.
INCOME_FIELDS = ("IS_ADJ_SALES_REVENUE_TURNOVER", "IS_OPER_INC", "NET_INCOME")
INCOME_LABELS = {
    "IS_ADJ_SALES_REVENUE_TURNOVER": "Total Revenue",
    "IS_OPER_INC": "Operating Income",
    "NET_INCOME": "Net Income Common Stockholders",
}

OVERVIEW_FIELDS = (
    "NAME",
    "GICS_SECTOR_NAME",
    "GICS_INDUSTRY_NAME",
    "PX_LAST",
    "CUR_MKT_CAP",
    "EQY_PE_RATIO",
    "EQY_SH_OUT",
    "CRNCY",
    "GROSS_MARGIN",
    "OPER_MARGIN",
    "EBITDA_MARGIN",
    "NET_INCOME",
    "VOLATILITY_260D",
    "LAST_UPDATE_DT",
)

# What the agents' prompts need to name the instrument: who it is, not what it costs.
IDENTITY_FIELDS = ("NAME", "GICS_SECTOR_NAME", "GICS_INDUSTRY_NAME")

# One day's bars are asked for once and reused by the snapshot, the indicators
# and the tool within the same run.
_BARS_TTL_S = 900
_bars_cache: dict[tuple[str, str, str], tuple[float, str]] = {}
_cache_lock = threading.Lock()

_session: object | None = None
_session_lock = threading.Lock()
# One request/response cycle at a time. A blpapi session is not thread-safe:
# two waiters on the same session steal each other's events and both hang until
# their timeout — which is exactly what a run produced ("the Bloomberg desktop
# API timed out") the moment two analysts' tool calls overlapped.
_request_lock = threading.Lock()


def _blpapi():
    try:
        import blpapi  # noqa: PLC0415 - lazy on purpose: Windows/terminal-only SDK
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise VendorNotConfiguredError(
            "The blpapi backend needs Bloomberg's Python SDK. Install it with "
            "pip install 'tradingagents[bloomberg]' (blpapi), keep the Terminal "
            "running, or select another vendor in the chain."
        ) from exc
    return blpapi


def _session_for_request():
    """The process's BLPAPI session, started on first use."""
    global _session
    with _session_lock:
        if _session is not None and _session.isValid():
            return _session
        blpapi = _blpapi()
        session = blpapi.Session(blpapi.SessionOptions())
        try:
            if not session.start():
                raise VendorUnavailableError(
                    "Bloomberg's desktop API did not start; is the Terminal running?"
                )
            if not session.openService(REFDATA):
                session.stop()
                raise VendorUnavailableError(
                    f"Bloomberg's desktop API could not open {REFDATA}; "
                    "is the Terminal running and logged in?"
                )
        except VendorUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - any SDK failure is "vendor down"
            with contextlib.suppress(Exception):
                session.stop()
            raise VendorUnavailableError(
                f"Bloomberg's desktop API could not connect ({exc})"
            ) from exc
        _session = session
        return session


def _cached_bars(key: str, start: str, end: str) -> str | None:
    with _cache_lock:
        hit = _bars_cache.get((key, start, end))
        if hit is None:
            return None
        written, csv = hit
        if time.time() - written > _BARS_TTL_S:
            _bars_cache.pop((key, start, end), None)
            return None
        return csv


def _remember_bars(key: str, start: str, end: str, csv: str) -> None:
    with _cache_lock:
        _bars_cache[(key, start, end)] = (time.time(), csv)


def _securities(message) -> list:
    """The security entries of a response, whatever shape the schema gives us.

    ``HistoricalDataResponse`` hands back one complex element;
    ``ReferenceDataResponse`` wraps it in an array. Indexing the array with
    ``["fieldData"]`` looks for the field on the array itself and fails, so the
    shape decides how to walk it.
    """
    element = message["securityData"]
    if element.isArray():
        return [element.getValue(index) for index in range(element.numValues())]
    return [element]


def _historical_frame(session, key: str, start: str, end: str,
                      fields: tuple[str, ...] = HISTORICAL_FIELDS) -> pd.DataFrame:
    """Daily prices or quarterly fundamentals for one key, as a raw frame."""
    quarterly = fields == INCOME_FIELDS
    service = session.getService(REFDATA)
    request = service.createRequest("HistoricalDataRequest")
    request.append("securities", key)
    for field in fields:
        request.append("fields", field)
    request.set("startDate", start.replace("-", ""))
    request.set("endDate", end.replace("-", ""))
    # ACTUAL + QUARTERLY buckets the series at rolling 3-month intervals counted
    # back from the end date (verified: rows land on 2026-04-30, 2026-07-30 …).
    # Statements must be dated on the fiscal period end the spec uses, which is
    # what FISCAL returns (2026-03-31, 2026-06-30 …). Prices stay ACTUAL.
    request.set("periodicityAdjustment", "FISCAL" if quarterly else "ACTUAL")
    request.set("periodicitySelection", "QUARTERLY" if quarterly else "DAILY")
    session.sendRequest(request)

    deadline = time.time() + RESPONSE_TIMEOUT_MS / 1000
    while time.time() < deadline:
        event = session.nextEvent(5000)
        for message in event:
            kind = str(message.messageType())
            if kind == "SessionTerminated":
                raise VendorUnavailableError("Bloomberg session terminated during the request")
            if kind != "HistoricalDataResponse":
                continue
            for security in _securities(message):
                error = security["securityError"] if security.hasElement("securityError") else None
                if error is not None and error.numValues():
                    raise NoMarketDataError(key, key, str(error["message"]))
                rows = security["fieldData"] if security.hasElement("fieldData") else None
                count = rows.numValues() if rows is not None else 0
                if count == 0:
                    raise NoMarketDataError(key, key, f"no bars between {start} and {end}")
                records = []
                for row_index in range(count):
                    row = rows.getValue(row_index)
                    record = {"Date": str(row.getElement("date").getValue())}
                    for field in fields:
                        record[_RECORD_KEYS.get(field, field)] = _number(row, field)
                    records.append(record)
                return pd.DataFrame(records)
    raise VendorUnavailableError(f"Bloomberg's desktop API timed out for {key}")


def _number(row, name: str) -> float | None:
    element = row.getElement(name)
    if element.isNull():
        return None
    return float(element.getValue())


# Vendor frame columns keep this system's names; income fields pass through as
# the mnemonic (mapped to the vendor_facts row labels when the CSV is written).
_RECORD_KEYS = {"OPEN": "Open", "HIGH": "High", "LOW": "Low", "PX_LAST": "Close", "VOLUME": "Volume"}

_INCOME_ROW_LABELS = INCOME_LABELS


def _income_statement_csv(records: list[dict], *, quarterly: bool, as_of: str) -> str:
    """A statement in the shape ``research.vendor_facts`` parses.

    Values arrive in millions (the terminal's reporting scale) and are written
    in raw units, which the parser scales into the model's unit. Period
    columns are labelled by period end — the same date a spec's periods use.

    ``annual`` sums trailing four quarters at each date, so no fiscal-calendar
    knowledge is needed: whichever quarter end matches the spec's year end
    carries the year's total. Columns with an incomplete four-quarter window
    are skipped rather than published as a part year. A period whose results
    may not have been public by ``as_of`` is dropped (the 90-day filing lag).
    """
    from tradingagents.research.vendor_facts import FILING_LAG_DAYS, is_public

    records = [row for row in sorted(records, key=lambda r: r["Date"])
               if is_public(row["Date"], as_of, FILING_LAG_DAYS)]

    columns: dict[str, dict[str, float | None]] = {}
    if quarterly:
        for row in records:
            columns[row["Date"]] = {label: row.get(key) for key, label in INCOME_LABELS.items()}
    else:
        for index in range(3, len(records)):
            window = records[index - 3:index + 1]
            span = (date.fromisoformat(window[-1]["Date"]) - date.fromisoformat(window[0]["Date"])).days
            if not 240 <= span <= 400:
                continue    # four quarters must span about a year
            columns[records[index]["Date"]] = {
                label: (sum(row.get(key) for row in window)
                        if all(row.get(key) is not None for row in window) else None)
                for key, label in INCOME_LABELS.items()
            }

    lines = [
        "# Income statement for the vendor's reported periods, in raw reporting-currency units.",
        f"# Periods are columns by period end; only periods public before {as_of} "
        f"(filing lag applied) appear.",
    ]
    if not columns:
        lines.append("withheld: no period was public early enough for this date.")
        return "\n".join(lines) + "\n"
    lines.append(",".join([""] + list(columns)))
    for label in _INCOME_ROW_LABELS.values():
        cells = [label] + [
            "" if columns[period].get(label) is None
            else str(int(round(columns[period][label] * 1_000_000)))
            for period in columns
        ]
        lines.append(",".join(cells))
    return "\n".join(lines) + "\n"


def _bars_csv(key: str, start: str, end: str) -> str:
    with _request_lock:
        session = _session_for_request()
        frame = _historical_frame(session, key, start, end)
    # Reject a stale frame before it reaches an agent (#1021): the same rule
    # the Yahoo loader applies.
    _assert_ohlcv_not_stale(frame, end, key, key)
    frame = frame.copy()
    for column in ("Open", "High", "Low", "Close"):
        if column in frame.columns:
            frame[column] = frame[column].round(2)
    return frame.to_csv(index=False)


def _reference_fields(key: str, fields: tuple[str, ...]) -> dict[str, object]:
    """One ReferenceDataRequest's worth of fields, or a typed failure."""
    with _request_lock:
        session = _session_for_request()
        service = session.getService(REFDATA)
        request = service.createRequest("ReferenceDataRequest")
        request.append("securities", key)
        for field in fields:
            request.append("fields", field)
        session.sendRequest(request)

        deadline = time.time() + RESPONSE_TIMEOUT_MS / 1000
        while time.time() < deadline:
            event = session.nextEvent(5000)
            for message in event:
                kind = str(message.messageType())
                if kind == "SessionTerminated":
                    raise VendorUnavailableError("Bloomberg session terminated during the request")
                if kind != "ReferenceDataResponse":
                    continue
                values: dict[str, object] = {}
                for security in _securities(message):
                    error = security["securityError"] if security.hasElement("securityError") else None
                    if error is not None and error.numValues():
                        raise NoMarketDataError(key, key, str(error["message"]))
                    row = security["fieldData"] if security.hasElement("fieldData") else None
                    if row is None:
                        continue
                    # fieldData is a complex element: its children are counted by
                    # numElements(), numValues() is 1 however many fields arrived.
                    for field_index in range(row.numElements()):
                        element = row.getElement(field_index)
                        # Name objects are not str: keep the plain name, or every
                        # `values.get("NAME")` lookup comes back empty.
                        values[str(element.name())] = None if element.isNull() else element.getValue()
                    exceptions = security["fieldExceptions"]
                    if exceptions is not None and exceptions.numValues():
                        bad = [
                            exceptions.getValue(i)["fieldId"] for i in range(exceptions.numValues())
                        ]
                        logger.info("Bloomberg fields not available for %s: %s", key, ", ".join(bad))
                if not values:
                    raise NoMarketDataError(key, key, "no fields returned")
                return values
        raise VendorUnavailableError(f"Bloomberg's desktop API timed out for {key}")


@register_backend("blpapi")
class BlpapiBackend:
    """Desktop Terminal over BLPAPI: bars today, statements when implemented."""

    name = "blpapi"

    def __init__(self) -> None:
        # Fails here only if the SDK itself is missing: a terminal that is
        # closed is discovered on the first request, so construction never
        # depends on a live session.
        _blpapi()

    def bars(self, symbol: str, start_date: str, end_date: str) -> str:
        key = to_bloomberg(symbol)
        cached = _cached_bars(key, start_date, end_date)
        if cached is not None:
            csv = cached
        else:
            csv = _bars_csv(key, start_date, end_date)
            _remember_bars(key, start_date, end_date, csv)

        header = f"# Stock data for {key} (from {symbol}) from {start_date} to {end_date}\n"
        header += f"# Total records: {csv.count(chr(10)) - 1}\n\n"
        return header + csv

    def overview(self, symbol: str, as_of: str) -> str:
        key = to_bloomberg(symbol)
        values = _reference_fields(key, OVERVIEW_FIELDS)
        currency = values.get("CRNCY")
        market_cap = values.get("CUR_MKT_CAP")

        fields = [
            ("Name", values.get("NAME")),
            ("Sector", values.get("GICS_SECTOR_NAME")),
            ("Industry", values.get("GICS_INDUSTRY_NAME")),
            ("Price", f"{values['PX_LAST']} {currency}" if values.get("PX_LAST") else None),
            ("Market Cap", f"{market_cap:,.0f} {currency}" if market_cap else None),
            ("PE Ratio", values.get("EQY_PE_RATIO")),
            ("Shares Outstanding (m)", values.get("EQY_SH_OUT")),
            ("Gross Margin", _percent(values.get("GROSS_MARGIN"))),
            ("Operating Margin", _percent(values.get("OPER_MARGIN"))),
            ("EBITDA Margin", _percent(values.get("EBITDA_MARGIN"))),
            ("Net Income (reported, millions)", values.get("NET_INCOME")),
            ("Volatility 260D", values.get("VOLATILITY_260D")),
            ("As of", values.get("LAST_UPDATE_DT")),
        ]
        lines = [f"{label}: {value}" for label, value in fields if value is not None]
        if not lines:
            raise NoMarketDataError(symbol, key, "no usable overview fields")
        return (
            f"# Company Fundamentals for {key} (Bloomberg)\n\n" + "\n".join(lines)
            + "\n\n# Fields are Bloomberg's: money figures are in "
            f"{currency} unless the line says otherwise; margins are percentages."
        )

    def statement(self, symbol: str, kind: str, freq: str, as_of: str) -> str:
        """The income statement as ``vendor_facts`` parses it; other kinds refuse.

        This is the fallback side of the source rule: the corpus's documents are
        the primary evidence, and this serves the figures they do not state —
        quarters and years, dated on period ends, gated by the filing lag.
        """
        if kind != "income":
            raise VendorNotConfiguredError(
                f"The blpapi backend serves prices, the company overview and the "
                f"INCOME statement; {kind} statements need a field sweep that has "
                'not been done — use fundamental_data="sec_edgar" (US filers) or '
                "the research corpus."
            )
        if freq not in ("annual", "quarterly"):
            raise VendorNotConfiguredError(
                f"The blpapi backend serves annual and quarterly income statements, "
                f"not {freq!r}."
            )

        run_date = str(as_of or "")[:10] or date.today().isoformat()
        start = (date.fromisoformat(run_date) - timedelta(days=4 * 365 + 100)).isoformat()
        key = to_bloomberg(symbol)
        with _request_lock:
            session = _session_for_request()
            frame = _historical_frame(session, key, start, run_date, fields=INCOME_FIELDS)

        # The CSV writer applies the filing lag: a period that may not have been
        # public by the run date never reaches the prompt or the analyst.
        records = [
            {"Date": day, **values}
            for day, values in zip(frame["Date"].astype(str), frame.to_dict("records"), strict=False)
        ]
        return _income_statement_csv(records, quarterly=(freq == "quarterly"), as_of=run_date)

    def identity(self, symbol: str) -> dict:
        """The company's name and classification, for the agents' prompts.

        Returns the system's identity keys directly (``company_name``,
        ``sector``, ``industry``), so the caller never sees vendor field names.
        """
        key = to_bloomberg(symbol)
        values = _reference_fields(key, IDENTITY_FIELDS)
        identity: dict[str, str] = {}
        for field, target in (
            ("NAME", "company_name"),
            ("GICS_SECTOR_NAME", "sector"),
            ("GICS_INDUSTRY_NAME", "industry"),
        ):
            value = values.get(field)
            if value:
                identity[target] = str(value)
        return identity


def _percent(value) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}%"
