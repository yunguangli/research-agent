"""Exact position sizes need a portfolio; without one they are not instructions.

A percentage of "a standard allocation" is an invented portfolio decision unless the
weight, cost basis, benchmark, mandate and loss budget are known. The agents are told
this in their prompts, but a free-text fallback ignores it, so the text is gated here:
one rewrite pass, accepted only if no exact size remains.
"""

from __future__ import annotations

import re

# An exact size is a number attached to a position instruction, not any number
# near the word "position": "Position size | ATR 10.2% of price" is a volatility
# note, and reading it as a size degraded every run whose table named the row.
_EXACT = re.compile(
    # "reduce by 2%", "trim 10%", "add 1.5%", "allocate 5%"
    r"\b(?:trim|cut|reduce|add|size|allocate|invest|buy|sell|shift|deploy)\w*\b"
    r"\s+(?:by|to|at|about|around|roughly|approximately)?\s*"
    r"\d{1,3}(?:\.\d+)?(?:\s*[-–]\s*\d{1,3}(?:\.\d+)?)?\s?%"
    # "Reduce 4062.T to about 50%": a named instrument between the verb and the size
    r"|\b(?:trim|cut|reduce|add|size|allocate)\w*\b.{0,40}?\b(?:to|by|at)\s+"
    r"(?:about|around|roughly|approximately)?\s*\d{1,3}(?:\.\d+)?\s?%"
    # "a 2% position", "5% of the portfolio", "10% weight"
    r"|\b\d{1,3}(?:\.\d+)?(?:\s*[-–]\s*\d{1,3}(?:\.\d+)?)?\s?%\s*"
    r"(?:of\s+)?(?:the\s+)?(?:allocation|sleeve|position|weight|book|target|portfolio|fund|nav)\b"
    r"|\b(?:position|weight|allocation|exposure|sleeve)\s*(?:of|:)?\s*"
    r"\d{1,3}(?:\.\d+)?\s?%"
    # "reduce by 50bp"
    r"|\b(?:trim|cut|reduce|add|shift|size)\w*\b[^.\d]{0,20}\d{1,4}\s?(?:bp|bps)\b"
    # "buy 100 shares", "sell 1,000 shares"
    r"|\b\d[\d,]*\s+shares?\b"
    # "invest ¥1 million", "commit $500,000"
    r"|\b(?:invest|commit|deploy|allocate)\b[^.]{0,25}(?:[¥$€£]\s?\d|\d[\d,]*\s?(?:million|mn|bn|thousand)\b)"
    # "sell one-third", "trim two-thirds"
    r"|\b(?:sell|trim|cut|reduce|exit|close)\s+(?:one-third|two-thirds|half|a third)\b"
    # "half of a full/normal/standard position"
    r"|\b(?:half|one-third|two-thirds|a third)\s+of\s+(?:a\s+)?"
    r"(?:full|normal|standard|neutral|approved|current|existing)\b"
    # "two tranches"
    r"|\b(?:two|three|four|five)\s+tranches?\b",
    re.IGNORECASE,
)

# "1.0x standard neutral allocation", "scale to 1.5x", "cut to 0.5x". A bare
# multiple is a valuation ratio ("39.8x EPS"), so it only counts when a sizing
# verb introduces it or it is "of a normal/standard position".
_MULTIPLE_SIZE = re.compile(
    r"\b(?:scale|size|sizing|trim|cut|reduce|add|allocate)\w*\b[^.]{0,30}?"
    r"\b\d{1,3}(?:\.\d+)?x\b"
    r"|\b\d{1,3}(?:\.\d+)?x\s*(?:of\s+)?(?:the\s+)?"
    r"(?:normal|standard|neutral|full|current|existing)\b",
    re.IGNORECASE,
)
_NEUTRAL = (
    "Exposure guidance is qualitative: no exact position size can be determined "
    "without portfolio weight, cost basis, benchmark or mandate."
)
_LABEL = re.compile(r"^(\s*(?:[-*]\s+)?(?:\*\*[^*]+\*\*:\s*)?)(.*)$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_LEVEL_LINE = re.compile(
    r"^(\s*)\*\*(Entry Price|Stop Loss|Reassessment Level)\*\*:\s*(.*)$"
)


