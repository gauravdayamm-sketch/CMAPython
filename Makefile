.PHONY: install test lint typecheck clean run-api run-demo

VENV      = venv
PYTHON    = $(VENV)/bin/python
PIP       = $(VENV)/bin/pip
PYTEST    = $(VENV)/bin/pytest

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

test:
	$(PYTHON) -m pytest tests/ -v

lint:
	$(PIP) install ruff
	$(VENV)/bin/ruff check src/ tests/ scripts/

typecheck:
	$(PIP) install mypy
	$(VENV)/bin/mypy src/ --exclude src/agents/committee.py --ignore-missing-imports

clean:
	rm -rf $(VENV)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete

run-api:
	$(PYTHON) -m uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8000 --reload

run-demo:
	$(PYTHON) src/test_cma.py

init-db:
	$(PYTHON) scripts/init_db.py

setup-test:
	$(PYTHON) src/setup_test_borrower.py

docker-build:
	docker build -t cma-python .

docker-run:
	docker run --rm -p 8000:8000 -v $(PWD)/db:/app/db cma-python
