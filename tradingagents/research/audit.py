"""Check the numbers the report marks as the model's against the model.

An agent marks every figure it takes from the verified model as ``[[229.5]]``. A
figure it computed itself — a PEG, a growth rate, a stop level, a position size —
or took from a vendor, filing or broker is not marked, and is not checked.

That split is the whole design. Deciding from the prose which number is whose was
tried: a model read each section and labelled the figures, and about one in ten
labels was wrong, so every run produced a handful of findings that were labels
rather than errors. A mark is made by the agent that knows where the number came
from, and the check here is arithmetic.

Two mark shapes are not figures and are handled before any comparison: a bare
single digit (``[[6]]``) is a citation, counted as unchecked, and a mark whose
content is not a number at all (``[[doc-5803.T-…]]``) is a label. ``reporting``
removes the brackets before the report is written, so the reader never sees them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .kernel import ModelResult
from .valuation import Valuation

# "[[229.5]]" or "[[JPY 4,111]]": what the agent marked as the model's.
_MARK = re.compile(r"\[\[([^\[\]\n]{1,80})\]\]")
# "[[1][2]]": two marks fused, the way an agent writes citations back to back.
# A fused mark is never a figure, but the report must not show the brackets.
_FUSED = re.compile(r"\[(\[[^\[\]\n]{1,80}\](?:\[[^\[\]\n]{1,80}\])+)\]")
_PAIR = re.compile(r"\[[^\[\]\n]{1,80}\]")
_CURRENCY = re.compile(r"[¥$€£]|\b(?:JPY|USD|EUR|GBP|CNY|KRW|TWD)\b", re.IGNORECASE)
# The minus that prose actually uses: ASCII, U+2212 MINUS SIGN, en dash.
_SIGNED = re.compile(r"(?<![\w.])([-\u2212\u2013])\s?(?=\d)")
_DATE_SPAN = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass
class Finding:
    section: str
    number: float
    context: str

    def describe(self) -> str:
        return f"{self.section}: {_fmt_number(self.number)} — {self.context}"


def _fmt_number(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:g}"


def marked_figures(text: str) -> list[str]:
    """The figures the text marks as the model's, in order."""
    return [match.group(1).strip() for match in _MARK.finditer(text)]


def strip_marks(text: str) -> str:
    """The text without the marks, for the report a person reads."""
    stripped = _MARK.sub(lambda match: match.group(1), text)
    # A fused cluster is citations, not a figure: unwrap each part so the report
    # reads "from the filing 1, 2" instead of showing "[[1][2]]" literally.
    return _FUSED.sub(
        lambda match: ", ".join(pair[1:-1] for pair in _PAIR.findall(match.group(1))),
        stripped,
    )


def _is_reference(figure: str) -> bool:
    """A bare single digit: how an agent numbers its citations.

    A model figure carries a decimal point, a unit, a percent, thousands
    separators, a sign or a second digit — ``[[80]]`` and ``[[12]]`` are model
    lines, ``[[6]]`` and ``[[2]]`` are footnotes. Numbered citations are what an
    agent writes when it cites a document, so they are counted and reported as
    unchecked rather than guessed at: a run whose citations outnumber its
    figures must not bury real findings in noise.
    """
    return re.fullmatch(r"\d", _CURRENCY.sub(" ", figure).strip()) is not None


