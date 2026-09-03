.PHONY: install test lint init dashboard
install:
	python -m pip install -e ".[dev]"
test:
	pytest -q
lint:
	ruff check .
init:
	pme init-db
dashboard:
	streamlit run dashboard/app.py
