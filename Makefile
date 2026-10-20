.PHONY: install api web test check build e2e compose-up compose-down

install:
	uv sync --frozen --extra dev
	npm --prefix apps/web ci
	npm --prefix tests/e2e ci

api:
	uv run uvicorn playmatch.main:app --host 127.0.0.1 --port 8000

web:
	npm --prefix apps/web run dev

test:
	uv run pytest
	npm --prefix apps/web test

check:
	uv run ruff check .
	uv run ruff format --check .
	npm --prefix apps/web run typecheck

build:
	npm --prefix apps/web run build

e2e:
	npm --prefix tests/e2e test

compose-up:
	docker compose up --build -d --wait

compose-down:
	docker compose down
