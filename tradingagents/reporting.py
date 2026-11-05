"""Reusable report-tree writer shared by the CLI and the programmatic API.

Writes a run's per-section markdown (analysts, research, trading, risk,
portfolio) plus a consolidated ``complete_report.md`` under ``save_path``. The
CLI and ``TradingAgentsGraph.save_reports`` both call this, so a headless / API
run produces the same on-disk report tree a CLI run does.
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from tradingagents.agents.rating import run_rating


def analyst_names(keys) -> list[str]:
    """The analysts as users select them: the sentiment analyst's key is "social"."""
    return ["sentiment" if key == "social" else key for key in keys or []]


def debt_provisional_reason(final_state: dict) -> str:
    """The unresolved challenge that leaves the debt or EV inputs provisional.

    A convertible classified as equity dilutes; classified as debt it belongs in
    gross debt. When the model leaves that unresolved, EV/EBITDA is still
    published, but labelled PROVISIONAL rather than VERIFIED.
    """
    try:
        data = json.loads(str(final_state.get("model_challenges") or "{}"))
    except ValueError:
        return ""
    for challenge in data.get("challenges") or []:
        if str(challenge.get("severity")) not in {"material", "blocking"}:
            continue
        if challenge.get("proposed_values"):
            continue
        fields = " ".join(str(field) for field in (challenge.get("affected_fields") or []))
        if re.search(r"convertible|net_debt|gross_debt|debt|ev_ebitda", fields, re.IGNORECASE):
            reason = str(challenge.get("challenge_type") or "debt treatment unresolved")
            return f"{reason.rstrip('.')} — detected but unresolved; not repaired"
    return ""


