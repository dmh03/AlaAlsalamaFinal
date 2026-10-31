PY ?= python

install:
	$(PY) -m pip install -e ".[dev]"

seed:
	$(PY) -m alasalama.seed

dashboard:
	streamlit run dashboard/Home.py

tick:
	$(PY) -m alasalama.tick_cli

eval:
	$(PY) -m eval.run_all

webhook:
	uvicorn webhooks.app:app --port 8000

telegram:
	$(PY) scripts/telegram_poll.py

live:
	$(PY) scripts/live_setup.py

test:
	$(PY) -m pytest -q

.PHONY: install seed dashboard tick eval test webhook telegram live
