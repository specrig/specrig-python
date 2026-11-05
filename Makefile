PROTOCOL ?= $(abspath $(CURDIR)/../protocol)
PYTHON ?= python3
VENV := $(CURDIR)/.venv

$(VENV):
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip

install: $(VENV)
	$(VENV)/bin/pip install -q -e ".[fastapi,django,postgres]" --group dev

generate: install
	cd samples/shop && $(VENV)/bin/specrig surface && $(VENV)/bin/specrig agents
	cd samples/reminders && $(VENV)/bin/specrig surface && $(VENV)/bin/specrig agents

test: install
	$(VENV)/bin/pytest -q
	cd samples/shop && $(VENV)/bin/pytest -q tests/internal
	cd samples/reminders && $(VENV)/bin/pytest -q tests/internal

lint: install
	$(VENV)/bin/ruff check src tests samples
	$(VENV)/bin/ruff format --check src tests samples

protocol:
	rm -rf src/specrig/protocol tests/fixtures/protocol
	mkdir -p src/specrig/protocol/agents tests/fixtures/protocol/records tests/fixtures/protocol/agents tests/fixtures/protocol/markers
	cp $(PROTOCOL)/modes.yaml src/specrig/protocol/
	cp $(PROTOCOL)/agents/*.md src/specrig/protocol/agents/
	cp $(PROTOCOL)/fixtures/records/* tests/fixtures/protocol/records/
	cp $(PROTOCOL)/fixtures/agents/*.yaml tests/fixtures/protocol/agents/
	cp $(PROTOCOL)/fixtures/markers/*.yaml tests/fixtures/protocol/markers/

.PHONY: install generate test lint protocol