def _header(ticker: str, final_state: dict, settings: dict | None) -> str:
    """The report's title and what produced it: analysis date, version, models, analysts, vendors."""
    lines = [f"# Trading Analysis Report: {ticker}", ""]
    if final_state.get("trade_date"):
        lines.append(f"- Analysis date: {final_state['trade_date']}")
    if final_state.get("final_trade_decision"):
        rating = run_rating(final_state)
        if str(final_state.get("model_status") or "").upper() == "UNVERIFIED":
            rating = "Not Rated (provisional)"
        elif str(final_state.get("decision_integrity") or "").startswith("DEGRADED"):
            # The 5-tier value is unchanged; the qualifier says the decision rests
            # on partially validated inputs (fallback prose, no audit, no book).
            rating = f"{rating} (provisional)"
        lines.append(f"- Rating: {rating}")
    lines.append(f"- Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    if settings:
        s = settings.get
        deep_provider = s("deep_think_provider") or s("llm_provider", "?")
        quick_provider = s("quick_think_provider") or s("llm_provider", "?")
        if deep_provider == quick_provider:
            models = f"{deep_provider}, deep {s('deep_think_llm', '?')}, quick {s('quick_think_llm', '?')}"
        else:   # each tier on its own provider (#1440)
            models = (f"deep {deep_provider} {s('deep_think_llm', '?')}, "
                      f"quick {quick_provider} {s('quick_think_llm', '?')}")
        lines.append(f"- TradingAgents {s('version', '?')}: {models}")
        research_used = final_state.get("research_rounds_used")
        risk_used = final_state.get("risk_rounds_used")
        research = (
            f"{research_used} of {s('max_debate_rounds', '?')}"
            if isinstance(research_used, int) else f"{s('max_debate_rounds', '?')}"
        )
        risk = (
            f"{risk_used} of {s('max_risk_discuss_rounds', '?')}"
            if isinstance(risk_used, int) else f"{s('max_risk_discuss_rounds', '?')}"
        )
        lines.append(f"- Analysts: {', '.join(analyst_names(s('analysts')))}; "
                     f"research debate rounds: {research}; risk debate rounds: {risk}")
        if s("decision_horizon"):
            lines.append(f"- Decision horizon: {s('decision_horizon')}; "
                         f"memory-log settlement window {s('holding_period_days', '?')} trading days")
        vendors = {**(s("data_vendors") or {}), **(s("tool_vendors") or {})}
        if vendors:
            lines.append("- Data vendors: " + ", ".join(f"{k} {v}" for k, v in vendors.items()))
    if final_state.get("memory_note"):
        lines.append(f"- Memory log: {final_state['memory_note']}")
    missing = [
        label
        for label, key in (("news", "news_report"), ("sentiment", "sentiment_report"))
        if not str(final_state.get(key) or "").strip()
    ]
    if missing:
        missing_text = " and ".join(missing)
        lines.append(
            f"- Missing inputs: {missing_text} unavailable; no positive, negative, or "
            "neutral adjustment was made. Vendor diagnostics are in the appendix."
        )
    if final_state.get("research_model_source") == "unavailable":
        reason = final_state.get("spec_compatibility_reason") or "no usable model spec"
        lines.append(
            f"- Research model: UNAVAILABLE — {reason}. This is the deterministic "
            "fundamental model, not the LLM: the run continued without its figures."
        )
    elif final_state.get("research_model_path"):
        source = final_state.get("research_model_source") or "unknown"
        created = final_state.get("research_model_created_at") or "unknown"
        compatibility = final_state.get("spec_compatibility") or "unknown"
        reason = final_state.get("spec_compatibility_reason") or ""
        lines.append(
            f"- Research model: {source} from {final_state['research_model_path']} "
            f"(file dated {created}; dependency compatibility {compatibility}"
            + (f" — {reason}" if reason else "")
            + ")"
        )
    if final_state.get("stale_model_path"):
        lines.append(
            f"- Stale model reference: {final_state['stale_model_path']} "
            "(kept for comparison; it did not drive this run)"
        )
    # Deterministic model state: the reader must be able to tell a verified model
    # from a rejected one without reading the appendix.
    status = str(final_state.get("model_status") or "")
    if status:
        lines.append(f"- Model status: {status}")
    aliases = {"eps_model_status": "eps_status", "sensitivity_model_status": "sensitivity_status"}
    for label, key in (
        ("Operating model", "operating_model_status"),
        ("EPS model", "eps_model_status"),
        ("Valuation", "valuation_status"),
        ("Sensitivity model", "sensitivity_model_status"),
    ):
        value = str(final_state.get(key) or final_state.get(aliases.get(key, "")) or "").strip()
        if value:
            lines.append(f"- {label}: {value}")
    try:
        methods = json.loads(str(final_state.get("valuation_method_statuses") or "{}"))
    except ValueError:
        methods = {}
    if isinstance(methods, dict) and methods:
        lines.append(
            "- Valuation methods: "
            + ", ".join(f"{name} {status}" for name, status in methods.items())
        )
    try:
        ev_inputs = json.loads(str(final_state.get("ev_input_diagnostics") or "{}"))
    except ValueError:
        ev_inputs = {}
    if isinstance(ev_inputs, dict) and ev_inputs.get("inputs"):
        lines.append(
            "- EV/EBITDA inputs: "
            + ", ".join(
                f"{name} {entry.get('status', '')}"
                for name, entry in ev_inputs["inputs"].items()
            )
        )
    provisional = (
        ev_inputs.get("provisional") if isinstance(ev_inputs, dict) else ""
    ) or debt_provisional_reason(final_state)
    if provisional:
        lines.append(f"- EV/EBITDA: PROVISIONAL — {provisional}")
    if os.environ.get("TRADINGAGENTS_FEATURE_EVIDENCE_ATTRIBUTES_V1", "").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        from tradingagents.research import evidence

        brokers = evidence.broker_summary(str(final_state.get("fundamentals_report") or ""))
        if brokers["count"]:
            lines.append(
                f"- Broker sources: {brokers['count']} "
                f"({', '.join(brokers['brokers'])})"
            )
    try:
        component = json.loads(str(final_state.get("component_status") or "{}"))
    except ValueError:
        component = {}
    if isinstance(component, dict) and component:
        method = str((component.get("ev_ebitda_method") or {}).get("status") or "")
        comparison = component.get("ev_per_share_comparison") or {}
        if method:
            lines.append(f"- EV/EBITDA enterprise-value method: {method}")
        if comparison.get("status"):
            reason = f" — {comparison['reason']}" if comparison.get("reason") else ""
            lines.append(f"- EV/EBITDA per-share comparison: {comparison['status']}{reason}")
        controlling = component.get("controlling_valuation") or {}
        if method:
            suffix = "" if controlling.get("ev_controlling") else " (EV/EBITDA is a cross-check)"
            lines.append(f"- Controlling valuation use: {controlling.get('use', 'pe only')}{suffix}")
        sensitivity = component.get("sensitivity") or {}
        controlling_sensitivity = component.get("controlling_sensitivity") or {}
        failed_rows = int(sensitivity.get("failed_rows") or 0)
        if failed_rows:
            lines.append(
                f"- Reason: {failed_rows} published rows failed downstream propagation"
            )
        if controlling_sensitivity.get("status"):
            lines.append(
                f"- Controlling sensitivities: {controlling_sensitivity['status']}, "
                f"{controlling_sensitivity.get('verified', 0)} of "
                f"{controlling_sensitivity.get('expected', 0)} controlling candidates verified"
            )
        if (component.get("decision_use") or {}).get("failed_rows_excluded"):
            lines.append(
                "- Decision-use exclusion: failed rows excluded from decision evidence"
            )
        reason = str((component.get("decision") or {}).get("provisional_reason") or "")
        if reason:
            lines.append(f"- Provisional reason: {reason}")
    components = {
        str(final_state.get(key) or "")
        for key in ("operating_model_status", "eps_model_status",
                    "valuation_status", "sensitivity_model_status")
    }
    if "VERIFIED" in components and components - {"VERIFIED", ""}:
        lines.append("- Overall research status: PARTIALLY VERIFIED")
    if final_state.get("overall_status"):
        lines.append(f"- Overall status: {final_state['overall_status']}")
    if final_state.get("price_conflict"):
        lines.append(f"- Price conflict: {final_state['price_conflict']}")
    if final_state.get("decision_status"):
        lines.append(f"- Decision status: {final_state['decision_status']}")
    if status == "UNVERIFIED":
        lines.append(
            "- Model output: suppressed — no unverified model figure may be used for the decision"
        )
    elif status == "NOT_APPLICABLE" and not final_state.get("research_model_path"):
        lines.append(
            "- Number audit: not run — no research corpus is configured (no deterministic model)"
        )
    try:
        blocking = json.loads(str(final_state.get("blocking_issue") or "[]"))
    except ValueError:
        blocking = []
    for issue in blocking if isinstance(blocking, list) else []:
        lines.append(f"- Blocking challenge: {issue.get('challenge_type') or 'unspecified'}")
        lines.append(
            "- Affected fields: " + (", ".join(issue.get("affected_fields") or []) or "not stated")
        )
        lines.append(f"- Verification result: {issue.get('verification_result') or 'unresolved'}")
        lines.append(
            "- Repair attempted: " + ("yes" if issue.get("repair_attempted") else "no")
        )
        if issue.get("failure_reason"):
            lines.append(f"- Repair failure reason: {issue['failure_reason']}")
        lines.append(
            f"- Publication impact: {issue.get('publication_impact') or 'model outputs suppressed'}"
        )
    if final_state.get("sizing_instruction"):
        lines.append(f"- Sizing control: matched \"{final_state['sizing_instruction']}\"")
    if str(final_state.get("trader_status") or "") == "advisory":
        lines.append("- Trader output: advisory — levels and size are not schema-validated")
    if final_state.get("decision_basis"):
        lines.append(f"- Decision basis: {final_state['decision_basis']}")
    version = str(final_state.get("active_model_version") or "")
    if version:
        lines.append(f"- Active model version: {version}")
    repairs = int(final_state.get("repairs_applied") or 0)
    changed_fields: list[str] = []
    try:
        changed_fields = [
            str(change.get("field")) for change in
            (json.loads(str(final_state.get("model_repair_log") or "{}")).get("changes") or [])
        ]
    except ValueError:
        changed_fields = []
    if repairs:
        fields = ", ".join(changed_fields[:4]) + ("…" if len(changed_fields) > 4 else "")
        lines.append(f"- Repairs applied: {repairs} — {fields}")
        lines.append(f"- Controlling model: v2. Repairs applied: {repairs} — {fields}.")
    else:
        lines.append("- Repairs applied: none")
    lines.append(
        "- Unresolved challenges: "
        f"{int(final_state.get('unresolved_blocking') or 0)} blocking, "
        f"{int(final_state.get('unresolved_material') or 0)} material"
    )
    lines.append(
        "- Share bases: market cap "
        f"{final_state.get('market_cap_basis') or 'not provided'}; forecast EPS "
        f"{final_state.get('forecast_eps_basis') or 'not provided'}; dilution sensitivity "
        f"{final_state.get('dilution_sensitivity_basis') or 'not provided'}; equity value "
        f"{final_state.get('equity_value_share_basis') or 'not provided'}"
    )
    low = final_state.get("controlling_fair_value_low")
    high = final_state.get("controlling_fair_value_high")
    base = final_state.get("controlling_fair_value_base")
    if low is not None and high is not None:
        centre = f" (base {base:,.0f})" if isinstance(base, (int, float)) else ""
        lines.append(f"- Controlling fair value: {float(low):,.0f}–{float(high):,.0f}{centre}")
    else:
        lines.append("- Controlling fair value: not provided")
    if settings and settings.get("run_purpose") not in (None, "production"):
        lines.append(
            f"- Authority: diagnostic (run purpose: {settings['run_purpose']}); "
            "this run is not recorded as a production decision"
        )
    if final_state.get("decision_integrity"):
        lines.append(f"- Decision integrity: {final_state['decision_integrity']}")
    repairs = final_state.get("model_repair_log")
    if repairs and str(final_state.get("active_model_version") or "") == "v2":
        try:
            changes = json.loads(str(repairs)).get("changes") or []
        except ValueError:
            changes = []
        lines.append(
            f"- Model repair: v1 -> v2, {len(changes)} field(s) changed; figures from v1 are "
            "superseded (see the appendix)"
        )
    review_missing = str(
        (final_state.get("investment_debate_state") or {}).get("review_missing") or ""
    ).strip()
    if review_missing:
        lines.append(
            f"- Adversarial review: incomplete — missing or empty sections: {review_missing}"
        )
    conflicts = final_state.get("decision_conflicts") or []
    if conflicts:
        lines.append("- Decision conflicts: " + "; ".join(conflicts))
    stage_status = final_state.get("stage_status") or {}
    if stage_status:
        lines.append(
            "- Stage status: "
            + "; ".join(f"{stage} {status}" for stage, status in stage_status.items())
        )
    if final_state.get("controlling_decision"):
        lines.append(
            "- Controlling decision: Portfolio Manager "
            "(Research Manager and Trader are in the appendix)"
        )
    fallbacks = final_state.get("structured_fallbacks") or {}
    if fallbacks:
        detail = "; ".join(
            f"{agent}: {str(reason)[:160]}" for agent, reason in fallbacks.items()
        )
        lines.append(
            f"- Structured output: {detail} — these proposals were not produced by "
            "their full schema"
        )
    return "\n".join(lines) + "\n\n"


# The report's sections in order: (heading, folder, [(agent, file, state path)]).
# Every one is written as its own section file and into `appendix.md`; the
# investor-facing `complete_report.md` is built by `_publication_sections`, which
# selects and caps, and never concatenates these raw reports.
_SECTIONS = (
    ("I. Analyst Team Reports", "1_analysts", (
        ("Market Analyst", "market.md", ("market_report",)),
        ("Sentiment Analyst", "sentiment.md", ("sentiment_report",)),
        ("News Analyst", "news.md", ("news_report",)),
        ("Fundamentals Analyst", "fundamentals.md", ("fundamentals_report",)),
    )),
    ("Fundamental Research Package", "1b_research", (
        ("Fundamental Research", "research_package.md",
         (("research_package_artifact",), ("research_package",))),
    )),
    ("Number Audit", "1c_audit", (
        ("Figures Not In The Model", "numbers.md", ("report_audit",)),
    )),
    ("Case Comparison", "1d_cases", (
        ("Cases Recomputed On The Model", "cases.md", ("case_table",)),
    )),
    ("Growth and Incremental Margin", "1e_metrics", (
        ("Period Metrics", "metrics.md", ("metrics_table",)),
    )),
    ("Stale Model Reference", "1b_research", (
        ("Previous Specification (not used)", "stale_model.md", ("stale_model_package",)),
    )),
    ("Model Repair", "1b_research", (
        ("Model Repair Note", "repair.md", ("model_repair_addendum",)),
    )),
    ("Adversarial Debate", "2_research", (
        ("Round 1 and material updates", "debate.md", ("debate_summary",)),
    )),
    ("II. Research Team Decision", "2_research", (
        ("Adversarial Investment Review", "challenge.md",
         ("investment_debate_state", "challenge_history")),
        ("Research Manager", "manager.md", ("investment_plan",)),
    )),
    ("III. Trading Team Plan", "3_trading", (
        ("Trader", "trader.md", ("trader_investment_plan",)),
    )),
    ("IV. Risk Management Team Decision", "4_risk", (
        ("Aggressive Analyst", "aggressive.md", ("risk_debate_state", "aggressive_history")),
        ("Conservative Analyst", "conservative.md", ("risk_debate_state", "conservative_history")),
        ("Neutral Analyst", "neutral.md", ("risk_debate_state", "neutral_history")),
    )),
    ("V. Portfolio Manager Decision", "5_portfolio", (
        ("Portfolio Manager", "decision.md", ("final_trade_decision",)),
    )),
)


def _state_value(state: dict, path: tuple):
    """The value at ``path``, or None when a level is missing."""
    value = state
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


_NEWS_DIAGNOSTICS = re.compile(
    r"(?im)^#{1,3}\s*(?:news\s+)?(?:data\s+|vendor\s+|retry\s+)?"
    r"(?:diagnostics|retries|retry history|vendor errors|data limitations)\b"
)


def _split_news_diagnostics(text: str) -> tuple[str, str]:
    """The news report without its diagnostics tail, and that tail."""
    match = _NEWS_DIAGNOSTICS.search(text or "")
    if not match:
        return text, ""
    return text[: match.start()].rstrip(), text[match.start():].strip()


def _report_parts(final_state: dict) -> list[tuple[str, str, list[tuple[str, str, str]]]]:
    """The sections with something to say: (heading, folder, [(agent, file, text)]).

    A section's ``path`` is a key path, or a tuple of alternative key paths tried
    in order: the published model prefers the artifact rendering and falls back
    to the prompt rendering a checkpoint saved before that split.

    Every section is written to its folder and to ``appendix.md``; the main
    report is built by ``_publication_sections`` from the run's state.

    The agents mark the figures they take from the model as ``[[229.5]]`` so the
    audit can check them; a reader should never see the brackets, so they are
    removed here, once, for both the Markdown and the HTML.
    """
    from tradingagents.research.audit import strip_marks

    parts = []
    for heading, folder, agents in _SECTIONS:
        written = []
        for agent, filename, path in agents:
            paths = path if path and isinstance(path[0], tuple) else (path,)
            value = next(
                (found for found in (_state_value(final_state, candidate) for candidate in paths)
                 if found),
                None,
            )
            if value:
                text = strip_marks(str(value))
                if agent == "News Analyst":
                    body, diagnostics = _split_news_diagnostics(text)
                    if diagnostics:
                        written.append((agent, filename, body))
                        written.append(
                            ("News Diagnostics", "news_diagnostics.md", diagnostics)
                        )
                        continue
                written.append((agent, filename, text))
        if written:
            parts.append((heading, folder, written))
    return parts


# Publication limits. The main report keeps complete components only; the full
# uncut text always lives in `appendix.md`.
MAX_MAIN_WORDS = 5000
SECTION_BUDGETS = {
    "Executive Summary": 300,
    "Fundamental Summary": 1800,
    "Forecast and Valuation": 900,
    "Debate Summary": 500,
    "Technical Summary": 320,
    "Catalysts and Risks": 350,
    "Portfolio Manager Decision": 400,
}

_APPENDIX_POINTER = "_Further detail is in `appendix.md`._"
_EVIDENCE_TAGS = ("fundamental", "valuation", "technical", "news_sentiment", "portfolio_action")
_HEADING_RE = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")


def _words(text: str) -> int:
    return len(str(text or "").split())


def _bold_field(text: str, field: str) -> str:
    match = re.search(rf"\*\*{re.escape(field)}\*\*:\s*(.+?)(?:\n\n|\Z)", text or "", re.S)
    return match.group(1).strip() if match else ""


def _row_value(text: str, label: str) -> str:
    match = re.search(rf"(?m)^\|\s*{re.escape(label)}\s*\|\s*(.+?)\s*\|\s*$", text or "")
    return match.group(1).strip() if match else ""


def _normalise_tables(text: str) -> str:
    """Turn indented pipe rows into real Markdown tables; leave real ones alone."""
    lines = (text or "").splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.count("|") >= 2 and not stripped.startswith("|"):
            rows: list[str] = []
            while index < len(lines) and lines[index].strip().count("|") >= 2:
                cells = [cell.strip() for cell in lines[index].strip().split("|")]
                rows.append("| " + " | ".join(cells) + " |")
                index += 1
            columns = rows[0].count("|") - 1
            out.append(rows[0])
            out.append("|" + "---|" * columns)
            out.extend(rows[1:])
            continue
        out.append(lines[index])
        index += 1
    return "\n".join(out)


_COMPUTED_MARK = re.compile(
    r"\[\[\s*computed\s*:\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*=\s*[^\]]+?\]\]", re.IGNORECASE
)
_SIZING_PHRASES = re.compile(
    r"(?i)\b(?:cut by half|small starter|benchmark weight|take to zero|meaningful size|"
    r"half (?:a )?standard weight|full position|quarter position|double weight)\b"
)
_SIZING_REPLACEMENT = (
    "Reduce exposure if the stated deterioration condition occurs; "
    "no target size is prescribed without portfolio context."
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _redact_sizing(text: str) -> str:
    """Replace an unsupported sizing clause with qualitative wording.

    No placeholder is shown to the reader; the decision's own section file and
    `appendix.md` keep the original wording and the validation finding.
    """
    lines: list[str] = []
    for line in str(text or "").splitlines():
        if not _SIZING_PHRASES.search(line):
            lines.append(line)
            continue
        match = re.match(r"^(\s*(?:[-*]\s+)?)(.*)$", line)
        prefix, body = (match.group(1), match.group(2)) if match else ("", line)
        sentences = _SENTENCE_SPLIT.split(body)
        rewritten = [
            _SIZING_REPLACEMENT if _SIZING_PHRASES.search(sentence) else sentence
            for sentence in sentences
        ]
        lines.append(prefix + " ".join(rewritten))
    return "\n".join(lines)


def _component_status_section(final_state: dict) -> str:
    """The shared component status, in the appendix and the report header."""
    try:
        component = json.loads(str(final_state.get("component_status") or "{}"))
    except ValueError:
        return ""
    if not isinstance(component, dict) or not component:
        return ""
    lines = ["## Component Status", "", "| Component | Status | Note |", "|---|---|---|"]
    for name, entry in component.items():
        if name in {"pre_repair_status", "post_repair_status"}:
            continue          # the audit trail, not a component
        if isinstance(entry, dict):
            status = (
                entry.get("status") or entry.get("integrity")
                or entry.get("use") or ""
            )
            if name == "decision_use":
                status = "failed_rows_excluded" if entry.get("failed_rows_excluded") else ""
                note = ""
            elif name == "sensitivity":
                note = f"failed_rows={entry.get('failed_rows', 0)}"
            elif name == "controlling_sensitivity":
                note = f"{entry.get('verified', 0)} of {entry.get('expected', 0)} verified"
            else:
                note = (
                    entry.get("reason") or entry.get("provisional_reason")
                    or entry.get("role") or ""
                )
        else:
            status, note = str(entry), ""
        lines.append(f"| {name} | {status} | {note} |")
    return "\n".join(lines)


def _redaction_note(final_state: dict) -> str:
    """The appendix record of what the investor report replaced, and why."""
    finding = str(final_state.get("sizing_instruction") or "").strip()
    decision = str(final_state.get("final_trade_decision") or "")
    if not finding and not _SIZING_PHRASES.search(decision):
        return ""
    return (
        "## Publication redactions\n\n"
        "- The investor report replaces unsupported position-size clauses with "
        "qualitative wording; the original decision text is preserved in the "
        "Portfolio Manager Decision section above.\n"
        "- Validation finding: "
        + (finding or "position-size language detected without portfolio context")
        + "\n"
    )


def _clean(text: str) -> str:
    """Reader-facing text: valid tables, readable tags, no vendor jargon."""
    from tradingagents.research.audit import strip_marks

    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("IFRS-equivalent JP GAAP", "Japanese GAAP")
    text = re.sub(r"\]\[(?=(?:" + "|".join(_EVIDENCE_TAGS) + r")\])", "] [", text)
    # Computed marks keep the figure, not the formula; every other audit mark
    # is unwrapped so no `[[…]]` reaches the reader.
    text = _COMPUTED_MARK.sub(lambda match: match.group(1), text)
    text = strip_marks(text)
    text = _normalise_tables(text)
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _demote_headings(text: str, minimum: int = 4) -> str:
    """Shift a block's headings down, preserving their relative depth."""
    matches = list(_HEADING_RE.finditer(text or ""))
    if not matches:
        return text
    levels = [len(match.group(1)) for match in matches]
    shift = minimum - min(levels)
    if shift <= 0:
        return text

    def replace(match: re.Match) -> str:
        return "#" * (len(match.group(1)) + shift) + " " + match.group(2)

    return _HEADING_RE.sub(replace, text)


def _components(text: str) -> list[tuple[int, str, str]]:
    """(heading level, heading, body), split at the document's own top level.

    Deeper headings stay inside their parent's body, so demoting a component
    preserves the source's relative hierarchy instead of flattening it.
    """
    text = _clean(text)
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [(0, "", text)] if text.strip() else []
    # A lone `# Document title` is a title, not a section level: splitting on it
    # would swallow every `##` section into one component.
    if len(matches) > 1 and len(matches[0].group(1)) == 1:
        matches = matches[1:]
        preamble = ""
    else:
        preamble = text[: matches[0].start()].strip()
    top_level = min(len(match.group(1)) for match in matches)
    starts = [match for match in matches if len(match.group(1)) == top_level]
    components: list[tuple[int, str, str]] = []
    if preamble:
        components.append((0, "", preamble))
    for position, match in enumerate(starts):
        end = starts[position + 1].start() if position + 1 < len(starts) else len(text)
        components.append(
            (top_level, match.group(2).strip(), text[match.end():end].strip())
        )
    return components


def _blocks(text: str) -> list[str]:
    """Complete blank-line separated blocks: a paragraph, a table or a bullet list."""
    return [block.strip() for block in _clean(text).split("\n\n") if block.strip()]


def _pick(components, pattern: str, label: str, limit: int = 1) -> list[str]:
    selected: list[str] = []
    for _level, heading, body in components:
        if len(selected) >= limit:
            break
        if heading and body and re.search(pattern, heading, re.IGNORECASE):
            selected.append(f"### {label}\n{_demote_headings(body)}")
    return selected


def _join(parts: list[str]) -> str:
    """Join components without breaking a bullet list into separate paragraphs."""
    out = ""
    for part in parts:
        if not out:
            out = part
            continue
        previous = out.rstrip().splitlines()[-1] if out.strip() else ""
        if part.startswith("- ") and (previous.startswith("- ") or previous.endswith(":")):
            out += "\n" + part
        else:
            out += "\n\n" + part
    return out


def _compose(components: list[str], budget: int) -> str:
    """Keep complete components that fit, pointer included in the budget.

    A component that does not fit its remaining budget is dropped whole — a
    table, paragraph or bullet list is never cut — and the pointer replaces it.
    """
    kept: list[str] = []
    used = 0
    dropped = 0
    for component in components:
        size = _words(component)
        if used + size <= budget:
            kept.append(component)
            used += size
        else:
            dropped += 1
    if dropped:
        while kept and used + _words(_APPENDIX_POINTER) > budget:
            used -= _words(kept.pop())
        text = _join(kept)
        return f"{text}\n\n{_APPENDIX_POINTER}" if text else _APPENDIX_POINTER
    return _join(kept)


def _list_items(field: str) -> list[str]:
    """The items of a semicolon-joined decision field, as complete bullets."""
    return [item.strip().rstrip(".") + "." for item in re.split(r";\s+", field or "") if item.strip()]


def _core_rationale(pm: str, max_words: int = 90) -> str:
    """The thesis's opening sentences, each complete, kept short enough to fit.

    Six PM fields are required inside 400 words, so the rationale is a bounded
    selection of whole sentences — never a half-sentence.
    """
    source = _bold_field(pm, "Investment Thesis") or _bold_field(pm, "Executive Summary")
    if not source:
        for _level, heading, body in _components(pm):
            if heading and body and re.search(r"investment thesis|thesis", heading, re.I):
                source = body
                break
    chosen: list[str] = []
    for sentence in _sentences(source or ""):
        if chosen and len(" ".join(chosen + [sentence]).split()) > max_words:
            break
        chosen.append(sentence)
        if len(chosen) == 2:
            break
    return " ".join(chosen).strip()


def _evidence_group(label: str, items: list[str], limit: int = 2) -> tuple[str, int]:
    """A labelled group of complete bullets, capped so the field always fits."""
    if not items:
        return f"**{label}**: not provided", 0
    shown = items[:limit]
    text = f"**{label}**:\n" + "\n".join(f"- {item}" for item in shown)
    return text, max(0, len(items) - len(shown))


def _fundamental_components(text: str) -> list[str]:
    if not text.strip():
        return ["### Fundamental summary\nNo fundamental report in this run."]
    components = _components(text)
    plan = (
        (r"executive|bottom line|conclusion|summary", "Rating and bottom line", 1),
        (r"latest results|results and operating|performance", "Latest results", 1),
        (r"guidance|management expectations", "Forecast versus guidance", 1),
        (r"independent forecast|forecast", "Independent forecast", 1),
        (r"consensus|variant perception", "Consensus and variant perception", 1),
        (r"bridge|driver|assumption", "Key drivers", 1),
        (r"cash flow|balance sheet", "Cash flow and balance sheet", 1),
        (r"risk|invalidation", "Main risks", 1),
        (r"monitor|watch|evidence", "Evidence that changes the view", 1),
    )
    selected: list[str] = []
    for pattern, label, limit in plan:
        selected.extend(_pick(components, pattern, label, limit))
    if selected:
        return selected
    # An unfamiliar template: keep whole blocks, still never cut mid-block.
    return [f"### Fundamental summary\n{_demote_headings(_clean(text))}"]


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\u00a5$€£])")


