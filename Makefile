BASE_URL ?= http://spark.example-tailnet.ts.net:8000/v1
MODELS   ?= chat,coder,fast,vision
REPS     ?= 48

.PHONY: setup probe plan smoke run report test lint fmt clean

setup:            ## install with uv (creates .venv)
	uv sync --extra dev

probe:            ## is the gateway there, and what is it serving?
	uv run lebenchmark probe --base-url $(BASE_URL)

plan:             ## how many calls a full run would make, and for how long
	uv run lebenchmark plan --models $(MODELS) --reps $(REPS)

smoke:            ## ~5 min end-to-end check on one model
	uv run lebenchmark run --base-url $(BASE_URL) --models fast --reps 4 \
		--skip-budget --skip-latency --label smoke

run:              ## the full suite — hours; run `make plan` first
	uv run lebenchmark run --base-url $(BASE_URL) --models $(MODELS) --reps $(REPS)

report:           ## re-aggregate an existing run: make report RUN=results/<id>
	uv run lebenchmark report $(RUN)

sitedata:         ## regenerate the study site's figures: make sitedata RUN=results/<id>
	uv run lebenchmark sitedata $(RUN)

site:             ## serve the study site at http://127.0.0.1:8899
	@echo "study site on http://127.0.0.1:8899 — ctrl-c to stop"
	@cd docs && python3 -m http.server 8899

test:             ## unit tests (no network)
	uv run pytest -q

lint:             ## ruff check
	uv run ruff check src tests

fmt:              ## ruff format + fix
	uv run ruff format src tests && uv run ruff check --fix src tests

clean:
	rm -rf .venv .pytest_cache .ruff_cache dist build *.egg-info
