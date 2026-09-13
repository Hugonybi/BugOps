from __future__ import annotations

from pathlib import Path

from git import Repo
from git.exc import GitCommandError

from bugops.models.context import BlameEntry, CommitInfo


def _clone_url(owner: str, name: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{owner}/{name}.git"


def ensure_clone(cache_dir: Path, owner: str, name: str, token: str) -> Repo:
    """Clones the repo on first use, fetches on every later call. Full (non-shallow) clone,
    since blame/log need real history and phase 3's sandbox reuses this same cache."""
    repo_path = cache_dir / f"{owner}__{name}"
    if repo_path.exists():
        repo = Repo(repo_path)
        repo.remotes.origin.fetch()
        return repo
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    return Repo.clone_from(_clone_url(owner, name, token), repo_path)


def read_file_at(repo: Repo, sha: str, path: str) -> str | None:
    try:
        return repo.git.show(f"{sha}:{path}")
    except GitCommandError:
        return None


def blame(repo: Repo, sha: str, path: str, line_no: int, context: int = 5) -> list[BlameEntry]:
    """Blame lines within `context` of `line_no`, using GitPython's porcelain blame."""
    try:
        entries = repo.blame(sha, path)
    except GitCommandError:
        return []
    if entries is None:
        return []
    results: list[BlameEntry] = []
    current_line = 1
    for commit, lines in entries:
        for _ in lines:
            if abs(current_line - line_no) <= context:
                results.append(
                    BlameEntry(
                        line_no=current_line,
                        commit_sha=commit.hexsha,
                        author=commit.author.name,
                        date=commit.authored_datetime.isoformat(),
                        summary=commit.summary,
                    )
                )
            current_line += 1
    return results


def recent_commits(repo: Repo, sha: str, path: str, max_count: int = 10) -> list[CommitInfo]:
    try:
        commits = list(repo.iter_commits(sha, paths=path, max_count=max_count))
    except GitCommandError:
        return []
    return [
        CommitInfo(
            sha=c.hexsha,
            author=c.author.name,
            date=c.authored_datetime.isoformat(),
            message=c.summary,
        )
        for c in commits
    ]
