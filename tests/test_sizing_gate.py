"""Exact position sizes need a portfolio; without one the text is rewritten."""

from __future__ import annotations

from tradingagents.agents.sizing import gate_sizing, has_exact_size


def test_a_size_is_detected_in_its_context():
    assert has_exact_size("Trim to 50-60% of a standard allocation.")
    assert has_exact_size("Reduce 4062.T to about 50% of its normal single-name target.")
    assert has_exact_size("Cut about one-third of a full position.")
    assert has_exact_size("Sell 500 shares.")


def test_valuation_percentages_are_not_sizes():
    assert not has_exact_size("The stock trades at 35.9x with a 0.25% yield.")
    assert not has_exact_size("Capital adequacy is 53.5% and net cash 258bn.")
    assert not has_exact_size("Operating margin of 23.5% is below the 28.6% model.")


def test_the_gate_rewrites_until_no_size_remains():
    text = "Trim to 5% of the portfolio."
    out, changed = gate_sizing(
        text, lambda prompt: "Trim below a normal full position, sized to the mandate.", "Trader"
    )
    assert changed
    assert not has_exact_size(out)


def test_a_rewrite_that_keeps_a_size_is_rejected():
    text = "Trim to 5% of the portfolio."
    out, changed = gate_sizing(text, lambda prompt: "Trim to 3% of the portfolio.", "Trader")
    assert not changed
    assert out == text


def test_a_failed_rewrite_is_rejected():
    text = "Trim to 5% of the portfolio."

    def boom(prompt):
        raise RuntimeError("provider down")

    out, changed = gate_sizing(text, boom, "Trader")
    assert not changed
    assert out == text


def test_allocation_multiples_are_sizes():
    assert has_exact_size("Keep 1.0x standard neutral allocation.")
    assert has_exact_size("Scale to 1.5x after the print.")
    assert not has_exact_size("The stock trades at 35.9x FY3/28 EPS.")


def test_scrub_keeps_a_labelled_summary_and_drops_only_the_size_sentence():
    from tradingagents.agents.sizing import scrub_sizing

    text = ("**Executive Summary**: Trim to 5% of the portfolio. Valuation is full, "
            "so wait for confirmation.")
    out = scrub_sizing(text)
    assert out.startswith("**Executive Summary**:")
    assert "Trim to 5%" not in out
    assert "Valuation is full" in out


def test_scrub_removes_a_size_line_the_rewrite_kept():
    from tradingagents.agents.sizing import scrub_sizing

    text = "Hold.\n- Keep 4062.T at 1.0x standard neutral allocation.\nDo not add."
    out = scrub_sizing(text)
    assert "1.0x" not in out
    assert "qualitative" in out
    assert "Do not add." in out
