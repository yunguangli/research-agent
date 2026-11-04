"""Guard the news analyst prompt against tool-signature drift (#1116).

The prompt used to advertise ``get_news(query, ...)``, tricking the LLM into
hallucinating free-text query calls. The instrument now comes from the run's
state, so the model passes only the window.
"""
import inspect

import pytest

import tradingagents.agents.analysts.news_analyst as na
from tradingagents.agents.tools import get_news


@pytest.mark.unit
def test_get_news_takes_the_window_not_a_query():
    arg_names = set(get_news.tool_call_schema.model_json_schema()["properties"])
    assert arg_names == {"start_date", "end_date"}


@pytest.mark.unit
def test_news_prompt_matches_get_news_signature():
    src = inspect.getsource(na)
    assert "get_news(start_date, end_date)" in src
    assert "get_news(query" not in src


@pytest.mark.unit
def test_the_news_analyst_is_not_offered_prediction_markets():
    """A single-name rating must not ride on election or recession odds."""
    names = {tool.name for tool in na.TOOLS}
    assert "get_prediction_markets" not in names
    src = inspect.getsource(na)
    assert "get_prediction_markets(" not in src
    assert "do not substitute global macro" in src.lower()
