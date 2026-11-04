"""Reusable report-tree writer shared by the CLI and the programmatic API.

Writes a run's per-section markdown (analysts, research, trading, risk,
portfolio) plus a consolidated ``complete_report.md`` under ``save_path``. The
CLI and ``TradingAgentsGraph.save_reports`` both call this, so a headless / API
run produces the same on-disk report tree a CLI run does.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from tradingagents.agents.rating import run_rating


def analyst_names(keys) -> list[str]:
    """The analysts as users select them: the sentiment analyst's key is "social"."""
    return ["sentiment" if key == "social" else key for key in keys or []]


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
    components = {
        str(final_state.get(key) or "")
        for key in ("operating_model_status", "eps_model_status",
                    "valuation_status", "sensitivity_model_status")
    }
    if "VERIFIED" in components and components - {"VERIFIED", ""}:
        lines.append("- Overall research status: PARTIALLY VERIFIED")
    if final_state.get("overall_status"):
        lines.append(f"- Overall status: {final_state['overall_status']}")
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


# The report's sections in order: (heading, folder, [(agent, file, state path, is_main)]).
# The main report is what an investor reads; the debate transcript, the number audit
# and the recomputed cases are written to `appendix.md` and the section files.
_SECTIONS = (
    ("I. Analyst Team Reports", "1_analysts", (
        ("Market Analyst", "market.md", ("market_report",), True),
        ("Sentiment Analyst", "sentiment.md", ("sentiment_report",), True),
        ("News Analyst", "news.md", ("news_report",), True),
        ("Fundamentals Analyst", "fundamentals.md", ("fundamentals_report",), True),
    )),
    ("Fundamental Research Package", "1b_research", (
        ("Fundamental Research", "research_package.md",
         (("research_package_artifact",), ("research_package",)), True),
    )),
    ("Number Audit", "1c_audit", (
        ("Figures Not In The Model", "numbers.md", ("report_audit",), False),
    )),
    ("Case Comparison", "1d_cases", (
        ("Cases Recomputed On The Model", "cases.md", ("case_table",), False),
    )),
    ("Growth and Incremental Margin", "1e_metrics", (
        ("Period Metrics", "metrics.md", ("metrics_table",), False),
    )),
    ("Stale Model Reference", "1b_research", (
        ("Previous Specification (not used)", "stale_model.md",
         ("stale_model_package",), False),
    )),
    ("Model Repair", "1b_research", (
        ("Model Repair Note", "repair.md", ("model_repair_addendum",), False),
    )),
    ("Adversarial Debate", "2_research", (
        ("Round 1 and material updates", "debate.md", ("debate_summary",), True),
    )),
    ("II. Research Team Decision", "2_research", (
        ("Adversarial Investment Review", "challenge.md",
         ("investment_debate_state", "challenge_history"), False),
        ("Research Manager", "manager.md", ("investment_plan",), False),
    )),
    ("III. Trading Team Plan", "3_trading", (
        ("Trader", "trader.md", ("trader_investment_plan",), False),
    )),
    ("IV. Risk Management Team Decision", "4_risk", (
        ("Aggressive Analyst", "aggressive.md", ("risk_debate_state", "aggressive_history"), False),
        ("Conservative Analyst", "conservative.md", ("risk_debate_state", "conservative_history"), False),
        ("Neutral Analyst", "neutral.md", ("risk_debate_state", "neutral_history"), False),
    )),
    ("V. Portfolio Manager Decision", "5_portfolio", (
        ("Portfolio Manager", "decision.md", ("final_trade_decision",), True),
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


def _report_parts(final_state: dict) -> list[tuple[str, str, list[tuple[str, str, str, bool]]]]:
    """The sections with something to say: (heading, folder, [(agent, file, text, main)]).

    A section's ``path`` is a key path, or a tuple of alternative key paths tried
    in order: the published model prefers the artifact rendering and falls back
    to the prompt rendering a checkpoint saved before that split.

    ``main`` marks the text an investor reads; the rest (debate transcript,
    number audit, case table) is written to ``appendix.md`` and the section file.

    The agents mark the figures they take from the model as ``[[229.5]]`` so the
    audit can check them; a reader should never see the brackets, so they are
    removed here, once, for both the Markdown and the HTML.
    """
    from tradingagents.research.audit import strip_marks

    parts = []
    for heading, folder, agents in _SECTIONS:
        written = []
        for agent, filename, path, main in agents:
            paths = path if path and isinstance(path[0], tuple) else (path,)
            value = next(
                (found for found in (_state_value(final_state, candidate) for candidate in paths)
                 if found),
                None,
            )
            if value:
                # An unverified model may not put its figures in the investor
                # report; the analyst's report is written against it, so it moves
                # to the appendix instead of being quoted as evidence.
                demote = (
                    str(final_state.get("model_status") or "").upper() == "UNVERIFIED"
                    and agent == "Fundamentals Analyst"
                )
                text = strip_marks(str(value))
                if agent == "News Analyst":
                    body, diagnostics = _split_news_diagnostics(text)
                    if diagnostics:
                        written.append((agent, filename, body, main and not demote))
                        written.append(
                            ("News Diagnostics", "news_diagnostics.md", diagnostics, False)
                        )
                        continue
                written.append((agent, filename, text, main and not demote))
        if written:
            parts.append((heading, folder, written))
    return parts


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
    main_sections = []
    appendix_sections = []
    for heading, folder, written in parts:
        (save_path / folder).mkdir(exist_ok=True)
        for _agent, filename, text, _main in written:
            (save_path / folder / filename).write_text(text, encoding="utf-8")
        main_written = [item for item in written if item[3]]
        appendix_written = [item for item in written if not item[3]]
        if main_written:
            content = "\n\n".join(f"### {agent}\n{text}" for agent, _f, text, _m in main_written)
            main_sections.append(f"## {heading}\n\n{content}")
        if appendix_written:
            content = "\n\n".join(f"### {agent}\n{text}" for agent, _f, text, _m in appendix_written)
            appendix_sections.append(f"## {heading}\n\n{content}")

    body = _header(ticker, final_state, settings) + "\n\n".join(main_sections)
    if appendix_sections:
        body += (
            "\n\n---\n\n## Appendix\n\nThe debate transcript, the number audit and the case "
            "table are in `appendix.md` beside this report.\n"
        )
    (save_path / "complete_report.md").write_text(body, encoding="utf-8")

    if appendix_sections:
        (save_path / "appendix.md").write_text(
            f"# Appendix — {ticker}\n\nDebate transcript, number audit and case table. "
            "Not part of the investor-facing report.\n\n" + "\n\n".join(appendix_sections),
            encoding="utf-8",
        )
    if html:
        from tradingagents.report_html import render_report

        main_parts = [
            (heading, folder, [(a, f, t) for a, f, t, _m in written if _m])
            for heading, folder, written in parts
            if any(item[3] for item in written)
        ]
        (save_path / "complete_report.html").write_text(
            render_report(ticker, final_state, settings, main_parts), encoding="utf-8"
        )
    return save_path / "complete_report.md"
