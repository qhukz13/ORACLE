# ORACLE — local quality gate.
#
# `make check` must be green before any commit (docs/TESTING.md#8-local-gate).
# From Phase 2 the security suite joins it and is NOT optional — a gate you have to
# remember to run is not a gate.

UI := apps/desktop

.PHONY: help setup check eval fmt lint types test test-py test-ui build run run-ui shell clean

help:
	@echo "setup   install python + node deps"
	@echo "check   fmt-check + lint + types + tests   <- the gate"
	@echo "fmt     format python and check ts"
	@echo "run     start oracled (backend only)"
	@echo "run-ui  start the vite dev server"
	@echo "shell   build+run the Tauri desktop shell"

setup:
	uv sync
	npm --prefix $(UI) install

# The real implementation lives in scripts/check.py so the gate is runnable without
# GNU make (which is not installed on this machine).
check:
	uv run python scripts/check.py

# Needs Ollama up and costs real inference time, which is why it is not part of `check`.
# Without make (not installed on this machine) the runnable form is the two `uv run` lines.
eval:
	uv run python scripts/eval_intent.py
	uv run python scripts/eval_selection.py

fmt:
	uv run ruff format src tests

lint:
	uv run ruff format --check src tests
	uv run ruff check src tests

types:
	uv run mypy
	npm --prefix $(UI) run typecheck

test: test-py test-ui

test-py:
	uv run pytest -q

test-ui:
	npm --prefix $(UI) run test

build:
	npm --prefix $(UI) run build

run:
	uv run oracled

run-ui:
	npm --prefix $(UI) run dev

shell:
	npm --prefix $(UI) run tauri dev

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache $(UI)/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
