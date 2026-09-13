from pathlib import Path

import pytest

from bugops.graph import build_graph
from bugops.models.context import BlameEntry, CommitInfo
from bugops.models.fix import FixOutput
from bugops.models.hypothesis import HypothesisOutput, InvestigationConclusion
from bugops.models.sentry import SentryIssueSummary
from bugops.nodes import gather_context, generate_fix, investigate, test_fix
from bugops.sandbox.docker_runner import SandboxResult

DIFF = (
    "diff --git a/src/utils/parse.ts b/src/utils/parse.ts\n"
    "--- a/src/utils/parse.ts\n"
    "+++ b/src/utils/parse.ts\n"
    "@@ -1 +1 @@\n"
    "-line 1\n"
    "+line one\n"
)

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
    max_fix_retries = 2
    sandbox_worktree_dir = Path("unused-worktrees")
    enable_sandbox_tests = True
    repo_test_cmd_map_json = "{}"

    def repo_map(self):
        import json

        return json.loads(self.github_repo_map_json)

    def repo_test_cmd_map(self):
        return {}


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
    """Returns `conclusion` for InvestigationConclusion and `fix_output` for FixOutput."""

    def __init__(self, conclusion, fix_output=None):
        self._conclusion = conclusion
        self._fix_output = fix_output

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return FakeMessage(tool_calls=[])

    def with_structured_output(self, schema):
        if schema is FixOutput:
            return FakeFinalCall(self._fix_output)
        return FakeFinalCall(self._conclusion)


class FakeDockerRunner:
    def __init__(self, results=None):
        self._results = list(results or [])

    def check_available(self):
        pass

    def run(self, worktree_path, install_command, test_command):
        if self._results:
            return self._results.pop(0)
        return SandboxResult(passed=True, command=test_command, stdout_tail="ok", stderr_tail="", duration_s=0.1)


@pytest.fixture(autouse=True)
def stub_local_repo(monkeypatch):
    fake_repo = object()
    monkeypatch.setattr(gather_context.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)
    monkeypatch.setattr(investigate.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)
    monkeypatch.setattr(generate_fix.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)
    monkeypatch.setattr(test_fix.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)

    file_contents = {"src/utils/parse.ts": "\n".join(f"line {i}" for i in range(1, 60))}
    monkeypatch.setattr(gather_context.local_repo, "read_file_at", lambda repo, sha, path: file_contents.get(path))
    monkeypatch.setattr(investigate.local_repo, "read_file_at", lambda repo, sha, path: file_contents.get(path))
    monkeypatch.setattr(generate_fix.local_repo, "read_file_at", lambda repo, sha, path: file_contents.get(path))
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

    monkeypatch.setattr(test_fix.worktree, "create_worktree", lambda repo, sha, dest: dest)
    monkeypatch.setattr(test_fix.worktree, "remove_worktree", lambda repo, dest: None)
    monkeypatch.setattr(test_fix.worktree, "apply_diff", lambda dest, diff: (True, ""))
    monkeypatch.setattr(test_fix, "detect_test_command", lambda dest, override_map, owner, name: ("npm ci", "npm test"))


def _conclusion():
    return InvestigationConclusion(
        hypotheses=[
            HypothesisOutput(
                summary="parseInput throws on empty input",
                suspected_files=["src/utils/parse.ts"],
                confidence=0.7,
                reasoning="matches the stack trace and blame",
            )
        ]
    )


def _fix_output():
    return FixOutput(diff=DIFF, explanation="fix parsing", files_touched=["src/utils/parse.ts"])


def _issue_summary():
    return SentryIssueSummary(
        short_id="BACKEND-1",
        title="boom",
        culprit="parseInput",
        platform="node",
        project_slug="backend-api",
        event_id="evt1",
        event_occurred_at="2026-01-01T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_graph_runs_ingest_through_investigate():
    mcp = FakeSentryMCPClient(_issue_summary())
    gh = FakeGitHubClient()
    model = FakeModel(_conclusion(), fix_output=_fix_output())
    docker_runner = FakeDockerRunner()

    graph = build_graph(FakeSettings(), mcp, gh, model=model, docker_runner=docker_runner)
    state = await graph.ainvoke(
        {"issue_url": "https://my-org.sentry.io/issues/BACKEND-1", "project_slug_override": None}
    )

    assert state["issue"].short_id == "BACKEND-1"
    assert [f.file for f in state["stack_frames"]] == ["src/utils/parse.ts"]
    assert state["hypotheses"][0]["summary"] == "parseInput throws on empty input"


@pytest.mark.asyncio
async def test_graph_generates_and_tests_a_passing_fix():
    mcp = FakeSentryMCPClient(_issue_summary())
    gh = FakeGitHubClient()
    model = FakeModel(_conclusion(), fix_output=_fix_output())
    docker_runner = FakeDockerRunner(
        results=[SandboxResult(passed=True, command="npm test", stdout_tail="ok", stderr_tail="", duration_s=0.5)]
    )

    graph = build_graph(FakeSettings(), mcp, gh, model=model, docker_runner=docker_runner)
    state = await graph.ainvoke(
        {"issue_url": "https://my-org.sentry.io/issues/BACKEND-1", "project_slug_override": None}
    )

    assert state["current_diff"] == DIFF
    assert state["test_attempts"][-1]["passed"] is True
    assert "drop_reason" not in state


@pytest.mark.asyncio
async def test_graph_retries_generate_fix_until_max_retries_then_stops():
    mcp = FakeSentryMCPClient(_issue_summary())
    gh = FakeGitHubClient()
    model = FakeModel(_conclusion(), fix_output=_fix_output())
    settings = FakeSettings()
    settings.max_fix_retries = 2
    docker_runner = FakeDockerRunner(
        results=[
            SandboxResult(passed=False, command="npm test", stdout_tail="", stderr_tail="fail 1", duration_s=0.1),
            SandboxResult(passed=False, command="npm test", stdout_tail="", stderr_tail="fail 2", duration_s=0.1),
        ]
    )

    graph = build_graph(settings, mcp, gh, model=model, docker_runner=docker_runner)
    state = await graph.ainvoke(
        {"issue_url": "https://my-org.sentry.io/issues/BACKEND-1", "project_slug_override": None}
    )

    assert state["retry_count"] == 2
    assert len(state["test_attempts"]) == 2
    assert state["drop_reason"] == "max_fix_retries_exhausted"
    assert state["current_diff"] == DIFF
