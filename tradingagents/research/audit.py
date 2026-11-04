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

import ast
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
    kind: str = "figure"     # "figure" = misquoted model value; "relation" = a
                             # comparative claim contradicted by its own
                             # operands; "percentage" = a level's distance from
                             # the price, recomputed
    subject: float | None = None
    object: float | None = None
    expected: str = ""

    def describe(self) -> str:
        return f"{self.section}: {_fmt_number(self.number)} — {self.context}"


def _fmt_number(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:g}"


# An analyst's own arithmetic may be marked for checking instead of suppressed:
# [[computed: 130 = 13/10*100]]. Only arithmetic is evaluated — numbers,
# parentheses, + - * / and unary sign — never names or calls.
_COMPUTED = re.compile(
    r"\[\[\s*computed\s*:\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*=\s*([^\]]+?)\]\]",
    re.IGNORECASE,
)


def _eval_formula(expression: str) -> float:
    tree = ast.parse(expression, mode="eval")

    def walk(node) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ValueError("only arithmetic is allowed in a computed figure")

    return walk(tree)


def extract_computed(text: str) -> tuple[str, set[float], list[tuple[float, str]]]:
    """Remove computed marks, returning their verified values and failures."""
    verified: set[float] = set()
    failures: list[tuple[float, str]] = []

    def replace(match: re.Match) -> str:
        try:
            claimed = float(match.group(1).replace(",", ""))
            value = _eval_formula(match.group(2))
        except (ValueError, SyntaxError, ZeroDivisionError):
            failures.append((0.0, match.group(0)))
            return " "
        if abs(value - claimed) <= max(0.01, abs(claimed) * 0.001):
            verified.add(claimed)
        else:
            failures.append((claimed, match.group(2).strip()))
        return " "

    return _COMPUTED.sub(replace, text), verified, failures


def marked_figures(text: str) -> list[str]:
    """The figures the text marks as the model's, in order.

    A sign glued to the mark (``−[[14]]``) belongs to the figure: models write
    the minus outside the brackets, but the number is still the negative one.
    """
    figures = []
    for match in _MARK.finditer(text):
        content = match.group(1).strip()
        start = match.start()
        previous = text[start - 1] if start else ""
        before = text[start - 2] if start > 1 else ""
        # A sign glued to the mark is the figure's sign — unless it is a range
        # separator after another number or mark: [[3,479]]–[[6,088]].
        if (
            previous in "-−–"
            and before not in "]0123456789"
            and not content.startswith(("-", "−", "–"))
        ):
            content = previous + content
        elif previous.isspace() and not content.startswith(("-", "−", "–")):
            # A sign written just before the mark with a space: "increase −
            # [[19.0]] tax". Never after a completed figure — "5 − [[3]]" and
            # "]] − [[3]]" are subtractions, and the operand stays positive.
            head = text[:start].rstrip()
            if head and head[-1] in "-−–":
                before_sign = head[:-1].rstrip()
                if not before_sign or before_sign[-1] not in "]0123456789":
                    content = head[-1] + content
        figures.append(content)
    return figures


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
        # A bridge formula is model-authored text printed in the package; the
        # numbers inside it (H1 actuals, H2 guidance, residuals) are figures an
        # analyst marks from that package, not from prose we cannot see.
        for line in bridge.lines:
            for number in re.findall(r"\d[\d,]*(?:\.\d+)?", line.formula or ""):
                try:
                    values.add(round(float(number.replace(",", "")), 2))
                except ValueError:
                    continue

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
            round(scenario.eps, 2), round(scenario.multiple, 2), round(scenario.resolved_target, 2),
        })
        if scenario.revenue is not None:
            values.add(round(scenario.revenue, 2))
        if scenario.operating_profit is not None:
            values.add(round(scenario.operating_profit, 2))
    for row in valuation.sensitivity:
        values.update({round(row.implied_price, 2), round(row.multiple, 2)})
    for scenario in valuation.scenarios:
        # The package prints the change as a rounded, signed percent ("(-42%)"),
        # so that display value is what a mark on it means.
        change = int(f"{scenario['change'] * 100:.0f}")
        values.update({
            round(scenario["target"], 2), round(scenario["eps"], 2),
            float(change), float(abs(change)),
        })

    # Consensus figures are external data the model compares itself to; quoting
    # them is not a claim about the model.
    if spec.consensus:
        for period_values in spec.consensus.periods.values():
            values.update(round(v, 2) for v in period_values.values() if v is not None)

    # Management's guidance is printed in the package as a comparison table, so
    # a mark on it quotes the model's own artifact — even when the forecast
    # overrides the guided year and the rows no longer carry these numbers.
    for guided in spec.guidance.values():
        for metric in ("revenue", "operating_profit", "ordinary_profit", "net_profit"):
            value = guided.get(metric)
            if value is not None:
                values.add(round(value, 2))

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
    if not re.match(r"[~≈+\-−–]?\d", text.strip()):
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
    probes = [f"[[{needle}]]", needle]
    if needle[:1] in "-−–":   # a sign written outside the mark: −[[14]]
        probes.append(needle[:1] + "[[" + needle[1:] + "]]")
    for probe in probes:
        for line in lines:
            flat = " ".join(line.split())
            for match in re.finditer(re.escape(probe), flat):
                if any(span.start() <= match.start() < span.end()
                       for span in _DATE_SPAN.finditer(flat)):
                    continue
                return line.strip()[:160]
    return figure


