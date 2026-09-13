from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from bugops.clients.github_client import GitHubClient
from bugops.clients.sentry_mcp import SentryMCPClient
from bugops.config import load_settings
from bugops.graph import build_graph
from bugops.logging import configure_logging
from bugops.state import BugOpsState

console = Console()


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _render(state: BugOpsState) -> None:
    issue = state["issue"]
    console.print(
        Panel(
            f"[bold]{issue.title or '(no title — markdown-only issue summary)'}[/bold]\n"
            f"culprit: {issue.culprit}\n"
            f"project: {issue.project_slug}   platform: {issue.platform}   status: {issue.status}\n"
            f"first_seen: {issue.first_seen}   last_seen: {issue.last_seen}\n"
            f"occurrences: {issue.occurrences}   users_impacted: {issue.users_impacted}\n"
            f"url: {issue.url}",
            title=f"Sentry Issue {issue.short_id}",
        )
    )
    console.print(f"repo: {state['repo']['owner']}/{state['repo']['name']}  release_sha: {state['release_sha']}\n")

    if state.get("stack_trace_markdown"):
        console.print(Panel(Markdown(state["stack_trace_markdown"]), title="Stack Trace"))
    if state.get("breadcrumbs_markdown"):
        console.print(Panel(Markdown(state["breadcrumbs_markdown"]), title="Breadcrumbs"))

    frames_by_file = {f.file: f for f in state.get("stack_frames", [])}
    for path, content in state.get("source_files", {}).items():
        frame = frames_by_file.get(path)
        console.print(f"\n[bold]{path}[/bold]" + (f"  (flagged line {frame.line})" if frame else ""))

        if frame:
            lines = content.splitlines()
            start, end = max(0, frame.line - 6), min(len(lines), frame.line + 5)
            for i in range(start, end):
                marker = "→" if i + 1 == frame.line else " "
                console.print(f"  {marker} {i + 1:>5} │ {lines[i]}")

        blame_entries = state.get("blame", {}).get(path, [])
        if blame_entries:
            table = Table(title="Blame")
            for col in ("line", "commit", "author", "date", "summary"):
                table.add_column(col)
            for b in blame_entries:
                table.add_row(str(b.line_no), b.commit_sha[:8], b.author, b.date, b.summary)
            console.print(table)

        commits = state.get("related_commits", {}).get(path, [])
        if commits:
            table = Table(title="Recent Commits")
            for col in ("sha", "author", "date", "message", "pr"):
                table.add_column(col)
            for c in commits:
                table.add_row(c.sha[:8], c.author, c.date, c.message, c.pr_url or "")
            console.print(table)

    for h in state.get("hypotheses", []):
        console.print(
            Panel(
                f"{h['summary']}\nfiles: {', '.join(h['suspected_files'])}\nreasoning: {h['reasoning']}",
                title=f"Hypothesis (confidence {h['confidence']:.2f})",
            )
        )

    if state.get("current_diff"):
        console.print(Panel(state["current_diff"], title="Proposed Fix (current_diff)"))

    for i, attempt in enumerate(state.get("test_attempts", []), start=1):
        status = "[green]PASSED[/green]" if attempt["passed"] else "[red]FAILED[/red]"
        console.print(
            Panel(
                f"command: {attempt['command']}\nduration: {attempt['duration_s']:.1f}s\n"
                f"stdout (tail):\n{attempt['stdout_tail']}\nstderr (tail):\n{attempt['stderr_tail']}",
                title=f"Test Attempt {i} — {status}",
            )
        )

    if state.get("drop_reason"):
        console.print(f"\n[yellow]drop_reason: {state['drop_reason']}[/yellow]")


async def _main(args: argparse.Namespace) -> None:
    configure_logging()
    settings = load_settings()
    mcp = SentryMCPClient(
        server_url=settings.sentry_mcp_url,
        token_cache_path=settings.sentry_token_cache_path,
        redirect_uri=settings.sentry_oauth_redirect_uri,
        client_name=settings.sentry_oauth_client_name,
    )
    gh = GitHubClient(settings.github_token.get_secret_value())

    graph = build_graph(settings, mcp, gh)
    state = await graph.ainvoke({"issue_url": args.issue_url, "project_slug_override": args.project_slug})

    _render(state)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(_jsonable(state), indent=2))
        console.print(f"\nWrote {args.json_out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the ingest -> gather_context -> investigate -> generate_fix -> test_fix graph "
            "against a real historical Sentry issue."
        )
    )
    parser.add_argument("--issue-url", required=True, help="e.g. https://my-org.sentry.io/issues/PROJECT-1Z43")
    parser.add_argument("--project-slug", default=None, help="Override if Sentry doesn't return one in structured output")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
