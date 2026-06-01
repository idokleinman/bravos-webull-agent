.DEFAULT_GOAL := help
PY := ./.venv/bin/python
STACK ?= bravos-webull-agent

.PHONY: help bootstrap venv test lint build deploy sync-secrets oauth smoke arm disarm clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

bootstrap: ## One-command setup from a filled .env (deploy + secrets + smoke test)
	./bootstrap.sh

venv: ## Create the local 3.12 venv with dev + runtime deps
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -r src/requirements.txt boto3 pytest

test: ## Run the unit/integration test suite
	$(PY) -m pytest -q

lint: ## Ruff lint
	$(PY) -m ruff check src tests

build: ## sam build
	sam build

build-SignalFunction: ## SAM makefile build target — bypasses pip build isolation to fix grpcio-tools pkg_resources issue
	pip install setuptools --quiet --target "$(ARTIFACTS_DIR)"
	pip install --no-build-isolation -r requirements.txt --target "$(ARTIFACTS_DIR)" --upgrade

deploy: ## sam deploy (uses saved samconfig.toml)
	sam deploy --no-confirm-changeset --no-fail-on-empty-changeset

sync-secrets: ## Push .env secrets → SSM SecureString
	$(PY) sync_secrets.py

oauth: ## Run the one-time Gmail consent and write the refresh token into .env
	$(PY) setup_oauth.py --write

smoke: ## Layer-2 dry-run smoke test against the deployed function
	@FN=$$(aws cloudformation describe-stacks --stack-name $(STACK) \
	  --query "Stacks[0].Outputs[?OutputKey=='FunctionName'].OutputValue" --output text); \
	aws lambda invoke --function-name $$FN --cli-binary-format raw-in-base64-out \
	  --payload '{"test_signal":"Moderate","message_id":"make-smoke"}' /dev/stdout

arm: ## Arm LIVE trading (deliberate). Flips DryRun=false + TradingEnabled=true.
	@echo "⚠️  Arming live trading. Ensure you have verified dry-run output."
	sam deploy --no-confirm-changeset --no-fail-on-empty-changeset \
	  --parameter-overrides DryRun=false TradingEnabled=true

disarm: ## Return to safe (dry + off)
	sam deploy --no-confirm-changeset --no-fail-on-empty-changeset \
	  --parameter-overrides DryRun=true TradingEnabled=false

clean: ## Remove build artifacts
	rm -rf .aws-sam .pytest_cache .ruff_cache
