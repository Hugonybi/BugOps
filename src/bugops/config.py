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

    def repo_map(self) -> dict[str, str]:
        import json

        return json.loads(self.github_repo_map_json)


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
