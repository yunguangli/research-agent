import hashlib
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import tradingagents
from tradingagents.agents.context import build_instrument_context, resolve_instrument_identity
from tradingagents.agents.rating import canonical_decision, display_decision, run_rating
from tradingagents.agents.sizing import has_exact_size
from tradingagents.dataflows.config import run_config, run_config_context, set_config
from tradingagents.dataflows.date_window import get_current_date, is_historical
from tradingagents.dataflows.symbols import canonical_ticker, safe_ticker_component
from tradingagents.dataflows.vendors.research_corpus import (
    research_dir_from_env,
    resolve_corpus_root,
)
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients import create_tier_client, tier_provider
from tradingagents.memory import TradingMemoryLog, settlement
from tradingagents.memory.reflection import Reflector
from tradingagents.reporting import debt_provisional_reason, write_report_tree

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .propagation import Propagator
from .setup import GraphSetup

logger = logging.getLogger(__name__)


class _ModelUnavailable(RuntimeError):
    """The research model cannot be used for this run; the text is the reason."""


def _feature_enabled(name: str) -> bool:
    """A feature flag read from the environment, never from config.

    Keeping flags out of `config` leaves `_run_signature`, checkpoints and the
    saved-report header unchanged whether a flag is on or off.
    """
    value = os.environ.get(f"TRADINGAGENTS_FEATURE_{name}", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def price_conflict_reason(model_price, market_close) -> str:
    """A dated model price and the market's close, when they disagree by >1%.

    The model price is an input, not a vendor quote: comparing it with the
    MarketSnapshot is a different check from comparing two vendors, and only the
    snapshot carries the session. Either value missing is no conflict.
    """
    try:
        model = float(model_price)
        close = float(market_close)
    except (TypeError, ValueError):
        return ""
    if not model or not close:
        return ""
    if abs(model - close) / abs(close) <= 0.01:
        return ""
    return (
        f"model price {model:,.0f} vs market close {close:,.0f} "
        f"({model / close - 1:+.1%}); the PM must choose the controlling price"
    )


def state_price_resolution(state: dict):
    """The recorded price resolution, for a later step that must not re-pick it."""
    from tradingagents.research import price_authority

    return price_authority.PriceResolution.from_state(state)


def apply_price_conflict(final_state: dict) -> None:
    """Degrade the decision and make it provisional; never stop the run."""
    conflict = str(final_state.get("price_conflict") or "").strip()
    if not conflict:
        return
    integrity = str(final_state.get("decision_integrity") or "validated")
    if integrity == "validated":
        final_state["decision_integrity"] = f"DEGRADED: {conflict}"
    elif conflict not in integrity:
        final_state["decision_integrity"] = f"{integrity}; {conflict}"
    if final_state.get("decision_status") == "FINAL":
        final_state["decision_status"] = "PROVISIONAL"


def build_component_status(statuses: dict, *, propagation_ok: bool,
                           diagnostics: dict | None = None,
                           controlling: dict | None = None,
                           failed_rows: int = 0) -> dict:
    """One status object built only from a validated component assessment."""
    diagnostics = diagnostics or {}
    controlling = controlling or {
        "status": statuses.get("controlling_sensitivity_status", ""),
        "verified": 0,
        "expected": 0,
    }
    return {
        "operating_forecast": {"status": statuses["operating_model_status"]},
        "forecast_eps": {"status": statuses["eps_model_status"]},
        "pe_valuation": {
            "status": statuses["valuation_method_statuses"].get("pe", ""),
            "role": "controlling",
        },
        "ev_ebitda_method": {
            "status": statuses["valuation_method_statuses"].get("ev_ebitda", ""),
        },
        "ev_per_share_comparison": {
            "status": statuses.get("ev_per_share_status", ""),
            "reason": diagnostics.get("cross_method_basis", ""),
        },
        "controlling_valuation": {"use": "pe only", "ev_controlling": False},
        "sensitivity": {
            "status": statuses["sensitivity_model_status"],
            "failed_rows": failed_rows,
            "propagation_ok": propagation_ok,
        },
        "controlling_sensitivity": controlling,
        "decision_use": {"failed_rows_excluded": True},
        "technical": {"status": "ADVISORY"},
    }


def repair_component_status(pre_status, post_status: dict, repair_id: str,
                            validated_at: str) -> dict:
    """Replace, never merge: the post-repair object is the only live one.

    ``pre_repair_status`` is kept for the audit trail only; header, appendix and
    the PM read the top-level post-repair components.
    """
    return {
        **post_status,
        "pre_repair_status": pre_status if isinstance(pre_status, dict) else {},
        "post_repair_status": post_status,
        "repair_id": repair_id,
        "validated_at": validated_at,
    }


def repaired_status_object(pre_status, post_status: dict, repair_id: str,
                           validated_at: str) -> dict:
    """The post-repair object, or the existing one when this repair already ran."""
    if isinstance(pre_status, dict) and pre_status.get("repair_id") == repair_id:
        return pre_status
    return repair_component_status(pre_status, post_status, repair_id, validated_at)


def provisional_reason(final_state: dict) -> str:
    """Why a decision is provisional, for the header and the shared status."""
    if str(final_state.get("decision_status") or "") != "PROVISIONAL":
        return ""
    if str(final_state.get("run_purpose") or "production").lower() != "production":
        return "diagnostic run purpose"
    if str(final_state.get("eps_basis_quality") or "") == "conservative_full_conversion":
        return "conservative full-conversion share basis"
    if str(final_state.get("action_status") or "") in {"advisory", "invalidated"}:
        return "decision action is advisory"
    if _audit_failed(final_state):
        return "decision audit failed"
    if str(final_state.get("model_status") or "") == "UNVERIFIED":
        return "deterministic model unverified"
    return "derived status"


def _audit_failed(final_state: dict) -> bool:
    """Whether the PM's decision-basis audit recorded a failure."""
    raw = final_state.get("decision_audit_result")
    if not raw:
        return False
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return False
    if not isinstance(data, dict) or not data:
        return False
    return not bool(data.get("validation_passed", True))


def _configured_spec(company_name: str):
    """The ``TRADINGAGENTS_RESEARCH_MODEL`` spec — only if it is this company's.

    Returns ``(path, spec)`` or ``None``. Another company's spec is never a
    model candidate and never a consensus source: its numbers belong to that
    company, whatever the environment says, and a leftover pointer from an
    earlier test must not colour the next run.
    """
    configured = os.environ.get("TRADINGAGENTS_RESEARCH_MODEL", "").strip()
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file():
        return None
    from tradingagents.research import report as research_report

    try:
        spec = research_report.load_spec(path)
    except Exception:  # noqa: BLE001 - an unreadable pointer is simply not used
        return None
    if canonical_ticker(str(spec.ticker or "")) != canonical_ticker(str(company_name)):
        logger.info(
            "Ignoring TRADINGAGENTS_RESEARCH_MODEL=%s: it is %r, not %r",
            configured, spec.ticker, company_name,
        )
        return None
    return path, spec


def _validate_trade_date(trade_date) -> str:
    """The run date as a canonical ``YYYY-MM-DD`` string no later than today."""
    value = str(trade_date)
    try:
        canonical = datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        canonical = False
    if not canonical:
        raise ValueError(f"trade_date must be a date in YYYY-MM-DD format, got {trade_date!r}")
    if value > get_current_date():
        raise ValueError(f"trade_date cannot be in the future: {value}")
    return value


# Config keys that do not change what a run writes: where it keeps its files,
# whether it checkpoints, and how often it retries a provider.
_NOT_IN_SIGNATURE = frozenset({
    "results_dir", "data_cache_dir", "memory_log_path", "checkpoint_enabled", "llm_max_retries",
})


def _unresolved_per_share_fields(final_state: dict) -> list[str]:
    """Fields of material challenges that were not applied, for the EPS gate."""
    try:
        data = json.loads(str(final_state.get("model_challenges") or "{}"))
    except ValueError:
        return []
    fields: list[str] = []
    for challenge in data.get("challenges") or []:
        if str(challenge.get("severity")) == "material" and not (challenge.get("proposed_values") or {}):
            fields.extend(challenge.get("affected_fields") or [])
    for issue in data.get("deterministic_issues") or []:
        if issue.get("kind") == "share_basis" and issue.get("severity") == "material":
            fields.extend(issue.get("fields") or [])
    return fields


def superseded_values(final_state: dict) -> set[float]:
    """The v1 values a model repair replaced; no later section may use them."""
    from tradingagents.research.superseded import values_from_log

    return values_from_log(final_state)


def _cites_superseded(text: str, values: set[float]) -> bool:
    """Whether a prose section reuses a figure the repair replaced."""
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            number = float(token.replace(",", ""))
        except ValueError:
            continue
        if any(abs(number - value) <= max(0.005, abs(value) * 0.002) for value in values):
            return True
    return False


_SEGMENT_LIMITATION_MARKERS = (
    "\n\nSEGMENT_RECONCILIATION_STATUS = ",
    "\n\nSEGMENT_EVIDENCE_SCHEMA_MISSING. ",
)


def _invoke_with_timeout(llm, prompt: str, timeout: float):
    """One model call with a hard ceiling, so a stalled provider cannot hang the run.

    The worker is a daemon thread: a provider that never answers is abandoned, the
    audit repair falls back to the original text, and the report still saves.
    """
    result: dict = {}

    def run() -> None:
        try:
            result["value"] = llm.invoke(prompt)
        except Exception as exc:  # noqa: BLE001 - re-raised in the caller
            result["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"the model did not answer within {timeout:.0f}s")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def controlling_valuation_resolution(spec) -> tuple[str, str]:
    """``(controlling method, conflict reason)`` from the spec.

    The primary source is ``valuation.controlling_valuation_method``; a method's
    own ``controlling`` flag is a consistency check. A disagreement is a
    configuration contradiction, never silently resolved.
    """
    valuation = getattr(spec, "valuation", None)
    method = str(
        getattr(valuation, "controlling_valuation_method", "") or "pe"
    ).strip().lower()
    if method not in {"pe", "ev_ebitda"}:
        return method or "pe", (
            f"controlling_valuation_method {method!r} is not pe or ev_ebitda"
        )
    methods = getattr(valuation, "valuation_methods", {}) or {}
    flags = {
        str(name).strip().lower(): entry
        for name, entry in methods.items()
        if isinstance(entry, dict)
    }
    own = flags.get(method)
    if isinstance(own, dict) and "controlling" in own and not own.get("controlling"):
        return method, f"{method}.controlling is false but {method} is the controlling method"
    others = sorted(
        name for name, entry in flags.items()
        if name != method and bool(entry.get("controlling"))
    )
    if others:
        return method, (
            f"{', '.join(others)}.controlling is true while {method} controls"
        )
    return method, ""


def without_existing_segment_limitation(text: str) -> str:
    """Drop a previously appended segment limitation so re-entry cannot stack it.

    The reviewer's gate can run more than once for a run. The limitation is the
    last block appended to the package, so the first marker cuts the old copy and
    the current audit appends exactly one fresh one.
    """
    cut = len(text or "")
    for marker in _SEGMENT_LIMITATION_MARKERS:
        index = (text or "").find(marker)
        if index >= 0:
            cut = min(cut, index)
    return (text or "")[:cut].rstrip()


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=("market", "social", "news", "fundamentals"),
        debug=False,
        config: dict[str, Any] = None,
        callbacks: list | None = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        set_config(self.config)

        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        extra = {"callbacks": self.callbacks} if self.callbacks else {}
        self.deep_thinking_llm = create_tier_client(self.config, "deep", **extra).get_llm()
        self.quick_thinking_llm = create_tier_client(self.config, "quick", **extra).get_llm()

        self.memory_log = TradingMemoryLog(self.config)

        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        # An analyst takes two graph steps per tool round, plus its first turn
        # and its wrap-up; a limit past the recursion limit would end the run
        # there instead.
        max_tool_rounds, max_recur_limit = self.config["max_tool_rounds"], self.config["max_recur_limit"]
        if 2 * max_tool_rounds + 2 >= max_recur_limit:
            raise ValueError(
                f"max_tool_rounds={max_tool_rounds} needs max_recur_limit above {2 * max_tool_rounds + 2}"
            )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.conditional_logic,
            max_tool_rounds,
        )

        self.propagator = Propagator(
            max_recur_limit=max_recur_limit,
        )
        self.reflector = Reflector(self.quick_thinking_llm)

        # Graph-shape-affecting run choices, kept for the checkpoint signature.
        self.selected_analysts = tuple(selected_analysts)

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.workflow = self.graph_setup.setup_graph(
            selected_analysts, memory_node=self._memory_step, repair_hook=self.run_repair_gate
        )
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None
        self._resuming = False
        # Provenance of the run's model spec, set when create_run_state loads it.
        self._spec_source = ""
        self._spec_created_at = ""
        self._spec_compatibility = ""
        self._spec_compatibility_reason = ""
        self._basis_names: dict[str, str] = {}
        self._spec_sector = ""
        self._sensitivity_items: list[dict] = []
        self._sensitivity_alias_map: dict[str, str] = {}
        self._sensitivity_gaps: list[str] = []
        self._sensitivity_table = ""
        self._component_status: dict = {}

    def resolve_instrument_context(self, ticker: str, asset_type: str = "stock",
                                   trade_date: str | None = None) -> str:
        """Resolve ticker identity once and return the full instrument context.

        Deterministic yfinance lookup (cached, fail-open) injected into a
        context string so every agent anchors to the real company instead of
        hallucinating one from the price chart (#814). Both the propagate()
        path and the CLI call this so the resolved identity reaches the whole
        graph regardless of entry point.
        """
        identity = resolve_instrument_identity(ticker)
        return build_instrument_context(ticker, asset_type, identity, trade_date)

    def _memory_as_of(self, trade_date) -> str | None:
        """Point-in-time cutoff for past-context lessons (#1251).

        A historical/backtest run (trade date before today) filters lessons to
        those already resolved by the trade date. A current-date run returns
        None, disabling the filter so live behavior and pre-migration entries
        (which have no stored resolution date) are unaffected.
        """
        return str(trade_date) if is_historical(trade_date) else None

    def _run_signature(self, asset_type: str, portfolio=None) -> str:
        """Run inputs that must invalidate a checkpoint if changed.

        Keyed into the checkpoint thread ID so a resume under a different analyst
        selection, debate/risk depth, or asset mode starts fresh instead of
        silently continuing the previous graph (#1089). The rest of the config
        counts too (provider, models, endpoint, language, vendors, limits): a
        resume must not carry reports that other settings produced. Only where
        the run keeps its files and how it retries are left out.
        """
        settings = {k: v for k, v in self.config.items() if k not in _NOT_IN_SIGNATURE}
        digest = hashlib.sha256(json.dumps(settings, sort_keys=True, default=str).encode()).hexdigest()[:12]
        parts = [
            "analysts=" + ",".join(self.selected_analysts),
            f"debate={self.config['max_debate_rounds']}",
            f"risk={self.config['max_risk_discuss_rounds']}",
            f"asset={asset_type}",
            # None, an empty book and a changed book are three different runs.
            f"portfolio={portfolio.fingerprint() if portfolio is not None else 'none'}",
            # The layout itself: a checkpoint saved when analysts ran one after
            # another has pending nodes this graph no longer has, and one saved
            # before the Memory Log step would resume without the lessons.
            "analysts=parallel",
            "memory=parallel",
            f"settings={digest}",
        ]
        # A different corpus is different evidence: resuming across one would mix
        # reports built from documents the run never saw. Vanilla adds nothing,
        # so its signature (and any existing checkpoint) is unchanged.
        research_root = research_dir_from_env()
        if research_root is not None:
            parts.append(f"research={research_root}")
        return "|".join(parts)

    def propagate(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """Run the trading agents graph for a company on a specific date.

        ``asset_type`` selects between the stock pipeline (default) and the
        crypto pipeline (``"crypto"``) shipped in #567 — the CLI auto-detects
        from the ticker; programmatic callers pass it explicitly. When
        ``checkpoint_enabled`` is set in config, the graph is recompiled with
        a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.

        Returns ``(final_state, signal)`` where ``signal`` is one of the 5-tier
        ratings (Buy / Overweight / Hold / Underweight / Sell) or ``"REVIEW"``
        when the decision had no parseable rating (#1170); guard with
        ``tradingagents.agents.rating.is_review`` before mapping it to the
        PortfolioRating enum.
        """
        # Accept a Bloomberg-style ticker (3711.TT) as readily as the system's
        # own (3711.TW): identity, corpus, benchmark and memory all key on this
        # one canonical spelling from here down.
        company_name = canonical_ticker(company_name)
        trade_date = _validate_trade_date(trade_date)

        with run_config(self.config), \
                self.checkpoint_scope(company_name, trade_date, asset_type, portfolio) as thread_id_value:
            return self._run_graph(
                company_name, trade_date, asset_type=asset_type,
                checkpoint_thread_id=thread_id_value, portfolio=portfolio,
            )

    def begin_checkpoint(self, company_name, trade_date, asset_type: str = "stock", portfolio=None) -> str | None:
        """Recompile the graph with a per-ticker checkpointer and return the
        ``thread_id`` to inject into the stream/invoke ``config`` (or ``None``
        when checkpointing is disabled).

        Pair every call with :meth:`end_checkpoint` in a ``finally``. Both
        ``propagate`` (via :meth:`checkpoint_scope`) and the CLI stream path use
        this so ``--checkpoint`` actually resumes (#1249); previously the setup
        lived only inside ``propagate`` and the CLI streamed the checkpointer-less
        graph, making the flag a no-op.
        """
        company_name = canonical_ticker(company_name)
        self._resuming = False
        if not self.config.get("checkpoint_enabled"):
            return None
        signature = self._run_signature(asset_type, portfolio)
        self._checkpointer_ctx = get_checkpointer(self.config["data_cache_dir"], company_name)
        saver = self._checkpointer_ctx.__enter__()
        self.graph = self.workflow.compile(checkpointer=saver)

        step = checkpoint_step(
            self.config["data_cache_dir"], company_name, str(trade_date), signature
        )
        self._resuming = step is not None
        if step is not None:
            logger.info("Resuming from step %d for %s on %s", step, company_name, trade_date)
        else:
            logger.info("Starting fresh for %s on %s", company_name, trade_date)
        return thread_id(company_name, str(trade_date), signature)

    def checkpoint_input(self, init_state):
        """The value to stream/invoke: ``None`` to resume an existing checkpoint,
        else the initial state for a fresh run.

        LangGraph resumes an interrupted thread when invoked with ``None``;
        re-passing the initial state instead appends it through the message
        reducer, duplicating messages in the resumed state (#1249).
        """
        return None if self._resuming else init_state

    def end_checkpoint(self):
        """Restore the plain uncheckpointed graph after a checkpointed run."""
        if self._checkpointer_ctx is not None:
            self._checkpointer_ctx.__exit__(None, None, None)
            self._checkpointer_ctx = None
            self.graph = self.workflow.compile()
        self._resuming = False

    @contextmanager
    def checkpoint_scope(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """Context-manager form of begin/end_checkpoint for the propagate path."""
        try:
            yield self.begin_checkpoint(company_name, trade_date, asset_type, portfolio)
        finally:
            self.end_checkpoint()

    def clear_checkpoint_on_success(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """Drop a completed run's checkpoint so a later run starts fresh (#1249)."""
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date),
                self._run_signature(asset_type, portfolio),
            )

    def run_settings(self) -> dict:
        """What produces this graph's runs, for the saved report and state log.

        An allowlist: endpoints (a backend_url can carry credentials), keys and
        local paths are never recorded.
        """
        cfg = self.config
        return {
            "version": tradingagents.__version__,
            "llm_provider": cfg.get("llm_provider"),
            "deep_think_provider": tier_provider(cfg, "deep") if cfg.get("llm_provider") else None,
            "deep_think_llm": cfg.get("deep_think_llm"),
            "quick_think_provider": tier_provider(cfg, "quick") if cfg.get("llm_provider") else None,
            "quick_think_llm": cfg.get("quick_think_llm"),
            "analysts": list(self.selected_analysts),
            "max_debate_rounds": cfg.get("max_debate_rounds"),
            "max_risk_discuss_rounds": cfg.get("max_risk_discuss_rounds"),
            "decision_horizon": cfg.get("decision_horizon"),
            "run_purpose": cfg.get("run_purpose"),
            "holding_period_days": cfg.get("holding_period_days"),
            "benchmark_ticker": cfg.get("benchmark_ticker"),
            "price_override": os.environ.get("TRADINGAGENTS_MODEL_PRICE") or None,
            "output_language": cfg.get("output_language"),
            "data_vendors": dict(cfg.get("data_vendors") or {}),
            "tool_vendors": dict(cfg.get("tool_vendors") or {}),
        }

    def prepare_final_state(self, final_state, ticker) -> dict:
        """Audit the prose, repair contradicted claims, and compute the case table.

        Runs before anything is recorded or rendered, so the report, the state log
        and the memory entry all carry the same text. Idempotent: a state that
        already has an audit or a case table is left alone, so a second save does
        not repair twice.
        """
        if research_dir_from_env() is None:
            # No corpus, no model: the audit is absent by design, not because a
            # figure passed. Say so, so a missing 1c_audit cannot read as clean.
            final_state["report_audit"] = (
                "The number audit did not run: no research corpus is configured, so this "
                "run built no deterministic model and there was no model figure to check. "
                "Set TRADINGAGENTS_RESEARCH_DIR to enable it."
            )
        elif final_state.get("research_model_source") == "unavailable":
            # The run used no model. Do not reopen an older spec to "audit" against
            # figures the report never quoted.
            final_state["report_audit"] = (
                "The number audit did not run: no research model was used in this run, "
                "so no figure could be checked against one."
            )
        elif research_dir_from_env() is not None and not final_state.get("report_audit"):
            audit = self.report_audit(final_state, ticker)
            if audit:
                final_state["report_audit"] = audit
                if audit.startswith("The number audit did not run") and (
                    final_state.get("research_model_source") not in {"built", "reused"}
                ):
                    # The header must carry the same verdict as the audit section.
                    # A model that was loaded and used keeps its provenance even if
                    # the later audit could not run: the report still shows its figures.
                    final_state["research_model_source"] = "unavailable"
                    final_state["spec_compatibility"] = "unavailable"
                    final_state["spec_compatibility_reason"] = (
                        "no usable research model (see 1c_audit/numbers.md)"
                    )
        if not final_state.get("debate_cases"):
            # A state saved before the case list was stored, or one whose list was
            # dropped, can rebuild it from the review transcript: the reviewer
            # appends the JSON objects, and parse_cases keeps the last per stance.
            history = str(
                (final_state.get("investment_debate_state") or {}).get("challenge_history") or ""
            )
            if history:
                from tradingagents.research.cases import parse_cases

                found = parse_cases(history)
                if found:
                    final_state["debate_cases"] = json.dumps([case.to_dict() for case in found])
        if (
            final_state.get("debate_cases")
            and not final_state.get("case_table")
            and final_state.get("model_status") != "UNVERIFIED"
            and final_state.get("research_model_source") != "unavailable"
            and final_state.get("research_fair_value_verified", True)
        ):
            try:
                model, valuation, _spec = self._research_model(
                    ticker,
                    final_state.get("trade_date"),
                    resolution=state_price_resolution(final_state),
                    allow_build=False,
                    spec_hint=final_state.get("research_model_path") or None,
                )
                from tradingagents.research import cases as case_module

                final_state["case_table"] = case_module.render_results(
                    model, valuation, json.loads(final_state["debate_cases"])
                )
            except Exception as exc:  # noqa: BLE001 - a report must still be written
                logger.warning("Case comparison could not be computed: %s", exc)
        if not final_state.get("case_table"):
            if not final_state.get("research_fair_value_verified", True):
                final_state["case_table"] = (
                    "No case table was produced: the per-share basis is unverified, so the "
                    "cases would price an indicative EPS."
                )
            else:
                debate_now = final_state.get("investment_debate_state") or {}
                if debate_now.get("first_review"):
                    if final_state.get("research_model_source") == "unavailable":
                        note = "No case table was produced: this run had no verified model."
                    else:
                        note = (
                            "No case table was produced: the adversarial review did not return "
                            "the two case objects. The debate text above is unquantified."
                        )
                    final_state["case_table"] = note
        if not final_state.get("metrics_table") and (
            final_state.get("research_model_path")
            and final_state.get("research_model_source") != "unavailable"
        ):
            # Growth and incremental margin are different numbers and the debate
            # has confused them; publish both for every consecutive model year.
            try:
                model, _valuation, _spec = self._research_model(
                    ticker,
                    final_state.get("trade_date"),
                    resolution=state_price_resolution(final_state),
                    allow_build=False,
                    spec_hint=final_state.get("research_model_path") or None,
                )
                from tradingagents.research.metrics import period_metrics, render_metrics

                metrics = period_metrics(model)
                if metrics:
                    final_state["metrics_table"] = render_metrics(metrics)
            except Exception as exc:  # noqa: BLE001 - a report must still be written
                logger.info("Metrics table could not be computed: %s", exc)
        debt_provisional = debt_provisional_reason(final_state)
        if debt_provisional:
            try:
                diagnostics = json.loads(str(final_state.get("ev_input_diagnostics") or "{}"))
            except ValueError:
                diagnostics = {}
            if isinstance(diagnostics, dict) and diagnostics.get("inputs"):
                diagnostics["provisional"] = debt_provisional
                final_state["ev_input_diagnostics"] = json.dumps(diagnostics)
        self._apply_decision_integrity(final_state)
        # A superseded conclusion that survives verbatim in the decision is
        # replaced by its repair text; anything the repair could not match is
        # already covered by the superseded-number check above.
        from tradingagents.research import repair as repair_module

        pairs = repair_module.conclusion_pairs(
            str(final_state.get("model_repair_addendum") or "")
        )
        decision_text = str(final_state.get("final_trade_decision") or "")
        if pairs and decision_text:
            replaced = 0
            for old, new in pairs:
                if old and old in decision_text:
                    decision_text = decision_text.replace(old, new)
                    replaced += 1
            if replaced:
                final_state["final_trade_decision"] = decision_text
                final_state["replacement_conclusions_applied"] = str(replaced)
        # One status for the report: a model that was loaded and audited is
        # VERIFIED; a corpus run whose model was rejected or failed repair is
        # UNVERIFIED; no corpus at all is NOT_APPLICABLE.
        source = str(final_state.get("research_model_source") or "")
        if source == "unavailable":
            final_state["model_status"] = (
                "UNVERIFIED" if research_dir_from_env() is not None else "NOT_APPLICABLE"
            )
        elif final_state.get("research_model_path"):
            final_state["model_status"] = "VERIFIED"
        else:
            final_state["model_status"] = "NOT_APPLICABLE"
        if final_state["model_status"] == "VERIFIED":
            from tradingagents.research import status as status_module

            try:
                sensitivity_ids = json.loads(str(final_state.get("sensitivity_ids") or "[]"))
            except ValueError:
                sensitivity_ids = []
            # The statuses set at run start (and updated by the repair gate) know
            # the balance-sheet and tax inputs; recompute only for an old
            # checkpoint that predates them.
            if not final_state.get("operating_model_status"):
                statuses = status_module.assess(
                    model_status="VERIFIED",
                    basis={
                        key: str(final_state.get(key) or "")
                        for key in ("market_cap_basis", "forecast_eps_basis",
                                    "dilution_sensitivity_basis")
                    },
                    has_band=final_state.get("research_fair_value_low") is not None,
                    challenge_fields=_unresolved_per_share_fields(final_state),
                    sensitivity_count=len(sensitivity_ids),
                    price=final_state.get("research_price") or None,
                    share_price_verified=state_price_resolution(final_state).verified,
                )
                for key in ("operating_model_status", "eps_model_status", "eps_status",
                            "valuation_status", "sensitivity_model_status",
                            "sensitivity_status"):
                    final_state[key] = statuses[key]
                final_state["calculation_status"] = final_state.get("valuation_status", "")
                final_state["valuation_method_statuses"] = json.dumps(
                    statuses["valuation_method_statuses"]
                )
                final_state["research_fair_value_verified"] = statuses["valuation_controlling"]
            final_state["overall_status"] = (
                "" if final_state.get("model_status") == "VERIFIED"
                else "Not Rated"
            )
            if (
                final_state.get("valuation_status") != status_module.VERIFIED
                or final_state.get("decision_status") == "PROVISIONAL"
                or final_state.get("action_status") in {"advisory", "invalidated"}
                or _audit_failed(final_state)
                or (
                    str(final_state.get("eps_basis_quality") or "")
                    == "conservative_full_conversion"
                )
                or str(final_state.get("run_purpose") or "production").lower() != "production"
            ):
                final_state["decision_status"] = "PROVISIONAL"
            else:
                final_state["decision_status"] = "FINAL"
        elif final_state["model_status"] == "UNVERIFIED":
            final_state["overall_status"] = "Not Rated"
            final_state["decision_status"] = "NOT_RATED"
        else:   # no deterministic model in this run at all
            final_state["operating_model_status"] = "NOT_APPLICABLE"
            final_state["eps_model_status"] = ""
            final_state["eps_status"] = ""
            final_state["valuation_status"] = ""
            final_state["sensitivity_model_status"] = ""
            final_state["sensitivity_status"] = ""
            final_state["research_fair_value_verified"] = False
            final_state["overall_status"] = ""
            final_state["decision_status"] = ""
        conflicts = self._detect_conflicts(final_state)
        final_state["decision_conflicts"] = conflicts
        verification_json, verification_status = self._derive_verification(final_state)
        final_state["verification_checks"] = verification_json
        final_state["verification_status"] = verification_status
        final_state["stage_status"] = self._stage_status(final_state)
        final_state["controlling_decision"] = (
            "portfolio_manager"
            if str(final_state.get("final_trade_decision") or "").strip()
            else ""
        )
        decision = str(final_state.get("final_trade_decision") or "")
        if conflicts and "**Superseded intermediate decisions**" not in decision:
            final_state["final_trade_decision"] = decision + (
                "\n\n**Superseded intermediate decisions**: " + "; ".join(conflicts) +
                ". The Portfolio Manager's decision is controlling; the intermediate "
                "proposals are in the appendix."
            )
        debate = final_state.get("investment_debate_state") or {}
        first = str(debate.get("first_review") or "").strip()
        if first:
            from tradingagents.research.cases import strip_case_json

            parts = ["### Round 1", strip_case_json(first).strip()]
            latest = str(debate.get("last_review") or "").strip()
            if (
                int(debate.get("count") or 0) > 1
                and latest
                and latest != first
                and "NO MATERIAL UPDATE" not in latest.upper()
            ):
                parts += ["", "### Subsequent Material Update", strip_case_json(latest).strip()]
            issue = str(debate.get("unresolved_issue") or "").strip()
            if issue:
                parts += ["", "### Remaining Unresolved Issue", issue]
            final_state["debate_summary"] = "\n\n".join(parts)
        risk = final_state.get("risk_debate_state") or {}
        final_state["research_rounds_used"] = int(debate.get("count") or 0)
        final_state["risk_rounds_used"] = int(risk.get("count") or 0) // 3
        # Applied last: it upgrades the derived status to PROVISIONAL and adds
        # the reason to an integrity string the earlier steps may have written.
        apply_price_conflict(final_state)
        # The PM's own decision, kept separately from what a reader may publish.
        # Only the first pass captures it; prepare_final_state runs again from
        # save_reports, when final_rating has already been gated.
        if not final_state.get("provisional_decision"):
            pre_gate = str(final_state.get("final_rating") or "").strip()
            if not pre_gate:
                pre_gate = str(run_rating(final_state) or "")
            final_state["provisional_decision"] = canonical_decision(pre_gate)
        provisional_code = str(
            final_state.get("provisional_decision") or "NOT_RATED"
        )
        unavailable = (
            str(final_state.get("model_status") or "").upper() == "UNVERIFIED"
            or str(final_state.get("research_model_source") or "") == "unavailable"
        )
        if unavailable:
            # A safe fallback: no decision output survives an unavailable model.
            final_state["final_rating"] = display_decision("NOT_RATED")
            final_state["overall_status"] = "Not Rated"
            final_state["decision_status"] = "NOT_RATED"
            final_state["published_decision"] = "NOT_RATED"
            for key in (
                "controlling_fair_value_low",
                "controlling_fair_value_base",
                "controlling_fair_value_high",
                "controlling_fair_value_method",
            ):
                final_state[key] = None
            final_state["publication_fair_value_status"] = "NOT_PUBLISHED"
            final_state["production_decision_allowed"] = False
            if not str(final_state.get("decision_integrity") or "").startswith("DEGRADED"):
                final_state["decision_integrity"] = "DEGRADED: no usable model this run"
        if final_state.get("research_model_path"):
            try:
                from tradingagents.research.report import load_spec

                spec = load_spec(final_state["research_model_path"])
                final_state["resolved_status"] = self._resolve_model_status(
                    final_state,
                    spec,
                    str(final_state.get("segment_reconciliation_status") or ""),
                    bool(final_state.get("dominant_segment"))
                    and float(final_state.get("dominant_segment_share") or 0)
                    >= float((getattr(self, "config", None) or {}).get(
                        "segment_profit_share_threshold", 0.60
                    )),
                )
            except Exception as exc:  # noqa: BLE001 - status is diagnostic
                logger.info("Final resolved status could not be recomputed: %s", exc)
        prior_blocked = (
            str(final_state.get("publication_status") or "").upper() == "BLOCKED"
        )
        publication_status, publication_reasons, publication_qualifications = (
            self._publication_status(final_state)
        )
        if prior_blocked and publication_status != "BLOCKED":
            # A checkpoint may already have been published as blocked even when
            # the original reasons are no longer re-derivable from this state.
            # The fallback stays fail-closed rather than silently publishing.
            publication_status = "BLOCKED"
            publication_reasons = publication_reasons or [
                "publication was blocked at an earlier checkpoint"
            ]
        final_state["publication_status"] = publication_status
        final_state["publication_reasons"] = publication_reasons
        final_state["publication_qualifications"] = publication_qualifications
        if publication_status == "BLOCKED":
            # A controlling figure disagrees with the accepted model version, or
            # the current evidence did not confirm the requirement. The report may
            # describe the run, but it may not publish a rating or a fair value.
            self._apply_blocked_publication(final_state, publication_reasons)
        elif not unavailable:
            final_state["published_decision"] = provisional_code
            final_state["final_rating"] = display_decision(provisional_code)
        from tradingagents.research import decision_checks

        final_state.update(decision_checks.assess(final_state))
        try:
            component = json.loads(str(final_state.get("component_status") or "{}"))
        except ValueError:
            component = {}
        if isinstance(component, dict):
            try:
                resolved_view = json.loads(
                    str(final_state.get("resolved_status") or "{}")
                )
            except ValueError:
                resolved_view = {}
            if isinstance(resolved_view, dict):
                ev_method = component.get("ev_ebitda_method")
                if isinstance(ev_method, dict) and resolved_view.get(
                    "ev_calculation_status"
                ):
                    ev_method["status"] = resolved_view["ev_calculation_status"]
                comparison = component.get("ev_per_share_comparison")
                if isinstance(comparison, dict) and resolved_view.get(
                    "per_share_comparison_status"
                ):
                    comparison["status"] = resolved_view["per_share_comparison_status"]
                controlling_method = str(
                    resolved_view.get("controlling_method")
                    or final_state.get("controlling_valuation_method")
                    or "pe"
                )
                component["controlling_valuation"] = {
                    "use": f"{controlling_method} only",
                    "ev_controlling": controlling_method == "ev_ebitda",
                }
            component["decision"] = {
                "integrity": final_state.get("decision_integrity", ""),
                "status": final_state.get("decision_status", ""),
                "provisional_reason": provisional_reason(final_state),
                "publication_status": publication_status,
                "publication_reasons": publication_reasons,
            }
            final_state["component_status"] = json.dumps(component)
        return final_state

    def _apply_blocked_publication(self, final_state: dict, reasons: list[str]) -> None:
        """Clear every decision output *before* the safety check runs.

        Fail-closed means the published surfaces are empty, not merely annotated.
        A stale ``controlling_fair_value_*`` left in state would make
        DECISION_SAFETY_CHECK fail on a report that publishes no fair value at
        all, which is the opposite of what the check is for.
        """
        final_state["final_rating"] = display_decision("NOT_RATED")
        final_state["overall_status"] = "Not Rated"
        final_state["decision_status"] = "NOT_RATED"
        final_state["published_decision"] = "NOT_RATED"
        for key in (
            "controlling_fair_value_low",
            "controlling_fair_value_base",
            "controlling_fair_value_high",
        ):
            final_state[key] = None
        final_state["controlling_fair_value_method"] = None
        final_state["publication_fair_value_status"] = "NOT_PUBLISHED"
        codes = "; ".join(str(reason) for reason in reasons if str(reason).strip())
        final_state["decision_integrity"] = (
            "DEGRADED: publication blocked"
            + (f" — {codes}" if codes else "")
        )
        final_state["production_decision_allowed"] = False
        decision = str(final_state.get("final_trade_decision") or "").strip()
        note = (
            "**Publication blocked**: "
            + (codes or "publication is blocked")
            + ". No rating or fair value is published until the controlling "
            "value is reconciled; the full record is in the appendix."
        )
        if decision and "**Publication blocked**" not in decision:
            final_state["final_trade_decision"] = decision + "\n\n" + note

    def _publication_status(self, final_state: dict) -> tuple[str, list[str], list[str]]:
        """Whether the report may publish a controlling rating and fair value.

        Returns ``(status, blockers, qualifications)``. BLOCKED means a controlling
        value disagrees, the decision was written against a different model
        version, or it presents a repaired-away figure as current. A defect in a
        non-controlling cross-check is a qualification, not a blocker:
        OK_WITH_QUALIFICATION still publishes the controlling P/E decision.
        """
        blockers: list[str] = []
        qualifications: list[str] = []

        def add(target: list[str], value: str) -> None:
            text = str(value or "").strip()
            if text and text not in target:
                target.append(text)

        conflict = str(final_state.get("price_authority_conflict") or "").strip()
        if conflict:
            add(blockers, conflict)
        try:
            resolved = json.loads(str(final_state.get("resolved_status") or "{}"))
        except ValueError:
            resolved = {}
        controlling = str(
            final_state.get("controlling_valuation_method") or "pe"
        ).strip().lower()
        ev_controlling = controlling == "ev_ebitda"
        if isinstance(resolved, dict):
            for item in resolved.get("contradictions") or []:
                code = str(item)
                if (
                    code == "EV_COMPARISON_VERIFIED_WITH_SHARE_MISMATCH"
                    and not ev_controlling
                ):
                    # A non-controlling cross-check cannot block the controlling
                    # P/E decision. It stays as a visible qualification.
                    add(qualifications, code)
                else:
                    add(blockers, code)
        configuration_conflict = str(
            final_state.get("controlling_valuation_conflict") or ""
        ).strip()
        if configuration_conflict:
            add(
                blockers,
                "CONTROLLING_VALUATION_METHOD_CONFLICT: " + configuration_conflict,
            )
        if (
            str(final_state.get("model_status") or "").upper() in {"VERIFIED", "UNVERIFIED"}
            and final_state.get("segment_evidence_schema_missing")
        ):
            add(blockers, "SEGMENT_EVIDENCE_SCHEMA_MISSING")
        if (
            str(final_state.get("segment_reconciliation_status") or "") == "INCOMPLETE"
            and final_state.get("segment_reconciliation_controlling")
        ):
            # Incomplete segment construction is a published qualification, not a
            # blocker, unless segment reconciliation controls the valuation or the
            # decision.
            add(blockers, "SEGMENT_RECONCILIATION_INCOMPLETE_CONTROLLING")
        if (
            final_state.get("deterministic_checks_executed") is False
            and str(final_state.get("model_status") or "").upper()
            in {"VERIFIED", "UNVERIFIED"}
        ):
            add(blockers, "DETERMINISTIC_CHECKS_NOT_EXECUTED")
        if (
            int(final_state.get("trigger_taxonomy_version") or 2) >= 2
            and "Legacy thesis invalidation" in str(
                final_state.get("final_trade_decision") or ""
            )
        ):
            add(blockers, "LEGACY_TRIGGER_RENDERED")
        active = str(final_state.get("active_model_version") or "").strip()
        decision_version = str(final_state.get("decision_model_version") or "").strip()
        if active and decision_version and active != decision_version:
            add(
                blockers,
                f"the decision was written against {decision_version}, but {active} controls",
            )
        values = superseded_values(final_state)
        if values:
            from tradingagents.research.superseded import (
                controlling_pairs_from_log,
                uses_superseded,
            )

            pairs = controlling_pairs_from_log(final_state)
            decision = str(final_state.get("final_trade_decision") or "")
            if pairs and uses_superseded(decision, pairs):
                add(
                    blockers,
                    "the controlling decision presents a figure superseded by the model repair",
                )
        if blockers:
            return "BLOCKED", blockers, qualifications
        if qualifications:
            return "OK_WITH_QUALIFICATION", [], qualifications
        return "OK", [], []

    # An exact size in prose: a percentage of a position, a share count, or a
    # tranche count. The pattern lives with the agent-side gate so both agree.
    def _stage_status(self, final_state) -> dict[str, str]:
        """Whether each decision stage produced output, and where a reader finds it.

        The 13:09 report moved the Research Manager and the Trader to the appendix
        without saying so; a reader could not tell a policy decision from a failure.
        """
        return {
            "research_manager": (
                "completed_appendix_only"
                if str(final_state.get("investment_plan") or "").strip()
                else "failed"
            ),
            "trader": (
                "completed_appendix_only"
                if str(final_state.get("trader_investment_plan") or "").strip()
                else "failed"
            ),
            "portfolio_manager": (
                "completed_main_report"
                if str(final_state.get("final_trade_decision") or "").strip()
                else "failed"
            ),
        }

    def _derive_verification(self, final_state) -> tuple[str, str]:
        """Run the three independent checks and return (JSON, overall outcome).

        Each check gets only its own inputs; the groups below are disjoint on
        purpose, so DECISION_CHECK cannot inherit a pass from MODEL_CHECK.
        """
        from tradingagents.agents.rating import run_rating
        from tradingagents.research import verification as verification_mod

        def _json(value, fallback):
            try:
                parsed = json.loads(str(value or ""))
            except (TypeError, ValueError):
                return fallback
            return parsed if isinstance(parsed, type(fallback)) else fallback

        corrections = _json(final_state.get("model_corrections"), [])
        sensitivities = _json(final_state.get("sensitivity_items"), [])
        audit = _json(final_state.get("decision_audit_result"), {})
        unresolved = [
            record for record in corrections
            if str(record.get("status") or "").upper() in {"PROPOSED", "UNRESOLVED"}
        ]
        statuses = {
            "operating_model": final_state.get("operating_model_status"),
            "eps_model": final_state.get("eps_model_status"),
            "valuation": final_state.get("valuation_status"),
            "sensitivity": final_state.get("sensitivity_model_status"),
            "ev_per_share": final_state.get("ev_per_share_status"),
        }
        statuses = {name: value for name, value in statuses.items() if value}
        low = final_state.get("research_fair_value_low")
        high = final_state.get("research_fair_value_high")
        mid = (low + high) / 2 if low is not None and high is not None else None
        price = final_state.get("research_price") or None
        result = verification_mod.assess(
            source={
                "model_status": final_state.get("model_status"),
                "conflicts": [
                    conflict for conflict in [final_state.get("price_conflict")] if conflict
                ],
                "unresolved": unresolved,
                "provenance": final_state.get("spec_compatibility"),
            },
            model={
                "model_status": final_state.get("model_status"),
                "output_statuses": statuses,
                "corrections": corrections,
                "superseded": len(superseded_values(final_state)),
                "decision_model_version": final_state.get("decision_model_version"),
                "active_model_version": final_state.get("active_model_version"),
            },
            decision={
                "action": (
                    run_rating(final_state)
                    if str(final_state.get("final_trade_decision") or "").strip() else ""
                ),
                "fair_value_low": low,
                "fair_value_mid": mid,
                "fair_value_high": high,
                "price": price,
                "audit": audit,
                "sensitivities": sensitivities,
                "corrections": corrections,
                "unresolved": unresolved,
            },
        )
        return json.dumps(result), result["overall"]

    def _detect_conflicts(self, final_state) -> list[str]:
        """Where an intermediate proposal disagrees with the controlling decision."""
        conflicts: list[str] = []
        rating = run_rating(final_state)
        plan = str(final_state.get("investment_plan") or "")
        trader = str(final_state.get("trader_investment_plan") or "")
        decision = str(final_state.get("final_trade_decision") or "")
        rm = re.search(r"(?m)^\*\*Recommendation\*\*:\s*(\w+)", plan)
        action = re.search(r"(?m)^\*\*Action\*\*:\s*(\w+)", trader)
        new_action = re.search(r"(?m)^\|\s*New investor\s*\|\s*(\w+)\s*\|", decision)
        if rm and rm.group(1).lower() != rating.lower():
            conflicts.append(
                f"Research Manager recommended {rm.group(1)}; the decision is {rating}"
            )
        if action and new_action:
            expected = {"buy": "initiate", "sell": "avoid", "hold": "wait"}.get(
                action.group(1).lower()
            )
            if expected and new_action.group(1).lower() not in {expected, "review"}:
                conflicts.append(
                    f"Trader proposed {action.group(1)}; the decision tells a new investor "
                    f"to {new_action.group(1)}"
                )
        no_book = not str(final_state.get("portfolio_context") or "").strip()
        if no_book and re.search(r"(?m)^\*\*Stop Loss\*\*:\s*(?!not provided)\S", trader):
            conflicts.append("an exact stop was proposed with no portfolio context")
        return conflicts

    def _apply_decision_integrity(self, final_state) -> None:
        """Mark a decision assembled from unverified or fallback inputs.

        Three things make a decision advisory rather than validated: no usable
        research model (so no number audit), a structured agent that answered in
        free text, and exact sizing proposed with no portfolio context. The report
        must say so in the header and in the decision itself — the PM's prose
        otherwise reads with the same confidence as a fully checked run.
        """
        reasons: list[str] = []
        if final_state.get("research_model_source") == "unavailable":
            reasons.append("research model unavailable, so no number audit ran")
        fallbacks = final_state.get("structured_fallbacks") or {}
        if fallbacks:
            reasons.append(
                "structured output fell back for "
                + ", ".join(sorted(fallbacks))
            )
        no_portfolio = not str(final_state.get("portfolio_context") or "").strip()
        decision = str(final_state.get("final_trade_decision") or "")
        trader = str(final_state.get("trader_investment_plan") or "")
        exact_sizing = no_portfolio and has_exact_size(decision + "\n" + trader)
        if exact_sizing:
            from tradingagents.agents.sizing import exact_size_match

            final_state["sizing_instruction"] = (
                exact_size_match(decision + "\n" + trader) or ""
            )
            reasons.append(
                "exact position sizes were proposed with no portfolio weight, cost "
                "basis, benchmark or mandate"
            )
        replaced = superseded_values(final_state)
        if replaced and _cites_superseded(decision + "\n" + trader, replaced):
            reasons.append(
                "the decision cites a figure superseded by the model repair"
            )
        if str(final_state.get("trader_status") or "") == "advisory":
            reasons.append(
                "the Trader answered in free text after two schema attempts, so its "
                "levels and size are advisory"
            )
        if _feature_enabled("COMPONENT_STATUS_V1") and any(
            str(final_state.get(key) or "").upper() == "INVALID"
            for key in ("operating_model_status", "eps_model_status", "valuation_status")
        ):
            reasons.append("a controlling component failed validation")

        final_state["decision_integrity"] = (
            "DEGRADED: " + "; ".join(reasons) if reasons else "validated"
        )
        if not reasons or not decision.strip():
            return

        if exact_sizing and "**Sizing note**" not in decision:
            decision += (
                "\n\n**Sizing note**: Exact position sizes appear above, but no portfolio "
                "weight, cost basis, benchmark or mandate was supplied. Treat them as "
                "illustrative, not as instructions."
            )
        if "**Decision integrity**" not in decision:
            decision += (
                "\n\n**Decision integrity**: DEGRADED — " + "; ".join(reasons) + ". "
                "The direction was reviewed; exact sizing and execution instructions are "
                "not schema-validated."
            )
        final_state["final_trade_decision"] = decision

    def save_reports(self, final_state, ticker, save_path=None, html=True) -> Path:
        """Write the report tree for a completed run, like the CLI does.

        Programmatic callers get the same on-disk reports the CLI produces. Pass
        an explicit ``save_path`` or let it default under ``results_dir``; the
        report is also written as one HTML page unless ``html`` is False.

        The audit and the case table are prepared here when the caller did not
        already do it (``prepare_final_state`` runs before the decision is
        recorded on both the CLI and ``propagate`` paths).
        """
        if save_path is None:
            save_path = self.default_report_path(ticker)
        # Duck-typed callers (the reporting tests) may supply only run_settings;
        # a real graph always has the method.
        prepare = getattr(self, "prepare_final_state", None)
        if prepare is not None:
            prepare(final_state, ticker)
        return write_report_tree(final_state, ticker, save_path, settings=self.run_settings(), html=html)

    def report_audit(self, final_state, company_name) -> str:
        """Compare the figures in the prose with the model's figures.

        With no usable model there is nothing to compare against, and the report
        must say that rather than silently drop the section: a missing audit reads
        like a clean one.
        """
        try:
            model, valuation, _spec = self._research_model(
                company_name,
                final_state.get("trade_date"),
                resolution=state_price_resolution(final_state),
                allow_build=False,
                spec_hint=final_state.get("research_model_path") or None,
            )
        except _ModelUnavailable as exc:
            reason = " ".join(str(exc).split())
            return (
                "The number audit did not run: no usable research model was available "
                f"({reason}). No figure in this report was checked against a model."
            )
        from tradingagents.research import audit, evidence as evidence_module

        # A forecast presented as company guidance is rewritten before the audit,
        # so the verified sections and the PM decision carry the correct label.
        for key in ("final_trade_decision", "investment_plan"):
            text = str(final_state.get(key) or "")
            if text.strip():
                rewritten, findings = evidence_module.enforce_forecast_labels(text, model.spec)
                if findings:
                    final_state[key] = rewritten

        stats: dict = {}
        findings = audit.audit_state(
            final_state, model, valuation, stats=stats, superseded=superseded_values(final_state),
            sensitivity_items=getattr(self, "_sensitivity_items", None),
        )
        if any(finding.kind in {"relation", "percentage"} for finding in findings):
            # A claim its own figures contradict is caught before publication:
            # one repair pass rewrites the sentence, and the same check must
            # accept the rewrite or the original (and the finding) stand.
            llm = getattr(self, "quick_thinking_llm", None)
            if llm is not None:
                def _ask(prompt: str) -> str:
                    timeout = float(
                        (getattr(self, "config", None) or {}).get(
                            "audit_repair_timeout", 180
                        )
                        or 180
                    )
                    response = _invoke_with_timeout(llm, prompt, timeout)
                    return str(getattr(response, "content", response))

                try:
                    repaired = audit.repair_relations(
                        final_state, findings, _ask, model=model
                    )
                except Exception as exc:  # noqa: BLE001 - the original text stands
                    logger.warning(
                        "Relational claim repair did not complete; the original "
                        "text and its finding stand: %s",
                        exc,
                    )
                    repaired = False
                if repaired:
                    # audit_state mutates the stats dict it is handed: build the
                    # fresh one first, then re-audit into IT (assigning {} on the
                    # left of the call would discard the counts it just recorded).
                    stats = {}
                    findings = audit.audit_state(
                        final_state, model, valuation, stats=stats,
                        superseded=superseded_values(final_state),
                        sensitivity_items=getattr(self, "_sensitivity_items", None),
                    )
        return audit.render(findings, stats=stats)

    def default_report_path(self, ticker) -> Path:
        """Where a run's reports go unless told otherwise: under results_dir, stamped now."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return Path(self.config["results_dir"]) / "reports" / f"{safe_ticker_component(ticker)}_{stamp}"

    def create_run_state(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """Build a run's initial state; propagate() and the CLI both start here.

        Injects the resolved instrument identity for every agent (#814). The
        memory log's lessons are not here: the graph's Memory Log step settles
        and loads them alongside the analysts (see ``_memory_step``).
        """
        state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            asset_type=asset_type,
            instrument_context=self.resolve_instrument_context(company_name, asset_type, trade_date),
            portfolio_context=portfolio.render(company_name) if portfolio is not None else "",
            # The run's horizon: the CLI question's answer rides on the config
            # (TRADINGAGENTS_DECISION_HORIZON or the default when unattended).
            decision_horizon=str(self.config.get("decision_horizon") or ""),
        )
        # Empty unless a corpus is configured, so vanilla runs keep the same state.
        package, artifact, price_resolution, spec_path = self._research_package(
            company_name, trade_date
        )
        state["research_package"] = package
        state["research_package_artifact"] = artifact
        state["source_facts"] = getattr(self, "_source_facts", "")
        state["model_derived_facts"] = package
        state["bridge_arithmetic"] = getattr(self, "_bridge_arithmetic", "[]")
        state["resolved_status"] = getattr(self, "_resolved_status", "{}")
        state["sensitivity_conflict"] = getattr(self, "_sensitivity_conflict", False)
        state["fundamental_packet"] = getattr(self, "_fundamental_packet", "")
        state["valuation_packet"] = getattr(self, "_valuation_packet", "")
        state["research_price"] = price_resolution.value or 0.0
        state["price_source"] = price_resolution.source
        state["price_as_of"] = price_resolution.as_of
        state["price_ref"] = price_resolution.ref
        state["price_note"] = price_resolution.note
        state["price_override"] = price_resolution.explicit
        state["price_authority_conflict"] = ""
        state["price_conflict"] = ""
        if _feature_enabled("PRICE_SNAPSHOT_V1") and price_resolution.value:
            try:
                from tradingagents.dataflows.prices import last_close

                market_close = last_close(company_name, str(trade_date))
            except Exception as exc:  # noqa: BLE001 - a snapshot failure is a gap
                logger.info("Price snapshot unavailable for %s: %s", company_name, exc)
                market_close = None
            # A user override is recorded, not treated as a conflict: the caller
            # chose it. A verified close is the session by construction. Only a
            # weaker class that somehow controls is compared with the session.
            if not price_resolution.explicit:
                state["price_conflict"] = price_conflict_reason(
                    price_resolution.value, market_close
                )
        state["research_model_path"] = spec_path
        state["research_model_path_v1"] = spec_path
        state["active_model_version"] = "v1" if spec_path else ""
        state["fundamentals_challenge_check"] = "pending" if spec_path else "not_applicable"
        state["model_challenges"] = ""
        state["model_corrections"] = ""
        state["model_repair_log"] = ""
        state["model_repair_addendum"] = ""
        state["verification_checks"] = ""
        state["verification_status"] = ""
        state["decision_model_version"] = ""
        from tradingagents.research.report import fair_value_range_from_summary

        low, high = fair_value_range_from_summary(package)
        state["research_fair_value_low"] = low
        state["research_fair_value_high"] = high
        try:
            from tradingagents.research.evidence import benchmark_statement
            from tradingagents.research.report import load_spec

            state["benchmark_basis"] = (
                benchmark_statement(load_spec(spec_path)) if spec_path else ""
            )
        except Exception:  # noqa: BLE001 - a missing statement is a gap, not a crash
            state["benchmark_basis"] = ""
        bases = getattr(self, "_basis_names", None) or {}
        state["market_cap_basis"] = bases.get("market_cap_basis", "")
        state["forecast_eps_basis"] = bases.get("forecast_eps_basis", "")
        state["dilution_sensitivity_basis"] = bases.get("dilution_sensitivity_basis", "")
        state["equity_value_share_basis"] = bases.get("equity_value_share_basis", "")
        output_status = getattr(self, "_output_status", None) or {}
        state["operating_model_status"] = output_status.get(
            "operating_model_status", "VERIFIED" if spec_path else "NOT_APPLICABLE"
        )
        state["eps_model_status"] = output_status.get(
            "eps_model_status", "UNVERIFIED" if spec_path else "SUPPRESSED"
        )
        state["eps_status"] = state["eps_model_status"]          # alias
        state["eps_basis_quality"] = output_status.get("eps_basis_quality", "")
        state["valuation_method_statuses"] = json.dumps(
            output_status.get("valuation_method_statuses", {})
        )
        state["valuation_status"] = output_status.get(
            "valuation_status", "UNVERIFIED" if spec_path else "SUPPRESSED"
        )
        state["calculation_status"] = state["valuation_status"]
        state["assumption_status"] = getattr(self, "_assumption_status", "")
        state["assumption_detail"] = getattr(self, "_assumption_detail", "")
        state["valuation_wording"] = getattr(self, "_valuation_wording", "")
        segment = getattr(self, "_segment_status", None)
        state["segment_reconciliation_status"] = segment.status if segment else ""
        state["segment_reconciliation_detail"] = segment.detail if segment else ""
        state["dominant_segment"] = segment.segment if segment else ""
        state["dominant_segment_share"] = segment.share if segment else None
        state["segment_table"] = getattr(self, "_segment_table", "")
        state["segment_build_type"] = ""
        state["bottom_up_claim_allowed"] = True
        state["operating_model_confidence"] = "VERIFIED"
        state["segment_reconciliation_controlling"] = False
        state["controlling_valuation_method"] = "pe" if spec_path else ""
        state["controlling_valuation_conflict"] = ""
        state["ev_decision_use"] = "CROSS_CHECK_ONLY"
        state["publication_qualifications"] = []
        if spec_path:
            # A cached NOT_REQUIRED is provisional until the current Fundamentals
            # SEGMENT_EVIDENCE is audited at the reviewer's entry.
            state["segment_reconciliation_status"] = "PENDING_CURRENT_EVIDENCE"
            state["segment_reconciliation_detail"] = (
                "awaiting the current Fundamentals SEGMENT_EVIDENCE block"
            )
            state["segment_requirement_provisional"] = True
            state["segment_limitation"] = ""
        state["sensitivity_model_status"] = output_status.get("sensitivity_model_status", "UNAVAILABLE")
        state["sensitivity_status"] = state["sensitivity_model_status"]   # alias
        state["model_node_sensitivity_status"] = state["sensitivity_model_status"]
        state["economic_driver_mapping_status"] = getattr(
            self, "_economic_driver_mapping_status", ""
        )
        state["failed_driver_mappings"] = getattr(self, "_failed_driver_mappings", 0)
        state["failed_driver_mapping_ids"] = json.dumps(
            getattr(self, "_failed_driver_mapping_ids", [])
        )
        state["deterministic_issues"] = ""
        state["deterministic_checks_executed"] = False
        state["provisional_decision"] = ""
        state["published_decision"] = ""
        state["ev_per_share_status"] = output_status.get("ev_per_share_status", "")
        state["research_fair_value_verified"] = bool(output_status.get("valuation_controlling"))
        state["action_status"] = "controlling"
        state["trader_status"] = "structured"
        state["run_purpose"] = str(
            self.config.get("run_purpose") or "production"
        ).strip().lower()
        state["ev_input_diagnostics"] = json.dumps(
            getattr(self, "_ev_diagnostic", {}) or {}
        )
        state["sector"] = getattr(self, "_spec_sector", "") or ""
        state["decision_basis"] = ""
        state["decision_audit_result"] = ""
        state["model_status"] = "VERIFIED" if spec_path else "NOT_APPLICABLE"
        state["overall_status"] = ""
        state["decision_status"] = (
            "NOT_RATED" if state["model_status"] == "UNVERIFIED"
            else "PROVISIONAL"
            if state["valuation_status"] != "VERIFIED"
            or state["run_purpose"] != "production"
            else "FINAL"
        ) if spec_path else ""
        state["blocking_issue"] = ""
        state["sizing_instruction"] = ""
        state["non_model_decision_basis"] = {}
        state["repairs_applied"] = 0
        state["unresolved_blocking"] = 0
        state["unresolved_material"] = 0
        state["sensitivity_table"] = getattr(self, "_sensitivity_table", "")
        state["component_status"] = json.dumps(
            getattr(self, "_component_status", {}) or {}
        )
        state["sensitivity_ids"] = json.dumps([
            item.get("sensitivity_id")
            for item in getattr(self, "_sensitivity_items", [])
            if item.get("decision_eligible")
        ])
        state["sensitivity_alias_map"] = json.dumps(
            getattr(self, "_sensitivity_alias_map", {}) or {}
        )
        state["sensitivity_gaps"] = json.dumps(getattr(self, "_sensitivity_gaps", []))
        state["controlling_fair_value_low"] = None
        state["controlling_fair_value_base"] = None
        state["controlling_fair_value_high"] = None
        source = getattr(self, "_spec_source", "") or ""
        state["research_model_source"] = source
        state["research_model_created_at"] = getattr(self, "_spec_created_at", "") or ""
        state["stale_model_path"] = ""
        state["stale_model_package"] = ""
        if package and not spec_path:
            # Keep the previous spec for comparison only; it must not drive the
            # rating, valuation or transaction of a run that could not use it.
            stale_path, stale_package = self._stale_model_reference(company_name, trade_date)
            state["stale_model_path"] = stale_path
            state["stale_model_package"] = stale_package
        state["spec_compatibility"] = (
            "not_applicable" if source == "built" else ("unknown" if spec_path else "")
        )
        state["spec_compatibility_reason"] = (
            "built for this run" if source == "built" else ""
        )
        if spec_path and source == "reused":
            state["spec_compatibility"] = getattr(self, "_spec_compatibility", "") or "unknown"
            state["spec_compatibility_reason"] = (
                getattr(self, "_spec_compatibility_reason", "") or ""
            )
        if package and not spec_path:
            # No usable spec: say which model is unavailable and why. This is the
            # deterministic research model, never the LLM client.
            state["research_model_source"] = "unavailable"
            state["spec_compatibility"] = "unavailable"
            state["spec_compatibility_reason"] = " ".join(package.split())[:400]
        return state

    def research_package(self, company_name: str, trade_date: str | None = None,
                         price: float | None = None) -> str:
        """The verified fundamental model for this ticker, or a note saying why not.

        Opt-in: returns "" unless ``TRADINGAGENTS_RESEARCH_DIR`` names a corpus. A
        spec that fails its arithmetic checks becomes a labelled gap, never a set
        of numbers the analyst might treat as correct.
        """
        return self._research_package(company_name, trade_date, price)[0]

    def _ev_diagnostics(self, spec, valuation_period: str, company_name: str,
                        trade_date: str | None) -> dict:
        """Which EV input is missing, and whether the corpus actually states it."""
        from tradingagents.research import input_probe

        debt = spec.net_debt
        needs_scan = (
            debt is None
            or not debt.components_verified()
            or not str(debt.as_of or "").strip()
            or not str(debt.source_unit or "").strip()
            or not str(debt.scope or "").strip()
            or spec.cashflow.get(valuation_period) is None
        )
        probe = None
        if needs_scan:
            root = research_dir_from_env()
            if root is not None:
                try:
                    from tradingagents.dataflows.vendors.research_corpus import CorpusStore

                    corpus = resolve_corpus_root(root, company_name)
                    store = CorpusStore(corpus, company_name)
                    probe = input_probe.scan(store, trade_date)
                except Exception:  # noqa: BLE001 - the diagnostic must not break a run
                    logger.warning("EV input probe could not scan the corpus", exc_info=True)
        return input_probe.diagnose(
            spec,
            valuation_period=valuation_period,
            trade_date=trade_date or "",
            probe=probe,
        )

    def _research_package(self, company_name: str, trade_date: str | None = None,
                          price: float | None = None) -> tuple[str, str, Any, str]:
        """The package text, the published version, the price resolution and the spec path.

        The two texts differ only in the imperative lines the prompt needs; the
        report must not publish instructions addressed to the analyst. The
        resolution carries the authority of the price so the state, the report and
        the repair gate can all say which value controls.
        """
        from tradingagents.research import price_authority as price_authority_module

        self._assumption_status = ""
        self._assumption_detail = ""
        self._valuation_wording = ""
        self._segment_status = None
        self._segment_table = ""
        self._source_facts = ""
        self._bridge_arithmetic = "[]"
        self._resolved_status = "{}"
        self._sensitivity_conflict = False
        self._economic_driver_mapping_status = ""
        self._failed_driver_mappings = 0
        self._failed_driver_mapping_ids: list[str] = []
        root = research_dir_from_env()
        if root is None:
            return "", "", price_authority_module.PriceResolution(value=0.0), ""

        try:
            model, valuation, spec_path = self._research_model(company_name, trade_date, price)
        except _ModelUnavailable as exc:
            note = str(exc)
            return note, note, price_authority_module.PriceResolution(value=0.0), ""

        from tradingagents.research import (
            assumptions,
            capital,
            facts as facts_module,
            price_authority,
            report as research_report,
            segments,
            sensitivity,
            status as status_module,
        )
        from tradingagents.research.shares import basis_report, diluted_kind

        self._source_facts = facts_module.render_source_facts(model.spec)
        self._bridge_arithmetic = json.dumps(
            [bridge.arithmetic.to_dict() for bridge in model.spec.bridges]
        )

        resolution = price_authority.PriceResolution.from_spec(model.spec)
        if resolution.value is None:
            resolution = price_authority.PriceResolution(
                value=float(valuation.price or 0) or None,
                source=price_authority.UNKNOWN,
                ticker=company_name,
                currency=str(getattr(model.spec, "currency", "") or ""),
            )

        eps_ok, _eps_reason = capital.eps_basis_ready(model.spec, valuation.period)
        cap_ok, _cap_reason = capital.market_cap_ready(model.spec)
        equity_ok, _equity_reason = capital.equity_basis_ready(model.spec)
        publishable_eps = bool(eps_ok and valuation.eps_available)
        items, gaps = sensitivity.rank(
            model.spec, top=3, eps_verified=publishable_eps, pe_verified=publishable_eps
        )
        gaps = sensitivity.filter_gaps(model.spec, items, gaps)
        mapping = sensitivity.mapping_summary(model.spec, items, gaps)
        self._economic_driver_mapping_status = mapping["economic_driver_mapping_status"]
        self._failed_driver_mappings = mapping["failed_driver_mappings"]
        self._failed_driver_mapping_ids = mapping["failed_driver_mapping_ids"]
        _gap_text = " ".join(gaps)
        self._sensitivity_conflict = any(
            item.sensitivity_id in _gap_text
            or any(alias and alias in _gap_text for alias in item.aliases)
            for item in items
        )
        table = sensitivity.render(items)
        basis = basis_report(model.spec)
        diagnostics = self._ev_diagnostics(model.spec, valuation.period, company_name,
                                           trade_date)
        if capital.unsettled_convertibles(model.spec):
            # The convertible is detected but cannot be included: there is no
            # convertible-debt field to recalculate net debt from. Say so rather
            # than implying it was repaired.
            diagnostics = {
                **diagnostics,
                "provisional": "convertible instruments detected but unresolved; not repaired",
            }
        if not valuation.cross_method_comparable:
            ev_count, pe_count, gap = capital.cross_method_basis(model.spec)
            conflicts = capital.treatment_conflicts(model.spec)
            reason = (
                f"EV per-share uses {ev_count:,.1f}m shares; P/E uses {pe_count:,.1f}m "
                f"({gap:.1%} apart); cross-method comparison unverified"
                if gap is not None and ev_count and pe_count
                else "; ".join(conflicts) or "share bases are not reconciled"
            )
            if conflicts:
                reason = f"{reason}; {'; '.join(conflicts)}"
            diagnostics = {**diagnostics, "cross_method_basis": reason}
        self._ev_diagnostic = diagnostics
        propagation_ok = all(
            item.verification_status == "VERIFIED" for item in items
        )
        controlling_rows = [item for item in items if item.controlling_candidate]
        controlling_verified = sum(1 for item in controlling_rows if item.decision_eligible)
        controlling_expected = len(controlling_rows)
        controlling_ok = controlling_expected > 0 and controlling_verified == controlling_expected
        failed_rows = sum(1 for item in items if not item.decision_eligible)
        statuses = status_module.assess(
            model_status="VERIFIED",
            basis=basis,
            propagation_ok=propagation_ok,
            controlling_propagation_ok=controlling_ok,
            has_band=(
                valuation.fair_value_low is not None and valuation.fair_value_high is not None
            ),
            sensitivity_count=len(items),
            price=valuation.price,
            has_depreciation=bool(model.spec.cashflow),
            has_net_debt=bool(
                model.spec.net_debt is not None
                and model.spec.net_debt.components_verified()
            ),
            ev_ebitda_multiple=model.spec.valuation.ev_ebitda_multiple,
            capital_verified=capital.validated(model.spec, valuation.period)[0],
            eps_basis_quality=diluted_kind(model.spec),
            ev_inputs_ready=diagnostics.get("status") == "EXTRACTED",
            market_cap_verified=cap_ok,
            eps_basis_verified=eps_ok,
            equity_basis_verified=equity_ok,
            share_price_verified=resolution.verified,
        )
        if statuses["valuation_method_statuses"].get("ev_ebitda") != status_module.VERIFIED:
            # No enterprise value was produced: there is nothing to compare.
            statuses["ev_per_share_status"] = status_module.UNAVAILABLE
            diagnostics.pop("provisional", None)
            diagnostics["cross_method_basis"] = "EV/EBITDA method unavailable"
        elif not valuation.ev_per_share_reconciled:
            # The enterprise value stands; only the per-share figure it cannot
            # reproduce is withheld.
            statuses["ev_per_share_status"] = status_module.SUPPRESSED
        elif not valuation.cross_method_comparable:
            # Arithmetic valid, comparison not: publish the number, label it.
            statuses["ev_per_share_status"] = status_module.UNVERIFIED
        self._output_status = statuses
        assumption = assumptions.controlling(
            model.spec, statuses["valuation_method_statuses"]
        )
        self._assumption_status = assumption.status if assumption else ""
        self._assumption_detail = json.dumps(assumption.to_dict()) if assumption else ""
        self._valuation_wording = assumptions.wording(
            statuses["valuation_status"], assumption
        )
        segment = segments.status(
            model.spec,
            float((getattr(self, "config", None) or {}).get(
                "segment_profit_share_threshold", segments.DEFAULT_PROFIT_SHARE_THRESHOLD
            )),
        )
        self._segment_status = segment
        self._segment_table = segments.table(model.spec)
        try:
            from tradingagents.research import resolved_status as status_resolver

            _ev_count, _pe_count, _gap = capital.cross_method_basis(model.spec)
            _share_mismatch = bool(_gap is not None and _gap > 0.01)
            _controlling_method, _controlling_conflict = (
                controlling_valuation_resolution(model.spec)
            )
            _resolved = status_resolver.resolve(
                spec=model.spec,
                statuses=statuses,
                assumption=assumptions.for_spec(model.spec, "ev_ebitda"),
                share_mismatch=_share_mismatch,
                convertible_unresolved=bool(capital.unsettled_convertibles(model.spec)),
                dominant_segment_detected=(
                    bool(segment.segment)
                    and float(segment.share or 0)
                    >= float((getattr(self, "config", None) or {}).get(
                        "segment_profit_share_threshold", segments.DEFAULT_PROFIT_SHARE_THRESHOLD
                    ))
                ),
                segment_status=segment.status,
                deterministic_checks_executed=True,
                trigger_taxonomy_version=int(
                    getattr(model.spec, "trigger_taxonomy_version", 2)
                ),
                legacy_rendered=False,
                sensitivity_conflict=self._sensitivity_conflict,
                controlling_method=_controlling_method,
                controlling_conflict=_controlling_conflict,
            )
            self._resolved_status = json.dumps(_resolved.to_dict())
        except Exception as exc:  # noqa: BLE001 - a status is not allowed to break the build
            logger.info("Resolved status unavailable: %s", exc)
            self._resolved_status = "{}"
        self._component_status = build_component_status(
            statuses, propagation_ok=propagation_ok, diagnostics=diagnostics,
            controlling={
                "status": statuses.get("controlling_sensitivity_status", ""),
                "verified": controlling_verified,
                "expected": controlling_expected,
            },
            failed_rows=failed_rows,
        )
        self._basis_names = basis
        self._sensitivity_items = [item.to_dict() for item in items]
        self._sensitivity_alias_map = sensitivity.alias_map(items)
        self._sensitivity_gaps = gaps
        self._sensitivity_table = table
        include_per_share = statuses["eps_model_status"] == status_module.VERIFIED
        include_ev = (
            statuses["valuation_method_statuses"].get("ev_ebitda") == status_module.VERIFIED
        )
        include_ev_per_share = bool(
            include_ev
            and valuation.ev_per_share is not None
            and valuation.ev_per_share_reconciled
        )
        ev_note = ""
        if statuses["valuation_method_statuses"].get("ev_ebitda") != status_module.VERIFIED:
            from tradingagents.research import input_probe

            ev_note = input_probe.render(diagnostics)
        self._fundamental_packet = research_report.compact_summary(
            model, valuation, instructions=False,
            include_per_share=False, include_ev_ebitda=False, include_valuation=False,
        )
        self._valuation_packet = research_report.valuation_packet(
            model, valuation, statuses
        )
        package = research_report.compact_summary(
            model, valuation, include_per_share=include_per_share,
            include_ev_ebitda=include_ev, include_ev_per_share=include_ev_per_share,
            ev_note=ev_note,
        )
        artifact = research_report.compact_summary(
            model, valuation, instructions=False,
            include_per_share=include_per_share,
            include_ev_ebitda=include_ev, include_ev_per_share=include_ev_per_share,
            ev_note=ev_note,
        )
        if table:
            gaps_line = (
                f"\n\nResearch gaps (no explicit model assumption): {', '.join(gaps)}."
                if gaps else ""
            )
            package = f"{package}\n\n{table}{gaps_line}"
            artifact = f"{artifact}\n\n{table}{gaps_line}"
        if not valuation.cross_method_comparable:
            basis_note = (
                f"\n\nCross-method basis: {diagnostics.get('cross_method_basis', '')}."
            )
            package += basis_note
            artifact += basis_note
        if segment.status != segments.NOT_REQUIRED:
            segment_lines = [
                f"SEGMENT_RECONCILIATION_STATUS = {segment.status}. {segment.detail}."
            ]
            if segment.status != segments.VERIFIED:
                segment_lines.append(segments.TOP_DOWN_SENTENCE)
                package = segments.neutralise_bottom_up(package)
                artifact = segments.neutralise_bottom_up(artifact)
            segment_table = segments.table(model.spec)
            if segment_table:
                segment_lines.append(segment_table)
            addition = "\n\n" + "\n".join(segment_lines)
            package += addition
            artifact += addition
        if resolution.value is None:
            resolution = price_authority.PriceResolution(
                value=float(valuation.price or 0) or None,
                source=price_authority.UNKNOWN,
                ticker=company_name,
                currency=str(getattr(model.spec, "currency", "") or ""),
            )
        return package, artifact, resolution, str(spec_path)

    def _research_model(self, company_name: str, trade_date: str | None = None,
                        price: float | None = None, allow_build: bool = True,
                        spec_hint: str | None = None, resolution=None):
        """The validated model and its valuation, or ``_ModelUnavailable``."""
        root = research_dir_from_env()
        if root is None:
            raise _ModelUnavailable("No research corpus is configured for this run.")

        corpus = resolve_corpus_root(root, company_name)
        candidates = [Path(spec_hint)] if spec_hint else []
        shared = _configured_spec(company_name)
        if shared is not None:
            candidates.append(shared[0])
        candidates.extend([corpus / "model.json", corpus / f"{safe_ticker_component(company_name)}.json"])
        cached = self._models_dir() / f"{safe_ticker_component(company_name)}.json"
        candidates.append(cached)
        spec_path = next((path for path in candidates if path.is_file()), None)
        rebuild = allow_build and os.environ.get("TRADINGAGENTS_REBUILD_MODEL", "").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if spec_path is None and not allow_build:
            raise _ModelUnavailable("No model spec is available to audit against.")
        rebuilt = spec_path is None or rebuild
        config = getattr(self, "config", None) or {}
        purpose = str(config.get("run_purpose") or "production").strip().lower()

        from tradingagents.research import kernel, provenance, report as research_report, valuation
        from tradingagents.research.schemas import ModelError

        compatibility, compatibility_reason = provenance.NOT_APPLICABLE, "built for this run"
        if not rebuilt:
            # Identity is read from the spec itself; an unreadable file is
            # reported by the load below, with the same message as before.
            try:
                probe = research_report.load_spec(spec_path)
                unit, currency = probe.unit, probe.currency
            except (ModelError, OSError, KeyError, ValueError):
                unit, currency = "", ""
            compatibility, compatibility_reason = provenance.check(
                spec_path, ticker=company_name, unit=unit, currency=currency, corpus=corpus
            )
            if compatibility == provenance.INCOMPATIBLE:
                if purpose == "production" and allow_build:
                    logger.warning(
                        "Model spec %s is incompatible (%s); rebuilding it",
                        spec_path, compatibility_reason,
                    )
                    rebuilt = True
                    compatibility = provenance.NOT_APPLICABLE
                    compatibility_reason = "rebuilt: the loaded spec was incompatible"
                elif purpose == "production":
                    raise _ModelUnavailable(
                        "MODEL UNAVAILABLE: the spec at "
                        f"{spec_path.name} is incompatible with this run "
                        f"({compatibility_reason}), and a production run may not reuse it. "
                        "Report the model as unavailable."
                    )
            elif compatibility == provenance.UNKNOWN:
                if purpose == "production" and allow_build:
                    # "Unknown" is not a licence to publish numbers: a production
                    # run rebuilds once rather than trust a spec it cannot verify.
                    logger.warning(
                        "Model spec %s cannot be verified (%s); rebuilding it",
                        spec_path, compatibility_reason,
                    )
                    rebuilt = True
                    compatibility = provenance.NOT_APPLICABLE
                    compatibility_reason = (
                        "rebuilt: the loaded spec's dependency compatibility could not be verified"
                    )
                else:
                    logger.warning(
                        "Model spec %s reused with unverified dependencies: %s",
                        spec_path, compatibility_reason,
                    )

        if rebuilt:
            try:
                spec_path = self._build_spec_from_corpus(company_name, corpus, trade_date)
            except ModelError as exc:
                raise _ModelUnavailable(
                    f"MODEL UNAVAILABLE: {exc} Report the model as unavailable; "
                    "do not estimate its numbers."
                ) from exc
            compatibility = provenance.NOT_APPLICABLE
            compatibility_reason = "built for this run"

        try:
            spec = research_report.load_spec(spec_path)
            resolved = self._resolve_price(
                spec, company_name, trade_date, price, resolution=resolution
            )
            if resolved.value:
                spec.valuation.price = resolved.value
                spec.valuation.price_source = resolved.source
                spec.valuation.price_as_of = resolved.as_of
                spec.valuation.price_ref = resolved.ref
            note = resolved.note
            if note and note not in spec.warnings:   # a reload must not stack the note
                spec.warnings.append(note)
            model = kernel.run(spec, strict=purpose != "replay")
            if allow_build:
                # Provenance for the report: built now, or loaded from a file
                # with a dependency verdict.
                from tradingagents.research.shares import basis_report

                self._spec_source = "built" if rebuilt else "reused"
                self._basis_names = basis_report(spec)
                self._spec_sector = spec.sector
                self._spec_compatibility = compatibility
                self._spec_compatibility_reason = compatibility_reason
                try:
                    self._spec_created_at = datetime.fromtimestamp(
                        Path(spec_path).stat().st_mtime
                    ).isoformat(timespec="seconds")
                except OSError:
                    self._spec_created_at = ""
            return model, valuation.value(model), spec_path
        except (ModelError, OSError, KeyError, ValueError) as exc:
            logger.warning("Research model at %s could not be used: %s", spec_path, exc)
            raise _ModelUnavailable(
                f"MODEL UNAVAILABLE: the model spec at {spec_path.name} failed its checks "
                f"({exc}). Report the model as unavailable; do not estimate its numbers."
            ) from exc

    def _build_spec_from_corpus(self, company_name: str, corpus: Path, trade_date: str | None) -> Path:
        """Have the model build the forecast from the corpus, then cache it.

        Only possible with a model available; without one the run reports the
        model as unavailable rather than inventing figures. The deep model is
        preferred: this is the highest-stakes text the system asks for, and a
        small quick model fails the schema more often than it saves time.
        """
        llm = getattr(self, "deep_thinking_llm", None) or getattr(self, "quick_thinking_llm", None)
        if llm is None:
            raise _ModelUnavailable(
                "MODEL UNAVAILABLE: no model spec exists and no LLM is available to build one. "
                "Add model.json to the corpus, or run inside the graph."
            )

        from tradingagents.dataflows.vendors.research_corpus import CorpusStore
        from tradingagents.research import builder, extract as extract_module

        try:
            extraction = extract_module.extract_filing(str(corpus), company_name, trade_date or "")
            facts_text = extract_module.describe(extraction)
            skip = {extraction.document_id}
        except Exception as exc:  # noqa: BLE001 - not every market has a parsable filing
            # Only Japanese quarterly releases have a deterministic parser. Elsewhere
            # the model reads the filing itself; the arithmetic checks still apply.
            logger.info("No machine-parsed filing for %s (%s); building from the documents", company_name, exc)
            extraction = None
            facts_text = ""
            skip = set()

        store = CorpusStore(corpus, company_name)

        # Corpus first, vendor second: the documents are the primary evidence and
        # the vendor statement only supplies periods the corpus never states
        # (merge_reported_actuals enforces the same order figure by figure).
        from tradingagents.research import vendor_facts

        unit = vendor_facts.statement_unit(company_name)
        reported: dict = {}
        quarters: dict = {}
        try:
            reported = vendor_facts.fetch_annual(company_name, unit=unit)
        except vendor_facts.VendorFactsError as exc:
            logger.info("No vendor annual statement for %s: %s", company_name, exc)
        try:
            quarters = vendor_facts.fetch_quarters(company_name, unit=unit)
        except vendor_facts.VendorFactsError as exc:
            logger.info("No vendor quarterly statement for %s: %s", company_name, exc)

        for block in (
            vendor_facts.describe(reported, str(trade_date or "")),
            vendor_facts.describe_quarters(quarters, str(trade_date or "")),
        ):
            if block:
                facts_text = f"{facts_text}\n\n{block}" if facts_text else block

        price = None
        if trade_date:
            from tradingagents.dataflows.prices import last_close

            price = last_close(company_name, str(trade_date))

        def overlay(spec) -> None:
            """The filing's disclosed numbers trump the model's restatement of them."""
            if extraction is not None:
                extract_module.merge(spec, extraction)
            # An actual year is a reported figure: the vendor statement is that
            # report, so it corrects a net profit the model derived.
            vendor_facts.merge_reported_actuals(spec, reported, str(trade_date or ""))
            # A supplied consensus for a past period is the same fact; it wins too.
            extract_module.merge_consensus_actuals(spec, spec.consensus)

        # The benchmark's consensus, but only when the pointer is this company's:
        # another ticker's estimates are another company's opinion.
        shared = _configured_spec(company_name)
        consensus = shared[1].consensus if shared else None

        spec, attempts = builder.build_spec(
            llm,
            ticker=company_name,
            company=company_name,
            as_of=str(trade_date or ""),
            store=store,
            facts_text=facts_text,
            price=price,
            skip_documents=skip,
            dump_path=self._models_dir() / f"{safe_ticker_component(company_name)}.failed.json",
            overlay=overlay,
            consensus=consensus,
        )
        logger.info(
            "Built a research model for %s from the corpus%s",
            company_name,
            f" after {len(attempts)} correction(s)" if attempts else "",
        )

        path = self._models_dir() / f"{safe_ticker_component(company_name)}.json"
        import json as _json

        path.write_text(_json.dumps(spec.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        # The dependencies this forecast was built from, beside the spec, so a
        # later run can say "compatible" instead of guessing.
        from tradingagents.research import provenance

        try:
            provenance.write_manifest(
                path,
                ticker=company_name,
                unit=spec.unit,
                currency=spec.currency,
                corpus=corpus,
                as_of=str(trade_date or ""),
            )
        except OSError as exc:
            logger.warning("Could not write the dependency manifest for %s: %s", path, exc)
        return path

    def _stale_model_reference(self, company_name: str, trade_date: str | None) -> tuple[str, str]:
        """The previous spec, kept for comparison only; it never drives the run.

        A failed rebuild or an incompatible spec leaves an older file on disk. It is
        rendered into the appendix with an explicit banner so a reader can compare,
        but the report, rating and transaction were built without it.
        """
        root = research_dir_from_env()
        if root is None:
            return "", ""
        corpus = resolve_corpus_root(root, company_name)
        safe = safe_ticker_component(company_name)
        candidates = []
        shared = _configured_spec(company_name)
        if shared is not None:
            candidates.append(shared[0])
        candidates.extend([
            corpus / "model.json",
            corpus / f"{safe}.json",
            self._models_dir() / f"{safe}.json",
        ])
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            return "", ""
        try:
            from tradingagents.research import kernel, report as research_report, valuation

            spec = research_report.load_spec(path)
            model = kernel.run(spec)
            banner = (
                "STALE MODEL — NOT USED\n\n"
                "This specification did not drive this run's rating, valuation or "
                "transaction. It is kept for comparison and diagnostics only.\n\n"
            )
            return str(path), banner + research_report.compact_summary(
                model, valuation.value(model), instructions=False
            )
        except Exception as exc:  # noqa: BLE001 - a stale reference is optional
            logger.info("Stale model reference at %s could not be rendered: %s", path, exc)
            return str(path), ""

    def _models_dir(self) -> Path:
        """Where LLM-built model specs (and failed attempts) are kept."""
        config = getattr(self, "config", None) or {}
        base = Path(
            config.get("data_cache_dir") or (Path.home() / ".tradingagents" / "cache")
        ).expanduser()
        path = base / "models"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _resolve_price(spec, company_name: str, trade_date: str | None,
                       price: float | None,
                       resolution=None):
        """Pick the share price and the class of evidence behind it.

        A model spec's price goes stale the moment the market moves; a valuation
        built on it is wrong in a way no arithmetic check can see. The run date's
        close wins, an explicit caller price wins over it, and the spec is the
        fallback. The returned ``PriceResolution`` carries the class, date and
        source so a later repair cannot replace a verified close with an older
        report value by accident.
        """
        from tradingagents.research import price_authority as pa

        def _make(value, source: str, as_of: str, ref: str, note: str = ""):
            return pa.PriceResolution(
                value=float(value),
                source=source,
                as_of=str(as_of or ""),
                ref=ref,
                ticker=str(company_name or ""),
                currency=str(getattr(spec, "currency", "") or ""),
                note=note,
            )

        if resolution is not None and resolution.value:
            target = pa.PriceResolution(
                ticker=str(company_name or ""),
                currency=str(getattr(spec, "currency", "") or ""),
            )
            if pa.same_security(resolution, target):
                return resolution.with_context(
                    ticker=str(company_name or ""),
                    currency=str(getattr(spec, "currency", "") or ""),
                )

        configured = os.environ.get("TRADINGAGENTS_MODEL_PRICE", "").strip()
        if configured:
            try:
                return _make(
                    configured.replace(",", ""), pa.USER_EXPLICIT,
                    str(trade_date or ""), "TRADINGAGENTS_MODEL_PRICE",
                    f"Share price from TRADINGAGENTS_MODEL_PRICE: {configured}.",
                )
            except ValueError:
                logger.warning("Ignoring invalid TRADINGAGENTS_MODEL_PRICE=%r", configured)
        if price is not None:
            return _make(
                price, pa.USER_EXPLICIT, str(trade_date or ""), "explicit argument"
            )

        spec_price = spec.valuation.price
        stored = pa.PriceResolution.from_spec(spec)
        def _stored_fallback(resolution):
            """A stored price is only current when its own date matches the run."""
            if (
                resolution.as_of
                and trade_date
                and str(resolution.as_of) != str(trade_date)
            ):
                return _make(
                    resolution.value,
                    pa.SPEC_PRICE,
                    resolution.as_of,
                    resolution.ref,
                    f"MODEL WARNING: the stored {resolution.source} price is dated "
                    f"{resolution.as_of}, not {trade_date}; it may be stale.",
                )
            return _make(
                resolution.value, resolution.source, resolution.as_of, resolution.ref
            )

        if trade_date:
            from tradingagents.dataflows.prices import last_close

            market = last_close(spec.ticker or company_name, str(trade_date))
            if market:
                note = ""
                if spec_price and abs(market - spec_price) / spec_price > 0.005:
                    note = (
                        f"Share price updated to the {trade_date} close ({market:,.0f} "
                        f"{spec.currency}) from the spec's {spec_price:,.0f}."
                    )
                return _make(market, pa.VERIFIED_CLOSE, str(trade_date), "analysis-date close",
                             note)
            if stored.value and stored.source:
                fallback = _stored_fallback(stored)
                if not fallback.note:
                    fallback = _make(
                        stored.value, stored.source, stored.as_of, stored.ref,
                        f"MODEL WARNING: no market price was available for {trade_date}; "
                        f"the valuation uses the spec price of {stored.value:,.0f} "
                        f"{spec.currency}, which may be stale.",
                    )
                return fallback
            if spec_price:
                return _make(
                    spec_price, pa.SPEC_PRICE, stored.as_of, "model spec",
                    f"MODEL WARNING: no market price was available for {trade_date}; the "
                    f"valuation uses the spec price of {spec_price:,.0f} {spec.currency}, "
                    "which may be stale.",
                )
        if stored.value and stored.source:
            return _make(stored.value, stored.source, stored.as_of, stored.ref)
        if spec_price:
            return _make(spec_price, pa.SPEC_PRICE, stored.as_of, "model spec")
        return pa.PriceResolution(ticker=str(company_name or ""),
                                  currency=str(getattr(spec, "currency", "") or ""))

    def _correction_records(
        self, spec, challenges, verified, unverified=None, structural=None
    ) -> list[dict]:
        """The correction ledger: what was proposed, accepted and propagated.

        One record per challenge, with the original value read from v1, the
        corrected value that was applied, the evidence, the affected fields and
        the downstream outputs. The Model Controller accepts only a correction
        the kernel applied; a correction whose proposed value contradicts the
        model is rejected; anything ambiguous stays unresolved and is scenario
        basis, never a silent value.
        """
        from tradingagents.research import repair as repair_mod

        accepted = {id(challenge) for challenge in verified or []}
        rejected = {id(challenge) for challenge in unverified or []}
        records: list[dict] = []
        for challenge in challenges or []:
            paths = list(getattr(challenge, "proposed_values", {}) or [])
            if id(challenge) in accepted:
                status = "ACCEPTED"
            elif id(challenge) in rejected and paths:
                status = "REJECTED"
            else:
                status = "UNRESOLVED"
            records.append({
                "challenge_type": getattr(challenge, "challenge_type", "") or "unspecified",
                "severity": getattr(challenge, "severity", "") or "material",
                "status": status,
                "accepted_by": "model_controller" if status != "UNRESOLVED" else "",
                "affected_fields": list(getattr(challenge, "affected_fields", []) or []),
                "downstream_outputs": list(
                    getattr(challenge, "blocked_outputs", []) or []
                ) or list(getattr(challenge, "affected_fields", []) or []),
                "original_value": (
                    {path: repair_mod.read_value(spec, path) for path in paths}
                    if spec is not None else dict.fromkeys(paths)
                ),
                "corrected_value": dict(getattr(challenge, "proposed_values", {}) or {}),
                "evidence_refs": list(getattr(challenge, "evidence_refs", []) or []),
                "stated_model_value": getattr(challenge, "stated_model_value", None),
                "source_class": getattr(challenge, "source_class", "") or "",
                "as_of": getattr(challenge, "as_of", "") or "",
                "rejection_reason": getattr(challenge, "rejection_reason", "") or "",
                "propagated": status == "ACCEPTED",
                "scenario_basis": status == "UNRESOLVED",
            })
        return records

    def _resolve_model_status(self, state, spec, segment_status: str, dominant: bool) -> str:
        """The one final EV/multiple/equity/per-share status object."""
        try:
            from tradingagents.research import (
                assumptions as assumptions_module,
                capital as capital_module,
                resolved_status as resolved_module,
            )

            try:
                methods = json.loads(str(state.get("valuation_method_statuses") or "{}"))
            except ValueError:
                methods = {}
            _ev, _pe, gap = capital_module.cross_method_basis(spec)
            controlling_method, controlling_conflict = controlling_valuation_resolution(spec)
            resolved = resolved_module.resolve(
                spec=spec,
                statuses={
                    "valuation_method_statuses": methods,
                    "ev_per_share_status": state.get("ev_per_share_status", ""),
                },
                assumption=assumptions_module.for_spec(spec, "ev_ebitda"),
                share_mismatch=bool(gap is not None and gap > 0.01),
                convertible_unresolved=bool(capital_module.unsettled_convertibles(spec)),
                dominant_segment_detected=dominant,
                segment_status=segment_status,
                deterministic_checks_executed=bool(
                    state.get("deterministic_checks_executed", True)
                ),
                trigger_taxonomy_version=int(
                    getattr(spec, "trigger_taxonomy_version", 2)
                ),
                legacy_rendered=False,
                sensitivity_conflict=bool(state.get("sensitivity_conflict")),
                controlling_method=controlling_method,
                controlling_conflict=controlling_conflict,
            )
            return json.dumps(resolved.to_dict())
        except Exception as exc:  # noqa: BLE001 - a status may not break the build
            logger.info("Resolved status could not be recomputed: %s", exc)
            return str(state.get("resolved_status") or "{}")

    def _revalidate_current_evidence(self, state, spec, report_text: str):
        """Deterministic segment and driver checks, before any LLM challenge call.

        The cached spec is provisional until the current Fundamentals output is
        audited. This runs at the reviewer's entry, after every analyst has filed.
        """
        from tradingagents.research import (
            drivers,
            issues as issues_module,
            segment_evidence as segment_module,
            segments as segments_module,
        )

        deterministic = issues_module.collect(spec)
        drivers.normalize(spec)
        raw = state.get("segment_evidence")
        if raw:
            try:
                evidence = segment_module.SegmentEvidence.from_dict(json.loads(str(raw)))
            except (TypeError, ValueError):
                evidence = segment_module.extract(report_text)
        else:
            evidence = segment_module.extract(report_text)
        threshold = float((getattr(self, "config", None) or {}).get(
            "segment_profit_share_threshold", segment_module.DEFAULT_THRESHOLD
        ))
        audit = segment_module.audit(spec, evidence, threshold)
        limitation = segment_module.limitation(audit)
        published = segment_module.published_status(audit)
        classification = segment_module.classify(audit)
        controlling = segment_module.is_controlling(spec)
        controlling_method, controlling_conflict = controlling_valuation_resolution(spec)
        ev_decision_use = (
            "CONTROLLING"
            if controlling_method == "ev_ebitda"
            else "CROSS_CHECK_ONLY"
        )
        segment_table = segments_module.table(spec)
        resolved = self._resolve_model_status(
            state, spec, published,
            bool(audit.segment) and float(audit.share or 0) >= threshold,
        )
        package = str(state.get("research_package") or "")
        artifact = str(state.get("research_package_artifact") or "")
        text_updates: dict = {}
        if limitation and package.strip():
            # The package the debate and the PM read must state the same
            # limitation as the report, or a bottom-up claim survives the
            # evidence audit in the only copy those agents ever see. Re-entry
            # replaces the previous limitation instead of stacking another, and
            # the segment table is re-appended because the cut removes it too.
            suffix = limitation + (
                f"\n\n{segment_table}" if segment_table else ""
            )
            package = without_existing_segment_limitation(
                segments_module.neutralise_bottom_up(package)
            )
            artifact = without_existing_segment_limitation(
                segments_module.neutralise_bottom_up(artifact)
            )
            package = package.rstrip() + "\n\n" + suffix
            artifact = artifact.rstrip() + "\n\n" + suffix
            text_updates = {
                "research_package": package,
                "research_package_artifact": artifact,
            }
        return {
            **text_updates,
            "resolved_status": resolved,
            "segment_evidence": json.dumps(evidence.to_dict()),
            "segment_evidence_source": evidence.source,
            "segment_evidence_schema_missing": evidence.schema_missing,
            "segment_reconciliation_status": published,
            "segment_reconciliation_detail": audit.detail,
            "segment_requirement_provisional": False,
            "segment_limitation": limitation,
            "segment_build_type": classification.build_type,
            "bottom_up_claim_allowed": classification.bottom_up_claim_allowed,
            "operating_model_confidence": classification.operating_model_confidence,
            "segment_reconciliation_controlling": controlling,
            "controlling_valuation_method": controlling_method,
            "controlling_valuation_conflict": controlling_conflict,
            "ev_decision_use": ev_decision_use,
            "dominant_segment": audit.segment,
            "dominant_segment_share": audit.share,
            "segment_table": segment_table,
            "deterministic_issues": json.dumps(
                [issue.to_dict() for issue in deterministic]
            ),
            "deterministic_checks_executed": True,
        }, deterministic

    def run_repair_gate(self, state) -> dict:
        """Repair the model before the debate, inside the reviewer's entry path.

        The Fundamentals Analyst's report is the evidence for the repair: it names
        the fields it believes are wrong. This step extracts those challenges as a
        bounded structured call, applies the scalar corrections to a copy of the
        spec, re-runs the kernel, and publishes a v2 package plus a repair note.
        A structural challenge is recorded as unresolved; a repair that breaks an
        identity or cannot be applied leaves no model to value the decision, which
        the report states rather than hides.

        No graph node or edge is added: this runs inside the existing Adversarial
        Reviewer transition, and is idempotent because ``active_model_version``
        is already ``v2`` on a re-entry or a resumed checkpoint.
        """
        company = str(state.get("company_of_interest") or "")
        trade_date = str(state.get("trade_date") or "")
        spec_path = str(state.get("research_model_path") or "")
        report_text = str(state.get("fundamentals_report") or "").strip()
        if research_dir_from_env() is None or not spec_path:
            return {"fundamentals_challenge_check": "not_applicable",
                    "model_status": "NOT_APPLICABLE"}
        from tradingagents.research import (
            repair as repair_mod,
            report as research_report,
            valuation as valuation_mod,
        )

        try:
            spec = research_report.load_spec(spec_path)
        except Exception as exc:  # noqa: BLE001 - an unreadable spec is reported, not used
            return self._repair_failed(
                f"the model spec could not be read ({exc})", "repair_failed", [],
                corrections=[],
            )
        base_updates, deterministic = self._revalidate_current_evidence(
            state, spec, report_text
        )
        if str(state.get("active_model_version") or "") == "v2":
            return {
                **base_updates,
                "fundamentals_challenge_check": (
                    str(state.get("fundamentals_challenge_check") or "repaired")
                ),
                "model_status": "VERIFIED",
            }
        if not report_text:
            # No Fundamentals analyst in this run: current segment evidence is
            # unavailable and cannot be retried. The run stays available for
            # diagnostics, but the missing evidence blocks publication.
            selected = [str(item) for item in (state.get("analysts") or [])]
            missing_reason = (
                "no_fundamentals_analyst"
                if "fundamentals" not in selected
                else "fundamentals_report_empty"
            )
            try:
                self._research_model(company, trade_date,
                                     resolution=state_price_resolution(state),
                                     allow_build=False, spec_hint=spec_path)
            except _ModelUnavailable as exc:
                return {**base_updates, **self._repair_failed(str(exc), "kernel_only")}
            return {
                **base_updates,
                "fundamentals_challenge_check": "kernel_only_pass",
                "model_status": "VERIFIED",
                "segment_evidence_retry": json.dumps({
                    "attempted": False,
                    "reason": missing_reason,
                    "valid_after_retry": False,
                    "findings": [
                        "SEGMENT_EVIDENCE_SCHEMA_MISSING: no Fundamentals report "
                        "was produced, so no current segment evidence exists"
                    ],
                }),
            }

        llm = getattr(self, "deep_thinking_llm", None) or getattr(self, "quick_thinking_llm", None)
        if llm is None:
            return {**base_updates, "fundamentals_challenge_check": "not_applicable"}

        package = str(state.get("research_package") or "")
        status, challenges = self._extract_challenges(llm, report_text, package)
        if status == "failure":
            # An extraction miss is not "no challenges": the review was never
            # confirmed, so no model figure may drive the decision.
            return {
                **base_updates,
                **self._repair_failed(
                    "Analyst challenge review was not confirmed "
                    "(structured extraction failed twice)",
                    "extraction_failed", challenges,
                    corrections=self._correction_records(None, challenges, []),
                ),
            }
        if status == "no_challenges_found" or not challenges:
            return {
                **base_updates,
                "fundamentals_challenge_check": "no_challenges_found",
                "model_challenges": repair_mod.to_json(challenges),
                "unresolved_material": sum(
                    1 for issue in deterministic if issue.severity == "material"
                ),
            }

        from tradingagents.research import (
            issues as issues_module,
            price_authority as price_authority_module,
        )

        current_price = price_authority_module.PriceResolution.from_state(state)
        deterministic_counts = issues_module.counts(deterministic)
        verified, unverified, structural = repair_mod.verify_challenges(
            spec, challenges, price_resolution=current_price
        )
        authority_conflict = "; ".join(
            challenge.rejection_reason
            for challenge in unverified
            if challenge.rejection_reason.startswith("AUTHORITY_CONFLICT")
        )
        authority_extra = (
            {"price_authority_conflict": authority_conflict} if authority_conflict else {}
        )
        corrections = self._correction_records(spec, challenges, verified, unverified, structural)
        unresolved = structural + unverified
        unresolved_blocking = sum(1 for c in unresolved if c.severity == "blocking")
        unresolved_material = (
            sum(1 for c in unresolved if c.severity == "material")
            + deterministic_counts.get("material", 0)
        )
        if unresolved_blocking:
            # An extracted "blocking" that could not be verified must not destroy
            # a valid model: only a verified blocking defect (or a repair that
            # fails revalidation) withholds the rating. Disclose it as material.
            logger.warning(
                "Downgrading %d unverified blocking challenge(s) to material: %s",
                unresolved_blocking,
                "; ".join(c.challenge_type for c in unresolved if c.severity == "blocking"),
            )
            unresolved_material += unresolved_blocking
            unresolved_blocking = 0
        if not verified:
            texts = [c.proposed_correction or c.challenge_type for c in unresolved]
            return {
                "fundamentals_challenge_check": "repair_unresolved",
                "model_challenges": repair_mod.to_json(
                    challenges, issues=[issue.to_dict() for issue in deterministic]
                ),
                "model_repair_log": json.dumps({
                    "changes": [], "unresolved": texts,
                    "unresolved_blocking": 0, "unresolved_material": unresolved_material,
                }),
                "model_repair_addendum": repair_mod.render_addendum([], texts),
                "unresolved_material": unresolved_material,
                "model_corrections": json.dumps(corrections),
                **authority_extra,
                **base_updates,
            }

        try:
            repaired, result, changes = repair_mod.repair_model(
                spec, verified, price_resolution=current_price
            )
        except Exception as exc:  # noqa: BLE001 - repair is optional, disclosure is not
            blocking = any(c.severity == "blocking" for c in verified)
            logger.warning("Model repair did not apply: %s", exc)
            if blocking:
                return {
                    **base_updates,
                    **self._repair_failed(
                        f"a blocking challenge could not be repaired ({exc})",
                        "repair_failed", challenges, corrections=corrections,
                    ),
                }
            texts = [c.proposed_correction or c.challenge_type for c in unresolved]
            return {
                "fundamentals_challenge_check": "repair_unresolved",
                "model_challenges": repair_mod.to_json(
                    challenges, issues=[issue.to_dict() for issue in deterministic]
                ),
                "model_repair_log": json.dumps({
                    "changes": [], "unresolved": texts,
                    "unresolved_blocking": 0, "unresolved_material": unresolved_material,
                }),
                "model_repair_addendum": repair_mod.render_addendum([], texts),
                "unresolved_material": unresolved_material,
                "model_corrections": json.dumps(corrections),
                **authority_extra,
                **base_updates,
            }

        valuation = valuation_mod.value(result)
        accepted_price = next(
            (
                challenge for challenge in verified
                if "valuation.price" in (challenge.proposed_values or {})
            ),
            None,
        )
        if accepted_price is not None:
            source = str(accepted_price.source_class or "").strip().upper()
            if source not in price_authority_module.SOURCES:
                source = price_authority_module.REPORT_PRICE
            repaired.valuation.price_source = source
            repaired.valuation.price_as_of = str(accepted_price.as_of or "")
            repaired.valuation.price_ref = str(accepted_price.source_ref or "")
        elif current_price.source:
            repaired.valuation.price_source = current_price.source
            repaired.valuation.price_as_of = current_price.as_of
            repaired.valuation.price_ref = current_price.ref
        v2_path = self._models_dir() / f"{safe_ticker_component(company)}.v2.json"
        v2_path.write_text(
            json.dumps(repaired.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        # The repaired model gets its own sensitivities: v1's table would price
        # the superseded share count and EPS.
        from tradingagents.research import (
            assumptions as assumptions_module,
            capital as capital_module,
            sensitivity as sensitivity_module,
        )

        eps_ok = capital_module.eps_basis_ready(repaired, valuation.period)[0]
        cap_ok = capital_module.market_cap_ready(repaired)[0]
        equity_ok = capital_module.equity_basis_ready(repaired)[0]
        items, gaps = sensitivity_module.rank(
            repaired, top=3, eps_verified=eps_ok, pe_verified=eps_ok
        )
        gaps = sensitivity_module.filter_gaps(repaired, items, gaps)
        mapping = sensitivity_module.mapping_summary(repaired, items, gaps)
        table = sensitivity_module.render(items)
        propagation_ok = all(
            item.verification_status == "VERIFIED" for item in items
        )
        controlling_rows = [item for item in items if item.controlling_candidate]
        controlling_verified = sum(1 for item in controlling_rows if item.decision_eligible)
        controlling_expected = len(controlling_rows)
        controlling_ok = controlling_expected > 0 and controlling_verified == controlling_expected
        failed_rows = sum(1 for item in items if not item.decision_eligible)
        low, high = valuation.fair_value_low, valuation.fair_value_high
        from tradingagents.research import status as status_module
        from tradingagents.research.shares import basis_report, diluted_kind

        bases = basis_report(repaired)
        statuses = status_module.assess(
            model_status="VERIFIED",
            basis=bases,
            has_band=low is not None and high is not None,
            challenge_fields=[
                field for challenge in unresolved for field in challenge.affected_fields
            ],
            sensitivity_count=len(items),
            propagation_ok=propagation_ok,
            controlling_propagation_ok=controlling_ok,
            price=valuation.price,
            has_depreciation=bool(repaired.cashflow),
            has_net_debt=bool(
                repaired.net_debt is not None and repaired.net_debt.components_verified()
            ),
            ev_ebitda_multiple=repaired.valuation.ev_ebitda_multiple,
            capital_verified=eps_ok,
            eps_basis_quality=diluted_kind(repaired),
            market_cap_verified=cap_ok,
            eps_basis_verified=eps_ok,
            equity_basis_verified=equity_ok,
            share_price_verified=current_price.verified,
        )
        assumption = assumptions_module.controlling(
            repaired, statuses["valuation_method_statuses"]
        )
        if statuses["valuation_method_statuses"].get("ev_ebitda") != status_module.VERIFIED:
            statuses["ev_per_share_status"] = status_module.UNAVAILABLE
            cross_reason = "EV/EBITDA method unavailable"
        elif not valuation.cross_method_comparable:
            ev_count, pe_count, gap = capital_module.cross_method_basis(repaired)
            statuses["ev_per_share_status"] = status_module.UNVERIFIED
            cross_reason = (
                f"EV per-share uses {ev_count:,.1f}m shares; P/E uses {pe_count:,.1f}m "
                f"({gap:.1%} apart); cross-method comparison unverified"
                if gap is not None and ev_count and pe_count
                else "share bases are not reconciled"
            )
        else:
            cross_reason = ""
        diagnostics = self._ev_diagnostics(repaired, valuation.period, company, trade_date)
        if cross_reason:
            diagnostics = {**diagnostics, "cross_method_basis": cross_reason}
        include_per_share = statuses["eps_model_status"] == status_module.VERIFIED
        include_ev = (
            statuses["valuation_method_statuses"].get("ev_ebitda") == status_module.VERIFIED
        )
        include_ev_per_share = bool(
            include_ev
            and valuation.ev_per_share is not None
            and valuation.ev_per_share_reconciled
        )
        package_v2 = research_report.compact_summary(
            result, valuation, include_per_share=include_per_share,
            include_ev_ebitda=include_ev, include_ev_per_share=include_ev_per_share,
        )
        artifact_v2 = research_report.compact_summary(
            result, valuation, instructions=False, include_per_share=include_per_share,
            include_ev_ebitda=include_ev, include_ev_per_share=include_ev_per_share,
        )
        if table:
            gaps_line = (
                f"\n\nResearch gaps (no explicit model assumption): {', '.join(gaps)}."
                if gaps else ""
            )
            package_v2 = f"{package_v2}\n\n{table}{gaps_line}"
            artifact_v2 = f"{artifact_v2}\n\n{table}{gaps_line}"

        revalidation_state = {
            **state,
            "research_package": package_v2,
            "research_package_artifact": artifact_v2,
            "valuation_method_statuses": json.dumps(
                statuses["valuation_method_statuses"]
            ),
            "ev_per_share_status": statuses.get("ev_per_share_status", ""),
            "deterministic_checks_executed": True,
            "sensitivity_conflict": any(
                item.sensitivity_id in " ".join(gaps)
                or any(alias and alias in " ".join(gaps) for alias in item.aliases)
                for item in items
            ),
        }
        segment_updates, _segment_issues = self._revalidate_current_evidence(
            revalidation_state, repaired, report_text
        )
        package_v2 = segment_updates.get("research_package", package_v2)
        artifact_v2 = segment_updates.get("research_package_artifact", artifact_v2)

        unresolved_texts = [c.proposed_correction or c.challenge_type for c in unresolved]
        addendum = self._repair_addendum(llm, report_text, changes, result, unresolved_texts)
        logger.info("Model repaired for %s: %s field(s) changed", company, len(changes))
        try:
            pre_status = json.loads(str(state.get("component_status") or "{}"))
        except ValueError:
            pre_status = {}
        repair_id = "repair:" + hashlib.sha256(
            json.dumps(changes, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        post_status = build_component_status(
            statuses, propagation_ok=propagation_ok, diagnostics=diagnostics,
            controlling={
                "status": statuses.get("controlling_sensitivity_status", ""),
                "verified": controlling_verified,
                "expected": controlling_expected,
            },
            failed_rows=failed_rows,
        )
        component = repaired_status_object(
            pre_status, post_status, repair_id,
            datetime.now().isoformat(timespec="seconds"),
        )
        return {
            "fundamentals_challenge_check": "repaired",
            "active_model_version": "v2",
            "research_model_path_v1": spec_path,
            "research_model_path": str(v2_path),
            "research_model_source": "repaired",
            "research_package": package_v2,
            "research_package_artifact": artifact_v2,
            "fundamental_packet": research_report.compact_summary(
                result, valuation, instructions=False,
                include_per_share=False, include_ev_ebitda=False, include_valuation=False,
            ),
            "valuation_packet": research_report.valuation_packet(result, valuation, statuses),
            "research_price": valuation.price,
            "price_source": repaired.valuation.price_source,
            "price_as_of": repaired.valuation.price_as_of,
            "price_ref": repaired.valuation.price_ref,
            "research_fair_value_low": low,
            "research_fair_value_high": high,
            "research_fair_value_verified": statuses["valuation_controlling"],
            "operating_model_status": statuses["operating_model_status"],
            "eps_model_status": statuses["eps_model_status"],
            "eps_status": statuses["eps_model_status"],
            "valuation_method_statuses": json.dumps(statuses["valuation_method_statuses"]),
            "valuation_status": statuses["valuation_status"],
            "calculation_status": statuses["valuation_status"],
            "assumption_status": assumption.status if assumption else "",
            "assumption_detail": json.dumps(assumption.to_dict()) if assumption else "",
            "valuation_wording": assumptions_module.wording(
                statuses["valuation_status"], assumption
            ),
            "segment_reconciliation_status": segment_updates["segment_reconciliation_status"],
            "segment_reconciliation_detail": segment_updates["segment_reconciliation_detail"],
            "segment_build_type": segment_updates["segment_build_type"],
            "bottom_up_claim_allowed": segment_updates["bottom_up_claim_allowed"],
            "operating_model_confidence": segment_updates["operating_model_confidence"],
            "segment_reconciliation_controlling": segment_updates[
                "segment_reconciliation_controlling"
            ],
            "controlling_valuation_method": segment_updates[
                "controlling_valuation_method"
            ],
            "controlling_valuation_conflict": segment_updates[
                "controlling_valuation_conflict"
            ],
            "ev_decision_use": segment_updates["ev_decision_use"],
            "segment_limitation": segment_updates["segment_limitation"],
            "dominant_segment": segment_updates["dominant_segment"],
            "dominant_segment_share": segment_updates["dominant_segment_share"],
            "segment_table": segment_updates["segment_table"],
            "resolved_status": segment_updates["resolved_status"],
            "deterministic_issues": segment_updates["deterministic_issues"],
            "deterministic_checks_executed": True,
            "sensitivity_model_status": statuses["sensitivity_model_status"],
            "sensitivity_status": statuses["sensitivity_model_status"],
            "model_node_sensitivity_status": statuses["sensitivity_model_status"],
            "economic_driver_mapping_status": mapping[
                "economic_driver_mapping_status"
            ],
            "failed_driver_mappings": mapping["failed_driver_mappings"],
            "failed_driver_mapping_ids": json.dumps(
                mapping["failed_driver_mapping_ids"]
            ),
            "sensitivity_table": table,
            "sensitivity_ids": json.dumps([
                item.sensitivity_id for item in items if item.decision_eligible
            ]),
            "sensitivity_alias_map": json.dumps(sensitivity_module.alias_map(items)),
            "component_status": json.dumps(component),
            "sensitivity_gaps": json.dumps(gaps),
            "market_cap_basis": bases["market_cap_basis"],
            "forecast_eps_basis": bases["forecast_eps_basis"],
            "dilution_sensitivity_basis": bases["dilution_sensitivity_basis"],
            "equity_value_share_basis": bases["equity_value_share_basis"],
            "repairs_applied": len(changes),
            "unresolved_blocking": 0,
            "unresolved_material": unresolved_material,
            "model_status": "VERIFIED",
            "model_challenges": repair_mod.to_json(
                challenges, changes, [issue.to_dict() for issue in deterministic]
            ),
            "model_repair_log": json.dumps({
                "changes": changes,
                "unresolved": unresolved_texts,
                "unresolved_blocking": 0,
                "unresolved_material": unresolved_material,
            }),
            "model_repair_addendum": addendum,
            "model_corrections": json.dumps(corrections),
            **authority_extra,
            **base_updates,
        }

    def _extract_challenges(self, llm, report_text: str, package: str) -> tuple[str, list]:
        """(status, challenges): one bounded call, one retry, then failure.

        ``failure`` is distinct from "the analyst found nothing": an unparseable
        answer, a prose fallback or a response without an explicit status means
        the review was never confirmed.
        """
        from tradingagents.agents.structured import (
            STRUCTURED_RETRY,
            bind_structured,
            invoke_structured,
        )
        from tradingagents.research.repair import (
            EXTRACTION_PROMPT,
            ModelChallenge,
            ModelChallengeList,
        )

        structured = bind_structured(llm, ModelChallengeList, "Model Repair Gate")
        if structured is None:
            return "failure", []
        prompt = EXTRACTION_PROMPT.replace("{package}", package[:6000]).replace(
            "{report}", report_text[:12000]
        )
        result = invoke_structured(structured, prompt, "Model Repair Gate")
        if result is None:
            result = invoke_structured(
                structured, prompt + STRUCTURED_RETRY, "Model Repair Gate"
            )
        if result is None:
            return "failure", []
        challenges = [
            challenge
            for challenge in getattr(result, "challenges", [])
            if isinstance(challenge, ModelChallenge)
        ][:8]
        status = str(getattr(result, "status", "") or "")
        if status == "no_challenges_found":
            return "no_challenges_found", []
        if status != "challenges_found":
            return "failure", challenges
        return "challenges_found", challenges

    def _repair_addendum(self, llm, report_text: str, changes: list[dict],
                         result, unresolved: list[str]) -> str:
        """A model-written note on affected conclusions, or the deterministic one.

        The model-written version is accepted only when every number it introduces
        is a value from the before/after models; otherwise the deterministic list
        of changed fields is published.
        """
        from tradingagents.agents.structured import bind_structured, invoke_structured
        from tradingagents.research import repair as repair_mod
        from tradingagents.research.repair import ADDENDUM_PROMPT, ModelRepairAddendum

        allowed = repair_mod.numbers_in(str(changes))
        for row in result.rows:
            allowed.update(v for v in (row.revenue, row.operating_profit, row.net_profit) if v)
            allowed.update(value for value in row.eps.values() if value)
        # The prompt shows the values as the tables print them; accept that
        # rounding in the answer as well as the raw float.
        allowed.update(round(float(value), 1) for value in list(allowed))
        structured = bind_structured(llm, ModelRepairAddendum, "Model Repair Addendum")
        if structured is not None:
            prompt = (
                ADDENDUM_PROMPT.replace(
                    "{changes}", "\n".join(
                        f"- {c['field']}: {repair_mod.shown(c.get('old'))} -> "
                        f"{repair_mod.shown(c.get('new'))}"
                        for c in changes
                    )
                )
                .replace("{conclusions}", report_text[:8000])
            )
            addendum = invoke_structured(structured, prompt, "Model Repair Addendum")
            if addendum is not None and repair_mod.addendum_is_grounded(addendum, allowed):
                return repair_mod.render_conclusions(addendum, changes)
            if addendum is not None:
                logger.warning("Repair addendum introduced an unverified figure; using the diff")
        return repair_mod.render_addendum(changes, unresolved)

    def _repair_failed(self, reason: str, stage: str, challenges=None, corrections=None) -> dict:
        """No usable model after a repair attempt: say so and let the run continue."""
        from tradingagents.research import repair as repair_mod

        logger.warning("Model repair gate: %s", reason)
        blocking = sum(1 for c in list(challenges or []) if getattr(c, "severity", "") == "blocking")
        material = sum(1 for c in list(challenges or []) if getattr(c, "severity", "") == "material")
        issues = [
            {
                "challenge_type": getattr(c, "challenge_type", "") or "unspecified",
                "affected_fields": list(getattr(c, "affected_fields", []) or []),
                "verification_result": "unresolved",
                "repair_attempted": bool(getattr(c, "proposed_values", {})),
                "failure_reason": reason,
                "publication_impact": "model EPS, valuation and rating suppressed",
            }
            for c in list(challenges or [])
            if getattr(c, "severity", "") in {"blocking", "material"}
        ]
        return {
            "fundamentals_challenge_check": stage,
            "active_model_version": "unverified",
            "research_model_source": "unavailable",
            "research_package": "",
            "research_package_artifact": "",
            "model_derived_facts": "",
            "research_fair_value_low": None,
            "research_fair_value_high": None,
            "spec_compatibility": "unavailable",
            "spec_compatibility_reason": reason,
            "model_status": "UNVERIFIED",
            "overall_status": "Not Rated",
            "decision_status": "Provisional",
            # Current-run propagation: a stale VERIFIED from an earlier model may
            # not survive a failed gate. Every dependent output is NOT_TESTED.
            "operating_model_status": "NOT_TESTED",
            "eps_model_status": "NOT_TESTED",
            "eps_status": "NOT_TESTED",
            "valuation_status": "NOT_TESTED",
            "calculation_status": "NOT_TESTED",
            "sensitivity_model_status": "NOT_TESTED",
            "sensitivity_status": "NOT_TESTED",
            "valuation_method_statuses": "{}",
            "ev_input_diagnostics": "{}",
            "market_cap_basis": "",
            "forecast_eps_basis": "",
            "dilution_sensitivity_basis": "",
            "equity_value_share_basis": "",
            "assumption_status": "",
            "assumption_detail": "",
            "valuation_wording": "Not tested in this run: the deterministic model is unavailable.",
            "segment_reconciliation_status": "NOT_TESTED",
            "segment_reconciliation_detail": reason,
            "dominant_segment": "",
            "dominant_segment_share": None,
            "segment_table": "",
            "research_fair_value_verified": False,
            "controlling_fair_value_low": None,
            "controlling_fair_value_base": None,
            "controlling_fair_value_high": None,
            "fundamental_packet": "",
            "valuation_packet": "",
            "benchmark_basis": "Benchmark: not established; the model is unavailable.",
            "repairs_applied": 0,
            "unresolved_blocking": blocking,
            "unresolved_material": material,
            "blocking_issue": json.dumps(issues, ensure_ascii=False),
            # A blocked model may not feed the debate through the back door.
            "sensitivity_table": "",
            "sensitivity_ids": "[]",
            "sensitivity_gaps": "[]",
            "case_table": "",
            "metrics_table": "",
            "model_challenges": repair_mod.to_json(list(challenges or [])),
            "model_corrections": json.dumps(list(corrections or [])),
            # A failed repair leaves every controlling component unverified; the
            # repaired numbers stay in diagnostics only.
            "component_status": json.dumps({
                "operating_forecast": {"status": "UNVERIFIED", "reason": reason},
                "forecast_eps": {"status": "UNVERIFIED", "reason": reason},
                "pe_valuation": {"status": "UNVERIFIED", "reason": reason},
                "ev_ebitda_method": {"status": "UNAVAILABLE", "reason": "model unverified"},
                "ev_per_share_comparison": {"status": "UNAVAILABLE", "reason": "model unverified"},
                "controlling_valuation": {"use": "none", "ev_controlling": False},
                "sensitivity": {"status": "UNAVAILABLE", "propagation_ok": False},
                "technical": {"status": "ADVISORY"},
                "repair_failure": {"stage": stage, "reason": reason},
            }),
        }

    def _memory_step(self, state):
        """The graph's Memory Log step, alongside the analysts (#1428): settle every
        ticker's due decisions (#1445), then return the lessons known by the
        trade date for the Portfolio Manager (#1251).

        Settling fetches prices and asks the model for a reflection per decision,
        so it runs beside the analysts instead of before them. A failure here
        does not end the run: the analysts' work is kept, the lessons already in
        the log are used, and the report says what could not be settled.
        """
        note = ""
        try:
            # Another run settling the same log does the work; this one goes on.
            done = self.settle_all_pending(wait=False)
            if done.failed:
                note = (f"{len(done.failed)} past decision(s) could not be settled this run "
                        "and stay pending.")
        except Exception as exc:
            logger.warning("Settling past decisions failed: %s", exc)
            note = f"Past decisions could not be settled this run ({type(exc).__name__}); they stay pending."
        try:
            past_context = self.memory_log.get_past_context(
                state["company_of_interest"], as_of=self._memory_as_of(state["trade_date"]))
        except Exception as exc:
            logger.warning("Reading the memory log failed: %s", exc)
            past_context = ""
            note = note or f"The memory log could not be read this run ({type(exc).__name__})."
        return {"past_context": past_context, "memory_note": note}

    def settle_pending(self, company_name) -> settlement.Settlement:
        """Settle this ticker's decisions whose holding window has now traded.

        A run settles every due decision alongside its analysts, so its own
        decision stays pending until a later run. A caller that is done
        analyzing a ticker (a backtest sweep, a scheduled job) calls this to
        settle it now. Returns what was settled and what failed.
        """
        with run_config(self.config):
            return settlement.settle_pending(company_name, self.memory_log, self.reflector, self.config)

    def settle_all_pending(self, wait: bool = True) -> settlement.Settlement:
        """Settle every ticker's decisions whose holding window has now traded (#1445).

        For a scheduler whose tickers rotate: a ticker it stops analysing would
        otherwise keep its decisions pending, and their lessons out of later runs.
        With ``wait=False`` a pass already running on the same log is not waited for.
        """
        with run_config(self.config):
            return settlement.settle_all_pending(self.memory_log, self.reflector, self.config, wait=wait)

    def record_decision(self, company_name, trade_date, final_state):
        """Record a finished run: its state log, and its decision in the memory log
        for reflection on the next same-ticker run. propagate() and the CLI both end here.

        A diagnostic run (``run_purpose`` other than ``production``) writes the
        state log but does not teach later runs: a reused specification whose
        dependencies were not verified must not become training feedback.
        """
        self._log_state(trade_date, final_state)
        purpose = str(self.config.get("run_purpose") or "production").strip().lower()
        if purpose != "production":
            logger.info("Run purpose %r: state log written, memory log untouched", purpose)
            return
        decision = final_state.get("final_trade_decision")
        if not decision:
            logger.warning("No final decision for %s on %s; nothing added to the memory log",
                           company_name, trade_date)
            return
        self.memory_log.store_decision(
            ticker=company_name, trade_date=trade_date, final_trade_decision=decision,
            rating=run_rating(final_state),
        )

    def _run_graph(self, company_name, trade_date, asset_type: str = "stock",
                   checkpoint_thread_id: str | None = None, portfolio=None):
        """Execute the graph and write the resulting state to disk and memory log."""
        init_agent_state = self.create_run_state(company_name, trade_date, asset_type, portfolio)
        args = self.propagator.get_graph_args()

        # Inject the checkpoint thread_id (from checkpoint_scope) so the same
        # ticker+date+graph-shape resumes; a different one starts fresh (#1089).
        if checkpoint_thread_id is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_thread_id

        # None resumes an existing checkpoint; init_agent_state starts fresh (#1249).
        graph_input = self.checkpoint_input(init_agent_state)
        if self.debug:
            # A state repeats the messages before it, so each prints once (#1027).
            final_state, printed = {}, set()
            for messages, state in self.stream_run(graph_input, **args):
                for msg in messages:
                    key = getattr(msg, "id", None) or (type(msg).__name__, getattr(msg, "content", None))
                    if key not in printed:
                        printed.add(key)
                        msg.pretty_print()
                if state is not None:
                    final_state.update(state)
        else:
            final_state = self.graph.invoke(graph_input, **args)

        # Audit and repair before recording: the memory entry and the state log
        # must carry the same text the published report does (#finding B5).
        self.prepare_final_state(final_state, company_name)
        self.record_decision(company_name, trade_date, final_state)

        # Clear checkpoint on successful completion to avoid stale state.
        self.clear_checkpoint_on_success(company_name, trade_date, asset_type, portfolio)

        return final_state, run_rating(final_state)

    def stream_run(self, graph_input, **args):
        """Stream a run as ``(messages, state)`` pairs.

        ``messages`` are the agents' messages, the analysts' included. ``state``
        is the run's state after a top-level step; for a step inside an analyst's
        graph it is that analyst's report once filed, else None.

        Each analyst works in a graph of its own, and the run's state takes the
        analysts' reports only when the slowest has finished, so their messages
        and reports come from their own finished steps ("tasks") as they happen.
        """
        args = {**args, "stream_mode": ["values", "tasks"]}
        # The graph's own config serves every tool call, as in propagate(), even
        # when the process-wide config has changed since. Each step runs in the
        # run's context, so the caller keeps its own between steps.
        context = run_config_context(self.config)
        stream = context.run(self.graph.stream, graph_input, subgraphs=True, **args)
        try:
            while (step := context.run(next, stream, None)) is not None:
                namespace, mode, chunk = step
                if namespace:
                    result = chunk.get("result") if mode == "tasks" else None
                    if isinstance(result, dict):
                        report = {k: v for k, v in result.items() if k != "messages" and v}
                        if result.get("messages") or report:
                            yield result.get("messages", []), report or None
                elif mode == "values":
                    yield chunk.get("messages", []), chunk
        finally:
            context.run(stream.close)

    def _log_state(self, trade_date, final_state):
        """Write a run's final state to JSON under the run's own ticker."""
        entry = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "challenge_history": final_state["investment_debate_state"].get("challenge_history", ""),
                "first_review": final_state["investment_debate_state"].get("first_review", ""),
                "first_review_raw": final_state["investment_debate_state"].get(
                    "first_review_raw", ""
                ),
                "unresolved_issue": final_state["investment_debate_state"].get(
                    "unresolved_issue", ""
                ),
                "review_missing": final_state["investment_debate_state"].get(
                    "review_missing", ""
                ),
                "no_material_update": final_state["investment_debate_state"].get(
                    "no_material_update", False
                ),
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
            },
            "trader_investment_plan": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
            "final_rating": run_rating(final_state),
            # The research layer and the audit, so the state log is the same
            # frozen artifact the report was rendered from.
            "research_package": final_state.get("research_package", ""),
            "research_package_artifact": final_state.get("research_package_artifact", ""),
            "research_price": final_state.get("research_price"),
            "research_model_path": final_state.get("research_model_path", ""),
            "stale_model_path": final_state.get("stale_model_path", ""),
            "report_audit": final_state.get("report_audit", ""),
            "case_table": final_state.get("case_table", ""),
            "decision_horizon": final_state.get("decision_horizon", ""),
            "decision_integrity": final_state.get("decision_integrity", ""),
            "research_rounds_used": final_state.get("research_rounds_used"),
            "risk_rounds_used": final_state.get("risk_rounds_used"),
            "research_model_source": final_state.get("research_model_source", ""),
            "research_model_created_at": final_state.get("research_model_created_at", ""),
            "spec_compatibility": final_state.get("spec_compatibility", ""),
            "spec_compatibility_reason": final_state.get("spec_compatibility_reason", ""),
            "structured_fallbacks": final_state.get("structured_fallbacks", {}),
            "run_purpose": self.config.get("run_purpose"),
            "run_settings": self.run_settings(),
        }

        # A ticker that would escape the results directory is rejected.
        safe_ticker = safe_ticker_component(final_state["company_of_interest"])
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            # Reports can be in any language and this file is read by a person.
            json.dump(entry, f, indent=4, ensure_ascii=False)
