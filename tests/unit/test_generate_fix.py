from pathlib import Path

import pytest

from bugops.models.fix import FixOutput
from bugops.models.sentry import SentryIssueSummary
from bugops.nodes import generate_fix

VALID_DIFF = (
    "diff --git a/src/utils/helpers.ts b/src/utils/helpers.ts\n"
    "index 1111111..2222222 100644\n"
    "--- a/src/utils/helpers.ts\n"
    "+++ b/src/utils/helpers.ts\n"
    "@@ -1 +1 @@\n"
    "-export function helper() {}\n"
    "+export function helper() { return null; }\n"
)


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
    def __init__(self, result):
        self._result = result

    async def ainvoke(self, messages):
        return self._result


class FakeModel:
    def __init__(self, responses, fix_output=None, structured_output_error=None):
        self._responses = list(responses)
        self._fix_output = fix_output
        self._structured_output_error = structured_output_error

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return self._responses.pop(0)

    def with_structured_output(self, schema):
        if self._structured_output_error:
            raise self._structured_output_error
        return FakeFinalCall(self._fix_output)


def _state(**overrides):
    state = {
        "issue": SentryIssueSummary(short_id="BACKEND-1", title="boom", culprit="parseInput", platform="node"),
        "stack_trace_markdown": "trace",
        "breadcrumbs_markdown": "crumbs",
        "source_files": {},
        "blame": {},
        "related_commits": {},
        "repo": {"owner": "myorg", "name": "backend-api", "default_branch": "main"},
        "release_sha": "abc1234",
        "hypotheses": [
            {
                "summary": "helper() returns undefined",
                "suspected_files": ["src/utils/helpers.ts"],
                "confidence": 0.8,
                "reasoning": "matches the crash",
            }
        ],
    }
    state.update(overrides)
    return state


@pytest.fixture(autouse=True)
def stub_local_repo(monkeypatch):
    fake_repo = object()
    monkeypatch.setattr(generate_fix.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)
    monkeypatch.setattr(generate_fix.local_repo, "read_file_at", lambda repo, sha, path: None)


@pytest.mark.asyncio
async def test_generate_fix_happy_path_sets_current_diff():
    final_response = FakeMessage(tool_calls=[])
    fix_output = FixOutput(
        diff=VALID_DIFF, explanation="return null instead of undefined", files_touched=["src/utils/helpers.ts"]
    )
    model = FakeModel([final_response], fix_output=fix_output)

    result = await generate_fix.run(_state(), FakeSettings(), model)

    assert result["current_diff"] == VALID_DIFF
    assert result["diff_history"] == [VALID_DIFF]


@pytest.mark.asyncio
async def test_generate_fix_includes_previous_failure_on_retry():
    seen_messages = []

    class RecordingModel(FakeModel):
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            seen_messages.extend(messages)
            return self._responses.pop(0)

    final_response = FakeMessage(tool_calls=[])
    fix_output = FixOutput(diff=VALID_DIFF, explanation="retry fix", files_touched=["src/utils/helpers.ts"])
    model = RecordingModel([final_response], fix_output=fix_output)

    state = _state(
        current_diff="old diff",
        test_attempts=[
            {
                "passed": False,
                "command": "npm test",
                "stdout_tail": "...",
                "stderr_tail": "TypeError: undefined",
                "duration_s": 1.0,
            }
        ],
        retry_count=1,
    )

    await generate_fix.run(state, FakeSettings(), model)

    combined = " ".join(m.content for m in seen_messages if hasattr(m, "content"))
    assert "TypeError: undefined" in combined
    assert "old diff" in combined


@pytest.mark.asyncio
async def test_generate_fix_rejects_diff_with_path_traversal():
    final_response = FakeMessage(tool_calls=[])
    bad_diff = (
        "diff --git a/../../etc/passwd b/../../etc/passwd\n"
        "--- a/../../etc/passwd\n"
        "+++ b/../../etc/passwd\n"
        "@@ -1 +1 @@\n"
        "-root\n"
        "+pwned\n"
    )
    fix_output = FixOutput(diff=bad_diff, explanation="escape", files_touched=["../../etc/passwd"])
    model = FakeModel([final_response], fix_output=fix_output)

    result = await generate_fix.run(_state(), FakeSettings(), model)

    assert "current_diff" not in result
    assert any("rejected diff" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_generate_fix_handles_structured_output_failure():
    final_response = FakeMessage(tool_calls=[])
    model = FakeModel([final_response], structured_output_error=RuntimeError("model refused"))

    result = await generate_fix.run(_state(), FakeSettings(), model)

    assert "current_diff" not in result
    assert any("structured output failed" in e for e in result["errors"])