def _sentences(text: str) -> list[str]:
    """Complete sentences and complete bullet/table lines.

    A bullet or table row is already a whole unit; splitting it would cut the
    structure the reader needs. Prose is split at sentence ends only, so a
    decimal point followed by a digit is not a break.
    """
    parts: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("- ", "* ", "|", "#")):
            parts.append(line)
            continue
        parts.extend(part for part in _SENTENCE_RE.split(line) if part.strip())
    return [part.strip() for part in parts if part.strip()]


def _sentences_matching(text: str, pattern: str, limit: int = 1) -> list[str]:
    found = [s for s in _sentences(text) if re.search(pattern, s, re.IGNORECASE)]
    return found[:limit]


def _level_bullets(table: str, limit: int = 3) -> list[str]:
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in (table or "").splitlines()
        if line.strip().startswith("|")
    ]
    body = [row for row in rows if row and not set("".join(row)) <= set("-: ")]
    bullets = []
    for row in body[1:limit + 1]:
        if len(row) >= 2 and row[0]:
            bullets.append(f"- {row[0]}" + (f": {row[1]}" if row[1] else ""))
    return bullets


def _technical_components(text: str) -> list[str]:
    """Trend, momentum, volatility, levels, action, confirmation, limitations.

    Sentences are whole; nothing is cut inside one. The full Market Analyst
    report and its indicator tables stay in `appendix.md`.
    """
    if not text.strip():
        return ["### Technical summary\nNo technical report in this run."]
    components = _components(text)

    def body(pattern: str) -> str:
        for _level, heading, component_body in components:
            if heading and re.search(pattern, heading, re.IGNORECASE):
                return component_body
        return ""

    conclusion = body(r"conclusion")
    evidence = body(r"evidence")
    levels = body(r"decision levels|levels|support")
    outlook = body(r"conditional outlook|outlook")
    implication = body(r"investment implication|implication")
    limitations = body(r"limitation|data quality")
    source = "\n\n".join(part for part in (conclusion, evidence, implication, limitations) if part)

    selected: list[str] = []
    used: set[str] = set()

    def take(pattern: str, limit: int = 1, source_text: str | None = None) -> list[str]:
        candidates = [
            sentence
            for sentence in _sentences_matching(source_text or source, pattern, limit + 6)
            if sentence not in used
        ]
        # Prefer the shortest complete sentence so all seven required items fit.
        candidates.sort(key=len)
        chosen = candidates[:limit]
        used.update(chosen)
        return chosen

    def as_bullets(sentences: list[str]) -> str:
        return "\n".join(
            sentence if sentence.startswith(("- ", "* ")) else f"- {sentence}"
            for sentence in sentences
        )

    for label, pattern in (
        ("Trend", r"\b(?:50|200)[- ]?(?:day|sma|ema)|trend|average"),
        ("Momentum", r"RSI|MACD|momentum|histogram"),
        ("Volatility", r"ATR|volatil|gap"),
    ):
        chosen = take(pattern)
        if chosen:
            selected.append(f"**{label}**\n{as_bullets(chosen)}")

    bullets = _level_bullets(levels) or take(r"support|resistance", 2)
    if bullets:
        selected.append("**Primary support and resistance**\n" + "\n".join(bullets))

    has_limitation = bool(limitations) or any(
        re.search(r"limitation|adjustment basis|unresolved|split|timezone", sentence, re.I)
        for sentence in _sentences(source)
    )
    limitation = take(r"limitation|adjustment basis|unresolved|split|timezone")
    if limitation:
        selected.append(f"**Material data limitation**\n{as_bullets(limitation)}")
    elif has_limitation:
        # The caveat was already published under another heading; do not repeat
        # it, but keep the reader pointed at the full note.
        selected.append(
            "**Material data limitation**\n- Full data-quality notes are in `appendix.md`."
        )

    action = take(r"initiat|adding|exposure|hold|trim|wait", 1, implication or conclusion)
    if action:
        selected.append(f"**Technical action**\n{as_bullets(action)}")

    confirmation = take(r"repair|confirm|improv|weaken|invalidat|trigger", 1, outlook)
    if confirmation:
        selected.append(f"**Confirmation and deterioration**\n{as_bullets(confirmation)}")

    return selected or [f"### Technical summary\n{_demote_headings(_clean(conclusion or text))}"]


