"""One adversarial reviewer replaces the two debating personas.

The old bull/bear pair read the same reports and re-argued the same facts from
fixed sides; this agent challenges the fundamental and technical analyses in a
single pass, producing both the strongest positive and the strongest negative
challenge, and appends both debate cases (aggressive and bearish) so the case
table, the managers and the synthesizer keep their quantified inputs.

The review is prose with a fixed Markdown contract, not a schema: the sections
are extracted by heading for the report, and a missing rebuttal triggers one
focused retry instead of a full re-review.
"""

from __future__ import annotations

import contextlib
import json
import re

from tradingagents.agents.context import (
    COMPARATIVE_CLAIM_RULE,
    EVIDENCE_DISCIPLINE,
    case_availability,
    evidence_pack,
    fill_placeholders,
    fundamentals_with_repair_notice,
    get_language_instruction,
    position_fields,
    report_or_absent,
)
from tradingagents.research.cases import (
    CAP_FORECAST_PCT,
    CAP_MULTIPLE_PCT,
    parse_cases,
    strip_case_json,
    with_case,
)

REVIEW_HEADINGS = (
    "Strongest Bull Case",
    "Strongest Bear Case",
    "What the Bull Fails to Explain",
    "What the Bear Fails to Explain",
    "Decisive Disagreement",
    "Evidence That Would Resolve It",
    "Strongest Opposing Argument",
    "Rebuttal or Concession",
    "Confidence",
)

# The sections without which the review is not a review. The 13:09 run published a
# review that carried only the rebuttal, so every heading in the contract is checked,
# not only the two cases.
CORE_HEADINGS = REVIEW_HEADINGS

