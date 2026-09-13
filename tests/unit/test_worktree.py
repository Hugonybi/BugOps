from git import Repo

from bugops.git import worktree


def _init_repo_with_commit(path):
    repo = Repo.init(path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@example.com")
    (path / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    sha = repo.index.commit("initial").hexsha
    return repo, sha


def test_create_and_remove_worktree(tmp_path):
    repo, sha = _init_repo_with_commit(tmp_path / "origin")
    dest = tmp_path / "wt1"

    worktree.create_worktree(repo, sha, dest)
    assert (dest / "file.txt").read_text() == "hello\n"

    worktree.remove_worktree(repo, dest)
    assert not dest.exists()


def test_apply_diff_success(tmp_path):
    repo, sha = _init_repo_with_commit(tmp_path / "origin")
    dest = tmp_path / "wt2"
    worktree.create_worktree(repo, sha, dest)

    diff = (
        "diff --git a/file.txt b/file.txt\n"
        "--- a/file.txt\n"
        "+++ b/file.txt\n"
        "@@ -1 +1 @@\n"
        "-hello\n"
        "+goodbye\n"
    )
    ok, err = worktree.apply_diff(dest, diff)
    assert ok, err
    assert (dest / "file.txt").read_text() == "goodbye\n"


def test_apply_diff_failure_on_mismatched_content(tmp_path):
    repo, sha = _init_repo_with_commit(tmp_path / "origin")
    dest = tmp_path / "wt3"
    worktree.create_worktree(repo, sha, dest)

    diff = (
        "diff --git a/file.txt b/file.txt\n"
        "--- a/file.txt\n"
        "+++ b/file.txt\n"
        "@@ -1 +1 @@\n"
        "-this line does not exist\n"
        "+goodbye\n"
    )
    ok, err = worktree.apply_diff(dest, diff)
    assert not ok
    assert err