def audit_text(
    text: str,
    section: str,
    known: set[float],
    *,
    band: tuple[float, float] | None = None,
    stats: dict | None = None,
    superseded: set[float] | None = None,
) -> list[Finding]:
    """Findings for the marked figures in one section.

    A mark is either checked against the model, counted as a citation (a bare
    single digit), or skipped because it is a label rather than a number (a
    document id, a date). Skipped is not checked: the counters in ``stats`` keep
    the three apart so the rendered audit can say what it actually did.

    ``superseded`` holds the values that changed in a model repair: a figure that
    matches one was valid when it was written and is no longer current. It is
    counted apart and never reported as a failure.
    """
    findings: list[Finding] = []
    seen: set[tuple[float, str]] = set()
    text, computed, computed_failures = extract_computed(text)
    known = set(known) | computed
    lines = text.splitlines()
    figures = marked_figures(text)
    references = 0
    skipped = 0
    checked = 0
    for figure in figures:
        if _is_reference(figure):
            references += 1
            continue
        candidates = _figure_candidates(figure)
        if not candidates:
            # A mark holding a document id or a label carries no number to check.
            skipped += 1
            continue
        checked += 1
        line = _line_for(figure, lines)
        value = candidates[0]
        if band is not None and band[0] <= value <= band[1]:
            continue
        rounded = _is_round_quote(value)
        if any(_matches(candidate, known, round_quote=rounded) for candidate in candidates):
            continue
        if superseded and any(
            _matches(candidate, superseded, round_quote=rounded) for candidate in candidates
        ):
            # Valid against the model the analyst read, invalid against v2.
            if stats is not None:
                stats["superseded"] = stats.get("superseded", 0) + 1
                stats.setdefault("superseded_values", []).append(value)
            continue
        key = (value, line)
        if key in seen:   # the same misquote quoted again is one error, not two
            continue
        seen.add(key)
        findings.append(Finding(section, value, line))

    for claimed, formula in computed_failures:
        findings.append(
            Finding(
                section,
                claimed,
                f"computed: {formula}",
                kind="computed",
                expected="the formula does not reproduce the stated figure",
            )
        )

    if stats is not None:
        stats.setdefault("sections", {})[section] = checked
        stats["marked"] = stats.get("marked", 0) + len(figures)
        stats["references"] = stats.get("references", 0) + references
        stats["skipped"] = stats.get("skipped", 0) + skipped
        stats["checked"] = stats.get("checked", 0) + checked
    return findings


# --- comparative claims (relational consistency) -----------------------------
# Every number in a false comparison can be individually correct, so the figure
# check above passes it: the error is in the RELATION ("38.6 already exceeds
# 63.7"). This check never asks where a number came from — only what the
# sentence itself asserts — so it does not reopen the provenance problem the
# mark split exists to avoid. Two tiers, both deliberately conservative:
#   1. the claim prints both operands (same unit class, no range, unambiguous);
#   2. the object is an explicit model period label with a stated metric, and
#      the subject is the figure attached to that metric in the same clause.

# The prose sections a reader sees, mirroring reporting._SECTIONS' state paths.
# Figure findings stay scoped to the four sections they were designed for;
# a false comparison can appear in any prose, so relations scan them all.
_PROSE_SECTIONS = (
    ("Market Analyst", ("market_report",)),
    ("Sentiment Analyst", ("sentiment_report",)),
    ("News Analyst", ("news_report",)),
    ("Fundamentals Analyst", ("fundamentals_report",)),
    ("Adversarial Reviewer", ("investment_debate_state", "challenge_history")),
    ("Aggressive Analyst", ("risk_debate_state", "aggressive_history")),
    ("Conservative Analyst", ("risk_debate_state", "conservative_history")),
    ("Neutral Analyst", ("risk_debate_state", "neutral_history")),
    ("Research Manager", ("investment_plan",)),
    ("Trader", ("trader_investment_plan",)),
    ("Portfolio Manager", ("final_trade_decision",)),
)

