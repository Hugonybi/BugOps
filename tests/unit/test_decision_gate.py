import pytest

from bugops.nodes import decision_gate

DIFF_1_FILE = (
    "diff --git a/src/index.ts b/src/index.ts\n"
    "--- a/src/index.ts\n"
    "+++ b/src/index.ts\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)

DIFF_3_FILES = (
    "diff --git a/src/a.ts b/src/a.ts\n"
    "--- a/src/a.ts\n"
    "+++ b/src/a.ts\n"
    "@@ -1 +1 @@\n"
    "-a\n"
    "+a2\n"
    "diff --git a/src/b.ts b/src/b.ts\n"
    "--- a/src/b.ts\n"
    "+++ b/src/b.ts\n"
    "@@ -1 +1 @@\n"
    "-b\n"
    "+b2\n"
    "diff --git a/src/c.ts b/src/c.ts\n"
    "--- a/src/c.ts\n"
    "+++ b/src/c.ts\n"
    "@@ -1 +1 @@\n"
    "-c\n"
    "+c2\n"
)


class FakeSettings:
    decision_confidence_floor = 0.75
    decision_max_files_for_low_risk = 2


def _state(**overrides):
    state = {
        "hypotheses": [{"summary": "s", "suspected_files": [], "confidence": 0.9, "reasoning": "r"}],
        "test_attempts": [
            {"passed": True, "command": "npm test", "stdout_tail": "ok", "stderr_tail": "", "duration_s": 1.0}
        ],
        "current_diff": DIFF_1_FILE,
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_low_risk_high_confidence_routes_auto_pr():
    result = await decision_gate.run(_state(), FakeSettings())

    assert result["confidence"] == 0.9
    assert result["risk_category"] == "low"
    assert result["route_decision"] == "auto_pr"


@pytest.mark.asyncio
async def test_multi_file_diff_over_threshold_is_medium_risk_comment_only():
    result = await decision_gate.run(_state(current_diff=DIFF_3_FILES), FakeSettings())

    assert result["risk_category"] == "medium"
    assert result["route_decision"] == "comment_only"


@pytest.mark.asyncio
async def test_confidence_below_floor_stays_comment_only_even_if_low_risk():
    state = _state(hypotheses=[{"summary": "s", "suspected_files": [], "confidence": 0.5, "reasoning": "r"}])

    result = await decision_gate.run(state, FakeSettings())

    assert result["risk_category"] == "low"
    assert result["route_decision"] == "comment_only"


@pytest.mark.asyncio
async def test_drop_reason_forces_high_risk_comment_only():
    state = _state(
        drop_reason="max_fix_retries_exhausted",
        test_attempts=[
            {"passed": False, "command": "npm test", "stdout_tail": "", "stderr_tail": "fail", "duration_s": 1.0}
        ],
    )

    result = await decision_gate.run(state, FakeSettings())

    assert result["risk_category"] == "high"
    assert result["route_decision"] == "comment_only"
    assert result["confidence"] == 0.9


@pytest.mark.asyncio
async def test_no_test_attempts_at_all_is_high_risk_no_crash():
    state = _state(drop_reason="sandbox_tests_disabled", test_attempts=[], current_diff=None)
    del state["current_diff"]

    result = await decision_gate.run(state, FakeSettings())

    assert result["risk_category"] == "high"
    assert result["route_decision"] == "comment_only"


@pytest.mark.asyncio
async def test_empty_hypotheses_yields_zero_confidence_no_crash():
    result = await decision_gate.run(_state(hypotheses=[]), FakeSettings())

    assert result["confidence"] == 0.0
    assert result["route_decision"] == "comment_only"


@pytest.mark.asyncio
async def test_takes_max_confidence_not_first_or_last():
    state = _state(
        hypotheses=[
            {"summary": "a", "suspected_files": [], "confidence": 0.3, "reasoning": "r"},
            {"summary": "b", "suspected_files": [], "confidence": 0.95, "reasoning": "r"},
            {"summary": "c", "suspected_files": [], "confidence": 0.6, "reasoning": "r"},
        ]
    )

    result = await decision_gate.run(state, FakeSettings())

    assert result["confidence"] == 0.95
