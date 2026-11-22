SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

COMPOSE := docker compose -f deploy/compose.yaml -f deploy/compose.dev.yaml --profile dev
SCHLUESSEL := config/schluessel

.PHONY: setup lint typ test check up down test-int

setup:
	uv sync --all-packages --frozen
	./deploy/dev-geheimnisse.sh $(SCHLUESSEL)

lint:
	uv run ruff check .
	uv run ruff format --check .

typ:
	uv run mypy

test:
	uv run coverage run -m pytest
	uv run coverage report
	# Mindestabdeckung kern (Implementierung 9)
	uv run coverage report --include='pakete/kern/*' --fail-under=85 > /dev/null

check: lint typ test

up:
	$(COMPOSE) up -d --wait postgres minio
	$(COMPOSE) run --rm --no-deps minio-init

down:
	$(COMPOSE) down

test-int: up
	uv run pytest -m integration
