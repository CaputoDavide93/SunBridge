# One entry point for the checks CI runs. `make check` before every push.
PYTHON := .venv/bin/python

.PHONY: venv test lint diagram check hassfest screenshots

venv: ## Home Assistant 2026.9.4 test environment, built with uv
	uv venv -q --allow-existing --python 3.14 .venv
	VIRTUAL_ENV=.venv uv pip install -q -r requirements_test.txt

test: ## The pytest suite
	$(PYTHON) -m pytest -q

lint: ## ruff
	$(PYTHON) -m ruff check .

diagram: ## Redraw docs/assets/*.svg and the generated docs
	python3 tools/gen_diagram.py
	python3 tools/gen_docs.py

check: lint test ## Everything CI checks
	python3 tools/gen_diagram.py --check
	python3 tools/gen_docs.py --check

hassfest: ## Home Assistant's own manifest and config-flow validation (needs Docker)
	docker run --rm -v "$(CURDIR)/custom_components/sunbridge:/github/workspace/custom_components/sunbridge" \
		ghcr.io/home-assistant/hassfest:latest --integration-path /github/workspace/custom_components/sunbridge

screenshots: ## Retake docs/assets/screenshots/ from a throwaway Home Assistant (not CI)
	test -x .venv-screenshots/bin/python || ( \
		uv venv -q --python 3.14 .venv-screenshots && \
		VIRTUAL_ENV=.venv-screenshots uv pip install -q -r tools/screenshots/requirements.txt )
	.venv-screenshots/bin/python -m playwright install chromium
	.venv-screenshots/bin/python tools/screenshots/take_screenshots.py
