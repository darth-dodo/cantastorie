# Cantastorie Makefile
# Usage: make [target]

.PHONY: install install-hooks dev dev-css build-css test test-js test-js-watch test-cov lint lint-fix format format-check typecheck check pre-commit pre-commit-update clean clean-all help
.DEFAULT_GOAL := help

# =============================================================================
# Installation
# =============================================================================

install: ## Install dependencies (uv + npm)
	uv sync --extra dev
	npm install

install-hooks: ## Install pre-commit hooks
	uv run pre-commit install
	uv run pre-commit install --hook-type commit-msg

# =============================================================================
# Development
# =============================================================================

dev: ## Serve the static player at http://localhost:8000
	uv run python -m http.server 8000 --directory static

dev-css: ## Watch and compile Tailwind CSS
	npx @tailwindcss/cli -i ./static/css/input.css -o ./static/css/output.css --watch

build-css: ## Compile Tailwind CSS once (minified)
	npx @tailwindcss/cli -i ./static/css/input.css -o ./static/css/output.css --minify

# =============================================================================
# Testing
# =============================================================================

test: ## Run all tests (Python + JS)
	uv run pytest
	npx vitest run

test-js: ## Run JavaScript tests with Vitest
	npx vitest run

test-js-watch: ## Run JavaScript tests in watch mode
	npx vitest

test-cov: ## Run Python tests with coverage report
	uv run pytest --cov=src --cov-report=html --cov-report=term-missing

# =============================================================================
# Code Quality
# =============================================================================

lint: ## Run Ruff linter
	uv run ruff check src/ tests/

lint-fix: ## Run Ruff linter with auto-fix
	uv run ruff check --fix src/ tests/

format: ## Format code with Ruff
	uv run ruff format src/ tests/

format-check: ## Check code formatting
	uv run ruff format --check src/ tests/

typecheck: ## Run MyPy type checker
	uv run mypy src/

check: lint format-check typecheck ## Run all checks (lint, format, typecheck)

# =============================================================================
# Pre-commit
# =============================================================================

pre-commit: ## Run pre-commit on all files
	uv run pre-commit run --all-files

pre-commit-update: ## Update pre-commit hooks
	uv run pre-commit autoupdate

# =============================================================================
# Cleanup
# =============================================================================

clean: ## Remove build artifacts and caches
	rm -rf __pycache__ .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	rm -rf dist/ build/ *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

clean-all: clean ## Remove all generated files including node_modules
	rm -rf node_modules/
	rm -f static/css/output.css

# =============================================================================
# Help
# =============================================================================

help: ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"; printf ""} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
