# Makefile — agents
#
# Bakes the 1Password `op run` prefix into every target that needs real
# secrets so it can't be forgotten, and keeps the test suite secret-free
# (real secrets break the absence-tests — see `test` below).
#   * op-run targets: migrate, makemigration, db-downgrade, db-shell,
#     serve-*, modal-deploy-*, run-script, run, shell. Wrapped in `op run`
#     so op:// refs resolve in-memory (no plaintext .env on disk).
#   * plain targets (no op run): help, check-op, test, test-live, lint,
#     format, format-check, typecheck. Only test-live reads a real key —
#     see its note.
# The env-file is a variable; override with: make migrate OP_ENV=.env.other.tpl
# Written for the macOS default /usr/bin/make — GNU Make 3.81, not BSD make
# (the one GNU-only construct is `$(or $(n),1)` in db-downgrade).

# 1Password env-file template (op:// references only — safe to commit).
OP_ENV ?= .env.tpl

# The op-run prefix every secret-needing target reuses.
OP_RUN = op run --env-file=$(OP_ENV) --

# uv is the task runner for all Python entrypoints.
UV = uv run

# Reusable preflight: fail fast (clear message) if `op` is missing, the
# env-file template is absent, OR there is no authenticated 1Password session.
# Every op-run recipe runs this first so auth/setup failures surface at the
# Makefile layer with an actionable message instead of deep inside the app.
define require_op
	@command -v op >/dev/null 2>&1 || { \
		echo "ERROR: 1Password CLI 'op' not found on PATH."; \
		echo "       Install it (brew install --cask 1password-cli) and sign in,"; \
		echo "       then re-run. Secret-needing targets require 'op run'."; \
		exit 1; \
	}
	@test -f "$(OP_ENV)" || { \
		echo "ERROR: env-file template '$(OP_ENV)' not found."; \
		echo "       Expected the committed op:// template at the repo root."; \
		echo "       Override with: make <target> OP_ENV=path/to/file.tpl"; \
		exit 1; \
	}
	@op whoami >/dev/null 2>&1 || op account list >/dev/null 2>&1 || { \
		echo "ERROR: 1Password 'op' is installed but not signed in."; \
		echo "       Run: eval \$$(op signin)   (or open the 1Password desktop app and unlock)"; \
		echo "       then re-run. Secret-needing targets need an active op session."; \
		exit 1; \
	}
endef

# Reusable guard for plain targets: refuse if a real ./.env exists. pydantic
# Settings(env_file='.env') would load it, injecting real secrets and
# breaking the absence-tests. Tests must run with no secrets in scope.
define refuse_dotenv
	@test ! -f ./.env || { \
		echo "ERROR: ./.env is present — refusing to run the test suite."; \
		echo "       A real .env (or running under 'op run') injects real"; \
		echo "       secrets, which makes the absence-tests FAIL."; \
		echo "       Tests must run with NO secrets: temporarily move it, e.g."; \
		echo "           mv .env .env.bak && make $@ && mv .env.bak .env"; \
		exit 1; \
	}
endef

.PHONY: help check-op \
        test test-live lint format format-check typecheck \
        migrate makemigration db-downgrade db-shell \
        serve-triager serve-job-apps serve-morning-brief serve-news-brief serve-spend-sync \
        modal-deploy-triager modal-deploy-job-apps modal-deploy-morning-brief modal-deploy-news-brief modal-deploy-spend-sync \
        run-script run shell

# ---------------------------------------------------------------------------
# help — DEFAULT GOAL (first target). Lists what you can run.
# ---------------------------------------------------------------------------
help:
	@echo "agents — make targets"
	@echo ""
	@echo "  Secrets resolve from 1Password via:  $(OP_RUN) <cmd>"
	@echo "  Override the env-file:               make <target> OP_ENV=path.tpl"
	@echo ""
	@echo "PLAIN (no op run):"
	@echo "  help                 Show this help (default)"
	@echo "  check-op             Verify 'op' CLI, \$$(OP_ENV), and an active op session"
	@echo "  test                 Run pytest (PLAIN, no op run; refuses if ./.env exists)"
	@echo "  test-live            Run live-LLM tests (AGENTS_LIVE_LLM=1; needs real key — see notes)"
	@echo "  lint                 ruff check + format drift (runs format-check first)"
	@echo "  format               ruff format"
	@echo "  format-check         ruff format --check"
	@echo "  typecheck            mypy"
	@echo ""
	@echo "OP RUN (wrapped — real secrets in-memory, no plaintext .env):"
	@echo "  migrate              alembic upgrade head"
	@echo "  makemigration m=...  alembic revision --autogenerate -m \"...\""
	@echo "  db-downgrade [n=1]   alembic downgrade -<n>"
	@echo "  db-shell             alembic current (DB connectivity smoke check; run before migrate)"
	@echo "  serve-<svc>          modal serve services/<svc>/modal_app.py"
	@echo "  modal-deploy-<svc>   modal deploy services/<svc>/modal_app.py"
	@echo "                       <svc>: triager job-apps morning-brief news-brief spend-sync"
	@echo "  run-script f=path    Run a scripts/*.py under op run; pass flags via ARGS="
	@echo "                       e.g. make run-script f=scripts/backtest_triager.py ARGS=\"--days 30 --max 50\""
	@echo "  run CMD=\"...\"        Generic escape hatch: op run -- <CMD>"
	@echo "  shell                Interactive subshell with secrets injected"
	@echo ""
	@echo "NOTE: op-run targets resolve secrets from 1Password at runtime. An"
	@echo "      empty 1Password field (e.g. an unpopulated DATABASE_URL) resolves"
	@echo "      to an empty value and surfaces as an app-layer failure, not a make"
	@echo "      error. Use 'make db-shell' as a pre-migrate connectivity smoke check."

