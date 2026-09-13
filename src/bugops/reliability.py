from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from bugops.clients.github_client import GitHubClient
    from bugops.config import Settings

Status = Literal["pending", "merged", "closed"]


class OutcomeRecord(TypedDict):
    repo: str
    pr_number: int
    pr_url: str
    risk_category: str
    status: Status


def load_records(settings: Settings) -> list[OutcomeRecord]:
    path = settings.pr_outcome_store_path
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_records(settings: Settings, records: list[OutcomeRecord]) -> None:
    path = settings.pr_outcome_store_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2))


def record_pending(settings: Settings, *, repo: str, pr_number: int, pr_url: str, risk_category: str) -> None:
    records = load_records(settings)
    records.append(
        {"repo": repo, "pr_number": pr_number, "pr_url": pr_url, "risk_category": risk_category, "status": "pending"}
    )
    save_records(settings, records)


def reconcile(settings: Settings, gh: GitHubClient) -> None:
    """Updates any still-"pending" record by asking GitHub whether that PR has since been merged
    or closed. Cheap no-op when there are no pending records — safe to call on every open_pr run."""
    records = load_records(settings)
    changed = False
    for record in records:
        if record["status"] != "pending":
            continue
        owner, name = record["repo"].split("/", 1)
        state = gh.pr_state(owner, name, record["pr_number"])
        if state != "open":
            record["status"] = state
            changed = True
    if changed:
        save_records(settings, records)


def is_reliable(settings: Settings, risk_category: str) -> bool:
    """Whether `risk_category` has enough of a merged track record to skip the manual approval
    prompt. Requires both a minimum sample size and a minimum merge rate among resolved
    (merged/closed) PRs in that category."""
    if not settings.enable_reliability_auto_approve:
        return False
    records = load_records(settings)
    resolved = [r for r in records if r["risk_category"] == risk_category and r["status"] in ("merged", "closed")]
    if len(resolved) < settings.reliability_min_sample_size:
        return False
    merged = sum(1 for r in resolved if r["status"] == "merged")
    return merged / len(resolved) >= settings.reliability_merge_rate_threshold


__all__ = ["OutcomeRecord", "is_reliable", "load_records", "reconcile", "record_pending", "save_records"]
