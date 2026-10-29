# Tech stack

- Python >= 3.11 (CI matrix 3.11–3.14). Windows dev host, one CI job runs `TZ=America/New_York`.
- Build: setuptools (>=61), backend `setuptools.build_meta`, dynamic version from
  `tradingagents.__version__`, `packages.find` includes `tradingagents*` and `cli*`.
- Core deps: langgraph, langgraph-checkpoint-sqlite, langchain-core/-openai/-anthropic/-google-genai,
  pandas>=3, yfinance, stockstats, typer, rich, questionary, python-dotenv, pytz, requests,
  markdown-it-py, cryptography, packaging, typing-extensions.
- Extras: `dev` = ruff (>=0.16) + pytest (>=8) + pytest-subtests; `bedrock` = langchain-aws (optional;
  core install stays lean, AWS SigV4 auth in `llm_clients/bedrock_client.py`).
- Package data: `cli = static/*`, `tradingagents = assets/*.svg`. Any new runtime import must be a
  real dependency in `[project]`, not only in `dev` — CI does a clean `pip install .` + import smoke.
- Test runner: pytest; `addopts = -ra --strict-markers -m "not integration"`; markers
  `unit`, `integration`, `smoke`. `testpaths = ["tests"]`, DeprecationWarnings ignored.
- Lint: ruff, target py311, line-length 100, `extend-exclude = ["results"]`.