def known_values(model: ModelResult, valuation: Valuation, cases: list[dict] | None = None) -> set[float]:
    """Every figure the model can legitimately produce, plus any computed cases."""
    spec = model.spec
    values: set[float] = {
        spec.valuation.price,
        valuation.eps,
        valuation.pe,
        valuation.fair_value_low,
        valuation.fair_value_mid,
        valuation.fair_value_high,
    }
    if model.market_cap is not None:
        values.add(model.market_cap)

    for row in model.rows:
        for metric in ("revenue", "operating_profit", "ordinary_profit", "net_profit", "operating_margin"):
            value = getattr(row, metric)
            if value is not None:
                values.add(round(value, 2))
        values.update(round(value, 2) for value in row.eps.values())

    for periods in model.pe.values():
        values.update(round(value, 2) for value in periods.values())

    for bridge in spec.bridges:
        values.add(round(bridge.base_value, 2))
        values.add(round(bridge.total, 2))
        values.update(round(line.value, 2) for line in bridge.lines)
        # The bridge's own total row is the sum of its lines, which is a model
        # figure even though the spec stores only the lines and the base.
        values.add(round(sum(line.value for line in bridge.lines), 2))

    for segments in spec.segments.values():
        for segment in segments:
            values.add(round(segment.revenue, 2))
            values.add(round(segment.operating_profit, 2))

    for flow in spec.cashflow.values():
        values.update({
            round(flow.net_profit, 2), round(flow.depreciation, 2),
            round(flow.working_capital_change, 2), round(flow.operating_cash_flow, 2),
            round(flow.capex, 2), round(flow.free_cash_flow, 2),
        })
        if flow.dividend is not None:
            values.add(round(flow.dividend, 2))

    for fin in [*spec.quarterly.values()]:
        for metric in ("revenue", "operating_profit", "net_profit"):
            value = fin.get(metric)
            if value is not None:
                values.add(round(value, 2))

    for base in spec.share_bases:
        values.add(round(base.shares_m, 2))
    for scenario in spec.scenarios:
        values.update({
            round(scenario.eps, 2), round(scenario.multiple, 2), round(scenario.target_price, 2),
        })
        if scenario.revenue is not None:
            values.add(round(scenario.revenue, 2))
        if scenario.operating_profit is not None:
            values.add(round(scenario.operating_profit, 2))
    for row in valuation.sensitivity:
        values.update({round(row.implied_price, 2), round(row.multiple, 2)})
    for scenario in valuation.scenarios:
        values.update({round(scenario["target"], 2), round(scenario["eps"], 2)})

    # Consensus figures are external data the model compares itself to; quoting
    # them is not a claim about the model.
    if spec.consensus:
        for period_values in spec.consensus.periods.values():
            values.update(round(v, 2) for v in period_values.values() if v is not None)

    # The debate's own arithmetic is legitimate: a manager quoting a case's EPS
    # or target is quoting a number this system computed.
    if cases:
        from .cases import Case, apply_case

        for raw in cases:
            try:
                result = apply_case(model, valuation, Case.from_dict(raw))
            except Exception:  # noqa: BLE001 - a malformed case must not block the audit
                continue
            values.update(
                {
                    round(result.revenue, 2),
                    round(result.operating_profit, 2),
                    round(result.net_profit, 2),
                    round(result.eps, 2),
                    round(result.multiple, 2),
                    round(result.target, 2),
                }
            )
    return values


def _figure_candidates(figure: str) -> list[float]:
    """The values a written figure could mean, at the scales a report mixes.

    A section writes "6.8tn" against a model in billions, "1,655.8m" shares
    against a share count in millions, and "4,111" for a price. Rather than pick
    one reading, return the plausible ones and accept if any matches the model.
    A leading minus is part of the number: a bridge line of -5 is not a claim of 5.
    """
    text = _CURRENCY.sub(" ", figure)
    # Only a figure can be a figure: a mark holding a document id or a label
    # ("doc-5803.T-…") must not be matched against the model on its digits.
    if not re.match(r"[~≈\-−–]?\d", text.strip()):
        return []
    negative = bool(_SIGNED.search(text))
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return []
    try:
        value = float(match.group(0).replace(",", ""))
    except ValueError:
        return []
    if negative:
        value = -value
    if re.search(r"(?:tn|trillion)\b", text, re.IGNORECASE):
        value *= 1000.0
    seen = [value, value / 1e3, value * 1e3, value / 1e6, value / 1e9]
    unique: list[float] = []
    for candidate in seen:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _matches(value: float, known: set[float], *, round_quote: bool = False) -> bool:
    """Whether the prose figure is one the model produced.

    A figure written as a round hundred or thousand ("~JPY 5,000") is a quote
    the author rounded, so it may sit 1% away from the model's 5,049. Anything
    written in full is held to 0.2%.
    """
    tolerance = abs(value) * (0.01 if round_quote else 0.002)
    return any(abs(value - candidate) <= max(0.15, tolerance) for candidate in known)


def _is_round_quote(value: float) -> bool:
    """A round hundred or thousand, e.g. a target written 5,000 or 5000.0."""
    return value >= 100 and float(value).is_integer() and value % 100 == 0


def _line_for(figure: str, lines: list[str]) -> str:
    """The line the figure was marked in, ignoring matches inside a date."""
    needle = " ".join(figure.split())
    if not needle:
        return figure
    # The mark first: a bare needle like "1" or "6" otherwise lands on the first
    # line that happens to contain the digit, which is not where the figure is.
    for probe in (f"[[{needle}]]", needle):
        for line in lines:
            flat = " ".join(line.split())
            for match in re.finditer(re.escape(probe), flat):
                if any(span.start() <= match.start() < span.end()
                       for span in _DATE_SPAN.finditer(flat)):
                    continue
                return line.strip()[:160]
    return figure


