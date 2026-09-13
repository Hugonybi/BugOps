from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel


class IssueRef(BaseModel):
    """Identifies a Sentry issue well enough to call any of the MCP tools we use.

    get_issue_details/get_issue_breadcrumbs accept issue_url directly; get_event_stacktrace
    requires org_slug + short_id explicitly, so we always resolve both up front.
    """

    org_slug: str
    short_id: str
    issue_url: str


def parse_issue_url(issue_url: str) -> IssueRef:
    """Parses a Sentry issue URL, e.g. https://my-org.sentry.io/issues/PROJECT-1Z43."""
    parsed = urlparse(issue_url)
    org_slug = parsed.hostname.split(".")[0] if parsed.hostname else ""
    short_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if not org_slug or not short_id:
        raise ValueError(f"Could not parse org slug / issue short id from {issue_url!r}")
    return IssueRef(org_slug=org_slug, short_id=short_id.upper(), issue_url=issue_url)


class SentryIssueSummary(BaseModel):
    """Best-effort structured view of get_issue_details.

    The MCP server only guarantees structured_content once an org is on Sentry's shared
    formatter rollout; otherwise every field here is None and callers fall back to
    `raw_markdown`.
    """

    short_id: str
    title: str | None = None
    culprit: str | None = None
    project_slug: str | None = None
    platform: str | None = None
    status: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    occurrences: int | None = None
    users_impacted: int | None = None
    url: str | None = None
    event_id: str | None = None
    event_occurred_at: str | None = None
    raw_markdown: str | None = None
