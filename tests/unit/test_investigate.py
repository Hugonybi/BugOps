from pathlib import Path

import pytest

from bugops.models.hypothesis import HypothesisOutput, InvestigationConclusion
from bugops.models.sentry import SentryIssueSummary
from bugops.nodes import investigate


class FakeSecret:
    def get_secret_value(self):
        return "fake-token"


class FakeSettings:
    git_cache_dir = Path("unused")
    github_token = FakeSecret()
    max_investigate_rounds = 4


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
    """Hand-rolled stand-in for a LangChain chat model: implements only bind_tools,
    ainvoke, and with_structured_output, the surface investigate.run actually calls."""

    def __init__(self, responses, conclusion=None, structured_output_error=None):
        self._responses = list(responses)
        self._conclusion = conclusion
        self._structured_output_error = structured_output_error

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return self._responses.pop(0)

    def with_structured_output(self, schema):
        if self._structured_output_error:
            raise self._structured_output_error
        return FakeFinalCall(self._conclusion)


def _state():
    return {
        "issue": SentryIssueSummary(short_id="BACKEND-1", title="boom", culprit="parseInput", platform="node"),
        "stack_trace_markdown": "trace",
        "breadcrumbs_markdown": "crumbs",
        "source_files": {},
        "blame": {},
        "related_commits": {},
        "repo": {"owner": "myorg", "name": "backend-api", "default_branch": "main"},
        "release_sha": "abc1234",
    }


@pytest.fixture(autouse=True)
def stub_local_repo(monkeypatch):
    fake_repo = object()
    monkeypatch.setattr(investigate.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)

    file_contents = {"src/utils/helpers.ts": "export function helper() {}"}
    monkeypatch.setattr(investigate.local_repo, "read_file_at", lambda repo, sha, path: file_contents.get(path))


@pytest.mark.asyncio
async def test_investigate_happy_path_reads_a_file_then_concludes():
    tool_call_response = FakeMessage(
        tool_calls=[{"name": "read_source_file", "args": {"path": "src/utils/helpers.ts"}, "id": "call1"}]
    )
    final_response = FakeMessage(tool_calls=[])
    conclusion = InvestigationConclusion(
        hypotheses=[
            HypothesisOutput(
                summary="helper() mutates shared state",
                suspected_files=["src/utils/helpers.ts"],
                confidence=0.8,
                reasoning="the helper is called from the crashing frame",
            )
        ]
    )
    model = FakeModel([tool_call_response, final_response], conclusion=conclusion)

    result = await investigate.run(_state(), FakeSettings(), model)

    assert result["hypotheses"] == [
        {
            "summary": "helper() mutates shared state",
            "suspected_files": ["src/utils/helpers.ts"],
            "confidence": 0.8,
            "reasoning": "the helper is called from the crashing frame",
        }
    ]
    assert result["investigate_round"] == 2
    assert result["investigate_tool_calls"] == [
        {"round": 1, "name": "read_source_file", "args": {"path": "src/utils/helpers.ts"}}
    ]


@pytest.mark.asyncio
async def test_investigate_stops_at_round_cap():
    def make_tool_call_response():
        return FakeMessage(tool_calls=[{"name": "read_source_file", "args": {"path": "x"}, "id": "call"}])

    settings = FakeSettings()
    settings.max_investigate_rounds = 2
    conclusion = InvestigationConclusion(hypotheses=[])
    model = FakeModel([make_tool_call_response() for _ in range(5)], conclusion=conclusion)

    result = await investigate.run(_state(), settings, model)

    assert result["investigate_round"] == 2
    assert len(result["investigate_tool_calls"]) == 2


@pytest.mark.asyncio
async def test_investigate_reports_missing_file_as_error_string_not_exception():
    tool_call_response = FakeMessage(
        tool_calls=[{"name": "read_source_file", "args": {"path": "does/not/exist.ts"}, "id": "call1"}]
    )
    final_response = FakeMessage(tool_calls=[])
    conclusion = InvestigationConclusion(hypotheses=[])
    model = FakeModel([tool_call_response, final_response], conclusion=conclusion)

    result = await investigate.run(_state(), FakeSettings(), model)

    assert result["hypotheses"] == []


@pytest.mark.asyncio
async def test_investigate_handles_structured_output_failure():
    final_response = FakeMessage(tool_calls=[])
    model = FakeModel([final_response], structured_output_error=RuntimeError("model refused"))

    result = await investigate.run(_state(), FakeSettings(), model)

    assert "hypotheses" not in result
    assert any("structured output failed" in e for e in result["errors"])
