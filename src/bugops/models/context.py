from __future__ import annotations

import re

from pydantic import BaseModel

# Matches the JS/TS frame header format emitted by sentry-mcp's formatFrameHeader:
# "path/to/file.ts:42:7 (functionName)" — column and function are both optional.
_JS_FRAME_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*\.[jt]sx?):(?P<line>\d+)(?::(?P<column>\d+))?(?:\s+\((?P<function>[^)]+)\))?$"
)


class ParsedFrame(BaseModel):
    file: str
    line: int
    column: int | None = None
    function: str | None = None


def parse_js_frames(stack_trace_markdown: str) -> list[ParsedFrame]:
    """Extracts (file, line, function) from the fenced code block in get_event_stacktrace output.

    The tool returns pre-formatted markdown, not structured per-frame JSON, so this is the only
    way to recover file/line pairs to drive GitHub blame/source lookups.
    """
    fence_matches = re.findall(r"```\n(.*?)\n```", stack_trace_markdown, re.DOTALL)
    if not fence_matches:
        return []
    frames: list[ParsedFrame] = []
    for line in fence_matches[-1].splitlines():
        match = _JS_FRAME_RE.match(line.strip())
        if not match:
            continue
        frames.append(
            ParsedFrame(
                file=match.group("file"),
                line=int(match.group("line")),
                column=int(match.group("column")) if match.group("column") else None,
                function=match.group("function"),
            )
        )
    return frames


class BlameEntry(BaseModel):
    line_no: int
    commit_sha: str
    author: str
    date: str
    summary: str


class CommitInfo(BaseModel):
    sha: str
    author: str
    date: str
    message: str
    pr_url: str | None = None


class GatheredContext(BaseModel):
    stack_trace_markdown: str | None = None
    breadcrumbs_markdown: str | None = None
    stack_frames: list[ParsedFrame] = []
    source_files: dict[str, str] = {}
    blame: dict[str, list[BlameEntry]] = {}
    related_commits: dict[str, list[CommitInfo]] = {}
