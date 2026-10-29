import hashlib
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import tradingagents
from tradingagents.agents.context import build_instrument_context, resolve_instrument_identity
from tradingagents.agents.rating import run_rating
from tradingagents.dataflows.config import run_config, run_config_context, set_config
from tradingagents.dataflows.date_window import get_current_date, is_historical
from tradingagents.dataflows.symbols import safe_ticker_component
from tradingagents.dataflows.vendors.research_corpus import (
    research_dir_from_env,
    resolve_corpus_root,
)
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients import create_tier_client, tier_provider
from tradingagents.memory import TradingMemoryLog, settlement
from tradingagents.memory.reflection import Reflector
from tradingagents.reporting import write_report_tree

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .propagation import Propagator
from .setup import GraphSetup

logger = logging.getLogger(__name__)


class _ModelUnavailable(RuntimeError):
    """The research model cannot be used for this run; the text is the reason."""


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
        self.workflow = self.graph_setup.setup_graph(selected_analysts, memory_node=self._memory_step)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None
        self._resuming = False

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
            "output_language": cfg.get("output_language"),
            "data_vendors": dict(cfg.get("data_vendors") or {}),
            "tool_vendors": dict(cfg.get("tool_vendors") or {}),
        }

    def save_reports(self, final_state, ticker, save_path=None, html=True) -> Path:
        """Write the report tree for a completed run, like the CLI does.

        Programmatic callers get the same on-disk reports the CLI produces. Pass
        an explicit ``save_path`` or let it default under ``results_dir``; the
        report is also written as one HTML page unless ``html`` is False.

        When a research model was used, the prose is audited against it first and
        the findings are written as a section: a figure an agent restated wrongly
        or took from a conflicting vendor snapshot is named rather than inherited.
        """
        if save_path is None:
            save_path = self.default_report_path(ticker)
        if research_dir_from_env() is not None and not final_state.get("report_audit"):
            audit = self.report_audit(final_state, ticker)
            if audit:
                final_state["report_audit"] = audit
        if final_state.get("debate_cases") and not final_state.get("case_table"):
            try:
                model, valuation, _spec = self._research_model(
                    ticker,
                    final_state.get("trade_date"),
                    final_state.get("research_price") or None,
                    allow_build=False,
                    spec_hint=final_state.get("research_model_path") or None,
                )
                from tradingagents.research import cases as case_module

                final_state["case_table"] = case_module.render_results(
                    model, valuation, json.loads(final_state["debate_cases"])
                )
            except Exception as exc:  # noqa: BLE001 - a report must still be written
                logger.warning("Case comparison could not be computed: %s", exc)
        return write_report_tree(final_state, ticker, save_path, settings=self.run_settings(), html=html)

    def report_audit(self, final_state, company_name) -> str:
        """Compare the figures in the prose with the model's figures."""
        try:
            model, valuation, _spec = self._research_model(
                company_name,
                final_state.get("trade_date"),
                final_state.get("research_price") or None,
                allow_build=False,
                spec_hint=final_state.get("research_model_path") or None,
            )
        except _ModelUnavailable:
            return ""
        from tradingagents.research import audit

        stats: dict = {}
        findings = audit.audit_state(final_state, model, valuation, stats=stats)
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
        )
        # Empty unless a corpus is configured, so vanilla runs keep the same state.
        package, price, spec_path = self._research_package(company_name, trade_date)
        state["research_package"] = package
        state["research_price"] = price or 0.0
        state["research_model_path"] = spec_path
        return state

    def research_package(self, company_name: str, trade_date: str | None = None,
                         price: float | None = None) -> str:
        """The verified fundamental model for this ticker, or a note saying why not.

        Opt-in: returns "" unless ``TRADINGAGENTS_RESEARCH_DIR`` names a corpus. A
        spec that fails its arithmetic checks becomes a labelled gap, never a set
        of numbers the analyst might treat as correct.
        """
        return self._research_package(company_name, trade_date, price)[0]

    def _research_package(self, company_name: str, trade_date: str | None = None,
                          price: float | None = None) -> tuple[str, float, str]:
        """The package text, the share price used, and the spec path behind them."""
        root = research_dir_from_env()
        if root is None:
            return "", 0.0, ""

        try:
            model, valuation, spec_path = self._research_model(company_name, trade_date, price)
        except _ModelUnavailable as exc:
            return str(exc), 0.0, ""

        from tradingagents.research import report as research_report

        return research_report.compact_summary(model, valuation), valuation.price, str(spec_path)

    def _research_model(self, company_name: str, trade_date: str | None = None,
                        price: float | None = None, allow_build: bool = True,
                        spec_hint: str | None = None):
        """The validated model and its valuation, or ``_ModelUnavailable``."""
        root = research_dir_from_env()
        if root is None:
            raise _ModelUnavailable("No research corpus is configured for this run.")

        configured = os.environ.get("TRADINGAGENTS_RESEARCH_MODEL", "").strip()
        corpus = resolve_corpus_root(root, company_name)
        candidates = [Path(spec_hint)] if spec_hint else []
        candidates += [Path(configured)] if configured else []
        candidates.extend([corpus / "model.json", corpus / f"{safe_ticker_component(company_name)}.json"])
        cached = self._models_dir() / f"{safe_ticker_component(company_name)}.json"
        candidates.append(cached)
        spec_path = next((path for path in candidates if path.is_file()), None)
        rebuild = allow_build and os.environ.get("TRADINGAGENTS_REBUILD_MODEL", "").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if spec_path is None and not allow_build:
            raise _ModelUnavailable("No model spec is available to audit against.")
        if spec_path is None or rebuild:
            from tradingagents.research.schemas import ModelError

            try:
                spec_path = self._build_spec_from_corpus(company_name, corpus, trade_date)
            except ModelError as exc:
                raise _ModelUnavailable(
                    f"MODEL UNAVAILABLE: {exc} Report the model as unavailable; "
                    "do not estimate its numbers."
                ) from exc

        from tradingagents.research import kernel, report as research_report, valuation
        from tradingagents.research.schemas import ModelError

        try:
            spec = research_report.load_spec(spec_path)
            resolved, note = self._resolve_price(spec, company_name, trade_date, price)
            if resolved:
                spec.valuation.price = resolved
            if note:
                spec.warnings.append(note)
            model = kernel.run(spec)
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

        # The vendor holds every reported annual figure; the corpus usually does
        # not. Without this, a model asked for a historical year derives it.
        from tradingagents.research import vendor_facts

        reported: dict = {}
        try:
            reported = vendor_facts.fetch_annual(company_name, unit="JPY_bn")
        except vendor_facts.VendorFactsError as exc:
            logger.info("No vendor annual statement for %s: %s", company_name, exc)
        reported_text = vendor_facts.describe(reported, str(trade_date or ""))
        if reported_text:
            facts_text = f"{facts_text}\n\n{reported_text}" if facts_text else reported_text

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

        consensus = None
        configured = os.environ.get("TRADINGAGENTS_RESEARCH_MODEL", "").strip()
        if configured and Path(configured).is_file():
            try:
                from tradingagents.research import report as research_report

                consensus = research_report.load_spec(configured).consensus
            except Exception:  # noqa: BLE001 - a benchmark must never block a build
                consensus = None

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
        return path

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
                       price: float | None) -> tuple[float | None, str]:
        """Pick the share price the valuation should use, and say where it came from.

        A model spec's price goes stale the moment the market moves; a valuation
        built on it is wrong in a way no arithmetic check can see. The run date's
        close wins, the spec is the fallback, and either way the report says which.
        """
        configured = os.environ.get("TRADINGAGENTS_MODEL_PRICE", "").strip()
        if configured:
            try:
                return float(configured.replace(",", "")), (
                    f"Share price from TRADINGAGENTS_MODEL_PRICE: {configured}."
                )
            except ValueError:
                logger.warning("Ignoring invalid TRADINGAGENTS_MODEL_PRICE=%r", configured)
        if price is not None:
            return float(price), ""
        spec_price = spec.valuation.price
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
                return market, note
            if spec_price:
                return spec_price, (
                    f"MODEL WARNING: no market price was available for {trade_date}; the "
                    f"valuation uses the spec price of {spec_price:,.0f} {spec.currency}, "
                    "which may be stale."
                )
        return spec_price, ""

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
        for reflection on the next same-ticker run. propagate() and the CLI both end here."""
        self._log_state(trade_date, final_state)
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
