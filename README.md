# BugOps

BugOps is an autonomous bug-fixing agent for Sentry issues in a handful of Node/TypeScript repos. It's built as a LangGraph state machine: given a Sentry issue, it gathers context (stack trace, blame, commit history), uses an LLM to hypothesize a root cause, generates and tests a fix in a sandboxed clone, and either opens a draft PR or posts a structured comment + Slack notification depending on a deterministic confidence/risk gate.

## Status: Phase 3 of 6

The project is being built in stages (see build order below). **Phases 1-3** are implemented so far: BugOps now runs as a real LangGraph `StateGraph` (`ingest -> gather_context -> investigate -> generate_fix -> test_fix`, looping back to `generate_fix` on a failed test up to a retry cap) that gathers context on a historical Sentry issue, asks an LLM to propose ranked root-cause hypotheses, generates a candidate diff, and verifies it against the repo's real test suite in a sandboxed Docker container. No live webhook or PR/Slack integration yet.

What the pipeline does, given a Sentry issue URL:

- Fetches issue details, the stack trace, and breadcrumbs from Sentry's hosted MCP server (`mcp.sentry.dev`)
- Maps the issue's Sentry project to a GitHub repo and resolves an approximate release commit SHA
- Clones the repo locally and, for each stack frame, pulls the source snippet, line-level blame, and recent commit history
- Hands that gathered context to an LLM (provider-agnostic via `langchain`'s `init_chat_model` — swap `LLM_PROVIDER`/`LLM_MODEL` in `.env`, no code change needed), which may read a few more files from the same local clone, then proposes one or more ranked root-cause hypotheses
- Hands the hypotheses to an LLM to propose a unified diff, then applies that diff to a throwaway `git worktree` and runs the repo's real test suite in a Docker container (network-isolated during the test run itself), retrying with the failure output fed back to the model up to `MAX_FIX_RETRIES` times
- Prints a human-readable report (and optionally dumps JSON) so the output can be checked against what Sentry's own UI shows for the same issue

Roadmap for the remaining phases:

1. ~~Ingest + gather context~~ (done)
2. ~~Hypothesize + investigate (LLM tool-use loop over the gathered context)~~ (done)
3. ~~Generate fix + test fix (sandboxed Docker test runs, bounded retries)~~ (done)
4. Decision gate + comment-only + Slack notification (suggest-only path ships first)
5. Open PR, gated behind manual approval
6. Remove the manual gate for risk categories that prove reliable over time

## Setup

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Copy `.env.example` to `.env` and fill in:
   - `GITHUB_TOKEN` — a GitHub token with read access to the target repos
   - `GITHUB_REPO_MAP_JSON` — maps Sentry project slugs to `"owner/repo"`, e.g. `{"backend-api":"myorg/backend-api"}`
   - `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` — which LLM the investigate step uses; any provider `langchain` has an integration package for (defaults to Anthropic)
3. No Sentry credentials to configure up front — the first run triggers a one-time interactive OAuth authorization (see below).
4. Have Docker (e.g. Docker Desktop) installed and running — `test_fix` runs the target repo's test suite in a container. Set `ENABLE_SANDBOX_TESTS=false` to skip that step on a machine without Docker.

## Usage

Run the context-gathering pipeline against a real Sentry issue:

```bash
uv run python -m bugops.scripts.run_pipeline --issue-url https://your-org.sentry.io/issues/PROJECT-123
```

On the first run, BugOps prints a Sentry authorization URL — open it, approve access, then paste the redirect URL back into the prompt. The resulting token is cached to `data/sentry_oauth_tokens.json` so later runs are silent.

Options:

- `--project-slug <slug>` — override the Sentry project slug, needed if your org isn't on Sentry's structured-output rollout (the issue summary comes back as markdown only in that case)
- `--json-out <path>` — dump the full gathered context as JSON, useful for diffing across runs or freezing test fixtures

## Development

```bash
uv run pytest tests/unit    # unit tests (no live credentials needed, no Docker needed)
uv run pytest -m docker     # sandbox integration test against a fixture repo (needs Docker)
uv run ruff check src tests # lint
```

## Project layout

```text
src/bugops/
  config.py           # settings (env vars)
  state.py             # BugOpsState — the full graph state schema (only a subset populated so far)
  graph.py              # StateGraph wiring ingest -> gather_context -> investigate -> generate_fix -> test_fix (Phase 3)
  nodes/
    ingest.py           # Sentry issue -> initial state (Phase 1)
    gather_context.py   # stack trace, blame, commits, source (Phase 1)
    investigate.py       # LLM hypothesize/investigate tool-use loop (Phase 2)
    generate_fix.py       # LLM proposes a unified diff from the ranked hypotheses (Phase 3)
    test_fix.py            # applies the diff in a sandboxed worktree + runs tests via Docker (Phase 3)
    tools.py                 # shared read_source_file tool + context-message builder
  clients/
    sentry_mcp.py        # Sentry hosted MCP client (OAuth)
    github_client.py     # GitHub REST (PyGithub)
  git/
    local_repo.py         # local clone, blame, commit log (GitPython)
    worktree.py             # throwaway per-attempt git worktrees + diff apply (Phase 3)
  sandbox/
    docker_runner.py         # runs install/test commands in Docker containers (Phase 3)
    test_command.py            # auto-detects (or overrides) a repo's install/test command (Phase 3)
  models/
    sentry.py              # Sentry-related data models
    context.py              # gathered-context data models
    hypothesis.py            # structured LLM output schema (Phase 2)
    fix.py                     # structured LLM output schema for a proposed diff (Phase 3)
  scripts/
    run_pipeline.py          # CLI harness that builds and runs the graph
tests/unit/                    # unit tests with fake client/model/docker stubs (no network, no Docker)
tests/integration/              # tests requiring a real Docker daemon (pytest -m docker)
```

This README is kept up to date as the app changes — new phases, new modules, or a changed setup step should be reflected here alongside the code that introduces them.
