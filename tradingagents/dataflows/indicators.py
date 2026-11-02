"""Shared technical-indicator computation.

Indicators are computed from a bar frame with stockstats; the vendor only
decides where the bars come from. One implementation means the Bloomberg path
and the Yahoo path produce identical numbers from identical bars, and a new
price vendor gets indicators by handing us its frame.

``INDICATOR_DOCS`` is what the tool shows the model: name, what it measures,
how it is used. Keep it vendor-neutral.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd
from dateutil.relativedelta import relativedelta
from stockstats import wrap

from tradingagents.dataflows.errors import VendorError

logger = logging.getLogger(__name__)

INDICATOR_DOCS: dict[str, str] = {
    # Moving Averages
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. "
        "Usage: Identify trend direction and serve as dynamic support/resistance. "
        "Tips: It lags price; combine with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. "
        "Usage: Confirm overall market trend and identify golden/death cross setups. "
        "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. "
        "Usage: Capture quick shifts in momentum and potential entry points. "
        "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
    ),
    # MACD Related
    "macd": (
        "MACD: Computes momentum via differences of EMAs. "
        "Usage: Look for crossovers and divergence as signals of trend changes. "
        "Tips: Confirm with other indicators in low-volatility or sideways markets."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. "
        "Usage: Use crossovers with the MACD line to trigger trades. "
        "Tips: Should be part of a broader strategy to avoid false positives."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal line. "
        "Usage: Visualize momentum strength and spot divergence early. "
        "Tips: Can be volatile; complement with additional filters for fast-moving markets."
    ),
    # Momentum Indicators
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. "
        "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
        "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
    ),
    # Volatility Indicators
    "boll": (
        "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
        "Usage: Acts as a dynamic benchmark for price movement. "
        "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
    ),
    "boll_ub": (
        "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
        "Usage: Signals potential overbought conditions and breakout zones. "
        "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
    ),
    "boll_lb": (
        "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
        "Usage: Indicates potential oversold conditions and support zones. "
        "Tips: Use additional analysis to avoid false reversal signals."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. "
        "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
        "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
    ),
    # Volume-Based Indicators
    "vwma": (
        "VWMA: A moving average weighted by volume. "
        "Usage: Confirm trends by integrating price action with volume data. "
        "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
    ),
    "mfi": (
        "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
        "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
        "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
    ),
}


def bulk_values(load, symbol: str, indicator: str, as_of_date: str) -> dict[str, str]:
    """Every date's value for one indicator, fetched once.

    ``load(symbol, as_of_date, fill_gaps=True)`` is the vendor's bar frame.
    """
    data = load(symbol, as_of_date)
    df = wrap(data)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    df[indicator]  # This triggers stockstats to calculate the indicator

    result: dict[str, str] = {}
    for _, row in df.iterrows():
        value = row[indicator]
        result[row["Date"]] = "N/A" if pd.isna(value) else str(value)
    return result


def value_on_date(load, symbol: str, indicator: str, as_of_date: str):
    """The indicator's value on ``as_of_date``, or a not-a-trading-day note."""
    data = load(symbol, as_of_date)
    df = wrap(data)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    as_of_str = pd.to_datetime(as_of_date).strftime("%Y-%m-%d")

    df[indicator]  # trigger stockstats to calculate the indicator
    matching = df[df["Date"].str.startswith(as_of_str)]
    if not matching.empty:
        return matching[indicator].values[0]
    return "N/A: Not a trading day (weekend or holiday)"


def render_window(
    load,
    on_date,
    symbol: str,
    indicator: str,
    as_of_date: str,
    look_back_days: int,
) -> str:
    """The tool's text: a value per day for the lookback window, plus the docs.

    ``load`` fetches the bar frame (bulk path), ``on_date`` answers a single day
    and carries the vendor's own error semantics (the per-day fallback).
    """
    if indicator not in INDICATOR_DOCS:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: "
            f"{list(INDICATOR_DOCS.keys())}"
        )

    as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    before = as_of_dt - relativedelta(days=look_back_days)

    try:
        indicator_data = bulk_values(load, symbol, indicator, as_of_date)

        date_values = []
        current_dt = as_of_dt
        while current_dt >= before:
            date_str = current_dt.strftime("%Y-%m-%d")
            value = indicator_data.get(date_str, "N/A: Not a trading day (weekend or holiday)")
            date_values.append((date_str, value))
            current_dt -= relativedelta(days=1)

        ind_string = "".join(f"{date_str}: {value}\n" for date_str, value in date_values)
    except VendorError:
        raise  # Unknown/delisted symbol — let the router emit the sentinel
    except Exception as exc:  # noqa: BLE001 - one bad bulk read must not end the tool
        logger.warning("Bulk stockstats fetch failed, falling back per-day: %s", exc)
        ind_string = ""
        current_dt = as_of_dt
        while current_dt >= before:
            date_str = current_dt.strftime("%Y-%m-%d")
            ind_string += f"{date_str}: {on_date(symbol, indicator, date_str)}\n"
            current_dt -= relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {as_of_date}:\n\n"
        + ind_string
        + "\n\n"
        + INDICATOR_DOCS.get(indicator, "No description available.")
    )