def _executive_components(pm: str) -> list[str]:
    field = _bold_field(pm, "Executive Summary")
    if field:
        return [field]
    for _level, heading, body in _components(pm):
        if heading and body and re.search(r"executive|conclusion|summary", heading, re.I):
            return [line for line in body.splitlines() if line.strip()]
    blocks = [block for block in _blocks(pm) if not block.startswith("**Rating**")]
    return blocks[:2] or ["No decision was produced in this run."]


def _debate_components(text: str, pm: str) -> list[str]:
    components = _components(text)
    plan = (
        (r"strongest bull|bull case", "Strongest bull case"),
        (r"strongest bear|bear case", "Strongest bear case"),
        (r"decisive disagreement", "Decisive numerical disagreement"),
        (r"rebuttal|concession", "Main concession"),
    )
    selected: list[str] = []
    for pattern, label in plan:
        selected.extend(_pick(components, pattern, label))
    if len(selected) < 2:
        # An older or free-text review has no contract headings: keep its
        # complete blocks rather than publishing only the resolution line.
        selected = [f"### Adversarial review\n{block}" for block in _blocks(text)]
    rating = _bold_field(pm, "Rating") or "not provided"
    new_investor = _row_value(pm, "New investor") or "not provided"
    holder = _row_value(pm, "Existing holder") or "not provided"
    selected.append(
        "### PM resolution\n"
        f"Rating {rating}; existing holders {holder}; new investors {new_investor}."
    )
    return selected


