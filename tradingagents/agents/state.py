from typing import Annotated

from langgraph.graph import MessagesState
from typing_extensions import TypedDict


def merge_fallbacks(left: dict | None, right: dict | None) -> dict:
    """Merge per-agent structured-output fallbacks; later agents add, never replace."""
    return {**(left or {}), **(right or {})}


# Researcher team state
class InvestDebateState(TypedDict):
    bull_history: Annotated[
        str, "Bullish Conversation history"
    ]
    bear_history: Annotated[
        str, "Bearish Conversation history"
    ]
    challenge_history: Annotated[
        str, "Adversarial Investment Reviewer's challenge, round by round"
    ]
    last_review: Annotated[str, "The reviewer's most recent answer, for a delta-only next round"]
    first_review: Annotated[str, "The round-1 review: the full bull and bear cases for the main report"]
    first_review_raw: Annotated[str, "The reviewer's first round-1 answer, before any repair — kept for diagnosis"]
    unresolved_issue: Annotated[str, "The one open issue a reader should watch, from the latest round"]
    review_missing: Annotated[str, "Required review headings the reviewer failed to supply; empty when complete"]
    no_material_update: Annotated[bool, "The reviewer reported no change; stop the debate"]
    decisive_sensitivity_id: Annotated[str, "The ranked sensitivity the reviewer named; empty means not established"]
    history: Annotated[str, "Conversation history"]
    current_response: Annotated[str, "Latest response"]
    count: Annotated[int, "Length of the current conversation"]


# Risk management team state
class RiskDebateState(TypedDict):
    aggressive_history: Annotated[
        str, "Aggressive Agent's Conversation history"
    ]
    conservative_history: Annotated[
        str, "Conservative Agent's Conversation history"
    ]
    neutral_history: Annotated[
        str, "Neutral Agent's Conversation history"
    ]
    history: Annotated[str, "Conversation history"]
    latest_speaker: Annotated[str, "Analyst that spoke last"]
    current_aggressive_response: Annotated[
        str, "Latest response by the aggressive analyst"
    ]
    current_conservative_response: Annotated[
        str, "Latest response by the conservative analyst"
    ]
    current_neutral_response: Annotated[
        str, "Latest response by the neutral analyst"
    ]
    count: Annotated[int, "Length of the current conversation"]


