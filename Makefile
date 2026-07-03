.PHONY: test lint format-check verify install-local

test:
	uv run --extra dev pytest

lint:
	uv run --extra dev ruff check .

format-check:
	uv run --extra dev ruff format --check .

verify: lint format-check test

install-local:
	uv tool install --force --editable .
