"""Prompt context shared by the agents: instrument identity, output language and portfolio."""

import functools
import json
import logging
import os
import re
from collections.abc import Mapping
from typing import Any

from tradingagents.dataflows.date_window import is_historical
from tradingagents.dataflows.vendors.yahoo.fundamentals import get_company_profile

logger = logging.getLogger(__name__)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    # The labelled lines are read by the program, so they keep their English
    # label and value: a translated rating line leaves the reader prose to
    # search, where a negated rating ("not a Sell") reads as the call (#1435).
    return (
        f" Write your entire response in {lang}, except the labelled lines the format"
        f" asks for (the \"**Rating**:\" line, \"FINAL TRANSACTION PROPOSAL:\"):"
        f" keep their label and value in English, exactly as specified."
    )


def opponent_argument_or_opening(text: str, opponent: str) -> str:
    """Opponent's latest argument, or an explicit opening marker when empty.

    The first speaker in each debate round receives an empty opponent response;
    interpolating it into a "refute the opponent" prompt makes the model
    fabricate the other side's position. Returning a clear "has not spoken yet"
    marker instead lets it open with its own case (#1176).
    """
    text = (text or "").strip()
    if text:
        return text
    return f"(The {opponent} has not spoken yet — open the debate with your own case.)"


def _clean_identity_value(value: Any) -> str | None:
    """Return a trimmed string, or None for empty / placeholder-ish values."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "n/a", "nan", "null"}:
        return None
    return cleaned


def resolve_instrument_identity(ticker: str) -> dict:
    """Resolve deterministic identity metadata (company name, sector, …) for a ticker.

    This exists to stop the pipeline from hallucinating a *different* company
    when a chart pattern suggests a different industry than the real one
    (#814): without a ground-truth name, the market analyst would pattern-match
    the price action to a narrative and invent an identity that then cascaded
    through every downstream agent.

    Best-effort by design: if yfinance is unavailable, rate-limited, or doesn't
    recognise the ticker, we return ``{}`` and the caller falls back to
    ticker-only context rather than failing before analysis starts. An answer
    is cached for the process; a failed lookup is asked again next time.

    Identity resolves for the same instrument the price path fetches
    (``XAUUSD`` -> ``GC=F``, #983).
    """
    try:
        return _identity(ticker)
    except Exception as exc:  # noqa: BLE001 — fail open, never block the run
        logger.debug("Could not resolve instrument identity for %s: %s", ticker, exc)
        return {}


def _bloomberg_identity(ticker: str) -> dict:
    """Bloomberg's identity fields when a Bloomberg backend is configured.

    A Bloomberg-configured run reads the company's name and classification from
    the source that prices it, so an unreachable Yahoo cannot strip the prompts
    down to a bare ticker. No backend configured costs one environment check,
    leaving vanilla behaviour untouched; any failure falls through to the Yahoo
    profile below rather than ending the lookup.
    """
    if not os.environ.get("TRADINGAGENTS_BLOOMBERG_BACKEND", "").strip():
        return {}
    try:
        from tradingagents.dataflows.vendors.bloomberg.backend import get_backend

        backend = get_backend()
        identity_getter = getattr(backend, "identity", None)
        if identity_getter is None:
            return {}   # a backend that does not answer identity questions
        found = identity_getter(ticker) or {}
    except Exception as exc:  # noqa: BLE001 — identity is best-effort by design
        logger.debug("Bloomberg identity for %s unavailable: %s", ticker, exc)
        return {}
    return {key: value for key, value in found.items() if value}


@functools.lru_cache(maxsize=256)
def _identity(ticker: str) -> dict:
    """The identity fields for ``ticker``; raises if the lookup fails.

    Bloomberg first when a backend is configured (its fields are already the
    final identity keys), otherwise the vendor profile.
    """
    identity = _bloomberg_identity(ticker)
    if identity:
        return identity
    info = get_company_profile(ticker)
    identity = {}
    company_name = _clean_identity_value(info.get("longName")) or _clean_identity_value(
        info.get("shortName")
    )
    if company_name:
        identity["company_name"] = company_name
    for source_key, target_key in (
        ("sector", "sector"),
        ("industry", "industry"),
        ("exchange", "exchange"),
        ("quoteType", "quote_type"),
        ("country", "country"),
        ("currency", "currency"),
    ):
        value = _clean_identity_value(info.get(source_key))
        if value:
            identity[target_key] = value
    return identity


def build_instrument_context(
    ticker: str,
    asset_type: str = "stock",
    identity: Mapping[str, str] | None = None,
    trade_date: str | None = None,
) -> str:
    """Describe the exact instrument so agents preserve identity and ticker.

    When ``identity`` is provided (resolved deterministically via
    :func:`resolve_instrument_identity`), the company name and business
    classification are injected so agents anchor to the real company rather
    than pattern-matching the price chart to a wrong one (#814).

    That profile carries no historical vintage: it describes the company today.
    A run dated earlier gets the current name alone, as a way to tell the
    company apart from others rather than as what it was called then; a sector,
    industry or exchange it holds today is not given, since it may not have held
    on the analysis date.
    """
    is_crypto = asset_type == "crypto"
    instrument_label = "asset" if is_crypto else "instrument"
    context = (
        f"The {instrument_label} to analyze is `{ticker}`. "
        "The tools serve this instrument; refer to it by this exact ticker in every report and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`, `-USD`)."
    )

    identity = identity or {}
    name = identity.get("company_name") or identity.get("name")
    label = "Name" if is_crypto else "Company"
    details = []
    if is_historical(trade_date):
        if name:
            details.append(
                f"{label}: {name} (its current name, given only to identify it; "
                f"on {trade_date} it may have been named differently)"
            )
    else:
        if name:
            details.append(f"{label}: {name}")
        sector, industry = identity.get("sector"), identity.get("industry")
        if sector and industry:
            details.append(f"Business classification: {sector} / {industry}")
        elif sector:
            details.append(f"Sector: {sector}")
        elif industry:
            details.append(f"Industry: {industry}")
        if identity.get("exchange"):
            details.append(f"Exchange: {identity['exchange']}")

    if details:
        context += (
            f" Resolved identity: {'; '.join(details)}. "
            "Do not substitute a different company or ticker unless a tool "
            "result explicitly disproves this resolved identity."
        )

    if is_crypto:
        context += (
            " Treat it as a crypto asset rather than a company, and do not "
            "assume company fundamentals are available."
        )
    return context


def get_instrument_context_from_state(state: Mapping[str, Any]) -> str:
    """Return the instrument context for the current run.

    Prefers the identity-resolved context computed once at run start and
    stored on the state (see ``TradingAgentsGraph.resolve_instrument_context``).
    Falls back to a ticker-only context — with no network lookup — when the
    state was constructed without it (bare programmatic states, tests), so a
    consumer is never forced to make a yfinance call mid-graph.
    """
    context = state.get("instrument_context")
    if isinstance(context, str) and context.strip():
        return context
    return build_instrument_context(
        str(state["company_of_interest"]),
        state.get("asset_type", "stock"),
    )


FIGURE_MARKER_RULE = (
    "\n\nMark the model's figures: when you quote a number that comes from the verified "
    "model above, wrap it in double square brackets, as [[229.5]] or [[JPY 4,111]]. "
    "The sign goes inside the brackets: [[−14]], not −[[14]]. Mark "
    "only figures you took from that model, and never a citation, a source number or a "
    "list number: [[6]] is read as the figure 6. A number you computed yourself (a PEG, a "
    "growth rate, a margin of safety, a position size, a stop or entry level — including "
    "a price you get as EPS × multiple: 215.4 × 50 = 10,770 is your arithmetic, not a "
    "model line) or took from a vendor, broker, filing or consensus stays unmarked. The "
    "brackets are removed before "
    "the report is shown; they let the system check your arithmetic against the model.\n"
    "Checking arithmetic is encouraged: if two figures in the package do not tie, report "
    "the discrepancy in one line rather than silently changing either number.\n"
)

COMPARATIVE_CLAIM_RULE = (
    "\nA comparative claim must be true of the figures printed beside it. Before writing "
    "\"exceeds\", \"above\", \"below\", \"grew\" or \"is X% of\", check the two numbers in "
    "that same sentence — 38.6bn is about 61% of 63.7bn; it does not exceed it. Recompute "
    "every growth rate from the figures you print: a rate that contradicts your own table "
    "(\"SG&A grew 4%\" above a table showing 37,589 vs 35,340 = +6.4%) is an error. "
    "Missing data is a gap, not evidence in either direction — never read direction from "
    "an absence. Two agents agreeing on the same evidence is one observation, not two — "
    "agreement is never cited as support. Write each figure once: never print it bare and "
    "then again in brackets. When the figures do not support a claim, restate the true "
    "relationship or drop the claim.\n"
)

SIZING_RULE = (
    "Without the caller's position size, cost basis, risk tolerance or mandate, keep "
    "sizing and protection qualitative: never prescribe a fixed trim percentage, a specific "
    "stop-loss distance or an exact position change as a portfolio instruction, and never "
    "convert a tactical chart pattern into a portfolio-level action — timing and sizing "
    "stay conditional and descriptive.\n"
)


def research_package_block(state: Mapping[str, Any], max_chars: int = 4000) -> str:
    """The verified model, for the agents that decide rather than gather.

    The Research Manager and Portfolio Manager otherwise see only prose, so a
    multiple the analyst mislabelled propagates into the rating. Bounded: it is a
    summary, not the full report.
    """
    package = str(state.get("research_package") or "").strip()
    if not package:
        return ""
    return (
        "\n\nVERIFIED MODEL (computed from the filings; use these numbers exactly, and do "
        "not substitute a vendor multiple for them):\n"
        + package[:max_chars]
        + "\n"
        + FIGURE_MARKER_RULE
        + COMPARATIVE_CLAIM_RULE
    )


def case_block(state: Mapping[str, Any], max_chars: int = 3000) -> str:
    """The cases each debater argued for, as deltas against the model."""
    raw = str(state.get("debate_cases") or "").strip()
    if not raw:
        return ""
    try:
        cases = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    from tradingagents.research.cases import render_proposals

    table = render_proposals(cases)
    if not table:
        return ""
    return (
        "\n\nCASES ARGUED (deltas against the verified model; the base case is the model "
        "itself, so do not average these):\n" + table[:max_chars] + "\n"
    )


def fill_placeholders(template: str, values: dict) -> str:
    """Substitute every {{placeholder}}; an unknown one becomes null, loudly."""
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    leftover = re.findall(r"\{\{[^}]+\}\}", template)
    if leftover:
        logging.getLogger(__name__).warning(
            "Prompt placeholder(s) with no runtime value: %s", sorted(set(leftover))
        )
        template = re.sub(r"\{\{[^}]+\}\}", "null", template)
    return template


def position_fields(portfolio_text: str) -> tuple[str, str]:
    """Current position and average cost from the rendered portfolio block.

    Three states stay distinct (position / flat / no context): an empty block is
    two nulls, never a flat book — treating "not provided" as "flat" would
    invent a fact about the caller's account.
    """
    text = (portfolio_text or "").strip()
    if not text:
        return "null", "null"
    if "No current position in" in text:
        return "flat — no position at the analysis date", "null"
    quantity = re.search(r"Current position in [^:]+:\s*(-?[\d.,]+)\s*units", text)
    price = re.search(r"average price\s*(-?[\d.,]+)", text)
    position = f"{quantity.group(1)} units" if quantity else "null"
    return position, (price.group(1) if price else "null")


def evidence_pack(ticker: str, as_of: str) -> str:
    """The corpus's document index for this ticker, point-in-time filtered.

    The same listing the corpus tool serves (dates, types, withheld-after-cutoff
    section), so a prompt-time "evidence pack" and a tool call agree on which
    documents existed on the analysis date. No corpus configured is a stated
    gap, not an empty pack.
    """
    from tradingagents.dataflows.vendors.research_corpus import (
        list_research_documents as corpus_list_documents,
        research_dir_from_env,
        resolve_corpus_root,
    )

    root = research_dir_from_env()
    if root is None:
        return "null — no research corpus is configured for this run."
    try:
        return corpus_list_documents(
            ticker=ticker, as_of=as_of, types="", root=str(resolve_corpus_root(root, ticker))
        )
    except Exception as exc:  # noqa: BLE001 - a listing failure is a stated gap, never a crash
        return f"null — the evidence pack could not be listed ({type(exc).__name__})."


def report_or_absent(text: str, source: str) -> str:
    """An analyst's report, or a marker saying it was never produced.

    A report is empty when its analyst was not selected, refused, or returned
    nothing. Interpolating that into a labelled section presents an absence as a
    blank finding, and the reading agent fills it in from nothing, the same way
    an empty opponent argument used to invite an invented rebuttal (#1176).
    """
    text = (text or "").strip()
    if text:
        return text
    return f"(No {source} report in this run: it is not available, not an empty finding.)"


def get_portfolio_context_from_state(state: Mapping[str, Any]) -> str:
    """Return the caller's portfolio block, or a notice that none was given.

    A run without portfolio context must not read as a flat book: the agents
    would otherwise size as if the caller held nothing, which is a claim about
    an account we were never told about.
    """
    context = state.get("portfolio_context")
    if isinstance(context, str) and context.strip():
        return context
    return (
        "Portfolio context: not provided. You do not know the caller's current "
        "holdings or cash, so do not assume a flat book; give direction and "
        "sizing guidance in terms the caller can apply to their own position."
    )
