from pathlib import Path

import pytest

from bugops.graph import build_graph
from bugops.models.context import BlameEntry, CommitInfo
from bugops.models.hypothesis import HypothesisOutput, InvestigationConclusion
from bugops.models.sentry import SentryIssueSummary
from bugops.nodes import gather_context, investigate

STACK_TRACE_MARKDOWN = """```
src/utils/parse.ts:42:7 (parseInput)
```
"""


class FakeSentryMCPClient:
    def __init__(self, issue_summary: SentryIssueSummary) -> None:
        self._issue_summary = issue_summary

    async def get_issue_details(self, issue_ref):
        return self._issue_summary

    async def get_event_stacktrace(self, issue_ref, event_id="latest"):
        return STACK_TRACE_MARKDOWN

    async def get_issue_breadcrumbs(self, issue_ref, event_id="latest"):
        return "## Breadcrumbs\n\n- clicked submit"


class FakeGitHubClient:
    def default_branch(self, owner, name):
        return "main"

    def sha_at_or_before(self, owner, name, branch, until_iso):
        return "abc1234"

    def branch_head_sha(self, owner, name, branch):
        return "abc1234"

    def pr_url_for_commit(self, owner, name, sha):
        return None


class FakeSecret:
    def get_secret_value(self):
        return "fake-token"


class FakeSettings:
    github_token = FakeSecret()
    github_repo_map_json = '{"backend-api": "myorg/backend-api"}'
    git_cache_dir = Path("unused")
    max_context_frames = 15
    max_investigate_rounds = 4

    def repo_map(self):
        import json

        return json.loads(self.github_repo_map_json)


class FakeMessage:
    def __init__(self, tool_calls=None):
        self.content = ""
        self.tool_calls = tool_calls or []


class FakeFinalCall:
    def __init__(self, conclusion):
        self._conclusion = conclusion

    async def ainvoke(self, messages):
        return self._conclusion


class FakeModel:
    def __init__(self, conclusion):
        self._conclusion = conclusion

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return FakeMessage(tool_calls=[])

    def with_structured_output(self, schema):
        return FakeFinalCall(self._conclusion)


@pytest.fixture(autouse=True)
def stub_local_repo(monkeypatch):
    fake_repo = object()
    monkeypatch.setattr(gather_context.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)
    monkeypatch.setattr(investigate.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)

    file_contents = {"src/utils/parse.ts": "\n".join(f"line {i}" for i in range(1, 60))}
    monkeypatch.setattr(gather_context.local_repo, "read_file_at", lambda repo, sha, path: file_contents.get(path))
    monkeypatch.setattr(investigate.local_repo, "read_file_at", lambda repo, sha, path: file_contents.get(path))
    monkeypatch.setattr(
        gather_context.local_repo,
        "blame",
        lambda repo, sha, path, line: [
            BlameEntry(line_no=line, commit_sha="commit1", author="alice", date="2026-01-01", summary="fix parsing")
        ],
    )
    monkeypatch.setattr(
        gather_context.local_repo,
        "recent_commits",
        lambda repo, sha, path, max_count=10: [
            CommitInfo(sha="commit1", author="alice", date="2026-01-01", message="fix parsing")
        ],
    )


@pytest.mark.asyncio
async def test_graph_runs_ingest_through_investigate():
    issue_summary = SentryIssueSummary(
        short_id="BACKEND-1",
        title="boom",
        culprit="parseInput",
        platform="node",
        project_slug="backend-api",
        event_id="evt1",
        event_occurred_at="2026-01-01T00:00:00Z",
    )
    mcp = FakeSentryMCPClient(issue_summary)
    gh = FakeGitHubClient()
    conclusion = InvestigationConclusion(
        hypotheses=[
            HypothesisOutput(
                summary="parseInput throws on empty input",
                suspected_files=["src/utils/parse.ts"],
                confidence=0.7,
                reasoning="matches the stack trace and blame",
            )
        ]
    )
    model = FakeModel(conclusion)

    graph = build_graph(FakeSettings(), mcp, gh, model=model)
    state = await graph.ainvoke(
        {"issue_url": "https://my-org.sentry.io/issues/BACKEND-1", "project_slug_override": None}
    )

    assert state["issue"].short_id == "BACKEND-1"
    assert [f.file for f in state["stack_frames"]] == ["src/utils/parse.ts"]
    assert state["hypotheses"][0]["summary"] == "parseInput throws on empty input"