def _pm_components(pm: str) -> list[str]:
    """The decision's own fields, one complete component each, all required."""
    if not pm.strip():
        return ["**Rating**: not provided", "No decision was produced in this run."]
    if not _bold_field(pm, "Rating"):
        # A free-text fallback decision has no fields; publish it whole rather
        # than dropping the only record of the decision.
        return [pm]
    rating = _bold_field(pm, "Rating") or "not provided"
    new_investor = _row_value(pm, "New investor") or "not provided"
    holder = _row_value(pm, "Existing holder") or "not provided"
    core = _core_rationale(pm) or "not provided"

    components = [
        f"**Rating**: {rating}",
        f"**Actions**\n- New investor: {new_investor}\n- Existing holder: {holder}",
        f"**Core rationale**: {core}",
    ]
    upgrade, skipped_up = _evidence_group(
        "Evidence to upgrade", _list_items(_bold_field(pm, "Evidence to upgrade"))
    )
    downgrade, skipped_down = _evidence_group(
        "Evidence to downgrade", _list_items(_bold_field(pm, "Evidence to downgrade"))
    )
    components.extend([upgrade, downgrade])
    if skipped_up or skipped_down:
        components.append(_APPENDIX_POINTER)
    fair_value = _bold_field(pm, "Fair Value") or _bold_field(pm, "Price Target")
    horizon = _bold_field(pm, "Time Horizon")
    if fair_value:
        components.append(
            f"**Fair value**: {fair_value}"
            + (f"\n**Time horizon**: {horizon}" if horizon else "")
        )
    return components


