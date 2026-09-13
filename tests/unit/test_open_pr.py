from pathlib import Path

import pytest

from bugops import reliability
from bugops.clients.github_client import CreatedPullRequest
from bugops.models.sentry import SentryIssueSummary
from bugops.nodes import open_pr

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
    def __init__(self, pr_outcome_store_path: Path):
        self.git_cache_dir = Path("unused")
        self.github_token = FakeSecret()
        self.sandbox_worktree_dir = Path("unused-worktrees")
        self.enable_pr_creation = True
        self.pr_draft = True
        self.pr_branch_prefix = "bugops/"
        self.pr_auto_approve = False
        self.pr_outcome_store_path = pr_outcome_store_path
        self.reliability_min_sample_size = 3
        self.reliability_merge_rate_threshold = 0.8
        self.enable_reliability_auto_approve = True


@pytest.fixture
def settings(tmp_path):
    return FakeSettings(tmp_path / "pr_outcomes.json")


class FakeGitHubClient:
    def __init__(self, pr_url="https://github.com/myorg/backend-api/pull/1", pr_number=1, raise_error=False):
        self._pr_url = pr_url
        self._pr_number = pr_number
        self._raise_error = raise_error
        self.calls = []
        self.pr_states: dict[int, str] = {}

    def create_pull_request(self, owner, name, *, title, body, head, base, draft=True):
        self.calls.append(
            {"owner": owner, "name": name, "title": title, "body": body, "head": head, "base": base, "draft": draft}
        )
        if self._raise_error:
            raise RuntimeError("422 validation failed")
        return CreatedPullRequest(url=self._pr_url, number=self._pr_number)

    def pr_state(self, owner, name, number):
        return self.pr_states.get(number, "open")


def _state(**overrides):
    state = {
        "issue": SentryIssueSummary(short_id="BACKEND-1", title="boom", culprit="parseInput"),
        "repo": {"owner": "myorg", "name": "backend-api", "default_branch": "main"},
        "release_sha": "abc1234",
        "current_diff": DIFF,
        "confidence": 0.9,
        "risk_category": "low",
        "route_decision": "suggest_pr",
        "hypotheses": [{"summary": "s", "suspected_files": [], "confidence": 0.9, "reasoning": "r"}],
        "test_attempts": [
            {"passed": True, "command": "npm test", "stdout_tail": "ok", "stderr_tail": "", "duration_s": 1.0}
        ],
    }
    state.update(overrides)
    return state


@pytest.fixture(autouse=True)
def stub_git(monkeypatch):
    fake_repo = object()
    monkeypatch.setattr(open_pr.local_repo, "ensure_clone", lambda *a, **kw: fake_repo)
    monkeypatch.setattr(open_pr.worktree, "create_worktree", lambda repo, sha, dest: dest)
    monkeypatch.setattr(open_pr.worktree, "remove_worktree", lambda repo, dest: None)
    monkeypatch.setattr(open_pr.worktree, "apply_diff", lambda dest, diff: (True, ""))
    monkeypatch.setattr(open_pr, "GitRepo", lambda dest: object())
    monkeypatch.setattr(open_pr.branch_ops, "create_branch", lambda repo, name: None)
    monkeypatch.setattr(open_pr.branch_ops, "commit_all", lambda repo, message: "deadbeef")
    monkeypatch.setattr(open_pr.branch_ops, "push_branch", lambda *a, **kw: (True, ""))


def _approve(result=True):
    calls = []

    def fn(state):
        calls.append(state)
        return result

    fn.calls = calls
    return fn


@pytest.mark.asyncio
async def test_comment_only_route_is_a_noop(settings):
    approve = _approve(True)
    gh = FakeGitHubClient()

    result = await open_pr.run(_state(route_decision="comment_only"), settings, gh, approve)

    assert result == {}
    assert approve.calls == []
    assert gh.calls == []


@pytest.mark.asyncio
async def test_pr_creation_disabled_is_a_noop(settings):
    approve = _approve(True)
    settings.enable_pr_creation = False
    gh = FakeGitHubClient()

    result = await open_pr.run(_state(), settings, gh, approve)

    assert result == {}
    assert approve.calls == []


@pytest.mark.asyncio
async def test_declined_approval_records_error_and_makes_no_api_calls(settings):
    approve = _approve(False)
    gh = FakeGitHubClient()

    result = await open_pr.run(_state(), settings, gh, approve)

    assert result == {"errors": ["open_pr_declined"]}
    assert gh.calls == []