def _is_percentage(line: str, figure: str) -> bool:
    """Whether the number, as the line writes it, is followed by a percent sign."""
    digits = re.escape(re.sub(r"[^\d.,]", "", figure))
    if not digits:
        return False
    return re.search(digits + r"[\d.]*\s*%", line) is not None


def audit_text(
    text: str,
    section: str,
    known: set[float],
    *,
    band: tuple[float, float] | None = None,
    stats: dict | None = None,
) -> list[Finding]:
    """Findings for the marked figures in one section."""
    findings: list[Finding] = []
    seen: set[tuple[float, str]] = set()
    lines = text.splitlines()
    figures = marked_figures(text)
    references = sum(1 for figure in figures if _is_reference(figure))
    if stats is not None:
        stats.setdefault("sections", {})[section] = len(figures) - references
        stats["marked"] = stats.get("marked", 0) + len(figures)
        stats["references"] = stats.get("references", 0) + references

    for figure in figures:
        if _is_reference(figure):
            continue
        line = _line_for(figure, lines)
        if _is_percentage(line, figure):
            continue
        candidates = _figure_candidates(figure)
        if not candidates:
            continue
        value = candidates[0]
        if band is not None and band[0] <= value <= band[1]:
            continue
        rounded = _is_round_quote(value)
        if any(_matches(candidate, known, round_quote=rounded) for candidate in candidates):
            continue
        key = (value, line)
        if key in seen:   # the same misquote quoted again is one error, not two
            continue
        seen.add(key)
        findings.append(Finding(section, value, line))
    return findings


def audit_state(
    final_state: dict,
    model: ModelResult,
    valuation: Valuation,
    *,
    stats: dict | None = None,
) -> list[Finding]:
    """Audit the marked figures in the sections a run writes for a reader."""
    import json

    raw_cases = final_state.get("debate_cases")
    cases = None
    if raw_cases:
        try:
            cases = json.loads(raw_cases)
        except json.JSONDecodeError:
            cases = None
    known = known_values(model, valuation, cases)
    band = (valuation.fair_value_low, valuation.fair_value_high)

    findings: list[Finding] = []
    for section, key in (
        ("Fundamentals Analyst", "fundamentals_report"),
        ("Research Manager", "investment_plan"),
        ("Trader", "trader_investment_plan"),
        ("Portfolio Manager", "final_trade_decision"),
    ):
        text = final_state.get(key)
        if isinstance(text, str) and text.strip():
            findings.extend(
                audit_text(text, section, known, band=band, stats=stats)
            )
    return findings


def render(findings: list[Finding], stats: dict | None = None) -> str:
    """The audit file's text, including what was actually checked."""
    if stats is not None and not stats.get("marked"):
        return (
            "No figure was marked as the model's in this run, so nothing was checked. "
            "The agents mark a figure they take from the verified model in double square brackets; "
            "an unmarked report cannot be audited."
        )

    marked = int((stats or {}).get("marked", 0))
    sections = (stats or {}).get("sections") or {}
    references = int((stats or {}).get("references", 0))
    checked = marked - references
    summary = _by_section(sections)
    if references:
        summary += (
            f" {references} mark(s) were plain reference numbers (a bare single digit, "
            "as the number 6) and were not treated as figures."
        )

    if not findings:
        return (
            f"All {checked} figure(s) the report marks as the model's were found in the "
            f"verified model. {summary}"
        )

    lines = [
        f"{len(findings)} marked figure(s) do not match the verified model. "
        "Each is either a misquote or an error.",
        "",
        "| Section | Figure | Where it appears |",
        "|---|---|---|",
    ]
    for finding in findings[:30]:
        context = finding.context.replace("|", "\\|")
        lines.append(f"| {finding.section} | {_fmt_number(finding.number)} | {context} |")
    if len(findings) > 30:
        lines.append(f"| … | | {len(findings) - 30} more |")
    lines += ["", f"Checked {checked} marked figure(s). {summary}"]
    return "\n".join(lines)


def _by_section(sections: dict) -> str:
    if not sections:
        return ""
    parts = [f"{name} {count}" for name, count in sections.items()]
    return "Figures checked by section: " + ", ".join(parts) + "."