def _catalysts_components(pm: str) -> list[str]:
    components: list[str] = []
    invalidation = _list_items(_bold_field(pm, "Thesis invalidation"))
    if invalidation:
        components.append("**What would invalidate the view**:")
        components.extend(f"- {item}" for item in invalidation)
    further = _list_items(_bold_field(pm, "Evidence that would downgrade further"))
    if further:
        components.append("**Evidence that would downgrade further**:")
        components.extend(f"- {item}" for item in further)
    applicability = _bold_field(pm, "Downgrade applicability")
    if applicability:
        components.append(f"**Downgrade applicability**: {applicability}")
    if components:
        return components
    # Older decisions carry the risks as a titled section rather than fields.
    for _level, heading, body in _components(pm):
        if heading and body and re.search(r"risk|invalidat|catalyst", heading, re.I):
            return _blocks(body)
    return ["No catalyst or risk conditions were stated in the decision."]


def _valuation_components(package: str) -> list[str]:
    if not package.strip():
        return ["### Verified valuation\nNo verified model in this run."]
    blocks = _blocks(package)
    plan = (
        (r"^annual figures|^- FY|forecast\):|^forecast", "Forecast"),
        (r"^management guidance versus", "Guidance versus model"),
        (r"^external consensus", "Consensus versus model"),
        (r"valuation:|EV/EBITDA:|Scenarios:", "Verified valuation"),
    )
    selected: list[str] = []
    for pattern, label in plan:
        for block in blocks:
            if re.search(pattern, block, re.IGNORECASE) and f"### {label}\n{block}" not in selected:
                selected.append(f"### {label}\n{block}")
                break
    return selected