TEMPLATE = r"""You are the Adversarial Investment Reviewer for {{instrument_ticker}}.

Your purpose is to challenge the fundamental and technical analyses without
manufacturing disagreement.

INPUTS

Source-extraction evidence pack:
{{evidence_pack_or_null}}

Independent fundamental analysis:
{{fundamental_analysis_or_null}}

Model audit:
{{model_audit_or_null}}

Technical analysis:
{{technical_analysis_or_null}}

Decision horizon: {{decision_horizon_or_null}}
Current position context: {{current_position_or_null}}
Mandate constraints: {{mandate_constraints_or_null}}

Prior rounds of this review: {{prior_review_or_null}}

TASK

Produce one integrated adversarial review containing the strongest bull case and the
strongest bear case. Do not repeat the analyst reports; select only the evidence that
materially supports or challenges the investment conclusion.

Each case must form a causal chain:

business or industry driver -> financial effect -> difference from market expectations ->
valuation or catalyst impact -> likely share-price effect over the decision horizon.

Use fundamental analysis for operating forecasts, financial performance, cash flow,
balance-sheet strength and fair value. Use technical analysis for timing, trend,
volatility, multiple risk and execution risk; technical evidence must not directly alter
the operating forecast or fundamental fair value. Use news and sentiment only when the
evidence is company-specific or has a clear, material, supported transmission mechanism;
if none exists, write "No usable incremental news or sentiment evidence." Do not substitute
unrelated macro, political, prediction-market or generic industry material.

Tag each evidence sentence with its class in square brackets: [fundamental], [valuation],
[technical], [news_sentiment] or [portfolio_action]. An internal forecast stays an internal
forecast: tag it [fundamental] but never call it company guidance, and never use it as a
beat/miss, upgrade/downgrade or catalyst hurdle when the company issued no guidance.

Rebut a claim primarily with evidence
of the same class; cross-class evidence may adjust timing, confidence or portfolio action,
but must not silently change the operating forecast or fair value. Every sentence that
states a number needs one of those tags; a number with no tag is an unsupported claim.

Select the strongest bull case, bear case and opposing argument only from the evidence in
this prompt: the current-run fundamental analysis, technical analysis, news and sentiment
reports, and the verified model. Do not introduce customer concentration, market share,
competitor capacity, product-roadmap exposure or any other figure that those inputs do not
state. If an exposure matters but is undisclosed, write that it is unknown and treat the
unknown as a risk — do not supply a remembered value for it.

A forecast equal to guidance or consensus by construction is not independent evidence:
say "The model contains no independent forecast differentiation for this period" and do
not use the absence of differentiation as bullish or bearish evidence.

Do not invent replacement revenue, earnings, cash-flow, EPS or valuation figures. Identify
a disputed model assumption and explain its direction; the deterministic model is the only
source of replacement numbers.

OUTPUT

Use exactly these Markdown headings, in this order. Write a substantive paragraph under
each; never leave one blank.

## Strongest Bull Case
The causal chain above, for the bull side. State one argument, not a list.

## Strongest Bear Case
The causal chain above, for the bear side. State one argument, not a list.

## What the Bull Fails to Explain
The single most important unresolved weakness in the bull case.

## What the Bear Fails to Explain
The single most important unresolved weakness in the bear case.

## Decisive Disagreement
The central fact, model assumption, valuation judgment, timing question, source-quality
issue or portfolio-action question that separates the two cases. Prefer one of the
verified model's load-bearing assumptions (a bridge line, an incremental margin, a
multiple); name the assumption and the number it carries. If a different variable
matters more, say why.

Then, on its own line, write exactly:

Decisive sensitivity ID: <one id from the ranked sensitivities in the model section>

Use only an id that appears there. If none of them is the decisive variable, say
"Decisive sensitivity ID: not established" and explain why.

## Evidence That Would Resolve It
The specific result, KPI, filing, disclosure, market development or price behaviour most
likely to settle the disagreement.

## Strongest Opposing Argument
The strongest evidence-based argument against the conclusion you currently favour. Do not
pick a convenient weaker point.

## Rebuttal or Concession
Rebut that argument with different evidence or a calculation; do not repeat the preferred
case. If it cannot be rebutted, concede it explicitly and say whether the concession changes
the recommendation, valuation, timing, confidence or portfolio action. A concession is a
valid answer; "none" is not.

## Confidence
low | medium | high, with one sentence of justification.

Limit the review to disagreements that could materially change the rating, target range or
timing. Do not issue portfolio sizing, stop-loss or hedging instructions.

MARKET EDGE

State whether the internal forecast differs from management guidance only, or from
market consensus as well. Beating guidance is not variant perception when the forecast
merely matches consensus; a model that equals consensus carries no forecast edge.

NUMERIC CONSEQUENCE

For the decisive assumption, show what happens to operating profit and fair value if it
is wrong, using the verified model's own numbers. Do not invent replacement figures.

Then append TWO JSON objects, each on its own line with nothing after them, so the debate's
case table can quantify both cases against the verified model. Caps: revenue_pct and
operating_profit_pct stay within +/-{{cap_pct}}% of the base case, the multiple stays within
+/-{{cap_multiple}}% of the base multiple, confidence is between 0 and 1; a case beyond a
cap is rejected:
{"stance": "aggressive", "probability": 0.34, "revenue_pct": 2.0, "operating_profit_pct": 3.0, "multiple": 30.0, "momentum": "...", "sentiment": "...", "evidence": ["document_id"]}
{"stance": "bearish", "probability": 0.34, "revenue_pct": -2.0, "operating_profit_pct": -3.0, "multiple": 22.0, "momentum": "...", "sentiment": "...", "evidence": ["document_id"]}"""


DELTA_TEMPLATE = r"""You are the Adversarial Investment Reviewer, round {{round}}.

Your previous review:
{{prior_review}}

No new source evidence has arrived since that review: the analyst reports, the verified
model and the decision horizon are unchanged, and you have no tools.

Report ONLY what changed. Use exactly these headings, in this order:

NEW EVIDENCE:
- a fact or calculation not in your earlier review, or "none"

CHANGED VIEW:
- a conclusion you now hold differently, and why, or "none"

WITHDRAWN CLAIMS:
- an earlier claim you no longer support, or "none"

UNRESOLVED ISSUE:
- the single issue still capable of changing the decision

Do not restate the earlier analysis, the model tables, the price structure or the case
table. If nothing material changed, answer exactly:

NO MATERIAL UPDATE

Only if a stance's numbers changed, append the same two case JSON objects as before,
updated; otherwise append nothing."""


DELTA_TEMPLATE_V2 = r"""You are the Adversarial Investment Reviewer, round {{round}}.

Your previous review:
{{prior_review}}

No new source evidence has arrived: the analyst reports, the verified model and the
decision horizon are unchanged. Report ONLY what changed, under exactly these headings:

POSITION CHANGE:
- the stance you now hold vs the one you held, or "none"

STRONGEST UNRESOLVED OBJECTION:
- the single objection still capable of changing the decision

DIRECT ANSWER:
- answer that objection, or state why it cannot be answered

CONCESSION:
- what you concede, or "none"

NEW EVIDENCE OR CALCULATION:
- a fact or calculation not in your earlier review, or "none"

DECISION IMPACT:
- how this changes the rating, target or timing, or "none"

Limit the answer to 150 words. Do not repeat a prior fact unless it is used in a new
calculation. If nothing material changed, answer exactly:

NO MATERIAL UPDATE

Only if a stance's numbers changed, append the same two case JSON objects as before,
updated; otherwise append nothing."""


