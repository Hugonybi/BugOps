from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sentry_mcp_url: str = "https://mcp.sentry.dev/mcp"
    sentry_oauth_client_name: str = "BugOps"
    sentry_oauth_redirect_uri: str = "http://localhost:3030/callback"
    sentry_token_cache_path: Path = Path("data/sentry_oauth_tokens.json")

    github_token: SecretStr
    github_repo_map_json: str

    git_cache_dir: Path = Path("data/repo_cache")

    max_context_frames: int = 15
    """Cap on how many in-app stack frames gather_context fetches source/blame for."""

    llm_provider: str = "anthropic"
    """Passed to langchain's init_chat_model as model_provider — switching models/providers
    is a config change, never a code change."""
    llm_model: str
    llm_api_key: SecretStr
    llm_base_url: str | None = None
    """Only needed for a self-hosted or OpenAI-compatible endpoint; omitted otherwise."""

    max_investigate_rounds: int = 4
    """Cap on investigate's agent<->tool loop iterations before it force-terminates. Also
    used by generate_fix's tool loop, to avoid a second near-duplicate setting."""

    max_fix_retries: int = 3
    """Cap on generate_fix <-> test_fix retry iterations before giving up on this issue."""

    docker_image: str = "node:20-bookworm-slim"
    """Generic sandbox image for install/test runs. No per-repo Dockerfiles yet."""

    repo_test_cmd_map_json: str = "{}"
    """Optional override of the auto-detected test command, keyed by 'owner/name'."""

    sandbox_worktree_dir: Path = Path("data/repo_worktrees")
    """Root dir for the throwaway git worktrees test_fix creates per attempt."""

    sandbox_install_timeout_s: int = 300
    sandbox_test_timeout_s: int = 300
    sandbox_memory_limit: str = "2g"
    sandbox_cpu_limit: str = "2"

    enable_sandbox_tests: bool = True
    """Feature flag to skip test_fix entirely (e.g. on a machine without Docker)."""

    decision_confidence_floor: float = 0.75
    """Minimum decision_gate `confidence` (from the top hypothesis) required for route_decision
    to ever be "suggest_pr" — below this it is always "comment_only"."""

    decision_max_files_for_low_risk: int = 2
    """A diff touching more files than this bumps risk_category above "low" even with high
    confidence and a passing test."""

    enable_slack_notify: bool = True
    """Feature flag to skip the actual Slack API call (e.g. no SLACK_BOT_TOKEN on this machine).
    decision_gate still runs and notify_slack still returns cleanly; only the network call is
    skipped, and this is never recorded as a drop_reason."""

    slack_bot_token: SecretStr | None = None
    slack_default_channel: str | None = None

    enable_pr_creation: bool = True
    """Feature flag to skip open_pr's git-push + GitHub API call entirely (e.g. a read-only
    GITHUB_TOKEN, or a repo you don't want touched yet). When False, open_pr always returns
    {} without prompting, exactly like enable_slack_notify does for notify_slack."""

    pr_draft: bool = True
    """Whether PRs open_pr opens are GitHub drafts. Phase 5 is gated behind manual approval,
    not ready-for-review, so drafts are the safer default."""

    pr_branch_prefix: str = "bugops/"
    """Prefix for the branch open_pr creates, e.g. bugops/backend-1-<random>."""

    pr_auto_approve: bool = False
    """Skips open_pr's interactive Confirm.ask prompt and treats every suggest_pr route as
    approved. Off by default so a run never opens a PR without a human saying so; run_pipeline.py
    exposes --yes to flip this per-invocation without editing .env."""

    def repo_map(self) -> dict[str, str]:
        import json

        return json.loads(self.github_repo_map_json)

    def repo_test_cmd_map(self) -> dict[str, str]:
        import json

        return json.loads(self.repo_test_cmd_map_json)


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