def _publication_sections(final_state: dict, total_budget: int = MAX_MAIN_WORDS - 100
                          ) -> list[tuple[str, str]]:
    """The investor report: complete, structured components; never truncated prose.

    Each section is assembled from the run's own headings and decision fields. A
    component that does not fit is dropped whole and the section points at
    `appendix.md`; nothing is cut mid-sentence and nothing is re-written by a
    model at publication time.
    """
    pm = _clean(str(final_state.get("final_trade_decision") or ""))
    fundamentals = str(final_state.get("fundamentals_report") or "")
    package = str(
        final_state.get("research_package_artifact") or final_state.get("research_package") or ""
    )
    debate = str(final_state.get("debate_summary") or "")
    technical = str(final_state.get("market_report") or "")

    # The PM's own summary repeats the rating line; the section already has one.
    executive = [
        re.sub(r"^\*\*Rating\*\*:\s*\w+\s*[—–-]\s*", "", component).strip()
        for component in _executive_components(pm)
    ]

    sections: list[tuple[str, list[str]]] = [
        ("Executive Summary", executive),
        ("Fundamental Summary", _fundamental_components(fundamentals)),
        ("Forecast and Valuation", _valuation_components(package)),
        ("Debate Summary", _debate_components(debate, pm)),
        ("Technical Summary", _technical_components(technical)),
        ("Catalysts and Risks", _catalysts_components(pm)),
        ("Portfolio Manager Decision", _pm_components(pm)),
    ]

    budgets = dict(SECTION_BUDGETS)

    def build() -> list[tuple[str, str]]:
        return [(heading, _compose(items, budgets[heading])) for heading, items in sections]

    rendered = build()
    # Total limit: reduce the least important section first, whole components
    # only. The Technical Summary is never reduced: it is required in full even
    # when the overall budget is tight.
    for heading in ("Catalysts and Risks", "Forecast and Valuation", "Fundamental Summary"):
        while sum(_words(text) for _h, text in rendered) > total_budget:
            if budgets[heading] <= 150:
                break
            budgets[heading] = max(150, budgets[heading] - 100)
            rendered = build()
    # Portfolio-size language is redacted in the investor report only; the
    # decision's section file and `appendix.md` keep the original text.
    return [(heading, _redact_sizing(text)) for heading, text in rendered]