# ---------------------------------------------------------------------------
# check-op — surface the preflight as its own target.
# ---------------------------------------------------------------------------
check-op:
	$(require_op)
	@echo "OK: 'op' found, '$(OP_ENV)' present, and an active op session."

# ===========================================================================
# PLAIN TARGETS  (no op run)
# ===========================================================================

# PLAIN pytest. No op run, and refuses if ./.env exists, because real secrets
# break the absence-tests.
test:
	$(refuse_dotenv)
	$(UV) pytest

# Live-LLM tests opt in explicitly. conftest skips its dummy ANTHROPIC_API_KEY
# when AGENTS_LIVE_LLM=1 and reads the real key from ./.env — so this is the
# ONE pytest path that legitimately wants a real key. Run it deliberately.
test-live:
	AGENTS_LIVE_LLM=1 $(UV) pytest

# format-check runs first via the prerequisite; together the two ruff
# invocations match CI's lint + format-check steps.
lint: format-check
	$(UV) ruff check .

format:
	$(UV) ruff format .

format-check:
	$(UV) ruff format --check .

# Bare mypy so the scope comes from mypy.ini's `files`, same as CI.
typecheck:
	$(UV) mypy

# ===========================================================================
# OP RUN TARGETS  (wrapped — run code that needs real secrets locally)
# ===========================================================================

# alembic upgrade head — migrations/env.py builds the engine from get_settings().
migrate:
	$(require_op)
	$(OP_RUN) $(UV) alembic upgrade head

# alembic revision --autogenerate. Requires a message:  make makemigration m="add x"
makemigration:
	$(require_op)
	@test -n "$(m)" || { echo "ERROR: provide a message: make makemigration m=\"add foo\""; exit 1; }
	$(OP_RUN) $(UV) alembic revision --autogenerate -m "$(m)"

# alembic downgrade -<n> (default 1):  make db-downgrade n=2
db-downgrade:
	$(require_op)
	$(OP_RUN) $(UV) alembic downgrade -$(or $(n),1)

# Connectivity smoke check against the real DB (prints current revision).
db-shell:
	$(require_op)
	$(OP_RUN) $(UV) alembic current

# --- modal serve (dev — executes local code that builds Settings) ----------
serve-triager:
	$(require_op)
	$(OP_RUN) $(UV) modal serve services/triager/modal_app.py

serve-job-apps:
	$(require_op)
	$(OP_RUN) $(UV) modal serve services/job_apps/modal_app.py

serve-morning-brief:
	$(require_op)
	$(OP_RUN) $(UV) modal serve services/morning_brief/modal_app.py

serve-news-brief:
	$(require_op)
	$(OP_RUN) $(UV) modal serve services/news_brief/modal_app.py

serve-spend-sync:
	$(require_op)
	$(OP_RUN) $(UV) modal serve services/spend_sync/modal_app.py

# --- modal deploy ----------------------------------------------------------
modal-deploy-triager:
	$(require_op)
	$(OP_RUN) $(UV) modal deploy services/triager/modal_app.py

modal-deploy-job-apps:
	$(require_op)
	$(OP_RUN) $(UV) modal deploy services/job_apps/modal_app.py

modal-deploy-morning-brief:
	$(require_op)
	$(OP_RUN) $(UV) modal deploy services/morning_brief/modal_app.py

modal-deploy-news-brief:
	$(require_op)
	$(OP_RUN) $(UV) modal deploy services/news_brief/modal_app.py

modal-deploy-spend-sync:
	$(require_op)
	$(OP_RUN) $(UV) modal deploy services/spend_sync/modal_app.py

# Run any scripts/*.py with secrets. Pass script flags via ARGS (the scripts are
# argparse/flag-driven, e.g. --days/--max/--hours):
#   make run-script f=scripts/backtest_triager.py ARGS="--days 30 --max 50"
#   make run-script f=scripts/verify_anthropic_spend.py ARGS="--hours 48"
run-script:
	$(require_op)
	@test -n "$(f)" || { echo "ERROR: provide a file: make run-script f=scripts/backtest_triager.py ARGS=\"--days 30\""; exit 1; }
	$(OP_RUN) $(UV) python "$(f)" $(ARGS)

# Generic escape hatch:  make run CMD="python -c 'import shared.settings'"
run:
	$(require_op)
	@test -n "$(CMD)" || { echo "ERROR: provide a command: make run CMD=\"...\""; exit 1; }
	$(OP_RUN) $(CMD)

# Interactive subshell with secrets injected — every command inherits them.
shell:
	$(require_op)
	$(OP_RUN) $$SHELL