_GREATER = re.compile(
    r"\b(exceeds?|exceeded|exceeding|more than|greater than|higher than|larger than|"
    r"surpasses?|surpassed|above)\b",
    re.IGNORECASE,
)
_LESSER = re.compile(
    r"\b(falls? short of|fell short of|below|beneath|under|less than|lower than|"
    r"smaller than|beneath)\b",
    re.IGNORECASE,
)
# Predicates whose subject may FOLLOW the word ("…mark: 38.6bn versus 63.7bn") —
# the appositive pattern. Prepositionals ("a close below 7,883") have no
# numeric subject at all and are never paired backwards.
_APPOSITIVE = {
    "exceed", "exceeds", "exceeded", "exceeding", "more than", "greater than",
    "higher than", "larger than", "surpass", "surpasses", "surpassed",
}
_HEDGE = re.compile(
    r"\b(?:approximately|roughly|about|around|circa|nearly|almost|approx\.?)\b|~|≈",
    re.IGNORECASE,
)
# Noise that looks like an operand but is not one.
_CITATION_SPAN = re.compile(r"\[[^\]]*\]")
_FISCAL_LABEL = re.compile(
    r"\b(?:Q[1-4]|H[12])\s+FY\s*\d{1,2}/\d{2}\b|\b(?:Q[1-4]|H[12])\s+FY\d{2,4}\b|"
    r"\bFY\s*\d{1,2}/\d{2}\b|\bFY\d{2,4}\b|\bCY\s?\d{2,4}\b",
    re.IGNORECASE,
)
_TICKER = re.compile(r"\b\d{3,4}\.?[A-Z]{1,3}\b")          # 4062.T, SPX
_DAY_COUNT = re.compile(
    r"\b\d+(?:\.\d+)?[\s-]?(?:day|days|month|months|week|weeks|sessions?)\b", re.IGNORECASE
)
# "the 10 EMA at 9,047.92": the period in an indicator's name is not a figure,
# or the claim pairs the price against the 10.
_INDICATOR_NAME = re.compile(
    r"\b\d{1,4}[\s-]?(?:EMA|SMA|VWMA|RSI|MACD|ATR)\b", re.IGNORECASE
)
_BP = re.compile(r"\d[\d,]*(?:\.\d+)?\s?(?:bp|bps)\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MM_DD = re.compile(r"\b\d{2}-\d{2}\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_PAGE = re.compile(r"\bp\.\s*\d+\b")
# "Cell 9", "Phase 2", "Option 3": a numbered label, not a figure to compare.
_ENTITY_NUMBER = re.compile(
    r"\b(?:Cell|Phase|Stage|Option|Step|Tranche|Facility|Line)\s+\d+\b", re.IGNORECASE
)
_RANGE = re.compile(r"\d[\d,]*(?:\.\d+)?\s?[–—-]\s*\d")     # 6,915–8,644: ambiguous
_OPERAND = re.compile(
    r"(?<![\w.])(?:[~≈]\s*)?(?:[¥$€£]\s*)?(?:(?:JPY|USD|EUR|GBP|CNY|KRW|TWD)\s+)?"
    # The number must end in a digit: a trailing comma belongs to the sentence
    # ("…at 8,045, add on…"), and swallowing it hid the clause boundary that
    # stops 8,045 from being compared with the next clause's 8,622.
    r"([-−]?\d(?:[\d,]*\d)?(?:\.\d+)?)\s?(bn|tn|mn|billion|million|trillion|x|%|percent)?",
    re.IGNORECASE,
)
_METRIC_KEYWORDS = (
    ("operating_profit", re.compile(r"operating (?:profit|income)", re.IGNORECASE)),
    ("ordinary_profit", re.compile(r"ordinary profit", re.IGNORECASE)),
    ("net_profit", re.compile(r"net (?:profit|income)", re.IGNORECASE)),
    ("revenue", re.compile(r"\b(?:revenue|net sales|topline|top line)\b", re.IGNORECASE)),
    ("operating_margin", re.compile(r"operating margin", re.IGNORECASE)),
    ("eps", re.compile(r"\bEPS\b")),
)
_FACTOR = {"bn": 1e9, "tn": 1e12, "mn": 1e6}
_UNIT_SUFFIX = {"bn": 1e9, "tn": 1e12, "m": 1e6}


def _state_get(state: dict, path: tuple) -> object:
    value: object = state
    for key in path:
        value = (value or {}).get(key) if isinstance(value, dict) else None
    return value


def _state_set(state: dict, path: tuple, value: str) -> None:
    cursor = state
    for key in path[:-1]:
        child = cursor.get(key)
        if not isinstance(child, dict):
            child = {}
            cursor[key] = child
        cursor = child
    cursor[path[-1]] = value


def _period_table(model: ModelResult) -> dict[str, dict[str, float | None]]:
    """Period label -> metric -> value, for Tier-2 object resolution."""
    spec = model.spec
    table: dict[str, dict[str, float | None]] = {}
    for row in model.rows:
        basis = spec.eps_basis or (next(iter(row.eps)) if row.eps else None)
        table[row.label] = {
            "revenue": row.revenue,
            "operating_profit": row.operating_profit,
            "ordinary_profit": row.ordinary_profit,
            "net_profit": row.net_profit,
            "operating_margin": row.operating_margin,
            "eps": row.eps.get(basis) if basis else None,
        }
    for label, fin in (spec.quarterly or {}).items():
        table.setdefault(label, {}).update(
            {metric: fin.get(metric) for metric in ("revenue", "operating_profit", "net_profit")}
        )
    return table


# --- percentages against the price -----------------------------------------
# "The 50-day SMA 10,168.07 ... sits 20.9% above price" is wrong when the price
# is 8,045: the SMA is 26.4% above it. The sentence prints both operands, so the
# claim is checkable without asking where either number came from.

_PCT_LEVEL = re.compile(
    r"\b(?:SMA|EMA|VWMA|moving average|average)\b[^\d]{0,16}"
    # The number must be a level, not the claim's own percentage: "the 200 SMA
    # is only 3.4% below spot" has no level printed, and reading 3.4 as one
    # reported a -100% miss every time a debater quoted another's percentage.
    r"(?P<level>\d[\d,]*(?:\.\d+)?)(?!\s*%)",
    re.IGNORECASE,
)
_PCT_VS_PRICE = re.compile(
    r"(?P<pct>\d+(?:\.\d+)?)\s*%\s*(?P<direction>above|below)\s+"
    r"(?:the\s+)?(?:current\s+)?(?:price|spot|close)",
    re.IGNORECASE,
)


def percentage_findings(text: str, section: str, price: float | None) -> list[Finding]:
    """Claims that an indicator sits X% above/below the price, recomputed.

    Conservative by construction: the sentence must name the indicator and its
    level, and must say "above/below price|spot|close". A percentage against a
    level ("3.4% above the 200-day SMA") is a different statement and is skipped.
    """
    if not price:
        return []
    findings: list[Finding] = []
    for sentence in _sentences(text):
        claim = _PCT_VS_PRICE.search(sentence)
        if claim is None:
            continue
        level_match = None
        for candidate in _PCT_LEVEL.finditer(sentence):
            if candidate.start() < claim.start():
                level_match = candidate
        if level_match is None:
            continue
        try:
            level = float(level_match.group("level").replace(",", ""))
            claimed = float(claim.group("pct")) / 100
        except ValueError:
            continue
        if level <= 0 or level == price:
            continue
        # A price level sits within an order of magnitude of the run price; a
        # small integer read as a level is a period count or a stray figure.
        if level < price * 0.05 or level > price * 20:
            continue
        if claim.group("direction").lower() == "below":
            claimed = -claimed
        expected = level / price - 1
        if (expected > 0) != (claimed > 0) or abs(expected - claimed) > max(
            0.01, abs(expected) * 0.10
        ):
            findings.append(
                Finding(
                    section,
                    float(claim.group("pct")),
                    " ".join(sentence.split())[:400],
                    kind="percentage",
                    subject=level,
                    object=price,
                    expected=f"{expected * 100:+.1f}%",
                )
            )
    return findings


def fix_percentage_claims(text: str, price: float | None) -> tuple[str, int]:
    """Rewrite a wrong "X% above/below price" claim to the measured value.

    Arithmetic, not prose: the level and the price are in the text, so the
    percentage can be corrected in place. Used before any model repair, so a
    repair pass is only asked for the claims that need judgement.
    """
    if not price:
        return text, 0
    fixes: list[tuple[int, int, str]] = []
    claims = 0
    for claim in _PCT_VS_PRICE.finditer(text):
        start = max(text.rfind(". ", 0, claim.start()), text.rfind("\n", 0, claim.start()))
        levels = list(_PCT_LEVEL.finditer(text, start, claim.start()))
        if not levels:
            continue
        level_match = levels[-1]
        try:
            level = float(level_match.group("level").replace(",", ""))
            claimed_pct = claim.group("pct")
            claimed = float(claimed_pct) / 100
        except ValueError:
            continue
        if level <= 0 or level == price:
            continue
        direction = claim.group("direction").lower()
        if direction == "below":
            claimed = -claimed
        expected = level / price - 1
        if abs(expected - claimed) <= max(0.01, abs(expected) * 0.10):
            continue
        decimals = 1 if "." in claimed_pct else 0
        fixes.append((claim.start("pct"), claim.end("pct"),
                      f"{abs(expected) * 100:.{decimals}f}"))
        new_direction = "above" if expected > 0 else "below"
        if new_direction != direction:
            fixes.append((claim.start("direction"), claim.end("direction"), new_direction))
        claims += 1
    if not fixes:
        return text, 0
    for start, end, replacement in sorted(fixes, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, claims


# --- grounding and terminology warnings -------------------------------------
# A warning, not a rejection: the reviewer's sentence may be correct, but a number
# with no evidence-class tag cannot be traced to the current run.

_EVIDENCE_TAG = re.compile(
    r"\[(?:fundamental|valuation|technical|news_sentiment|portfolio_action)\]",
    re.IGNORECASE,
)
_NUMERIC_CLAIM = re.compile(
    r"[¥$€£]\s?\d|"
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:%|x\b|bn\b|tn\b|mn\b|bp\b|bps\b|percent\b)|"
    r"\b\d{1,3}(?:,\d{3})+\b|"
    r"\b\d+\.\d+\b"
)
_INTERESTING_PCT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%")


def _first_number(sentence: str) -> float:
    match = re.search(r"\d[\d,]*(?:\.\d+)?", sentence)
    if not match:
        return 0.0
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return 0.0


def grounding_findings(text: str, section: str = "Adversarial Reviewer") -> list[Finding]:
    """Numerical sentences in the review with no evidence-class tag.

    Only the tag is checked: deciding whether the number itself is right needs the
    whole provenance machinery, and a false finding is worse than a warning. A
    marked figure (``[[229.5]]``) needs no tag: it names the model as its source.
    """
    findings: list[Finding] = []
    seen: set[str] = set()
    for sentence in _sentences(text):
        if _EVIDENCE_TAG.search(sentence) or _MARK.search(sentence):
            continue
        if not _NUMERIC_CLAIM.search(sentence):
            continue
        context = " ".join(sentence.split())[:400]
        if context in seen:
            continue
        seen.add(context)
        findings.append(
            Finding(
                section,
                _first_number(sentence),
                context,
                kind="grounding",
                expected="no evidence-class tag",
            )
        )
    return findings


def terminology_findings(text: str, metrics, section: str, bridge_line_count: int = 0) -> list[Finding]:
    """Sentences that call an operating-profit growth rate an incremental margin."""
    growth = {
        round(row.op_growth * 100, 1)
        for row in metrics
        if row.op_growth is not None
    }
    incremental = {
        round(row.incremental_margin * 100, 1)
        for row in metrics
        if row.incremental_margin is not None
    }
    findings: list[Finding] = []
    seen: set[str] = set()
    for sentence in _sentences(text):
        if not re.search(r"incremental", sentence, re.IGNORECASE):
            continue
        candidates = set(growth)
        # A growth rate printed in the same sentence is the likely source of the
        # mislabel ("operating profit +97% ... a 97% incremental margin").
        for match in re.finditer(
            r"(?:operating profit|op)\b[^.;]{0,60}?"
            r"(?:grew|grow|growing|rose|rise|rising|increased?|increasing|"
            r"jumped|advanced|expanded|surged|up|\+)\s*(?:by\s*)?"
            r"([+-]?\d+(?:\.\d+)?)\s*%",
            sentence,
            re.IGNORECASE,
        ):
            try:
                candidates.add(abs(float(match.group(1))))
            except ValueError:
                continue
        if re.search(r"\b(?:nearly|almost|roughly|about|more than)?\s*doubled\b",
                     sentence, re.IGNORECASE):
            candidates.update({85.0, 90.0, 95.0, 97.0, 100.0})
        if re.search(r"\btripled\b", sentence, re.IGNORECASE):
            candidates.update({180.0, 190.0, 200.0})
        labels = list(re.finditer(r"incremental", sentence, re.IGNORECASE))
        percentages = list(_INTERESTING_PCT.finditer(sentence))
        if not labels or not percentages:
            continue
        for label in labels:
            # Only the percentage the word "incremental" labels: the nearest one,
            # either side ("97% incremental margin", "incremental margin of 39%").
            nearest = min(
                percentages,
                key=lambda pct: min(
                    abs(pct.end() - label.start()), abs(label.end() - pct.start())
                ),
            )
            # A margin named AFTER the label ("incremental operating margin of
            # ~39%") is the figure being labelled. The growth rate printed before
            # it is not: preferring raw proximity flagged 13.1% revenue growth
            # beside a correctly-labelled 39% margin.
            margin_after = re.search(
                r"\bmargin\b", sentence[label.end():label.end() + 60], re.IGNORECASE
            )
            if margin_after:
                margin_pos = label.end() + margin_after.start()
                following = [
                    pct for pct in percentages
                    if label.end() <= pct.start() <= margin_pos + 30
                ]
                if following:
                    nearest = following[0]
            gap = min(abs(nearest.end() - label.start()), abs(label.end() - nearest.start()))
            if gap > 25:
                continue
            phrase = sentence[min(nearest.start(), label.start()):
                              max(nearest.end(), label.end()) + 30]
            if not re.search(r"\b(?:margin|contribution)\b", phrase, re.IGNORECASE):
                continue                       # "incremental capacity", not a margin
            try:
                value = float(nearest.group(1))
            except ValueError:
                continue
            # Only an exact match to a published growth rate counts: a value 1.3
            # points away (41% H1 incremental margin vs 39.7% FY3/28 op growth)
            # is a different quantity, and flagging it made four false findings
            # in the 16:59 report.
            labelled_growth = any(abs(value - candidate) <= 0.5 for candidate in candidates)
            too_high = value >= 90.0
            if too_high and not labelled_growth and _reproduces_incremental(sentence, value):
                if bridge_line_count >= 2:
                    # The ratio is arithmetically right; the attribution is not.
                    # That is a warning, never a failed figure.
                    context = " ".join(sentence.split())[:400]
                    if context not in seen:
                        seen.add(context)
                        findings.append(
                            Finding(
                                section,
                                value,
                                context,
                                kind="attribution",
                                expected=(
                                    "the profit difference cannot be explained by the revenue "
                                    "difference alone; reconcile margin, mix, utilization, "
                                    "pricing, yield, depreciation or ramp costs"
                                ),
                            )
                        )
                continue                       # 13 / 10 = 130%: arithmetically correct
            if not labelled_growth and not too_high:
                continue
            if any(abs(value - candidate) <= 1.5 for candidate in incremental):
                continue                       # it really is the incremental margin
            context = " ".join(sentence.split())[:400]
            if context in seen:
                continue
            seen.add(context)
            findings.append(
                Finding(
                    section,
                    value,
                    context,
                    kind="terminology",
                    expected=(
                        "this is an operating-profit growth rate, not an incremental margin"
                        if labelled_growth
                        else "an incremental margin above 90% is implausible; verify the label"
                    ),
                )
            )
            break
    return findings


def _reproduces_incremental(sentence: str, value: float) -> bool:
    """Whether two figures in the sentence produce ``value`` as their ratio x 100.

    "an implied 130% incremental margin" beside "+10 revenue and +13 operating
    profit" is a correctly labelled margin, however implausible; the >90% rule
    exists to catch a growth rate relabelled as a margin, not this.
    """
    numbers: list[float] = []
    for token in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)", sentence):
        try:
            numbers.append(float(token))
        except ValueError:
            continue
    for numerator in numbers:
        for denominator in numbers:
            if denominator and abs(numerator / denominator * 100 - value) <= 2:
                return True
    return False


