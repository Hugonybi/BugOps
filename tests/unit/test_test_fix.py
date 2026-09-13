from pathlib import Path

import pytest

from bugops.nodes import test_fix
from bugops.sandbox.docker_runner import DockerUnavailableError, SandboxResult

DIFF = (
    "diff --git a/src/index.ts b/src/index.ts\n"
    "--- a/src/index.ts\n"
    "+++ b/src/index.ts\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


class FakeSecret:
    def get_secret_value(self):
        return "fake-token"


class FakeSettings:
    git_cache_dir = Path("unused")
    github_token = FakeSecret()
    sandbox_worktree_dir = Path("unused-worktrees")
    max_fix_retries = 3
    enable_sandbox_tests = True
    repo_test_cmd_map_json = "{}"

    def repo_test_cmd_map(self):
        return {}


class FakeDockerRunner:
    def __init__(self, result=None, unavailable=False):
        self._result = result
        self._unavailable = unavailable
        self.ran_with = None

    def check_available(self):
        if self._unavailable:
            raise DockerUnavailableError("no daemon")

    def run(self, worktree_path, install_command, test_command):
        self.ran_with = (worktree_path, install_command, test_command)
        return self._result


def _state(**overrides):
    state = {
        "repo": {"owner": "myorg", "name": "backend-api", "default_branch": "main"},
        "release_sha": "abc1234",
        "current_diff": DIFF,
    }
    state.update(overrides)
    return state


@pytest.fixture(autouse=True)
def stub_git(monkeypatch):
    fake_repo = object()
    monkeypatch.setattr(test_fix.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)
    monkeypatch.setattr(test_fix.worktree, "create_worktree", lambda repo, sha, dest: dest)
    monkeypatch.setattr(test_fix.worktree, "remove_worktree", lambda repo, dest: None)
    monkeypatch.setattr(test_fix.worktree, "apply_diff", lambda dest, diff: (True, ""))
    monkeypatch.setattr(
        test_fix,
        "detect_test_command",
        lambda dest, override_map, owner, name: ("npm ci", "npm test"),
    )


@pytest.mark.asyncio
async def test_test_fix_records_passing_attempt_and_stops():
    runner = FakeDockerRunner(
        result=SandboxResult(passed=True, command="npm test", stdout_tail="ok", stderr_tail="", duration_s=1.2)
    )

    result = await test_fix.run(_state(), FakeSettings(), runner)

    assert result["test_attempts"] == [
        {"passed": True, "command": "npm test", "stdout_tail": "ok", "stderr_tail": "", "duration_s": 1.2}
    ]
    assert result["retry_count"] == 1
    assert "drop_reason" not in result


@pytest.mark.asyncio
async def test_test_fix_sets_drop_reason_at_max_retries():
    runner = FakeDockerRunner(
        result=SandboxResult(passed=False, command="npm test", stdout_tail="", stderr_tail="fail", duration_s=1.0)
    )
    settings = FakeSettings()
    settings.max_fix_retries = 2

    result = await test_fix.run(_state(retry_count=1), settings, runner)

    assert result["retry_count"] == 2
    assert result["drop_reason"] == "max_fix_retries_exhausted"


@pytest.mark.asyncio
async def test_test_fix_handles_docker_unavailable():
    runner = FakeDockerRunner(unavailable=True)

    result = await test_fix.run(_state(), FakeSettings(), runner)

    assert result["drop_reason"] == "docker_unavailable"
    assert "test_attempts" not in result


@pytest.mark.asyncio
async def test_test_fix_apply_failure_recorded_as_failed_attempt(monkeypatch):
    monkeypatch.setattr(test_fix.worktree, "apply_diff", lambda dest, diff: (False, "patch does not apply"))
    runner = FakeDockerRunner()

    result = await test_fix.run(_state(), FakeSettings(), runner)

    assert result["test_attempts"][0]["passed"] is False
    assert result["test_attempts"][0]["command"] == "git apply"
    assert runner.ran_with is None


@pytest.mark.asyncio
async def test_test_fix_no_test_command_detected(monkeypatch):
    monkeypatch.setattr(test_fix, "detect_test_command", lambda *a, **kw: None)
    runner = FakeDockerRunner()

    result = await test_fix.run(_state(), FakeSettings(), runner)

    assert result["drop_reason"] == "no_test_command_detected"


@pytest.mark.asyncio
async def test_test_fix_disabled_by_feature_flag():
    settings = FakeSettings()
    settings.enable_sandbox_tests = False
    runner = FakeDockerRunner()

    result = await test_fix.run(_state(), settings, runner)

    assert result["drop_reason"] == "sandbox_tests_disabled"
