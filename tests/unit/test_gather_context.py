from pathlib import Path

import pytest

from bugops.models.context import BlameEntry, CommitInfo
from bugops.models.sentry import SentryIssueSummary
from bugops.nodes import gather_context

STACK_TRACE_MARKDOWN = """## Selected Thread

**Selection**: Sentry default selection
**Thread ID**: 1
**Name**: main
**State**: crashed
**Crashed**: true
**Current**: true

## Stacktrace

**Full Stacktrace:**
```
src/utils/parse.ts:42:7 (parseInput)
src/index.ts:10:3 (main)
```
"""


class FakeSentryMCPClient:
    async def get_event_stacktrace(self, issue_ref, event_id="latest"):
        return STACK_TRACE_MARKDOWN

    async def get_issue_breadcrumbs(self, issue_ref, event_id="latest"):
        return "## Breadcrumbs\n\n- navigated to /checkout\n- clicked submit"


class FakeGitHubClient:
    def pr_url_for_commit(self, owner, name, sha):
        return f"https://github.com/{owner}/{name}/pull/1" if sha == "commit1" else None


class FakeSecret:
    def get_secret_value(self):
        return "fake-token"


class FakeSettings:
    git_cache_dir = Path("unused")
    github_token = FakeSecret()
    max_context_frames = 15


@pytest.fixture(autouse=True)
def stub_local_repo(monkeypatch):
    fake_repo = object()
    monkeypatch.setattr(gather_context.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)

    file_contents = {
        "src/utils/parse.ts": "\n".join(f"line {i}" for i in range(1, 60)),
    }
    monkeypatch.setattr(gather_context.local_repo, "read_file_at", lambda repo, sha, path: file_contents.get(path))
    monkeypatch.setattr(
        gather_context.local_repo,
        "blame",
        lambda repo, sha, path, line: [BlameEntry(line_no=line, commit_sha="commit1", author="alice", date="2026-01-01", summary="fix parsing")],
    )
    monkeypatch.setattr(
        gather_context.local_repo,
        "recent_commits",
        lambda repo, sha, path, max_count=10: [
            CommitInfo(sha="commit1", author="alice", date="2026-01-01", message="fix parsing")
        ],
    )


@pytest.mark.asyncio
async def test_gather_context_populates_state_from_parsed_frames():
    state = {
        "issue_url": "https://my-org.sentry.io/issues/BACKEND-1",
        "org_slug": "my-org",
        "issue_short_id": "BACKEND-1",
        "issue": SentryIssueSummary(short_id="BACKEND-1", event_id="evt1"),
        "repo": {"owner": "myorg", "name": "backend-api", "default_branch": "main"},
        "release_sha": "abc1234",
    }

    result = await gather_context.run(state, FakeSettings(), FakeSentryMCPClient(), FakeGitHubClient())

    assert result["stack_trace_markdown"] == STACK_TRACE_MARKDOWN
    assert [f.file for f in result["stack_frames"]] == ["src/utils/parse.ts", "src/index.ts"]

    # src/index.ts isn't in the fake file_contents map, so it should be skipped, not error
    assert set(result["source_files"]) == {"src/utils/parse.ts"}
    assert result["blame"]["src/utils/parse.ts"][0].commit_sha == "commit1"
    assert result["related_commits"]["src/utils/parse.ts"][0].pr_url == "https://github.com/myorg/backend-api/pull/1"
