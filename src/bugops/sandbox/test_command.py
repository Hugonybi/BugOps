from __future__ import annotations

import json
from pathlib import Path


def detect_test_command(
    worktree_path: Path, override_map: dict[str, str], owner: str, name: str
) -> tuple[str, str] | None:
    """Returns (install_command, test_command), or None if the repo looks untestable.

    Checks `override_map` (REPO_TEST_CMD_MAP_JSON, keyed by "owner/name") first, then falls
    back to auto-detecting from package.json + lockfile presence.
    """
    override = override_map.get(f"{owner}/{name}")
    if override:
        return _package_manager_install(worktree_path), override

    package_json = worktree_path / "package.json"
    if not package_json.exists():
        return None

    try:
        manifest = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not manifest.get("scripts", {}).get("test"):
        return None

    install_cmd = _package_manager_install(worktree_path)
    test_cmd = _package_manager_test(worktree_path)
    return install_cmd, test_cmd


def _package_manager_install(worktree_path: Path) -> str:
    if (worktree_path / "pnpm-lock.yaml").exists():
        return "pnpm install --frozen-lockfile"
    if (worktree_path / "yarn.lock").exists():
        return "yarn install --frozen-lockfile"
    if (worktree_path / "package-lock.json").exists():
        return "npm ci"
    return "npm install"


def _package_manager_test(worktree_path: Path) -> str:
    if (worktree_path / "pnpm-lock.yaml").exists():
        return "pnpm test"
    if (worktree_path / "yarn.lock").exists():
        return "yarn test"
    return "npm test"


__all__ = ["detect_test_command"]
