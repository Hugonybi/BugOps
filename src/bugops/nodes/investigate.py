from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from bugops.config import Settings
from bugops.git import local_repo
from bugops.logging import get_logger
from bugops.models.hypothesis import InvestigationConclusion
from bugops.state import BugOpsState, Hypothesis

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are investigating a production bug reported via Sentry. You have the \
stack trace, breadcrumbs, and source/blame/commit history for the frames Sentry flagged. You \
may call `read_source_file` to inspect additional files in the repo (e.g. a caller, a shared \
util, a config file) not already provided, if that would sharpen your hypothesis. When you \
have enough evidence, stop calling tools and give your final hypotheses."""


def _build_context_message(state: BugOpsState) -> str:
    issue = state["issue"]
    parts = [
        f"## Issue\n{issue.title}\nculprit: {issue.culprit}\nplatform: {issue.platform}",
        f"## Stack Trace\n{state.get('stack_trace_markdown') or '(none)'}",
        f"## Breadcrumbs\n{state.get('breadcrumbs_markdown') or '(none)'}",
    ]
    for path, content in state.get("source_files", {}).items():
        parts.append(f"## Source: {path}\n```\n{content}\n```")
        for b in state.get("blame", {}).get(path, []):
            parts.append(f"blame {path}:{b.line_no} — {b.commit_sha[:8]} {b.author} {b.summary}")
        for c in state.get("related_commits", {}).get(path, []):
            parts.append(f"commit {c.sha[:8]} {c.author} {c.message} {c.pr_url or ''}")
    return "\n\n".join(parts)


def _make_read_file_tool(repo, sha: str):
    @tool
    def read_source_file(path: str) -> str:
        """Read a file from the repo at the investigated commit, by repo-relative path,
        for files not already included in the gathered context."""
        content = local_repo.read_file_at(repo, sha, path)
        if content is None:
            return f"ERROR: {path!r} not found at {sha}"
        return content

    return read_source_file


async def run(state: BugOpsState, settings: Settings, model: BaseChatModel) -> BugOpsState:
    repo = state["repo"]
    local = local_repo.ensure_clone(
        settings.git_cache_dir, repo["owner"], repo["name"], settings.github_token.get_secret_value()
    )
    read_tool = _make_read_file_tool(local, state["release_sha"])
    bound_model = model.bind_tools([read_tool])

    messages: list = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=_build_context_message(state))]
    tool_calls_made: list[dict] = []
    round_no = 0

    while round_no < settings.max_investigate_rounds:
        round_no += 1
        response = await bound_model.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for call in response.tool_calls:
            tool_calls_made.append({"round": round_no, "name": call["name"], "args": call["args"]})
            result = read_tool.invoke(call["args"])
            messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

    try:
        structured_model = model.with_structured_output(InvestigationConclusion)
        conclusion = await structured_model.ainvoke(
            messages + [HumanMessage(content="Give your final hypotheses now.")]
        )
        hypotheses: list[Hypothesis] = [h.model_dump() for h in conclusion.hypotheses]
    except Exception as exc:  # noqa: BLE001 — LLM call boundary; any failure here should degrade to an error, not crash the graph
        logger.warning("investigate_structured_output_failed", error=str(exc))
        return {
            **state,
            "investigate_round": round_no,
            "investigate_tool_calls": tool_calls_made,
            "errors": [f"investigate: structured output failed: {exc}"],
        }

    return {
        **state,
        "hypotheses": hypotheses,
        "investigate_round": round_no,
        "investigate_tool_calls": tool_calls_made,
    }


__all__ = ["run"]
