from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from bugops.config import Settings
from bugops.git import local_repo
from bugops.logging import get_logger
from bugops.models.fix import FixOutput
from bugops.nodes.tools import build_context_message, make_read_file_tool
from bugops.state import BugOpsState

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are fixing a production bug reported via Sentry. You have ranked \
root-cause hypotheses plus the stack trace, breadcrumbs, and source/blame/commit history that \
produced them. You may call `read_source_file` to inspect additional files in the repo before \
proposing a fix. When you have enough evidence, stop calling tools and produce a unified diff \
(git apply compatible, with `diff --git a/<path> b/<path>` headers) that fixes the root cause. \
Only touch files necessary for the fix."""


def _build_hypotheses_message(state: BugOpsState) -> str:
    parts = ["## Ranked Hypotheses"]
    for h in state.get("hypotheses", []):
        parts.append(
            f"- (confidence {h['confidence']:.2f}) {h['summary']}\n"
            f"  files: {', '.join(h['suspected_files'])}\n"
            f"  reasoning: {h['reasoning']}"
        )
    return "\n".join(parts)


def _build_retry_message(state: BugOpsState) -> str | None:
    if not state.get("test_attempts"):
        return None
    last_diff = state.get("current_diff")
    last_attempt = state["test_attempts"][-1]
    return (
        "## Previous Attempt Failed\n"
        f"Previous diff:\n```diff\n{last_diff}\n```\n"
        f"Test command: {last_attempt['command']}\n"
        f"stdout (tail):\n{last_attempt['stdout_tail']}\n"
        f"stderr (tail):\n{last_attempt['stderr_tail']}\n"
        "Propose a different diff that addresses this failure."
    )


def _valid_paths(files_touched: list[str]) -> bool:
    for path in files_touched:
        if path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
            return False
    return True


def _diff_paths(diff: str) -> list[str]:
    paths = []
    for line in diff.splitlines():
        if line.startswith("diff --git a/"):
            # "diff --git a/<path> b/<path>"
            rest = line[len("diff --git a/") :]
            a_path = rest.split(" b/", 1)[0]
            paths.append(a_path)
    return paths


async def run(state: BugOpsState, settings: Settings, model: BaseChatModel) -> BugOpsState:
    repo = state["repo"]
    local = local_repo.ensure_clone(
        settings.git_cache_dir, repo["owner"], repo["name"], settings.github_token.get_secret_value()
    )
    read_tool = make_read_file_tool(local, state["release_sha"])
    bound_model = model.bind_tools([read_tool])

    human_parts = [build_context_message(state), _build_hypotheses_message(state)]
    retry_message = _build_retry_message(state)
    if retry_message:
        human_parts.append(retry_message)

    messages: list = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content="\n\n".join(human_parts))]
    round_no = 0

    while round_no < settings.max_investigate_rounds:
        round_no += 1
        response = await bound_model.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for call in response.tool_calls:
            result = read_tool.invoke(call["args"])
            messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

    try:
        structured_model = model.with_structured_output(FixOutput)
        fix = await structured_model.ainvoke(messages + [HumanMessage(content="Give your final diff now.")])
    except Exception as exc:  # noqa: BLE001 — LLM call boundary; any failure here should degrade to an error, not crash the graph
        logger.warning("generate_fix_structured_output_failed", error=str(exc))
        return {"errors": [f"generate_fix: structured output failed: {exc}"]}

    diff_paths = _diff_paths(fix.diff)
    if not _valid_paths(fix.files_touched) or not _valid_paths(diff_paths):
        logger.warning("generate_fix_rejected_paths", files_touched=fix.files_touched, diff_paths=diff_paths)
        return {"errors": ["generate_fix: rejected diff touching disallowed paths"]}
    if set(diff_paths) != set(fix.files_touched):
        logger.warning("generate_fix_diff_files_mismatch", files_touched=fix.files_touched, diff_paths=diff_paths)
        return {"errors": ["generate_fix: files_touched does not match diff headers"]}

    return {"current_diff": fix.diff, "diff_history": [fix.diff]}


__all__ = ["run"]