def _feature(name: str) -> bool:
    import os

    return os.environ.get(f"TRADINGAGENTS_FEATURE_{name}", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


RETRY_TEMPLATE = r"""Your review of {{instrument_ticker}} omitted the mandatory rebuttal. Here is
the review:

{{review}}

State the strongest evidence-based argument against the conclusion you currently favour,
then either rebut it with different evidence or a calculation, or concede it and say what
the concession changes. Return only these two headings and their paragraphs:

## Strongest Opposing Argument
...

## Rebuttal or Concession
..."""

SENSITIVITY_RETRY = (
    "Your review did not cite a valid ranked sensitivity. The valid IDs are: {ids}. "
    "Reply with one line only: `Decisive sensitivity ID: <id>` using one of those IDs, "
    "or `Decisive sensitivity ID: not established` if none of them is the decisive "
    "variable. Do not repeat the review."
)

_SENSITIVITY_LINE = re.compile(
    r"(?im)^\s*(?:decisive\s+)?sensitivity\s+id\s*:\s*`?([A-Za-z0-9_\-]+)`?"
)


REPAIR_TEMPLATE = r"""Your review of {{instrument_ticker}} did not supply every required
section. Here is what you wrote:

{{review}}

Write ONLY the missing sections below, each with its exact heading and a substantive
paragraph. Do not repeat a section you already wrote, and do not rewrite the review:

{{missing_headings}}"""


_SECTION_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
_PLACEHOLDERS = {"", "none", "n/a", "na", "not applicable"}


def parse_review_sections(text: str) -> dict[str, str]:
    """The review's sections by heading; anything before the first heading is ignored."""
    text = text or ""
    matches = list(_SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[name] = text[match.end():end].strip()
    return sections


def needs_rebuttal_retry(text: str) -> bool:
    """Whether the review's rebuttal section is missing, blank or a placeholder."""
    value = parse_review_sections(text).get("Rebuttal or Concession", "").strip().lower()
    return value in _PLACEHOLDERS


def missing_headings(text: str) -> list[str]:
    """The core sections the review did not supply, in contract order."""
    sections = parse_review_sections(text)
    missing = []
    for name in CORE_HEADINGS:
        value = sections.get(name, "").strip().lower()
        if value in _PLACEHOLDERS:
            missing.append(name)
    return missing


def merge_rebuttal(argument: str, retry: str) -> str:
    """Put the retried argument/rebuttal where the empty headings were."""
    sections = parse_review_sections(retry)
    opponent = sections.get("Strongest Opposing Argument", "").strip()
    rebuttal = sections.get("Rebuttal or Concession", "").strip()
    if not opponent and not rebuttal:
        return argument
    for name in ("Strongest Opposing Argument", "Rebuttal or Concession"):
        argument = re.sub(
            rf"(?ms)^##\s+{re.escape(name)}\s*$.*?(?=^##\s|\Z)", "", argument
        )
    argument = argument.rstrip()
    if opponent:
        argument += f"\n\n## Strongest Opposing Argument\n{opponent}"
    if rebuttal:
        argument += f"\n\n## Rebuttal or Concession\n{rebuttal}"
    return argument


def _is_substantive(value: str) -> bool:
    return bool(value.strip()) and value.strip().lower() not in _PLACEHOLDERS


def cited_sensitivity(argument: str) -> str:
    """The id exactly as the review wrote it (an alias, possibly)."""
    match = _SENSITIVITY_LINE.search(argument or "")
    if not match:
        return ""
    value = match.group(1)
    return "" if value.lower() == "not" else value


def decisive_sensitivity(
    argument: str, valid: set[str], aliases: dict[str, str] | None = None
) -> str:
    """The ranked sensitivity the review cites, canonicalised, or "".

    A legacy alias resolves to its canonical id; the caller records that id, so
    an alias never becomes a second sensitivity in the state.
    """
    value = cited_sensitivity(argument)
    if not value:
        return ""
    if not valid:
        return value
    if value in valid:
        return value
    return (aliases or {}).get(value, "")


def merge_sections(raw: str, repair: str) -> str:
    """Complete the review in contract order, filling only the sections that were missing.

    The 13:09 run lost the bull and bear cases because a retry overwrote the answer
    instead of completing it. The raw answer is never discarded: a section the repair
    did not supply stays exactly as the reviewer first wrote it.
    """
    original = parse_review_sections(raw)
    repaired = parse_review_sections(repair)
    if not original:
        # An unstructured answer (no contract headings, possibly a legacy review
        # with its case JSON): keep it whole and append only what the repair adds.
        blocks = [raw.strip()] if raw.strip() else []
        for name in REVIEW_HEADINGS:
            text = repaired.get(name, "")
            if _is_substantive(text):
                blocks.append(f"## {name}\n{text.strip()}")
        return "\n\n".join(blocks)

    blocks: list[str] = []
    first_heading = _SECTION_RE.search(raw)
    if first_heading is not None:
        preamble = raw[: first_heading.start()].strip()
        if preamble:
            blocks.append(preamble)
    for name in REVIEW_HEADINGS:
        text = ""
        for source in (original, repaired):
            if _is_substantive(source.get(name, "")):
                text = source[name].strip()
                break
        if text:
            blocks.append(f"## {name}\n{text}")
    # A heading outside the contract (an older review, a stray section) keeps its
    # content at the end rather than vanishing from the record.
    for name, text in original.items():
        if name not in REVIEW_HEADINGS and _is_substantive(text):
            blocks.append(f"## {name}\n{text.strip()}")
    return "\n\n".join(blocks)


def unresolved_issue(text: str) -> str:
    """The one open issue a reader should watch: the delta line, else the resolving evidence."""
    match = re.search(r"(?ims)^UNRESOLVED ISSUE:\s*\n?(.+?)(?=\n[A-Z][A-Z ]+:|\Z)", text or "")
    if match:
        return match.group(1).strip()[:1200]
    sections = parse_review_sections(text)
    for name in ("Evidence That Would Resolve It", "Decisive Disagreement"):
        if sections.get(name):
            return sections[name].strip()[:1200]
    return ""


def _fill(state: dict, ticker: str) -> str:
    package = str(state.get("research_package") or "").strip()
    debate = state.get("investment_debate_state") or {}
    position, _average_cost = position_fields(str(state.get("portfolio_context") or ""))
    prior = strip_case_json(
        str(debate.get("last_review") or debate.get("challenge_history") or "")
    ).strip()

    # Round 2 onwards is a delta: the reviewer sees its own last answer, not the
    # whole debate, and is asked for changes only. Re-reading the full history is
    # what made Deep mode repeat itself (#finding: repetition).
    if int(debate.get("count") or 0) >= 1:
        template = DELTA_TEMPLATE_V2 if _feature("REVIEWER_DELTA_V2") else DELTA_TEMPLATE
        return fill_placeholders(
            template,
            {
                "round": int(debate.get("count") or 0) + 1,
                "prior_review": prior or "none — no earlier review was recorded.",
            },
        )

    values = {
        "instrument_ticker": ticker or "null",
        "evidence_pack_or_null": evidence_pack(ticker, str(state.get("trade_date") or "")),
        "fundamental_analysis_or_null": fundamentals_with_repair_notice(state),
        "model_audit_or_null": package or "null — no verified model was built for this run.",
        "technical_analysis_or_null": report_or_absent(state.get("market_report"), "market"),
        "decision_horizon_or_null": str(state.get("decision_horizon") or "").strip() or "null",
        "current_position_or_null": position,
        "mandate_constraints_or_null": "null",
        "prior_review_or_null": prior or "none — this is the first pass.",
        "cap_pct": f"{CAP_FORECAST_PCT:g}",
        "cap_multiple": f"{CAP_MULTIPLE_PCT:g}",
    }
    return fill_placeholders(TEMPLATE, values)


def create_adversarial_reviewer(llm):
    def reviewer_node(state) -> dict:
        debate = state["investment_debate_state"]
        ticker = str(state.get("company_of_interest") or "").strip()
        first_round = int(debate.get("count") or 0) == 0

        prompt = (
            _fill(state, ticker)
            + COMPARATIVE_CLAIM_RULE
            + EVIDENCE_DISCIPLINE
            + case_availability(state)
            + get_language_instruction()
        )

        response = llm.invoke(prompt)
        raw = str(response.content).strip()
        argument = raw

        missing = missing_headings(raw)
        if first_round and missing:
            # A review without the bull/bear cases is not a review. One repair pass
            # asks ONLY for the missing sections; the first answer is kept whole, so
            # the retry can add to it but never replace it.
            repair_prompt = fill_placeholders(
                REPAIR_TEMPLATE,
                {
                    "instrument_ticker": ticker or "null",
                    "review": raw,
                    "missing_headings": "\n".join(f"## {name}\n..." for name in missing),
                },
            )
            try:
                repair = str(llm.invoke(prompt + "\n\n" + repair_prompt).content).strip()
            except Exception:  # noqa: BLE001 - keep the first answer
                repair = ""
            if repair:
                argument = merge_sections(raw, repair)
            missing = missing_headings(argument)

        if first_round and needs_rebuttal_retry(argument):
            retry = fill_placeholders(
                RETRY_TEMPLATE,
                {"instrument_ticker": ticker or "null", "review": argument},
            )
            with contextlib.suppress(Exception):  # a failed retry keeps the review as written
                argument = merge_rebuttal(argument, str(llm.invoke(retry).content))
            missing = missing_headings(argument)

        material = "NO MATERIAL UPDATE" not in argument.upper()
        challenge = str(debate.get("challenge_history") or "")
        issue = unresolved_issue(argument)
        valid_ids: set[str] = set()
        alias_ids: dict[str, str] = {}
        try:
            valid_ids = {
                str(value) for value in json.loads(str(state.get("sensitivity_ids") or "[]"))
            }
            alias_ids = {
                str(key): str(value)
                for key, value in json.loads(
                    str(state.get("sensitivity_alias_map") or "{}")
                ).items()
            }
        except ValueError:
            valid_ids, alias_ids = set(), {}
        cited = cited_sensitivity(argument)
        decisive = decisive_sensitivity(argument, valid_ids, alias_ids)
        if cited and cited in alias_ids:
            # A legacy id is accepted but recorded against its canonical key,
            # and the report says the citation predates the stable ids.
            argument += (
                f"\n\nSensitivity id note: the legacy id {cited!r} resolves to "
                f"{alias_ids[cited]!r}; the canonical id is used from here."
            )
        if valid_ids and not decisive and first_round:
            # One focused retry: an id is the only thing the review is missing.
            try:
                retry = llm.invoke(
                    argument
                    + "\n\n"
                    + SENSITIVITY_RETRY.format(ids=", ".join(sorted(valid_ids)))
                )
                decisive = decisive_sensitivity(str(retry.content), valid_ids)
            except Exception:  # noqa: BLE001 - a failed retry leaves it unestablished
                decisive = ""
        if valid_ids and not decisive:
            argument += (
                "\n\nDecisive sensitivity ID: not established — no ranked sensitivity was "
                "cited, so the decisive disagreement could not be ranked against the model."
            )
        elif not valid_ids and first_round:
            argument += (
                "\n\nDecisive disagreement: not established — deterministic sensitivity "
                "analysis is unavailable for this run."
            )
        new_debate_state = {
            "history": str(debate.get("history") or "") + "\n" + argument,
            "challenge_history": (challenge + "\n" + argument) if challenge else argument,
            "last_review": argument,
            "first_review": (
                argument if first_round else str(debate.get("first_review") or "")
            ),
            "first_review_raw": (
                raw if first_round else str(debate.get("first_review_raw") or "")
            ),
            "unresolved_issue": issue or str(debate.get("unresolved_issue") or ""),
            "review_missing": "" if first_round and not missing else (
                ", ".join(missing) if first_round else str(debate.get("review_missing") or "")
            ),
            "decisive_sensitivity_id": decisive,
            "no_material_update": not material,
            # Kept for old checkpoints and the state log; the reviewer never writes them.
            "bull_history": debate.get("bull_history", ""),
            "bear_history": debate.get("bear_history", ""),
            "current_response": argument,
            "count": debate.get("count", 0) + 1,
        }

        result: dict = {"investment_debate_state": new_debate_state,
                        "decisive_sensitivity_id": decisive}
        cases = parse_cases(argument) if material else []
        if cases:
            existing = state.get("debate_cases", "")
            for case in cases:
                existing = with_case(existing, case)
            result["debate_cases"] = existing
        return result

    return reviewer_node
