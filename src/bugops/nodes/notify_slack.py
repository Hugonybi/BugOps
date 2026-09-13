from __future__ import annotations

import asyncio

from slack_sdk.errors import SlackApiError

from bugops.clients.slack_client import SlackClient
from bugops.config import Settings
from bugops.logging import get_logger
from bugops.state import BugOpsState

logger = get_logger(__name__)


def _build_message(state: BugOpsState) -> str:
    issue = state["issue"]
    header = f"*{issue.title or issue.short_id}*  (<{issue.url or ''}|{issue.short_id}>)"
    culprit_line = f"culprit: `{issue.culprit}`" if issue.culprit else ""

    drop_reason = state.get("drop_reason")
    passed = bool(state.get("test_attempts")) and state["test_attempts"][-1]["passed"]

    if drop_reason or not passed:
        reason = drop_reason or "tests failed after exhausting retries"
        tail = ""
        if state.get("test_attempts"):
            tail = f"\nlast stderr (tail):\n```{state['test_attempts'][-1]['stderr_tail']}```"
        body = f":x: *Could not fix.* reason: `{reason}`{tail}"
    else:
        route = state.get("route_decision")
        confidence = state.get("confidence")
        risk = state.get("risk_category")
        decision_line = f"confidence: {confidence:.2f}   risk: {risk}   route: {route}"
        if route == "suggest_pr":
            pr_url = state.get("pr_url")
            pr_errors = [e for e in state.get("errors", []) if e.startswith("open_pr_")]
            if pr_url:
                decision_line += f"\n:rocket: opened draft PR: {pr_url}"
            elif "open_pr_declined" in pr_errors:
                decision_line += "\n_(PR not opened — declined during manual approval)_"
            elif pr_errors:
                decision_line += f"\n:warning: PR creation failed: `{pr_errors[0]}`"
            else:
                decision_line += "  _(PR creation disabled)_"
        diff_block = f"\n```diff\n{state.get('current_diff', '')}\n```"
        body = f":white_check_mark: *Suggested fix.*\n{decision_line}{diff_block}"

    return "\n".join(part for part in [header, culprit_line, body] if part)


def _run_sync(state: BugOpsState, settings: Settings, slack_client: SlackClient | None) -> BugOpsState:
    if not settings.enable_slack_notify or slack_client is None or not settings.slack_default_channel:
        logger.info("notify_slack_skipped", enabled=settings.enable_slack_notify)
        return {}

    message = _build_message(state)
    try:
        ts = slack_client.post_message(settings.slack_default_channel, message)
    except SlackApiError as exc:
        logger.warning("notify_slack_post_failed", error=str(exc))
        return {}
    return {"slack_thread_ts": ts} if ts else {}


async def run(state: BugOpsState, settings: Settings, slack_client: SlackClient | None) -> BugOpsState:
    return await asyncio.to_thread(_run_sync, state, settings, slack_client)


__all__ = ["run"]
