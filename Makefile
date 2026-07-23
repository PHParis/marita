.PHONY: sync test lint format typecheck smoke test-db ci

sync:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run pyright

smoke:
	uv run marita smoke

test-db:
	uv run marita test-db

ci: lint typecheck test
