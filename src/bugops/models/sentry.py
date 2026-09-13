from __future__ import annotations

import re
from urllib.parse import urlparse

from pydantic import BaseModel

# Matches the issue id segment right after "/issues/", ignoring anything after it (e.g. a
# trailing "/events/<event_id>/" when the URL was copied from a specific event's permalink).
# Sentry issue ids in the URL are either numeric ("141205107") or a short id ("PROJECT-1Z43").
_ISSUE_ID_RE = re.compile(r"/issues/([^/]+)")


class IssueRef(BaseModel):
    """Identifies a Sentry issue well enough to call any of the MCP tools we use.

    get_issue_details/get_issue_breadcrumbs accept issue_url directly; get_event_stacktrace
    requires org_slug + short_id explicitly, so we always resolve both up front.
    """

    org_slug: str
    short_id: str
    issue_url: str


def parse_issue_url(issue_url: str) -> IssueRef:
    """Parses a Sentry issue URL. Handles both a bare issue URL
    (https://my-org.sentry.io/issues/PROJECT-1Z43) and a specific-event permalink
    (https://my-org.sentry.io/issues/141205107/events/<event_id>/) — only the issue id
    matters here, so anything after it in the path is ignored."""
    parsed = urlparse(issue_url)
    org_slug = parsed.hostname.split(".")[0] if parsed.hostname else ""
    match = _ISSUE_ID_RE.search(parsed.path)
    short_id = match.group(1) if match else ""
    if not org_slug or not short_id:
        raise ValueError(f"Could not parse org slug / issue id from {issue_url!r}")
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