@pytest.mark.asyncio
async def test_auto_approve_skips_the_prompt(settings):
    approve = _approve(True)
    settings.pr_auto_approve = True
    gh = FakeGitHubClient()

    result = await open_pr.run(_state(), settings, gh, approve)

    assert approve.calls == []
    assert result == {"pr_url": "https://github.com/myorg/backend-api/pull/1"}


@pytest.mark.asyncio
async def test_happy_path_opens_a_draft_pr(settings):
    approve = _approve(True)
    gh = FakeGitHubClient(pr_url="https://github.com/myorg/backend-api/pull/7", pr_number=7)

    result = await open_pr.run(_state(), settings, gh, approve)

    assert result == {"pr_url": "https://github.com/myorg/backend-api/pull/7"}
    assert len(gh.calls) == 1
    call = gh.calls[0]
    assert call["owner"] == "myorg"
    assert call["name"] == "backend-api"
    assert call["base"] == "main"
    assert call["draft"] is True
    assert call["head"].startswith("bugops/backend-1-")


@pytest.mark.asyncio
async def test_happy_path_records_a_pending_outcome(settings):
    approve = _approve(True)
    gh = FakeGitHubClient(pr_url="https://github.com/myorg/backend-api/pull/7", pr_number=7)

    await open_pr.run(_state(), settings, gh, approve)

    records = reliability.load_records(settings)
    assert records == [
        {
            "repo": "myorg/backend-api",
            "pr_number": 7,
            "pr_url": "https://github.com/myorg/backend-api/pull/7",
            "risk_category": "low",
            "status": "pending",
        }
    ]


@pytest.mark.asyncio
async def test_reliable_risk_category_skips_the_prompt(settings):
    for i in range(3):
        reliability.record_pending(
            settings, repo="myorg/backend-api", pr_number=i, pr_url=f"https://github.com/myorg/backend-api/pull/{i}",
            risk_category="low",
        )
    records = reliability.load_records(settings)
    for r in records:
        r["status"] = "merged"
    reliability.save_records(settings, records)

    approve = _approve(True)
    gh = FakeGitHubClient(pr_url="https://github.com/myorg/backend-api/pull/99", pr_number=99)

    result = await open_pr.run(_state(), settings, gh, approve)

    assert approve.calls == []
    assert result == {"pr_url": "https://github.com/myorg/backend-api/pull/99"}


@pytest.mark.asyncio
async def test_open_pr_reconciles_pending_outcomes_before_deciding(settings):
    reliability.record_pending(
        settings, repo="myorg/backend-api", pr_number=1, pr_url="https://github.com/myorg/backend-api/pull/1",
        risk_category="low",
    )
    approve = _approve(True)
    gh = FakeGitHubClient(pr_url="https://github.com/myorg/backend-api/pull/2", pr_number=2)
    gh.pr_states[1] = "merged"

    await open_pr.run(_state(), settings, gh, approve)

    records = reliability.load_records(settings)
    first = next(r for r in records if r["pr_number"] == 1)
    assert first["status"] == "merged"


@pytest.mark.asyncio
async def test_no_diff_records_error_without_touching_git(settings):
    approve = _approve(True)
    gh = FakeGitHubClient()

    result = await open_pr.run(_state(current_diff=None), settings, gh, approve)

    assert result == {"errors": ["open_pr_no_diff"]}
    assert gh.calls == []


@pytest.mark.asyncio
async def test_apply_failure_records_error(monkeypatch, settings):
    monkeypatch.setattr(open_pr.worktree, "apply_diff", lambda dest, diff: (False, "patch does not apply"))
    approve = _approve(True)
    gh = FakeGitHubClient()

    result = await open_pr.run(_state(), settings, gh, approve)

    assert result == {"errors": ["open_pr_apply_failed: patch does not apply"]}
    assert gh.calls == []


@pytest.mark.asyncio
async def test_push_failure_records_error_and_skips_api_call(monkeypatch, settings):
    monkeypatch.setattr(open_pr.branch_ops, "push_branch", lambda *a, **kw: (False, "auth failed"))
    approve = _approve(True)
    gh = FakeGitHubClient()

    result = await open_pr.run(_state(), settings, gh, approve)

    assert result == {"errors": ["open_pr_push_failed: auth failed"]}
    assert gh.calls == []


@pytest.mark.asyncio
async def test_github_api_failure_records_error(settings):
    approve = _approve(True)
    gh = FakeGitHubClient(raise_error=True)

    result = await open_pr.run(_state(), settings, gh, approve)

    assert result["errors"][0].startswith("open_pr_github_api_failed:")
