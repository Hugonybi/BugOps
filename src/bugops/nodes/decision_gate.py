from __future__ import annotations

from bugops.config import Settings
from bugops.diffutils import diff_paths
from bugops.logging import get_logger
from bugops.state import BugOpsState

logger = get_logger(__name__)


def _top_confidence(state: BugOpsState) -> float:
    hypotheses = state.get("hypotheses", [])
    if not hypotheses:
        return 0.0
    return max(h["confidence"] for h in hypotheses)


def _last_test_passed(state: BugOpsState) -> bool:
    attempts = state.get("test_attempts", [])
    return bool(attempts) and attempts[-1]["passed"]


def _files_touched(state: BugOpsState) -> int:
    diff = state.get("current_diff")
    return len(diff_paths(diff)) if diff else 0


def _risk_category(*, passed: bool, drop_reason: str | None, files_touched: int, settings: Settings) -> str:
    if drop_reason or not passed:
        return "high"
    if files_touched > settings.decision_max_files_for_low_risk:
        return "medium"
    return "low"


def _route_decision(*, confidence: float, risk_category: str, settings: Settings) -> str:
    if risk_category == "low" and confidence >= settings.decision_confidence_floor:
        return "suggest_pr"
    return "comment_only"


async def run(state: BugOpsState, settings: Settings) -> BugOpsState:
    confidence = _top_confidence(state)
    passed = _last_test_passed(state)
    drop_reason = state.get("drop_reason")
    files_touched = _files_touched(state)

    risk_category = _risk_category(passed=passed, drop_reason=drop_reason, files_touched=files_touched, settings=settings)
    route_decision = _route_decision(confidence=confidence, risk_category=risk_category, settings=settings)

    logger.info(
        "decision_gate_routed",
        confidence=confidence,
        risk_category=risk_category,
        route_decision=route_decision,
        passed=passed,
        drop_reason=drop_reason,
        files_touched=files_touched,
    )

    return {"confidence": confidence, "risk_category": risk_category, "route_decision": route_decision}


__all__ = ["run"]
