from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_fixed

from bugops.config import Settings
from bugops.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SandboxResult:
    passed: bool
    command: str
    stdout_tail: str
    stderr_tail: str
    duration_s: float


def _tail(text: str, max_lines: int = 100) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


class DockerUnavailableError(RuntimeError):
    pass


class DockerTestRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def check_available(self) -> None:
        try:
            result = self._run_docker(["version"], timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DockerUnavailableError(str(exc)) from exc
        if result.returncode != 0:
            raise DockerUnavailableError(result.stderr.decode("utf-8", errors="replace"))

    def run(self, worktree_path: Path, install_command: str, test_command: str) -> SandboxResult:
        logger.info(
            "docker_install_start", command=install_command, timeout_s=self._settings.sandbox_install_timeout_s
        )
        install_result = self._run_in_container(
            worktree_path, install_command, network_disabled=False, timeout=self._settings.sandbox_install_timeout_s
        )
        if install_result is None:
            return SandboxResult(
                passed=False,
                command=install_command,
                stdout_tail="",
                stderr_tail=f"install timed out after {self._settings.sandbox_install_timeout_s}s",
                duration_s=float(self._settings.sandbox_install_timeout_s),
            )
        if install_result.returncode != 0:
            return SandboxResult(
                passed=False,
                command=install_command,
                stdout_tail=_tail(install_result.stdout.decode("utf-8", errors="replace")),
                stderr_tail=_tail(install_result.stderr.decode("utf-8", errors="replace")),
                duration_s=0.0,
            )

        logger.info("docker_test_start", command=test_command, timeout_s=self._settings.sandbox_test_timeout_s)
        start = time.monotonic()
        test_result = self._run_in_container(
            worktree_path, test_command, network_disabled=True, timeout=self._settings.sandbox_test_timeout_s
        )
        duration_s = time.monotonic() - start
        if test_result is None:
            return SandboxResult(
                passed=False,
                command=test_command,
                stdout_tail="",
                stderr_tail=f"test run timed out after {self._settings.sandbox_test_timeout_s}s",
                duration_s=duration_s,
            )
        return SandboxResult(
            passed=test_result.returncode == 0,
            command=test_command,
            stdout_tail=_tail(test_result.stdout.decode("utf-8", errors="replace")),
            stderr_tail=_tail(test_result.stderr.decode("utf-8", errors="replace")),
            duration_s=duration_s,
        )

    def _run_in_container(
        self, worktree_path: Path, command: str, *, network_disabled: bool, timeout: int
    ) -> subprocess.CompletedProcess | None:
        args = [
            "run",
            "--rm",
            "-w",
            "/repo",
            "-v",
            f"{worktree_path}:/repo",
            f"--memory={self._settings.sandbox_memory_limit}",
            f"--cpus={self._settings.sandbox_cpu_limit}",
        ]
        if network_disabled:
            args += ["--network", "none"]
        args += [self._settings.docker_image, "sh", "-c", command]
        try:
            return self._run_docker(args, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    @retry(reraise=True, stop=stop_after_attempt(2), wait=wait_fixed(1))
    def _run_docker(self, args: list[str], timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], capture_output=True, timeout=timeout, check=False)


__all__ = ["DockerTestRunner", "DockerUnavailableError", "SandboxResult"]
