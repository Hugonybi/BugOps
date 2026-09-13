from __future__ import annotations

import asyncio

from bugops.clients.github_client import GitHubClient
from bugops.clients.sentry_mcp import SentryMCPClient
from bugops.config import Settings
from bugops.git import local_repo
from bugops.logging import get_logger
from bugops.models.context import parse_js_frames
from bugops.models.sentry import IssueRef
from bugops.state import BugOpsState

logger = get_logger(__name__)


async def run(state: BugOpsState, settings: Settings, mcp: SentryMCPClient, gh: GitHubClient) -> BugOpsState:
    issue_ref = IssueRef(org_slug=state["org_slug"], short_id=state["issue_short_id"], issue_url=state["issue_url"])
    event_id = state["issue"].event_id or "latest"

    stack_trace_markdown, breadcrumbs_markdown = await asyncio.gather(
        mcp.get_event_stacktrace(issue_ref, event_id),
        mcp.get_issue_breadcrumbs(issue_ref, event_id),
    )

    frames = parse_js_frames(stack_trace_markdown or "")[: settings.max_context_frames]

    repo = state["repo"]
    local = local_repo.ensure_clone(settings.git_cache_dir, repo["owner"], repo["name"], settings.github_token.get_secret_value())
    release_sha = state["release_sha"]

    source_files: dict[str, str] = {}
    blame: dict[str, list] = {}
    related_commits: dict[str, list] = {}

    seen_files: set[str] = set()
    for frame in frames:
        if frame.file in seen_files:
            continue
        seen_files.add(frame.file)

        content = local_repo.read_file_at(local, release_sha, frame.file)
        if content is None:
            logger.warning("source_file_not_found", file=frame.file, release_sha=release_sha)
            continue
        source_files[frame.file] = content
        blame[frame.file] = local_repo.blame(local, release_sha, frame.file, frame.line)
        commits = local_repo.recent_commits(local, release_sha, frame.file)
        for commit in commits[:3]:  # PR lookup is a GitHub API call each; only enrich the most recent few
            commit.pr_url = gh.pr_url_for_commit(repo["owner"], repo["name"], commit.sha)
        related_commits[frame.file] = commits

    return {
        **state,
        "stack_trace_markdown": stack_trace_markdown,
        "breadcrumbs_markdown": breadcrumbs_markdown,
        "stack_frames": frames,
        "source_files": source_files,
        "blame": blame,
        "related_commits": related_commits,
    }