class AgentState(MessagesState):
    company_of_interest: Annotated[str, "Company that we are interested in trading"]
    asset_type: Annotated[str, "Asset type under analysis such as stock or crypto"]
    instrument_context: Annotated[str, "Deterministic ticker identity resolved at run start"]
    trade_date: Annotated[str, "The analysis date; data is served as of it"]

    # research step
    market_report: Annotated[str, "Report from the Market Analyst"]
    sentiment_report: Annotated[str, "Report from the Sentiment Analyst"]
    news_report: Annotated[str, "Report from the News Analyst on company and world news"]
    fundamentals_report: Annotated[str, "Report from the Fundamentals Analyst"]

    # researcher team discussion step
    investment_debate_state: Annotated[
        InvestDebateState, "Current state of the debate on if to invest or not"
    ]
    investment_plan: Annotated[str, "Investment plan from the Research Manager"]

    trader_investment_plan: Annotated[str, "Transaction proposal from the Trader"]

    # risk management team discussion step
    risk_debate_state: Annotated[
        RiskDebateState, "Current state of the debate on evaluating risk"
    ]
    final_trade_decision: Annotated[str, "Final decision from the Portfolio Manager"]
    final_rating: Annotated[str, "The Portfolio Manager's 5-tier rating, or REVIEW when it has none"]
    past_context: Annotated[str, "Memory log context for the Portfolio Manager (same-ticker decisions + cross-ticker lessons), written by the Memory Log step"]
    memory_note: Annotated[str, "What the Memory Log step could not settle or read this run, for the report; empty when all went well"]
    portfolio_context: Annotated[str, "Caller-supplied holdings and cash, rendered at run start; empty when not provided"]
    decision_horizon: Annotated[str, "The horizon the technical analysis is framed against; from TRADINGAGENTS_DECISION_HORIZON or the CLI question (default 3-6 months), empty when nothing supplied it"]
    research_package: Annotated[str, "Deterministic fundamental model (forecasts, bridges, valuation) for this run; empty unless a research corpus and model spec are configured"]
    research_package_artifact: Annotated[str, "The same model without the prompt's imperative lines, for the published report"]
    research_fair_value_low: Annotated[float | None, "Low end of the verified model's fair-value band; None when no model"]
    research_fair_value_high: Annotated[float | None, "High end of the verified model's fair-value band; None when no model"]
    research_price: Annotated[float, "The share price the model's valuation used; 0 when no model was used"]
    price_source: Annotated[str, "USER_EXPLICIT | VERIFIED_CLOSE | REPORT_PRICE | SPEC_PRICE: the authority behind the controlling price"]
    price_as_of: Annotated[str, "The date the controlling price belongs to"]
    price_ref: Annotated[str, "The source that supplied the controlling price"]
    price_note: Annotated[str, "What the price resolution changed, for the report header"]
    price_override: Annotated[bool, "Whether the controlling price was supplied by the caller, not taken from the market"]
    price_authority_conflict: Annotated[str, "Two controlling prices at the same authority disagree; empty when they do not"]
    research_model_path: Annotated[str, "The model spec file this run used, so the audit checks the same numbers"]
    research_model_source: Annotated[str, "Whether that spec was 'built' in this run or 'reused' from an earlier one"]
    research_model_created_at: Annotated[str, "The spec file's timestamp, so a reader can judge reuse"]
    research_model_path_v1: Annotated[str, "The spec before the repair gate; v1 figures are superseded, not current"]
    active_model_version: Annotated[str, "v1, v2 when the repair gate changed the model, or unverified"]
    model_challenges: Annotated[str, "The analyst's model challenges and the applied changes, as JSON"]
    model_corrections: Annotated[str, "JSON: the correction ledger — original, corrected, evidence, fields, downstream outputs, acceptance status"]
    model_repair_log: Annotated[str, "Changed fields and unresolved challenges, as JSON"]
    model_repair_addendum: Annotated[str, "The repair note the debate reads when v2 differs from v1"]
    fundamentals_challenge_check: Annotated[str, "repaired | no_material_challenges | repair_unresolved | repair_failed | kernel_only_pass | not_applicable"]
    model_status: Annotated[str, "VERIFIED | UNVERIFIED | NOT_APPLICABLE, derived after the repair gate and the audit"]
    repairs_applied: Annotated[int, "How many model fields the repair gate changed"]
    unresolved_blocking: Annotated[int, "Blocking challenges the repair gate could not apply"]
    unresolved_material: Annotated[int, "Material challenges the repair gate could not apply"]
    market_cap_basis: Annotated[str, "Named share base behind the market capitalisation"]
    forecast_eps_basis: Annotated[str, "Named share base behind the forecast EPS and P/E"]
    dilution_sensitivity_basis: Annotated[str, "Named share base for the full-conversion dilution case"]
    equity_value_share_basis: Annotated[str, "Named share base for an EV-based per-share value"]
    price_conflict: Annotated[str | None, "Model price vs market close when they disagree by >1%"]
    controlling_fair_value_low: Annotated[float | None, "The PM decision's controlling fair-value low"]
    controlling_fair_value_base: Annotated[float | None, "The PM decision's controlling fair-value base"]
    controlling_fair_value_high: Annotated[float | None, "The PM decision's controlling fair-value high"]
    stale_model_path: Annotated[str, "An older spec kept for comparison only; never used for the rating"]
    stale_model_package: Annotated[str, "The stale spec rendered for the appendix, with a NOT USED banner"]
    spec_compatibility: Annotated[str, "compatible | unknown | not_applicable — verified only with a dependency manifest"]
    spec_compatibility_reason: Annotated[str, "Why that status was reached, for the report header"]
    structured_fallbacks: Annotated[dict, merge_fallbacks]
    replacement_conclusions_applied: Annotated[str, "How many superseded conclusions the repair note replaced in the decision"]
    operating_model_status: Annotated[str, "VERIFIED when revenue and operating profit may be used"]
    eps_model_status: Annotated[str, "VERIFIED only when the share bases and per-share arithmetic verified"]
    eps_status: Annotated[str, "Alias of eps_model_status"]
    eps_basis_quality: Annotated[str, "weighted_average | conservative_full_conversion | declared"]
    valuation_status: Annotated[str, "VERIFIED only when a controlling fair value exists"]
    calculation_status: Annotated[str, "Whether arithmetic, units, share counts and propagation reconcile (the old valuation_status)"]
    assumption_status: Annotated[str, "COMPANY_REPORTED | COMPANY_GUIDANCE | EXTERNAL_ESTIMATE | DERIVED_FROM_SOURCED_INPUTS | ANALYST_ASSUMPTION | UNRESOLVED"]
    assumption_detail: Annotated[str, "JSON: per-method earnings and multiple evidence and labels"]
    valuation_wording: Annotated[str, "The one published sentence separating mechanical verification from assumption support"]
    segment_reconciliation_status: Annotated[str, "VERIFIED | INSUFFICIENT_DATA | UNRESOLVED | NOT_REQUIRED"]
    segment_reconciliation_detail: Annotated[str, "Why the segment reconciliation status was reached"]
    dominant_segment: Annotated[str, "The segment carrying the largest disclosed profit share"]
    dominant_segment_share: Annotated[float | None, "Its share of positive disclosed segment profit"]
    segment_table: Annotated[str, "The dominant segment's forecast revenue, profit and margin"]
    segment_evidence: Annotated[str, "JSON: the current-run Fundamentals SEGMENT_EVIDENCE block"]
    segment_evidence_source: Annotated[str, "STRUCTURED | LEGACY_MARKDOWN_FALLBACK | MISSING"]
    segment_evidence_schema_missing: Annotated[bool, "Whether the required machine-readable block was absent or invalid"]
    segment_evidence_retry: Annotated[str, "JSON: whether one focused SEGMENT_EVIDENCE retry ran and what it found"]
    segment_limitation: Annotated[str, "The visible main-report limitation when segment reconciliation is required but missing"]
    segment_requirement_provisional: Annotated[bool, "True until current-run segment evidence has audited the cached requirement"]
    segment_build_type: Annotated[str, "BOTTOM_UP | TOP_DOWN | NOT_APPLICABLE"]
    bottom_up_claim_allowed: Annotated[bool, "False when the consolidated forecast is not segment-reconciled"]
    operating_model_confidence: Annotated[str, "VERIFIED | DEGRADED: segment incompleteness degrades the build confidence"]
    segment_reconciliation_controlling: Annotated[bool, "True only when segment reconciliation is an explicit controlling valuation or decision input"]
    valuation_method_statuses: Annotated[str, "JSON: independent status per valuation method (pe, ev_ebitda)"]
    ev_per_share_status: Annotated[str, "Status of the EV-derived per-share value only"]
    sensitivity_model_status: Annotated[str, "VERIFIED only when ranked sensitivities are publishable"]
    sensitivity_status: Annotated[str, "Alias of sensitivity_model_status"]
    model_node_sensitivity_status: Annotated[str, "Whether the constructed model-node shocks propagate (scope: model nodes only)"]
    economic_driver_mapping_status: Annotated[str, "COMPLETE | PARTIAL: whether every canonical economic driver maps to a model node"]
    failed_driver_mappings: Annotated[int, "Distinct canonical economic drivers with no verified model-node mapping"]
    failed_driver_mapping_ids: Annotated[str, "JSON list of the distinct canonical driver ids that failed mapping"]
    research_fair_value_verified: Annotated[bool, "Whether the model band may control the decision"]
    trader_status: Annotated[str, "structured | advisory — advisory output may not control the rating or levels"]
    sector: Annotated[str, "Sector used for severity thresholds, from the model spec"]
    run_purpose: Annotated[str, "production | comparison | research — derived decision status needs it"]
    decision_basis: Annotated[str, "fundamental | event | technical | mixed, as audited"]
    technical_overlay: Annotated[str, "advisory when the technical leg did not verify but the fundamental basis did"]
    technical_evidence: Annotated[str, "JSON: verified technical evidence items with stable IDs"]
    ev_input_diagnostics: Annotated[str, "JSON: per-input EV readiness and the reason it is unavailable"]
    fundamental_packet: Annotated[str, "Model earnings facts with no price or per-share output"]
    valuation_packet: Annotated[str, "Only valuation methods whose inputs verified"]
    benchmark_basis: Annotated[str, "Visible statement: whether the benchmark is company-issued guidance or an internal forecast"]
    decision_audit_result: Annotated[str, "JSON: the non-valuation decision audit result"]
    verification_checks: Annotated[str, "JSON: the independent SOURCE_CHECK, MODEL_CHECK and DECISION_CHECK results"]
    verification_status: Annotated[str, "PASS | PASS_WITH_QUALIFICATION | FAIL, the worst independent check"]
    decision_model_version: Annotated[str, "The active model version the PM decision was written against"]
    decision_findings: Annotated[str, "JSON: named findings raised while building the decision"]
    decision_triggers: Annotated[str, "JSON: the three trigger lists as structured objects"]
    trigger_taxonomy_version: Annotated[int, "Trigger object taxonomy version; legacy prose is migrated, never rendered"]
    blocking_issue: Annotated[str, "JSON: the exact challenge that blocked the model, its fields, verification and publication impact"]
    overall_status: Annotated[str, "Not Rated when the model is unverified, else empty"]
    decision_status: Annotated[str, "Provisional when the model is unverified, else empty"]
    provisional_decision: Annotated[str, "The Portfolio Manager's own rating before any publication block"]
    published_decision: Annotated[str, "The rating a reader may use; NOT_RATED when publication is blocked"]
    publication_status: Annotated[str, "OK | OK_WITH_QUALIFICATION | BLOCKED: whether a controlling rating and fair value may be published"]
    publication_reasons: Annotated[list[str], "Why publication is blocked; empty when it is not"]
    publication_qualifications: Annotated[list[str], "Defects retained as visible qualifications, not publication blockers"]
    controlling_valuation_method: Annotated[str, "pe | ev_ebitda: the method that controls the decision"]
    controlling_valuation_conflict: Annotated[str, "Configuration contradiction between the spec method and the method-level controlling flags"]
    ev_decision_use: Annotated[str, "CONTROLLING | CROSS_CHECK_ONLY for the EV/EBITDA method"]
    decision_safety_check: Annotated[str, "PASS | FAIL: whether the run published a rating or fair value it must not"]
    decision_process_check: Annotated[str, "PASS | DEGRADED: whether the decision process was fully validated"]
    production_decision_allowed: Annotated[bool, "True only when safety and process pass, the decision is FINAL and the run is production"]
    safety_reasons: Annotated[list[str], "Why the decision safety check failed; empty when it passed"]
    process_reasons: Annotated[list[str], "Why the decision process check is degraded; empty when it passed"]
    source_facts: Annotated[str, "Verified reported/guided facts, kept even when the model is unavailable"]
    model_derived_facts: Annotated[str, "The model package; empty when the model is unavailable"]
    bridge_arithmetic: Annotated[str, "JSON: deterministic bridge base/components/total/difference/reconciliation_status"]
    resolved_status: Annotated[str, "JSON: the final EV/multiple/equity/per-share/decision-use statuses"]
    analyst_judgments: Annotated[str, "Analyst prose and transcript judgments, separate from model and source facts"]
    sizing_instruction: Annotated[str, "The phrase the sizing detector matched, for the report diagnostics"]
    non_model_decision_basis: Annotated[dict, "A deterministic, event-driven basis that may justify a directional call without the model"]
    debate_cases: Annotated[str, "Cases argued in the debate as JSON: stance, capped deltas, multiple, probability"]
    case_table: Annotated[str, "The cases recomputed on the model, rendered as a table"]
    metrics_table: Annotated[str, "Growth and incremental margin for each consecutive model year"]
    sensitivity_table: Annotated[str, "The model's assumptions ranked by fair-value impact"]
    component_status: Annotated[str, "JSON: one status object for every component, shared by report, appendix and PM"]
    sensitivity_ids: Annotated[str, "JSON list of the ranked sensitivity IDs, for reviewer validation"]
    sensitivity_alias_map: Annotated[str, "JSON map of legacy sensitivity ids to their canonical ids"]
    sensitivity_gaps: Annotated[str, "JSON list of research drivers with no explicit model assumption"]
    deterministic_issues: Annotated[str, "JSON: deterministic model issues collected before any LLM challenge"]
    deterministic_checks_executed: Annotated[bool, "Whether the deterministic checks ran for this run"]
    decisive_sensitivity_id: Annotated[str, "The ranked sensitivity the reviewer named as decisive; empty means not established"]
