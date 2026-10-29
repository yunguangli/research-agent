from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.analysts.turn import take_turn
from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction
from tradingagents.agents.tools import (
    CORPUS_TOOLS,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
)

# The tools this analyst is offered; its tool node is built from the same tuple.
TOOLS = (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_insider_transactions,
)

CORPUS_TOOL_NAMES = frozenset(tool.name for tool in CORPUS_TOOLS)


def create_fundamentals_analyst(llm, tools=None):
    active_tools = list(tools) if tools is not None else list(TOOLS)
    corpus_enabled = any(tool.name in CORPUS_TOOL_NAMES for tool in active_tools)

    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)
        research_package = (state.get("research_package") or "").strip()

        corpus_note = ""
        if corpus_enabled:
            corpus_note = (
                " You also have this company's research corpus: `list_research_documents`, "
                "`read_research_document`, and `search_research_documents`. List the documents "
                "first, then read or search only what you need. Cite the document_id and page for "
                "every fact you take from a document, in single square brackets, as "
                "[doc-5803.T-…-sd00 p.3]. Double square brackets are reserved for figures taken "
                "from the verified model below, so never cite with them: `[[6]]` or `[[1][2]]` is "
                "read as the figure 6, and the citation is then checked — and reported — as a "
                "wrong number. If you use short source labels or source numbers instead of full "
                "document_ids, add a 'Documents cited' list at the end of the report mapping each "
                "label to its document_id, so a reader can resolve it. A document typed "
                "`internal_note` is private meeting material: label it as such and never present it "
                "as company disclosure. A document the tool reports as withheld was either "
                "published after the analysis date or has no known publication date; give the "
                "reason the tool gives rather than treating it as absent. Documents may be in "
                "English, Japanese, Simplified or Traditional Chinese, or Korean: read each in its "
                "own language, and search with words from the document's language rather than "
                "English keywords."
            )

        package_note = ""
        if research_package:
            package_note = (
                "\n\nVERIFIED MODEL (computed in Python from the filings and the research corpus). "
                "Use these numbers exactly as given; do not recompute them or restate them "
                "differently. Write your analysis around them, and cite the documents behind each "
                "assumption:\n\n" + research_package + "\n\n"
                "Rules for the verified model:\n"
                "- Mark every figure you take from the model with double square brackets, as "
                "[[229.5]], [[490.0]] or [[JPY 4,111]] — the form the model writes it in. Mark "
                "figures only: never mark a citation, a source number or a list number, because "
                "[[6]] is read as the figure 6 and will be reported as a wrong number. A number "
                "you computed yourself (a PEG, a growth rate, a stop or entry level) or took from "
                "a vendor, filing or broker stays unmarked. The brackets are removed before the "
                "report is shown, and they let the system check your arithmetic against the "
                "model.\n"
                "- Japanese and Chinese filings quote 億円 (100 million yen): 403億円 is "
                "JPY40.3bn, not 403bn. Convert every such figure before you write it, and "
                "reconcile each year-on-year change you describe with the model's own annual "
                "rows — a history sentence whose arithmetic does not tie back to the model's "
                "figures is a unit error, and must be corrected or dropped rather than "
                "published.\n"
                "- Where a vendor tool disagrees with it (for example a forward P/E or forward EPS "
                "from company-overview data), the verified model wins. Report the vendor figure as "
                "a conflicting snapshot and label its source; never mix the two as if they were "
                "one estimate.\n"
                "- Do not value the company on a vendor multiple. The valuation section, the "
                "summary table and every P/E you quote must use the model's EPS, P/E, fair-value "
                "range and scenarios. A vendor forward P/E (for example Yahoo's) may appear only on "
                "a line that explicitly calls it a vendor snapshot; it is never the company's P/E.\n"
                "- Take quarterly actuals, guidance, margins, EPS, and the fair-value range from "
                "the model. Do not re-read the filing's tables for them: those tables list the "
                "current period first and the prior period second, and citing the prior period as "
                "the current one is the most likely error in this report.\n"
                "- Every figure you add that is not in the model must name the document it came "
                "from and the page.\n"
                "- Quote market capitalisation as the model's price times its share count. Do not "
                "put a vendor market cap beside the model's share price: the two imply different "
                "prices, and a reader cannot tell which one you believe.\n"
            )

        system_message = (
            "You are a researcher tasked with analyzing fundamental information about a company "
            "as of the analysis date. Please write a comprehensive report of the company's "
            "fundamental information such as financial documents, company profile, basic company "
            "financials, and company financial history to gain a full view of the company's "
            "fundamental information to inform traders. Make sure to include as much detail as "
            "possible. Provide specific, actionable insights with supporting evidence to help "
            "traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements, and `get_insider_transactions` for recent insider buying and selling."
            + corpus_note
            + package_note
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " Report what your tools support; another agent decides the trade."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in active_tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        result, report = take_turn(prompt, llm, active_tools, state["messages"])

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
