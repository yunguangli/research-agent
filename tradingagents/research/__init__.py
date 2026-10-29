"""Deterministic fundamental research: evidence, model, valuation, report.

The rule for everything in this package: Python computes, the LLM explains.
A number that appears in a report must come from a model line, and a model line
must say how it is known.
"""

from .kernel import ModelError, ModelResult, run
from .render import annual_table, bridge_table, cashflow_table, segment_table, to_markdown
from .report import build, build_markdown, load_spec, write_package
from .schemas import (
    Bridge,
    Citation,
    Financials,
    Line,
    ModelSpec,
    Period,
    Scenario,
    Segment,
    ShareBase,
)
from .valuation import Valuation, value

__all__ = [
    "Bridge",
    "Citation",
    "Financials",
    "Line",
    "ModelError",
    "ModelResult",
    "ModelSpec",
    "Period",
    "Scenario",
    "Segment",
    "ShareBase",
    "Valuation",
    "annual_table",
    "bridge_table",
    "build",
    "build_markdown",
    "cashflow_table",
    "load_spec",
    "run",
    "segment_table",
    "to_markdown",
    "value",
    "write_package",
]
