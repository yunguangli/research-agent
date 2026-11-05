"""Why an EV/EBITDA input is missing: extraction, availability, or reconciliation.

Three failures look identical in the package ("net debt unavailable") and need
different fixes:

- ``PRESENT_NOT_EXTRACTED`` - the corpus states cash and debt, the builder did
  not record them. The probe finds the numbered line independently of the LLM.
- ``ABSENT_IN_CORPUS`` - the documents genuinely do not disclose the input.
- ``EXTRACTED_NOT_RECONCILED`` - the builder recorded a value, but its date,
  unit or scope cannot be reconciled with the model.

The probe is deliberately conservative: a line counts only when a label and a
number share one source line, and the matched line is kept as evidence. A
narrative "no debt" with no number is not a disclosure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import balance_sheet

EXTRACTED = "EXTRACTED"
ABSENT = "ABSENT_IN_CORPUS"
NOT_EXTRACTED = "PRESENT_NOT_EXTRACTED_BY_BUILDER"
NOT_RECONCILED = "EXTRACTED_NOT_RECONCILED"

_CASH = re.compile(
    r"cash and (?:cash )?equivalents|cash and deposits|現金及び預金|現金預金", re.IGNORECASE
)
_DEBT = re.compile(
    r"interest[- ]bearing debt|borrowings|bonds payable|convertible bonds?"
    r"|long[- ]term loans?|short[- ]term loans?|有利子負債|社債|借入金",
    re.IGNORECASE,
)
_DEPRECIATION = re.compile(r"depreciation|減価償却", re.IGNORECASE)
_LEASE = re.compile(r"lease liabilities?|リース債務", re.IGNORECASE)
_MINORITY = re.compile(r"non[- ]controlling interests?|minority interests?|非支配株主持分", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

KNOWN_UNITS = {
    "JPY", "JPY_BN", "JPY_M", "JPY_MM", "USD", "USD_BN", "USD_M", "USD_MM",
    "EUR", "EUR_BN", "EUR_M", "GBP", "GBP_BN", "GBP_M",
}


@dataclass
class Hit:
    document_id: str
    line: str


@dataclass
class Probe:
    """What the documents state, independent of the builder."""

    documents: int = 0
    cash: list[Hit] = field(default_factory=list)
    debt: list[Hit] = field(default_factory=list)
    depreciation: list[Hit] = field(default_factory=list)
    leases: list[Hit] = field(default_factory=list)
    minorities: list[Hit] = field(default_factory=list)

    def empty(self) -> bool:
        return not any(
            (self.cash, self.debt, self.depreciation, self.leases, self.minorities)
        )


def _has_number(line: str) -> bool:
    for match in _NUMBER.finditer(line):
        try:
            value = float(match.group(0).replace(",", ""))
        except ValueError:
            continue
        # A bare year is a date, not a balance.
        if not (1900 <= value <= 2100 and "." not in match.group(0)):
            return True
    return False


def probe_lines(document_id: str, text: str) -> dict[str, list[Hit]]:
    """Numbered disclosure lines in one document, by input."""
    found: dict[str, list[Hit]] = {
        "cash": [], "debt": [], "depreciation": [], "leases": [], "minorities": [],
    }
    for row in balance_sheet.extract(text, document_id):
        hit = Hit(document_id, row.line)
        if row.component == balance_sheet.CASH:
            found["cash"].append(hit)
        elif row.component in balance_sheet.DEBT_COMPONENTS:
            found["debt"].append(hit)
        elif row.component == balance_sheet.LEASES:
            found["leases"].append(hit)
        elif row.component == balance_sheet.MINORITY:
            found["minorities"].append(hit)
    for raw in (text or "").splitlines():
        line = raw.strip()
        if len(line) < 4 or not _has_number(line):
            continue
        if _DEPRECIATION.search(line):
            found["depreciation"].append(Hit(document_id, line[:240]))
    return found


def scan(store, as_of: str | None = None) -> Probe:
    """Scan the in-window corpus for balance-sheet and depreciation lines."""
    probe = Probe()
    try:
        records = store.list_documents(as_of)
    except Exception:  # noqa: BLE001 - a probe must never break a run
        return probe
    probe.documents = len(records)
    for record in records:
        try:
            text = store.text_of(record.document_id).text
        except Exception:  # noqa: BLE001
            continue
        found = probe_lines(record.document_id, text)
        for key in ("cash", "debt", "depreciation", "leases", "minorities"):
            getattr(probe, key).extend(found[key])
    return probe


def _unit_family(unit: str) -> str:
    return str(unit or "").strip().upper().split("_")[0]


def _unit_ok(source_unit: str, model_unit: str) -> str:
    source = str(source_unit or "").strip().upper()
    if not source:
        return "the source unit is not stated"
    normalised = source.replace(" ", "_")
    if normalised not in KNOWN_UNITS and _unit_family(normalised) not in {
        "JPY", "USD", "EUR", "GBP"
    }:
        return f"the source unit {source_unit!r} is not a known currency unit"
    if _unit_family(normalised) != _unit_family(model_unit):
        return (
            f"the source unit {source_unit!r} is not in the model's currency "
            f"({model_unit})"
        )
    return ""


def _state(value, present: list[Hit], **checks) -> dict:
    if value is not None:
        for reason in checks.values():
            if reason:
                return {"status": NOT_RECONCILED, "reason": reason}
        return {"status": EXTRACTED, "reason": ""}
    if present:
        return {
            "status": NOT_EXTRACTED,
            "reason": (
                f"the corpus states it ({present[0].document_id}: "
                f"{present[0].line}), but the model recorded none"
            ),
        }
    return {"status": ABSENT, "reason": "no numbered disclosure was found in the corpus"}


def diagnose(
    spec,
    *,
    valuation_period: str,
    trade_date: str = "",
    probe: Probe | None = None,
    multiple_is_assumption: bool = True,
) -> dict:
    """Per-input readiness plus the one-line reason EV/EBITDA is unavailable."""
    probe = probe or Probe()
    debt = spec.net_debt
    gross = debt.gross_debt if debt is not None else None
    cash = debt.cash_and_equivalents if debt is not None else None
    flow = spec.cashflow.get(valuation_period)
    depreciation = flow.depreciation if flow is not None else None
    multiple = spec.valuation.ev_ebitda_multiple

    date_reason = ""
    if debt is not None and (gross is not None or cash is not None):
        if not str(debt.as_of or "").strip():
            date_reason = "the balance-sheet date is not stated"
        elif trade_date and str(debt.as_of) > str(trade_date):
            date_reason = f"the balance sheet is dated {debt.as_of}, after the trade date"
        elif not str(debt.scope or "").strip():
            date_reason = "the consolidation scope is not stated"
        elif str(debt.scope).lower() not in {"consolidated", "parent"}:
            date_reason = f"the scope {debt.scope!r} is neither consolidated nor parent"
        elif str(debt.scope).lower() == "parent":
            date_reason = "the figures are parent-company, not consolidated"

    unit_reason = ""
    if debt is not None and (gross is not None or cash is not None):
        unit_reason = _unit_ok(debt.source_unit, spec.unit)

    treatment_reason = ""
    if debt is not None and (gross is not None or cash is not None):
        if probe.leases and str(debt.lease_treatment or "").lower() not in {
            "included", "excluded"
        }:
            treatment_reason = "the corpus has lease liabilities but the treatment is unstated"
        if probe.minorities and str(debt.minority_treatment or "").lower() not in {
            "included", "excluded"
        }:
            treatment_reason = (
                "the corpus has minority interests but the treatment is unstated"
            )

    internal_reason = ""
    if (
        debt is not None
        and debt.reported_net_debt is not None
        and debt.value is not None
        and abs(debt.reported_net_debt - debt.value) > max(0.5, abs(debt.value) * 0.01)
    ):
        internal_reason = (
            "the stated net debt does not reconcile with its own components"
        )

    common = {
        "date": date_reason,
        "unit": unit_reason,
        "treatment": treatment_reason,
        "internal": internal_reason,
    }
    inputs = {
        "gross_debt": _state(gross, probe.debt, **common),
        "cash": _state(cash, probe.cash, **common),
        "depreciation": _state(depreciation, probe.depreciation),
        "ev_multiple": {
            "status": EXTRACTED if multiple is not None else ABSENT,
            "reason": (
                "the analyst assumption is recorded"
                if multiple is not None
                else "the model selected no EV/EBITDA multiple"
            ),
        },
    }
    missing = [name for name, item in inputs.items() if item["status"] != EXTRACTED]
    return {
        "inputs": inputs,
        "missing": missing,
        "status": EXTRACTED if not missing else "UNAVAILABLE",
        "documents_scanned": probe.documents,
        "multiple_is_assumption": bool(multiple_is_assumption and multiple is not None),
    }


def render(diagnostics: dict) -> str:
    """One sentence naming the exact dependency and which failure class it is."""
    if diagnostics.get("status") == EXTRACTED:
        line = "EV/EBITDA inputs: cash, gross debt, EBITDA and the multiple all verified"
        if diagnostics.get("multiple_is_assumption"):
            line += " (the multiple is an analyst assumption)"
        return line + "."
    labels = {
        NOT_EXTRACTED: "corpus extraction failure",
        ABSENT: "data unavailable",
        NOT_RECONCILED: "model insufficiency",
    }
    parts = []
    for name in diagnostics.get("missing") or []:
        entry = diagnostics["inputs"][name]
        parts.append(
            f"{name}: {labels.get(entry['status'], entry['status'])} - {entry['reason']}"
        )
    return "EV/EBITDA unavailable. " + "; ".join(parts) + "."
