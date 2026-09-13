from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from bugops.models.context import BlameEntry, CommitInfo, ParsedFrame
from bugops.models.sentry import SentryIssueSummary


class RepoRef(TypedDict):
    owner: str
    name: str
    default_branch: str


class Hypothesis(TypedDict):
    summary: str
    suspected_files: list[str]
    confidence: float
    reasoning: str


class TestResult(TypedDict):
    passed: bool
    command: str
    stdout_tail: str
    stderr_tail: str
    duration_s: float


class BugOpsState(TypedDict, total=False):
    # --- ingest (phase 1) ---
    issue_url: str
    project_slug_override: str | None
    org_slug: str
    issue_short_id: str
    issue: SentryIssueSummary
    repo: RepoRef
    release_sha: str | None

    # --- gather_context (phase 1) ---
    stack_trace_markdown: str | None
    breadcrumbs_markdown: str | None
    stack_frames: list[ParsedFrame]
    source_files: dict[str, str]
    blame: dict[str, list[BlameEntry]]
    related_commits: dict[str, list[CommitInfo]]

    # --- hypothesize / investigate (phase 2) ---
    hypotheses: Annotated[list[Hypothesis], operator.add]
    investigate_round: int
    investigate_tool_calls: Annotated[list[dict], operator.add]

    # --- generate_fix / test_fix (phase 3) ---
    current_diff: str | None
    diff_history: Annotated[list[str], operator.add]
    test_attempts: Annotated[list[TestResult], operator.add]
    retry_count: int

    # --- decision_gate (phase 4) ---
    confidence: float | None
    risk_category: Literal["low", "medium", "high"] | None
    route_decision: Literal["suggest_pr", "comment_only"] | None

    # --- open_pr / notify_slack (phase 4-5) ---
    pr_url: str | None
    slack_thread_ts: str | None
    fingerprint: str | None

    # --- cross-cutting ---
    errors: Annotated[list[str], operator.add]
    drop_reason: str | None
