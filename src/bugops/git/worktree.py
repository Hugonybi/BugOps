from __future__ import annotations

import subprocess
from pathlib import Path

from git import Repo
from git.exc import GitCommandError


def create_worktree(repo: Repo, sha: str, dest: Path) -> Path:
    """Adds a detached worktree at `dest` checked out at `sha`, off `repo`'s object store.
    Isolated from the shared cached clone's working tree; safe to mutate/remove freely."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    repo.git.worktree("add", "--detach", str(dest), sha)
    return dest


def remove_worktree(repo: Repo, path: Path) -> None:
    try:
        repo.git.worktree("remove", "--force", str(path))
    except GitCommandError:
        pass


def apply_diff(worktree_path: Path, diff_text: str) -> tuple[bool, str]:
    """Applies a unified diff to the worktree via `git apply`. Not retried on failure — a
    hunk mismatch is logical, not transient."""
    result = subprocess.run(
        ["git", "-C", str(worktree_path), "apply", "--whitespace=nowarn", "-"],
        input=diff_text.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return True, ""
    return False, result.stderr.decode("utf-8", errors="replace")


__all__ = ["apply_diff", "create_worktree", "remove_worktree"]