def _sentences(text: str):
    """Prose sentences: table rows are column structure, not claims."""
    for line in text.splitlines():
        stripped = line.strip()
        # Tables are column structure; a case blob ({"stance": …}) and code
        # fences are data, not prose claims.
        if stripped.startswith("|") or stripped.startswith("{") or stripped.startswith("```"):
            continue
        # Split unconditionally: guarding against digits after the period made
        # "…the analyst will. 4062.T is below the 10 EMA…" one sentence, and the
        # claim then paired across the boundary. Decimals carry no space after
        # their point, so they are never split.
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if sentence.strip():
                yield sentence.strip()


def _clean(sentence: str, *, keep_labels: bool = False) -> str:
    """Noise-free sentence. Tier 2 keeps fiscal labels — they are its object."""
    text = strip_marks(sentence)
    text = _ENTITY_NUMBER.sub(" ", text)
    text = _CITATION_SPAN.sub(" ", text)
    if not keep_labels:
        text = _FISCAL_LABEL.sub(" ", text)
    text = _TICKER.sub(" ", text)
    text = _DAY_COUNT.sub(" ", text)
    text = _INDICATOR_NAME.sub(" ", text)
    text = _BP.sub(" ", text)
    text = _ISO_DATE.sub(" ", text)
    text = _MM_DD.sub(" ", text)
    text = _YEAR.sub(" ", text)
    text = _PAGE.sub(" ", text)
    return text


