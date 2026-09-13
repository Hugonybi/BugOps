from __future__ import annotations

from bugops.clients.github_client import GitHubClient
from bugops.clients.sentry_mcp import SentryMCPClient
from bugops.config import Settings
from bugops.logging import get_logger
from bugops.models.sentry import SentryIssueSummary, parse_issue_url
from bugops.state import BugOpsState, RepoRef

logger = get_logger(__name__)


async def from_issue_url(
    issue_url: str,
    settings: Settings,
    mcp: SentryMCPClient,
    gh: GitHubClient,
    project_slug_override: str | None = None,
) -> BugOpsState:
    """Builds initial BugOpsState from a historical Sentry issue, for the CLI harness.

    Mirrors what a future `from_webhook` (phase 4) will do once there's a live event to
    validate/dedupe/filter — this path skips that, since there's no webhook to filter yet.
    """
    issue_ref = parse_issue_url(issue_url)
    issue_summary = await mcp.get_issue_details(issue_ref)

    project_slug = project_slug_override or issue_summary.project_slug
    if not project_slug:
        raise ValueError(
            "Sentry didn't return a project slug for this issue (org likely isn't on the "
            "structured-output rollout) — pass --project-slug explicitly."
        )

    repo_map = settings.repo_map()
    if project_slug not in repo_map:
        raise ValueError(
            f"No GitHub repo mapped for Sentry project {project_slug!r}; "
            f"add it to GITHUB_REPO_MAP_JSON (known: {sorted(repo_map)})"
        )
    owner, name = repo_map[project_slug].split("/", 1)

    default_branch = gh.default_branch(owner, name)
    repo_ref: RepoRef = {"owner": owner, "name": name, "default_branch": default_branch}
    release_sha = _resolve_release_sha(gh, owner, name, default_branch, issue_summary)

    return {
        "issue_url": issue_url,
        "org_slug": issue_ref.org_slug,
        "issue_short_id": issue_ref.short_id,
        "issue": issue_summary,
        "repo": repo_ref,
        "release_sha": release_sha,
    }


def _resolve_release_sha(
    gh: GitHubClient, owner: str, name: str, default_branch: str, issue_summary: SentryIssueSummary
) -> str:
    """get_issue_details doesn't surface a release/tag field, so there's no SHA to read
    directly off the issue. We approximate "the code that was deployed" via the most recent
    default-branch commit before the event's timestamp; falling back to branch HEAD when no
    timestamp is available at all (e.g. markdown-only issue summary)."""
    timestamp = issue_summary.event_occurred_at or issue_summary.last_seen
    if timestamp:
        sha = gh.sha_at_or_before(owner, name, default_branch, timestamp)
        logger.info("release_sha_resolved", method="commit_before_timestamp", sha=sha, timestamp=timestamp)
        return sha
    sha = gh.branch_head_sha(owner, name, default_branch)
    logger.warning("release_sha_resolved", method="branch_head_fallback", sha=sha)
    return sha


__all__ = ["from_issue_url"]
