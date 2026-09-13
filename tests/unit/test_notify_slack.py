import pytest

from bugops.models.sentry import SentryIssueSummary
from bugops.nodes import notify_slack

DIFF = (
    "diff --git a/src/index.ts b/src/index.ts\n"
    "--- a/src/index.ts\n"
    "+++ b/src/index.ts\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


class FakeSlackClient:
    def __init__(self, ts="171234.5678"):
        self._ts = ts
        self.calls = []

    def post_message(self, channel, text):
        self.calls.append((channel, text))
        return self._ts


class FakeSettings:
    enable_slack_notify = True
    slack_default_channel = "#bugops"


def _state(**overrides):
    state = {
        "issue": SentryIssueSummary(
            short_id="BACKEND-1", title="boom", culprit="parseInput", platform="node", url="https://sentry.io/x"
        ),
        "current_diff": DIFF,
        "confidence": 0.9,
        "risk_category": "low",
        "route_decision": "comment_only",
        "test_attempts": [
            {"passed": True, "command": "npm test", "stdout_tail": "ok", "stderr_tail": "", "duration_s": 1.0}
        ],
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_passing_fix_posts_message_with_issue_and_diff():
    client = FakeSlackClient()

    result = await notify_slack.run(_state(), FakeSettings(), client)

    assert len(client.calls) == 1
    channel, text = client.calls[0]
    assert channel == "#bugops"
    assert "BACKEND-1" in text
    assert "new" in text
    assert result == {"slack_thread_ts": "171234.5678"}


@pytest.mark.asyncio
async def test_suggest_pr_route_with_pr_url_mentions_the_pr_link():
    client = FakeSlackClient()

    await notify_slack.run(
        _state(route_decision="suggest_pr", pr_url="https://github.com/myorg/backend-api/pull/42"),
        FakeSettings(),
        client,
    )

    assert "https://github.com/myorg/backend-api/pull/42" in client.calls[0][1]


@pytest.mark.asyncio
async def test_suggest_pr_route_declined_mentions_declined():
    client = FakeSlackClient()

    await notify_slack.run(
        _state(route_decision="suggest_pr", errors=["open_pr_declined"]), FakeSettings(), client
    )

    assert "declined" in client.calls[0][1]


@pytest.mark.asyncio
async def test_suggest_pr_route_failed_mentions_failure():
    client = FakeSlackClient()

    await notify_slack.run(
        _state(route_decision="suggest_pr", errors=["open_pr_push_failed: auth failed"]), FakeSettings(), client
    )

    assert "failed" in client.calls[0][1]
    assert "auth failed" in client.calls[0][1]


@pytest.mark.asyncio
async def test_comment_only_route_does_not_mention_pr():
    client = FakeSlackClient()

    await notify_slack.run(_state(route_decision="comment_only"), FakeSettings(), client)

    assert "PR" not in client.calls[0][1]


@pytest.mark.asyncio
async def test_dropped_attempt_posts_drop_reason():
    client = FakeSlackClient()
    state = _state(drop_reason="sandbox_tests_disabled", test_attempts=[])

    result = await notify_slack.run(state, FakeSettings(), client)

    assert len(client.calls) == 1
    assert "sandbox_tests_disabled" in client.calls[0][1]
    assert result == {"slack_thread_ts": "171234.5678"}


@pytest.mark.asyncio
async def test_failed_attempt_includes_stderr_tail():
    client = FakeSlackClient()
    state = _state(
        drop_reason="max_fix_retries_exhausted",
        test_attempts=[
            {"passed": False, "command": "npm test", "stdout_tail": "", "stderr_tail": "TypeError boom", "duration_s": 1.0}
        ],
    )

    await notify_slack.run(state, FakeSettings(), client)

    assert "TypeError boom" in client.calls[0][1]


@pytest.mark.asyncio
async def test_disabled_flag_skips_and_returns_empty():
    client = FakeSlackClient()
    settings = FakeSettings()
    settings.enable_slack_notify = False

    result = await notify_slack.run(_state(), settings, client)

    assert client.calls == []
    assert result == {}
    assert "drop_reason" not in result


@pytest.mark.asyncio
async def test_no_client_configured_skips_and_returns_empty():
    result = await notify_slack.run(_state(), FakeSettings(), None)

    assert result == {}


@pytest.mark.asyncio
async def test_unset_channel_skips_and_returns_empty():
    client = FakeSlackClient()
    settings = FakeSettings()
    settings.slack_default_channel = None

    result = await notify_slack.run(_state(), settings, client)

    assert client.calls == []
    assert result == {}