def _operands(text: str) -> list[tuple[int, int, float, str]]:
    """(start, end, value, unit) — unit: pct | mult | bn | tn | mn | plain.

    The end includes the unit suffix: "25bn above" welds the number to the
    claim, and adjacency tests must see the suffix as part of the figure.
    """
    found = []
    for match in _OPERAND.finditer(text):
        suffix = (match.group(2) or "").lower()
        suffix = {"percent": "%", "billion": "bn", "trillion": "tn", "million": "mn",
                  "x": "mult"}.get(suffix, suffix) or "plain"
        try:
            value = float(match.group(1).replace(",", "").replace("−", "-"))
        except ValueError:
            continue
        found.append((match.start(1), match.end(), value, suffix))
    return found


def _predicate(text: str):
    """The first directional claim in the sentence: (span, direction, lemma)."""
    hits = []
    for match in _GREATER.finditer(text):
        hits.append((match.start(), match.group(1).lower(), +1))
    for match in _LESSER.finditer(text):
        hits.append((match.start(), match.group(1).lower(), -1))
    if not hits:
        return None
    start, lemma, direction = min(hits, key=lambda hit: hit[0])
    end = start + len(lemma)
    return (start, end), direction, lemma


def _pair(first: tuple, second: tuple) -> tuple[float, float] | None:
    """Comparable values for two operands, or None when units do not line up."""
    _, _, v1, u1 = first
    _, _, v2, u2 = second
    if u1 == u2:
        if u1 in _FACTOR:
            return v1 * _FACTOR[u1], v2 * _FACTOR[u1]
        return v1, v2
    if "plain" in (u1, u2):
        return None                      # a bare number's unit is unknowable here
    if u1 in _FACTOR and u2 in _FACTOR:
        return v1 * _FACTOR[u1], v2 * _FACTOR[u2]
    return None                          # pct vs absolute, mult vs absolute …


def _violates(direction: int, first: float, second: float, hedged: bool) -> bool:
    if abs(first - second) <= 1e-9:
        return False                     # equal figures make no comparative claim
    margin = 0.10 if hedged else 0.02
    if direction > 0:
        return first < second * (1 - margin)
    return first > second * (1 + margin)


def _slash_between(text: str, operands: list[tuple], operand: tuple) -> bool:
    """Whether ``operand`` is the other half of a slash-joined pair.

    "revenue/operating profit of 680.0/190.0 sits above 650.0/165.0" compares
    two ordered pairs, not 190 with 650. A '/' between neighbours is the marker.
    """
    for left, right in zip(operands, operands[1:], strict=False):
        if (operand is left or operand is right) and re.fullmatch(
            r"\s*/\s*", text[left[1]:right[0]]
        ):
            return True
    return False


