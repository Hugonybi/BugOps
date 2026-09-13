# BugOps

BugOps is an autonomous bug-fixing agent for Sentry issues in a handful of Node/TypeScript repos. It's built as a LangGraph state machine: given a Sentry issue, it gathers context (stack trace, blame, commit history), uses an LLM to hypothesize a root cause, generates and tests a fix in a sandboxed clone, and either opens a draft PR or posts a structured comment + Slack notification depending on a deterministic confidence/risk gate.

## Status: Phase 1 of 6

The project is being built in stages (see build order below). Only **Phase 1** is implemented so far: proving out the Sentry + GitHub context-gathering pipeline against real historical issues, with no LLM, no live webhook, and no LangGraph graph yet.

What Phase 1 does, given a Sentry issue URL:

- Fetches issue details, the stack trace, and breadcrumbs from Sentry's hosted MCP server (`mcp.sentry.dev`)
- Maps the issue's Sentry project to a GitHub repo and resolves an approximate release commit SHA
- Clones the repo locally and, for each stack frame, pulls the source snippet, line-level blame, and recent commit history
- Prints a human-readable report (and optionally dumps JSON) so the output can be checked against what Sentry's own UI shows for the same issue

Roadmap for the remaining phases:

1. ~~Ingest + gather context~~ (done)
2. Hypothesize + investigate (LLM tool-use loop over the gathered context)
3. Generate fix + test fix (sandboxed Docker test runs, bounded retries)
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
3. No Sentry credentials to configure up front — the first run triggers a one-time interactive OAuth authorization (see below).

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
uv run pytest tests/unit    # unit tests (no live credentials needed)
uv run ruff check src tests # lint
```

## Project layout

```text
src/bugops/
  config.py           # settings (env vars)
  state.py             # BugOpsState — the full graph state schema (only a subset populated so far)
  nodes/
    ingest.py           # Sentry issue -> initial state (Phase 1)
    gather_context.py   # stack trace, blame, commits, source (Phase 1)
  clients/
    sentry_mcp.py        # Sentry hosted MCP client (OAuth)
    github_client.py     # GitHub REST (PyGithub)
  git/
    local_repo.py         # local clone, blame, commit log (GitPython)
  models/
    sentry.py              # Sentry-related data models
    context.py              # gathered-context data models
  scripts/
    run_pipeline.py          # CLI harness for Phase 1
tests/unit/                    # unit tests with fake client stubs (no network)
```

This README is kept up to date as the app changes — new phases, new modules, or a changed setup step should be reflected here alongside the code that introduces them.