def _appendix_sections(parts) -> list[tuple[str, str]]:
    """Every full section, regardless of whether the main report summarises it."""
    sections = []
    for heading, _folder, written in parts:
        if written:
            content = "\n\n".join(f"### {agent}\n{text}" for agent, _f, text in written)
            sections.append((heading, content))
    return sections


def write_report_tree(final_state: dict, ticker: str, save_path, settings: dict | None = None,
                      html: bool = True) -> Path:
    """Save a completed run's reports to ``save_path``; return the complete-report path.

    ``complete_report.md`` is the investor-facing report; the debate transcript,
    the number audit and the case table go to ``appendix.md`` (and their own
    section files). ``settings`` adds what produced the run to the header;
    ``html`` also writes ``complete_report.html`` of the main report.
    """
    save_path = Path(save_path)
    save_path.mkdir(parents=True, exist_ok=True)
    parts = _report_parts(final_state)
    for _heading, folder, written in parts:
        (save_path / folder).mkdir(exist_ok=True)
        for _agent, filename, text in written:
            (save_path / folder / filename).write_text(text, encoding="utf-8")

    header = _header(ticker, final_state, settings)
    closing = (
        "\n\n---\n\n**Full record.** The complete analyst reports, the full debate transcript, "
        "the model diagnostics, the number audit, the retry and vendor diagnostics and the case "
        "table are in `appendix.md` beside this report.\n"
    )
    # The header, the section headings and the closing note all count toward the
    # 5,000-word limit, so the sections are given what remains.
    total_budget = MAX_MAIN_WORDS - _words(header) - _words(closing) - 30
    main_sections = _publication_sections(final_state, total_budget)
    appendix_sections = _appendix_sections(parts)

    body = header
    body += "\n\n".join(f"## {heading}\n\n{text}" for heading, text in main_sections)
    body += closing
    (save_path / "complete_report.md").write_text(body, encoding="utf-8")

    if appendix_sections:
        note = _redaction_note(final_state)
        (save_path / "appendix.md").write_text(
            f"# Appendix — {ticker}\n\nFull analyst reports, debate transcript, model "
            "diagnostics, audit findings, retry logs and source conflicts. Not part of the "
            "investor-facing report.\n\n"
            + "\n\n".join(f"## {heading}\n\n{text}" for heading, text in appendix_sections)
            + (f"\n\n{note}" if note else "")
            + (f"\n\n{status_section}" if (status_section := _component_status_section(final_state)) else ""),
            encoding="utf-8",
        )
    if html:
        from tradingagents.report_html import render_report

        # The HTML mirrors the main report's sections, not the raw reports.
        main_parts = [
            (heading, "", [(heading, f"{index}.md", text)])
            for index, (heading, text) in enumerate(main_sections, 1)
        ]
        (save_path / "complete_report.html").write_text(
            render_report(ticker, final_state, settings, main_parts), encoding="utf-8"
        )
    return save_path / "complete_report.md"