def _tier_one(cleaned: str) -> tuple[float, float] | None:
    if _RANGE.search(cleaned):
        return None                      # "the 6,915–8,644 band above 8,000": which end?
    claim = _predicate(cleaned)
    if claim is None:
        return None
    (start, end), direction, lemma = claim
    if re.search(r"\bno(?:t)?\s+$", cleaned[:start]):
        return None                      # "no more than 0.5%" is a cap, not a comparison
    operands = _operands(cleaned)
    before = [operand for operand in operands if operand[1] <= start]
    after = [operand for operand in operands if operand[0] >= end]

    if before:
        subject = before[-1]
        # A clause boundary between the figure and the claim means the claim
        # opened a new clause with an implied subject of its own: "…¥350bn next
        # year and more than ¥400bn thereafter" does not compare 350 with 400,
        # and "half-cut at 7,779; full exit below 7,400" does not compare the
        # two levels. Welded = only whitespace or markdown emphasis between the
        # figure and the claim: a welded figure on a prepositional predicate
        # ("§25bn above guidance", "2% above base revenue") reads as a delta,
        # not a subject; for a verbal claim it is a delta when an earlier
        # same-unit figure sits behind it.
        gap = cleaned[subject[1]:start]
        # A participle opens a new clause about a trajectory ("350bn ... rising
        # above 400bn thereafter"), so the figure before it is not the subject of
        # the comparison. Finite forms ("rose above 100") stay comparable.
        if re.search(
            r"[,;:()\[\]]|\b(?:and|or|but|yet|while|whereas|then|plus|"
            r"ris(?:e|es|ing)|fall(?:s|ing)|grow(?:s|ing)|climb(?:s|ing)|"
            r"declin(?:e|es|ing)|increas(?:e|es|ing)|decreas(?:e|es|ing))\b",
            gap,
            re.IGNORECASE,
        ):
            return None
        welded = not gap.strip(" \t*_—–`#")
        if welded and lemma not in _APPOSITIVE:
            return None
        if welded and any(o[3] == subject[3] for o in before[:-1]):
            return None
        if not after:
            return None
        object_ = after[0]
    else:
        if lemma not in _APPOSITIVE or len(after) < 2:
            return None                  # prepositional object with no numeric subject
        subject, object_ = after[0], after[1]
    # Two ordered pairs joined by "/" ("revenue/operating profit of 680.0/190.0
    # sits above 650.0/165.0") compare tuple positions, not the two inner
    # numbers: pairing 190 with 650 reported a false contradiction in every run.
    if _slash_between(cleaned, operands, subject) or _slash_between(cleaned, operands, object_):
        return None
    pair = _pair(subject, object_)
    if pair is None:
        return None
    # The object must belong to the claim's own clause: "…in under a month, and
    # now sits only 4.9% above…" does not pair the month with 4.9%. A colon
    # introducing the operands (the appositive pattern) is not a boundary.
    if re.search(r"[,–—]|\b(?:and|or|but|yet|while|whereas)\b",
                 cleaned[end:object_[0]], re.IGNORECASE):
        return None
    # A hedge widens the tolerance only when it is inside the claim itself —
    # an "about" a paragraph later must not excuse this comparison.
    hedged = bool(_HEDGE.search(cleaned[subject[0]:object_[1]]))
    if _violates(direction, pair[0], pair[1], hedged):
        return pair
    return None


def _tier_two(cleaned: str,
              table: dict[str, dict[str, float | None]], unit: str) -> tuple[float, float] | None:
    if not table:
        return None
    claim = _predicate(cleaned)
    if claim is None:
        return None
    (start, end), direction, lemma = claim

    subject = None
    metric = None
    best_end = -1
    for name, keywords in _METRIC_KEYWORDS:
        for match in keywords.finditer(cleaned[:start]):
            if start - match.end() > 80:
                continue                 # too far from the claim to be its subject
            attached = [
                operand
                for operand in _operands(cleaned)
                if match.end() <= operand[0] < start and operand[0] - match.end() <= 30
            ]
            if attached and match.end() > best_end:
                # The metric nearest the predicate is the claim's subject; taking
                # the first keyword in a fixed order paired "net profit ~118" with
                # a period introduced by a later "capex above …" clause.
                best_end = match.end()
                subject, metric = attached[0], name
    if subject is None:
        return None                      # no figure is tied to a metric before the claim
    # The subject must be in the claim's own clause. "net profit ~118 + D&A ~90 -
    # capex ~350), capex above JPY400bn in FY3/29" pairs net profit with FY3/29
    # across a comma; the claim there is about capex, which is not a model metric.
    if re.search(r"[,;:()\[\]]|\b(?:and|but|or)\b",
                 cleaned[subject[1]:start], re.IGNORECASE):
        return None

    tail = cleaned[end:]
    labels = sorted(table, key=len, reverse=True)
    found = None
    for label in labels:
        match = re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", tail)
        if match and (found is None or match.start() < found[1]):
            found = (label, match.start())
    if found is None:
        return None
    label, label_at = found
    if label_at - subject[1] > 120:
        return None                      # the period label belongs to a later clause
    if any(operand[0] < label_at for operand in _operands(tail)):
        return None                      # the object was printed — Tier 1 had its chance
    for name, keywords in _METRIC_KEYWORDS:
        if name != metric and keywords.search(tail[:label_at]):
            return None                  # a different metric sits between: ambiguous

    model_value = table[label].get(metric)
    if model_value is None:
        return None
    value, operand_unit = subject[2], subject[3]
    if operand_unit == "%":
        if metric != "operating_margin":
            return None
        first, second = value, model_value
    elif operand_unit == "mult":
        return None
    elif operand_unit in _FACTOR:
        suffix = str(unit).rsplit("_", 1)[-1].lower()
        second = model_value * _UNIT_SUFFIX.get(suffix, 1.0)
        first = value * _FACTOR[operand_unit]
    else:
        first, second = value, model_value   # bare figure: read in the model's unit
    if _violates(direction, first, second, bool(_HEDGE.search(cleaned))):
        return first, second
    return None


