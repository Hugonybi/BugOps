from __future__ import annotations

import asyncio
import uuid

from bugops.config import Settings
from bugops.git import local_repo, worktree
from bugops.logging import get_logger
from bugops.sandbox.docker_runner import DockerTestRunner, DockerUnavailableError
from bugops.sandbox.test_command import detect_test_command
from bugops.state import BugOpsState, TestResult

logger = get_logger(__name__)


def _run_sync(state: BugOpsState, settings: Settings, docker_runner: DockerTestRunner) -> BugOpsState:
    retry_count = state.get("retry_count", 0) + 1
    logger.info("test_fix_attempt_start", attempt=retry_count, max_attempts=settings.max_fix_retries)

    if not settings.enable_sandbox_tests:
        return {"retry_count": retry_count, "drop_reason": "sandbox_tests_disabled"}

    try:
        docker_runner.check_available()
    except DockerUnavailableError as exc:
        logger.warning("test_fix_docker_unavailable", error=str(exc))
        return {"retry_count": retry_count, "drop_reason": "docker_unavailable"}

    repo = state["repo"]
    diff = state.get("current_diff")
    local = local_repo.ensure_clone(
        settings.git_cache_dir, repo["owner"], repo["name"], settings.github_token.get_secret_value()
    )

    dest = settings.sandbox_worktree_dir / f"{repo['owner']}__{repo['name']}__{uuid.uuid4().hex[:8]}"
    worktree.create_worktree(local, state["release_sha"], dest)
    try:
        if diff:
            ok, stderr = worktree.apply_diff(dest, diff)
            if not ok:
                result: TestResult = {
                    "passed": False,
                    "command": "git apply",
                    "stdout_tail": "",
                    "stderr_tail": stderr,
                    "duration_s": 0.0,
                }
                return {"retry_count": retry_count, "test_attempts": [result]}

        detected = detect_test_command(dest, settings.repo_test_cmd_map(), repo["owner"], repo["name"])
        if detected is None:
            return {"retry_count": retry_count, "drop_reason": "no_test_command_detected"}
        install_command, test_command = detected

        sandbox_result = docker_runner.run(dest, install_command, test_command)
        result = {
            "passed": sandbox_result.passed,
            "command": sandbox_result.command,
            "stdout_tail": sandbox_result.stdout_tail,
            "stderr_tail": sandbox_result.stderr_tail,
            "duration_s": sandbox_result.duration_s,
        }

        update: BugOpsState = {"retry_count": retry_count, "test_attempts": [result]}
        if not sandbox_result.passed and retry_count >= settings.max_fix_retries:
            update["drop_reason"] = "max_fix_retries_exhausted"
        return update
    finally:
        worktree.remove_worktree(local, dest)


async def run(state: BugOpsState, settings: Settings, docker_runner: DockerTestRunner) -> BugOpsState:
    return await asyncio.to_thread(_run_sync, state, settings, docker_runner)


__all__ = ["run"]
