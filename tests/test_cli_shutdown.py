"""The CLI must end even when nobody can answer its last questions."""

from __future__ import annotations

from types import SimpleNamespace

from cli import run


def _refuse(*args, **kwargs):
    raise EOFError


def test_ask_returns_no_answer_instead_of_raising(monkeypatch):
    monkeypatch.setattr(run.typer, "prompt", _refuse)
    assert run._ask("Save report?") is None
    assert run._yes("Also save it as an HTML page?") is False


def test_offer_reports_finishes_without_a_terminal(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run.typer, "prompt", _refuse)
    graph = SimpleNamespace(default_report_path=lambda ticker: tmp_path / "report")

    run._offer_reports({"fundamentals_report": "text"}, graph, "5803.T",
                       save=None, show=None, html=None)

    printed = capsys.readouterr().out
    assert "Run finished" in printed
    assert not (tmp_path / "report").exists()   # nothing saved without an answer


def test_a_typed_answer_still_works(monkeypatch):
    monkeypatch.setattr(run.typer, "prompt", lambda *args, **kwargs: "y")
    assert run._ask("Save report?") == "y"
    assert run._yes("Save?") is True
