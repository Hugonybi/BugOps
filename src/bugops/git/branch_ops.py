from __future__ import annotations

from git import Repo
from git.exc import GitCommandError


def create_branch(worktree_repo: Repo, branch_name: str) -> None:
    """Creates and checks out branch_name off the worktree's current (detached) HEAD."""
    worktree_repo.git.checkout("-b", branch_name)


def commit_all(worktree_repo: Repo, message: str) -> str:
    """Stages all changes (the already-applied diff) and commits. Returns the new commit sha."""
    worktree_repo.git.add("-A")
    worktree_repo.git.commit("-m", message)
    return worktree_repo.head.commit.hexsha


def push_branch(worktree_repo: Repo, owner: str, name: str, branch_name: str, token: str) -> tuple[bool, str]:
    """Pushes branch_name to GitHub over HTTPS using a one-off remote (mirrors
    local_repo._clone_url's URL pattern rather than reusing 'origin', since the worktree's
    origin points at the shared local cache, not GitHub). Failure is an expected, handled
    outcome — mirrors worktree.apply_diff's (bool, message) convention."""
    url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
    try:
        remote = worktree_repo.create_remote("bugops-push", url)
        remote.push(branch_name)
        return True, ""
    except GitCommandError as exc:
        return False, str(exc)


__all__ = ["commit_all", "create_branch", "push_branch"]