def relational_findings(text: str, section: str,
                        table: dict[str, dict[str, float | None]] | None = None,
                        unit: str = "") -> list[Finding]:
    """Comparative claims in one section that the figures beside them contradict."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for sentence in _sentences(text):
        cleaned = _clean(sentence)
        pair = _tier_one(cleaned)
        if pair is None and table is not None:
            pair = _tier_two(_clean(sentence, keep_labels=True), table, unit)
        if pair is None:
            continue
        key = " ".join(sentence.split())[:400]
        if key in seen:
            continue
        seen.add(key)
        first, second = pair
        expected = (
            f"{_fmt_number(first)} is {'below' if first < second else 'above'} "
            f"{_fmt_number(second)}"
        )
        findings.append(
            Finding(section, first, key, kind="relation",
                    subject=first, object=second, expected=expected)
        )
    return findings


def repair_relations(final_state: dict, findings: list[Finding], ask,
                     *, model: ModelResult) -> list[str]:
    """Fix contradicted comparisons, and percentages the arithmetic contradicts.

    Percentages are corrected in place (the level and the price are in the
    text); only the claims that need judgement go to the model. An answer is
    accepted only when the same checks pass it, so a repair that still fails
    leaves the original text and the finding stands. Returns the sections
    rewritten.
    """
    table = _period_table(model)
    unit = model.spec.unit
    price = model.spec.valuation.price
    relation_claims: dict[str, list[str]] = {}
    percentage_claims: dict[str, list[str]] = {}
    for finding in findings:
        if finding.kind == "relation":
            relation_claims.setdefault(finding.section, []).append(finding.context)
        elif finding.kind == "percentage":
            percentage_claims.setdefault(finding.section, []).append(finding.context)

    changed: list[str] = []
    for section, path in _PROSE_SECTIONS:
        text = _state_get(final_state, path)
        if not isinstance(text, str):
            continue
        if percentage_claims.get(section):
            fixed_text, count = fix_percentage_claims(text, price)
            if count:
                _state_set(final_state, path, fixed_text)
                text = fixed_text
                changed.append(section)
        claims = relation_claims.get(section)
        if not claims:
            continue
        prompt = (
            "You proofread one section of an investment report. A checker found comparisons "
            "that contradict the figures printed beside them: each quoted claim says one "
            "number exceeds or falls below another when the two figures show the opposite "
            "(for example, 'H1 net profit of 38.6bn already exceeds all of FY3/26' when the "
            "full year was 63.7).\n\n"
            "For EACH quoted claim, correct ONLY that sentence: restate the true relationship "
            "using the figures themselves (38.6bn is about 61% of 63.7bn), or delete the "
            "claim if it cannot be corrected from the figures at hand. Leave every other "
            "sentence, number, table, heading and citation byte-identical. Keep the section's "
            "language. Return the full corrected section and nothing else — no preamble, no "
            "code fence.\n\n"
            "Claims to fix:\n" + "\n".join(f"- {claim}" for claim in claims)
            + "\n\nSECTION:\n" + str(text)
        )
        try:
            answer = str(ask(prompt)).strip()
        except Exception:  # noqa: BLE001 - a failed repair must not block publication
            continue
        if answer.startswith("```"):     # a fence is a wrapper, not the section
            answer = answer.strip("`").lstrip("markdown").strip()
        if not answer or answer == str(text):
            continue
        if len(answer) < len(str(text)) // 2:
            continue                     # a stub, not the section: never swap prose for less
        if relational_findings(answer, section, table, unit):
            continue                     # still self-contradictory: keep the original
        if percentage_findings(answer, section, model.spec.valuation.price):
            continue                     # the percentage still does not match the price
        _state_set(final_state, path, answer)
        if section not in changed:
            changed.append(section)
    return changed


def audit_state(
    final_state: dict,
    model: ModelResult,
    valuation: Valuation,
    *,
    stats: dict | None = None,
    superseded: set[float] | None = None,
) -> list[Finding]:
    """Audit the marked figures in the sections a run writes for a reader.

    Two different checks run here. The figure findings verify marks against the
    model (provenance-based, see the module docstring). The relation findings
    verify comparative claims — "38.6 already exceeds 63.7" — against the
    operands the same sentence prints or an explicit model period label with a
    stated metric. The relational check never asks where a number came from, so
    it does not reopen the provenance problem the mark split exists to avoid.
    """
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
                audit_text(text, section, known, band=band, stats=stats, superseded=superseded)
            )

    table = _period_table(model)
    for section, path in _PROSE_SECTIONS:
        text = _state_get(final_state, path)
        if isinstance(text, str) and text.strip():
            findings.extend(relational_findings(text, section, table, model.spec.unit))
            findings.extend(percentage_findings(text, section, valuation.price))

    review = _state_get(final_state, ("investment_debate_state", "first_review"))
    if isinstance(review, str) and review.strip():
        findings.extend(grounding_findings(review))

    from .metrics import period_metrics

    metrics = period_metrics(model)
    bridge_lines = max(
        (len(bridge.lines) for bridge in model.spec.bridges
         if bridge.metric == "operating_profit"),
        default=0,
    )
    if metrics:
        for section, path in _PROSE_SECTIONS:
            text = _state_get(final_state, path)
            if isinstance(text, str) and text.strip():
                findings.extend(
                    terminology_findings(text, metrics, section, bridge_lines)
                )
    return findings


def render(findings: list[Finding], stats: dict | None = None) -> str:
    """The audit file's text, including what was actually checked."""
    figures = [
        finding
        for finding in findings
        if finding.kind not in {"relation", "percentage", "grounding", "terminology",
                                "attribution", "computed"}
    ]
    relations = [finding for finding in findings if finding.kind == "relation"]
    percentages = [finding for finding in findings if finding.kind == "percentage"]
    grounding = [finding for finding in findings if finding.kind == "grounding"]
    terminology = [finding for finding in findings if finding.kind == "terminology"]
    attribution = [finding for finding in findings if finding.kind == "attribution"]
    computed = [finding for finding in findings if finding.kind == "computed"]

    blocks: list[str] = []
    if stats is not None and not stats.get("marked"):
        if not relations:
            blocks.append(
                "No figure was marked as the model's in this run, so nothing was checked. "
                "The agents mark a figure they take from the verified model in double square brackets; "
                "an unmarked report cannot be audited."
            )
        else:
            blocks.append(
                "No figure was marked as the model's in this run, so no marked figure was checked. "
                "Comparative claims were still checked against the figures beside them."
            )
    else:
        marked = int((stats or {}).get("marked", 0))
        sections = (stats or {}).get("sections") or {}
        references = int((stats or {}).get("references", 0))
        skipped = int((stats or {}).get("skipped", 0))
        checked = int((stats or {}).get("checked", marked - references - skipped))
        failed = len(figures)
        summary = _by_section(sections)
        counters = [
            f"- Marked: {marked}",
            f"- Independently checked: {checked}",
            f"- Reference-only: {references} (plain reference numbers, a bare single digit "
            "such as 6, are counted, not checked)",
            f"- Skipped: {skipped} (a label or identifier, not a figure)",
            f"- Failed: {failed}",
        ]
        superseded_count = int((stats or {}).get("superseded", 0))
        if superseded_count:
            # Four statuses; valid-unaffected only exists when a v1 field
            # disappears from v2, so it is normally zero.
            counters.append(
                f"- By status: current {max(checked - superseded_count, 0)}, "
                f"superseded {superseded_count}, valid-unaffected 0, failed {failed}"
            )

        if not figures:
            blocks.append(
                "\n".join(
                    [
                        f"All {checked} checked figure(s) were found in the verified model.",
                        "",
                        *counters,
                        "",
                        summary,
                    ]
                ).strip()
            )
        else:
            lines = [
                f"{failed} marked figure(s) do not match the verified model. "
                "Each is either a misquote or an error.",
                "",
                "| Section | Figure | Where it appears |",
                "|---|---|---|",
            ]
            for finding in figures[:30]:
                context = finding.context.replace("|", "\\|")
                lines.append(f"| {finding.section} | {_fmt_number(finding.number)} | {context} |")
            if len(figures) > 30:
                lines.append(f"| … | | {len(figures) - 30} more |")
            lines += ["", *counters, "", summary]
            blocks.append("\n".join(lines))

    superseded_values = sorted({float(v) for v in (stats or {}).get("superseded_values", [])})
    if superseded_values:
        blocks.append(
            "Superseded by the model repair (v2): these figures were correct in the model "
            "the Fundamentals Analyst read and are no longer current. They are not audit "
            "failures, and no later section may use them.\n\n"
            + "\n".join(f"- {_fmt_number(value)}" for value in superseded_values)
        )

    if relations:
        lines = [
            f"{len(relations)} comparative claim(s) contradict the figures printed beside them. "            "Each says one number exceeds or falls below another when the figures show the "
            "opposite.",
            "",
            "| Section | The claim | Figures | What the figures say |",
            "|---|---|---|---|",
        ]
        for finding in relations[:30]:
            claim = finding.context.replace("|", chr(92) + "|")
            figures = (
                f"{_fmt_number(finding.subject)} vs {_fmt_number(finding.object)}"
                if finding.subject is not None and finding.object is not None else "—"
            )
            lines.append(
                f"| {finding.section} | {claim} | {figures} | "
                f"{finding.expected.replace('|', chr(92) + '|')} |"
            )
        if len(relations) > 30:
            lines.append(f"| … | | | {len(relations) - 30} more |")
        blocks.append("\n".join(lines))

    if percentages:
        lines = [
            f"{len(percentages)} percentage claim(s) do not match the price they compare with. "
            "Each states how far a level sits above or below the run price; the level and the "
            "price are printed in the same sentence.",
            "",
            "| Section | The claim | Level vs price | Recomputed |",
            "|---|---|---|---|",
        ]
        for finding in percentages[:30]:
            claim = finding.context.replace("|", chr(92) + "|")
            figures_text = (
                f"{_fmt_number(finding.subject)} vs {_fmt_number(finding.object)}"
                if finding.subject is not None and finding.object is not None else "—"
            )
            lines.append(
                f"| {finding.section} | {claim} | {figures_text} | {finding.expected} |"
            )
        if len(percentages) > 30:
            lines.append(f"| … | | | {len(percentages) - 30} more |")
        blocks.append("\n".join(lines))

    if terminology:
        lines = [
            f"{len(terminology)} sentence(s) call an operating-profit growth rate an "
            "incremental margin. Growth is current / prior - 1; incremental margin is "
            "(change in operating profit) / (change in revenue).",
            "",
            "| Section | The claim | Figure | What it is |",
            "|---|---|---|---|",
        ]
        for finding in terminology[:20]:
            lines.append(
                f"| {finding.section} | {finding.context.replace('|', chr(92) + '|')} | "
                f"{_fmt_number(finding.number)}% | {finding.expected} |"
            )
        blocks.append("\n".join(lines))

    if computed:
        lines = [
            f"{len(computed)} computed figure(s) fail their own arithmetic. The formula "
            "printed with the mark does not reproduce the stated value.",
            "",
            "| Section | Figure | Formula |",
            "|---|---|---|",
        ]
        for finding in computed[:20]:
            lines.append(
                f"| {finding.section} | {_fmt_number(finding.number)} | "
                f"{finding.context.replace('|', chr(92) + '|')} |"
            )
        blocks.append("\n".join(lines))

    if attribution:
        lines = [
            f"{len(attribution)} incremental-margin claim(s) use a correct ratio whose "
            "attribution is incomplete. The profit difference cannot be explained by the "
            "revenue difference alone; reconcile margin, mix, utilization, pricing, yield, "
            "depreciation or ramp costs. This is a warning, not a failed figure.",
            "",
            "| Section | Figure | The claim |",
            "|---|---|---|",
        ]
        for finding in attribution[:20]:
            lines.append(
                f"| {finding.section} | {_fmt_number(finding.number)}% | "
                f"{finding.context.replace('|', chr(92) + '|')} |"
            )
        blocks.append("\n".join(lines))

    if grounding:
        lines = [
            f"{len(grounding)} numerical sentence(s) in the adversarial review carry no "
            "evidence-class tag ([fundamental], [valuation], [technical], [news_sentiment]). "
            "This is a warning, not a rejection: the number may still be correct, but its "
            "source is not stated.",
            "",
            "| Section | The sentence |",
            "|---|---|",
        ]
        for finding in grounding[:20]:
            lines.append(
                f"| {finding.section} | {finding.context.replace('|', chr(92) + '|')} |"
            )
        if len(grounding) > 20:
            lines.append(f"| … | {len(grounding) - 20} more |")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _by_section(sections: dict) -> str:
    if not sections:
        return ""
    parts = [f"{name} {count}" for name, count in sections.items()]
    return "Figures checked by section: " + ", ".join(parts) + "."
