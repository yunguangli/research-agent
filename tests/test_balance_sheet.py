"""Table-aware balance-sheet extraction: labels, columns, unit, page."""

from __future__ import annotations

from tradingagents.research import balance_sheet

ENGLISH = """(Millions of yen)
Current Prior Change
Cash and deposits 420,487 295,681 124,805
Long-term loans receivable 7 7 (0)
Current portion of bonds 10,000 15,000 (5,000)
Bonds payable 35,000 45,000 (10,000)
Long-term loans payable 61,000 60,000 1,000
Lease obligations 50 61 (11)
Non-controlling interests 7,550 7,339 210
Repayment of lease obligations (158) (855) 697
"""

JAPANESE = """(百万円)
当期 前期 増減
現金及び預金 420,487 295,681 124,805
1年内償還予定の社債 10,000 15,000 (5,000)
社債 35,000 45,000 (10,000)
長期借入金 61,000 60,000 1,000
リース債務 50 61 (11)
"""


def test_english_rows_map_to_components_with_columns_and_unit():
    rows = balance_sheet.extract(ENGLISH, "doc-bs")
    by_component = {row.component: row for row in rows}

    assert by_component[balance_sheet.CASH].values == {
        "current": 420487.0, "prior": 295681.0, "change": 124805.0,
    }
    assert by_component[balance_sheet.CASH].unit == "JPY_m"
    assert by_component[balance_sheet.BONDS].values["current"] == 35000.0
    assert by_component[balance_sheet.CURRENT_DEBT].values["current"] == 10000.0
    assert by_component[balance_sheet.LONG_LOANS].values["current"] == 61000.0
    assert by_component[balance_sheet.LEASES].values["current"] == 50.0


def test_a_receivable_and_a_repayment_are_not_balances():
    rows = balance_sheet.extract(ENGLISH, "doc-bs")
    labels = " ".join(row.label for row in rows).lower()

    assert "receivable" not in labels
    assert "repayment" not in labels


def test_japanese_aliases_extract_cash_and_every_debt_component():
    rows = balance_sheet.extract(JAPANESE, "doc-jp")
    found = {row.component for row in rows}

    assert balance_sheet.CASH in found
    assert balance_sheet.CURRENT_DEBT in found
    assert balance_sheet.BONDS in found
    assert balance_sheet.LONG_LOANS in found
    assert balance_sheet.LEASES in found
    assert all(row.unit == "JPY_m" for row in rows)


def test_component_totals_use_the_current_column():
    totals = balance_sheet.components(balance_sheet.extract(ENGLISH, "doc-bs"))

    assert totals[balance_sheet.CASH] == 420487.0
    assert totals[balance_sheet.BONDS] == 35000.0
    assert totals[balance_sheet.CURRENT_DEBT] == 10000.0
    assert totals[balance_sheet.LONG_LOANS] == 61000.0
    assert totals[balance_sheet.LEASES] == 50.0


def test_the_prompt_block_names_the_source_unit_and_label():
    block = balance_sheet.render(balance_sheet.extract(ENGLISH, "doc-bs"))

    assert "doc-bs" in block
    assert "JPY_m" in block
    assert "long-term loans receivable" not in block.lower()
    assert "cash_and_equivalents: current 420,487" in block


def test_long_term_borrowings_is_mapped_like_long_term_loans():
    rows = balance_sheet.extract("Long-term borrowings 61,000 60,000 1,000", "doc")
    assert rows[0].component == balance_sheet.LONG_LOANS


def test_derived_gross_and_net_debt_sum_itemised_components_only():
    derived = balance_sheet.derive(balance_sheet.extract(ENGLISH, "doc-bs"))

    assert derived["gross_debt"] == 106000.0        # 10,000 + 35,000 + 61,000
    assert derived["cash"] == 420487.0
    assert derived["net_debt"] == -314487.0        # net cash
    assert "sum of itemised components" in derived["note"]


def test_a_stated_total_is_used_only_when_nothing_is_itemised():
    text = "Interest-bearing debt 162,000\nCash and deposits 420,487"
    derived = balance_sheet.derive(balance_sheet.extract(text, "doc"))

    assert derived["gross_debt"] == 162000.0
    assert derived["net_debt"] == -258487.0
    assert "stated total" in derived["note"]


def test_a_stated_total_that_disagrees_with_components_is_reported():
    text = (
        "Current portion of bonds 10,000\nBonds payable 35,000\n"
        "Interest-bearing debt 50,000\nCash and deposits 420,487"
    )
    derived = balance_sheet.derive(balance_sheet.extract(text, "doc"))

    assert derived["gross_debt"] == 45000.0        # components win
    assert "disagrees" in derived["note"]


def test_a_narrative_line_without_a_balance_is_ignored():
    rows = balance_sheet.extract(
        "The company has no interest-bearing debt and ample cash.", "doc"
    )

    assert rows == []