def exact_size_match(text: str) -> str | None:
    """The phrase that was read as a position size, or None.

    Kept beside the detector so the report can say *what* triggered the sizing
    control instead of asserting that a size exists.
    """
    if not text:
        return None
    match = _EXACT.search(text) or _MULTIPLE_SIZE.search(text)
    return " ".join(match.group(0).split())[:160] if match else None


def has_exact_size(text: str) -> bool:
    """Whether the text gives a position size a caller could act on."""
    return exact_size_match(text) is not None


def _scrub_line(line: str) -> str:
    match = _LABEL.match(line)
    prefix, content = (match.group(1), match.group(2)) if match else ("", line)
    if re.match(r"^\s*[-*]\s+", prefix):
        return prefix + _NEUTRAL          # a bullet is one idea
    sentences = [part for part in _SENTENCE_SPLIT.split(content.strip()) if part]
    kept = [part for part in sentences if not has_exact_size(part)]
    return prefix + (" ".join(kept).strip() if kept else _NEUTRAL)


def scrub_sizing(text: str) -> str:
    """Remove the sentences that state a size, keeping the line and its label.

    Line-scoped replacement deleted a whole Executive Summary because one sentence
    in it carried a size; this keeps the label and every sentence that is not a size.
    """
    if not text:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|") or not has_exact_size(line):
            kept.append(line)
            continue
        fixed = _scrub_line(line)
        if not kept or kept[-1].strip() != fixed.strip():
            kept.append(fixed)
    return "\n".join(kept)


def scrub_execution_levels(text: str) -> str:
    """Replace an exact entry/stop/reassessment level with a qualitative line."""
    if not text:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        match = _LEVEL_LINE.match(line)
        if match:
            kept.append(
                f"{match.group(1)}**{match.group(2)}**: not provided — valuation "
                "unverified and no portfolio context"
            )
        else:
            kept.append(line)
    return "\n".join(kept)


def downgrade_buy(text: str) -> tuple[str, bool]:
    """Downgrade a Buy to Hold when the valuation cannot be verified.

    A Buy with no verified valuation and no levels is a conviction claim the run
    cannot support; the PM still makes the final call.
    """
    if not re.search(r"(?m)^\*\*Action\*\*:\s*Buy\b", text or ""):
        return text, False
    changed = re.sub(r"(?m)^\*\*Action\*\*:\s*Buy\b", "**Action**: Hold", text, count=1)
    changed += (
        "\n\n**Action note**: the original Buy was downgraded to Hold because the "
        "valuation is unverified and no portfolio context was supplied."
    )
    return changed, True


def gate_sizing(text: str, ask, agent_name: str) -> tuple[str, bool]:
    """Rewrite exact sizes as qualitative guidance; return ``(text, changed)``.

    ``ask`` is a callable taking a prompt and returning text. One pass only: if the
    answer still contains a size, or is a stub, the original stands and the decision
    is marked degraded by the caller.
    """
    if not text or not has_exact_size(text):
        return text, False
    prompt = (
        f"You are editing the {agent_name}'s output for an investment report. It states "
        "an exact position size, but no portfolio weight, cost basis, benchmark, mandate "
        "or loss budget was supplied, so the size is not actionable.\n\n"
        "Rewrite ONLY the sentences that give an exact size — a percentage, a share "
        "count, a tranche count, or a fraction of a full/normal/standard position — as "
        "qualitative guidance (for example, 'reduce below a normal full position, sized "
        "to the mandate'). Keep every other sentence, number, table and heading "
        "byte-identical. Do not add a size, a stop or a hedge. Return the full corrected "
        "text and nothing else.\n\nTEXT:\n" + text
    )
    try:
        answer = str(ask(prompt)).strip()
    except Exception:  # noqa: BLE001 - a failed rewrite must not block the run
        return text, False
    if not answer or has_exact_size(answer) or len(answer) < len(text) // 2:
        return text, False
    return answer, True
