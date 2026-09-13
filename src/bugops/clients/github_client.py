from __future__ import annotations

from datetime import datetime

from github import Github
from github.GithubException import GithubException


class GitHubClient:
    """PyGithub REST wrapper for the calls a local clone can't answer: default branch,
    a commit's associated PR, and a best-effort SHA for "the code as of some timestamp"."""

    def __init__(self, token: str) -> None:
        self._gh = Github(token)

    def get_repo(self, owner: str, name: str):
        return self._gh.get_repo(f"{owner}/{name}")

    def default_branch(self, owner: str, name: str) -> str:
        return self.get_repo(owner, name).default_branch

    def branch_head_sha(self, owner: str, name: str, branch: str) -> str:
        return self.get_repo(owner, name).get_branch(branch).commit.sha

    def sha_at_or_before(self, owner: str, name: str, branch: str, until_iso: str) -> str:
        """Best-effort release SHA when we only know an event timestamp, not a release tag.

        Sentry's get_issue_details doesn't surface a release/tag field through the tools we
        use, so this approximates "the deployed code at the time of the event" via the most
        recent commit on the default branch before that timestamp.
        """
        repo = self.get_repo(owner, name)
        until = datetime.fromisoformat(until_iso)
        commits = repo.get_commits(sha=branch, until=until)
        for commit in commits:
            return commit.sha
        return self.branch_head_sha(owner, name, branch)

    def pr_url_for_commit(self, owner: str, name: str, sha: str) -> str | None:
        try:
            pulls = self.get_repo(owner, name).get_commit(sha).get_pulls()
            return pulls[0].html_url if pulls.totalCount else None
        except GithubException:
            return None
