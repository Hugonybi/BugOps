from __future__ import annotations

from langchain_core.tools import tool

from bugops.git import local_repo
from bugops.state import BugOpsState


def make_read_file_tool(repo, sha: str):
    """Shared by investigate and generate_fix: lets the model pull in a file from the same
    local clone that isn't already part of the gathered context."""

    @tool
    def read_source_file(path: str) -> str:
        """Read a file from the repo at the investigated commit, by repo-relative path,
        for files not already included in the gathered context."""
        content = local_repo.read_file_at(repo, sha, path)
        if content is None:
            return f"ERROR: {path!r} not found at {sha}"
        return content

    return read_source_file


def build_context_message(state: BugOpsState) -> str:
    issue = state["issue"]
    parts = [
        f"## Issue\n{issue.title}\nculprit: {issue.culprit}\nplatform: {issue.platform}",
        f"## Stack Trace\n{state.get('stack_trace_markdown') or '(none)'}",
        f"## Breadcrumbs\n{state.get('breadcrumbs_markdown') or '(none)'}",
    ]
    for path, content in state.get("source_files", {}).items():
        parts.append(f"## Source: {path}\n```\n{content}\n```")
        for b in state.get("blame", {}).get(path, []):
            parts.append(f"blame {path}:{b.line_no} — {b.commit_sha[:8]} {b.author} {b.summary}")
        for c in state.get("related_commits", {}).get(path, []):
            parts.append(f"commit {c.sha[:8]} {c.author} {c.message} {c.pr_url or ''}")
    return "\n\n".join(parts)


__all__ = ["build_context_message", "make_read_file_tool"]
