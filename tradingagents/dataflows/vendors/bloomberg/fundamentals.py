"""Fundamentals from Bloomberg.

Field names differ per backend (B-PIPE fields, Data License tables, terminal
screens), so each backend returns formatted text and this module only enforces
the point-in-time rule: a statement must be the one filed on or before the run
date, never a present-day snapshot.
"""

from __future__ import annotations

from .backend import get_backend


def get_fundamentals(ticker: str, curr_date: str) -> str:
    """Company overview as of ``curr_date``."""
    return get_backend().overview(ticker, curr_date)


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    return get_backend().statement(ticker, "balance_sheet", freq, curr_date or "")


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    return get_backend().statement(ticker, "cashflow", freq, curr_date or "")


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    return get_backend().statement(ticker, "income", freq, curr_date or "")
