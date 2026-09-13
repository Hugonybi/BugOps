import shutil
from pathlib import Path

import pytest

from bugops.config import Settings
from bugops.sandbox.docker_runner import DockerTestRunner
from bugops.sandbox.test_command import detect_test_command

pytestmark = pytest.mark.docker

FIXTURE_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_node_repo"


def _settings() -> Settings:
    return Settings(github_token="unused", github_repo_map_json="{}", llm_model="unused", llm_api_key="unused")


def test_docker_available():
    DockerTestRunner(_settings()).check_available()


def test_sandbox_run_fails_on_buggy_repo(tmp_path):
    worktree = tmp_path / "buggy"
    shutil.copytree(FIXTURE_REPO, worktree)

    install_command, test_command = detect_test_command(worktree, {}, "test", "tiny-node-repo")
    result = DockerTestRunner(_settings()).run(worktree, install_command, test_command)

    assert result.passed is False


def test_sandbox_run_passes_after_fix_applied(tmp_path):
    worktree = tmp_path / "fixed"
    shutil.copytree(FIXTURE_REPO, worktree)
    (worktree / "index.js").write_text(
        "function add(a, b) {\n  return a + b;\n}\n\nmodule.exports = { add };\n"
    )

    install_command, test_command = detect_test_command(worktree, {}, "test", "tiny-node-repo")
    result = DockerTestRunner(_settings()).run(worktree, install_command, test_command)

    assert result.passed is True
