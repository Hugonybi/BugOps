from __future__ import annotations


def diff_paths(diff: str) -> list[str]:
    """Parses `diff --git a/<path> b/<path>` headers out of a unified diff string."""
    paths = []
    for line in diff.splitlines():
        if line.startswith("diff --git a/"):
            rest = line[len("diff --git a/") :]
            a_path = rest.split(" b/", 1)[0]
            paths.append(a_path)
    return paths


__all__ = ["diff_paths"]
