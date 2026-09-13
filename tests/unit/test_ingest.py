import pytest

from bugops.models.sentry import SentryIssueSummary
from bugops.nodes import ingest


class FakeSentryMCPClient:
    def __init__(self, issue_summary: SentryIssueSummary) -> None:
        self._issue_summary = issue_summary

    async def get_issue_details(self, issue_ref):
        return self._issue_summary


class FakeGitHubClient:
    def __init__(self, default_branch: str = "main", sha_at_or_before: str = "abc1234", branch_head: str = "headsha") -> None:
        self._default_branch = default_branch
        self._sha_at_or_before = sha_at_or_before
        self._branch_head = branch_head
        self.sha_at_or_before_calls: list[tuple] = []

    def default_branch(self, owner, name):
        return self._default_branch

    def sha_at_or_before(self, owner, name, branch, until_iso):
        self.sha_at_or_before_calls.append((owner, name, branch, until_iso))
        return self._sha_at_or_before

    def branch_head_sha(self, owner, name, branch):
        return self._branch_head


class FakeSettings:
    github_repo_map_json = '{"backend-api": "myorg/backend-api"}'

    def repo_map(self):
        import json

        return json.loads(self.github_repo_map_json)


@pytest.mark.asyncio
async def test_from_issue_url_resolves_release_sha_from_event_timestamp():
    issue_summary = SentryIssueSummary(
        short_id="BACKEND-1",
        project_slug="backend-api",
        event_occurred_at="2026-01-01T00:00:00Z",
        last_seen="2026-01-02T00:00:00Z",
    )
    mcp = FakeSentryMCPClient(issue_summary)
    gh = FakeGitHubClient()

    state = await ingest.from_issue_url(
        "https://my-org.sentry.io/issues/BACKEND-1", FakeSettings(), mcp, gh
    )

    assert state["repo"] == {"owner": "myorg", "name": "backend-api", "default_branch": "main"}
    assert state["release_sha"] == "abc1234"
    assert state["org_slug"] == "my-org"
    assert state["issue_short_id"] == "BACKEND-1"
    assert gh.sha_at_or_before_calls == [("myorg", "backend-api", "main", "2026-01-01T00:00:00Z")]


@pytest.mark.asyncio
async def test_from_issue_url_falls_back_to_branch_head_with_no_timestamp():
    issue_summary = SentryIssueSummary(short_id="BACKEND-1", project_slug="backend-api")
    mcp = FakeSentryMCPClient(issue_summary)
    gh = FakeGitHubClient(branch_head="deadbeef")

    state = await ingest.from_issue_url(
        "https://my-org.sentry.io/issues/BACKEND-1", FakeSettings(), mcp, gh
    )

    assert state["release_sha"] == "deadbeef"
    assert gh.sha_at_or_before_calls == []


@pytest.mark.asyncio
async def test_from_issue_url_requires_project_slug():
    issue_summary = SentryIssueSummary(short_id="BACKEND-1", project_slug=None, raw_markdown="# fallback")
    mcp = FakeSentryMCPClient(issue_summary)
    gh = FakeGitHubClient()

    with pytest.raises(ValueError, match="project slug"):
        await ingest.from_issue_url("https://my-org.sentry.io/issues/BACKEND-1", FakeSettings(), mcp, gh)


@pytest.mark.asyncio
async def test_from_issue_url_rejects_unmapped_project():
    issue_summary = SentryIssueSummary(short_id="BACKEND-1", project_slug="unmapped-project")
    mcp = FakeSentryMCPClient(issue_summary)
    gh = FakeGitHubClient()

    with pytest.raises(ValueError, match="No GitHub repo mapped"):
        await ingest.from_issue_url("https://my-org.sentry.io/issues/BACKEND-1", FakeSettings(), mcp, gh)
