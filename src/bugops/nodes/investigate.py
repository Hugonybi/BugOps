from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from bugops.config import Settings
from bugops.git import local_repo
from bugops.logging import get_logger
from bugops.models.hypothesis import InvestigationConclusion
from bugops.nodes.tools import build_context_message, make_read_file_tool
from bugops.state import BugOpsState, Hypothesis

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are investigating a production bug reported via Sentry. You have the \
stack trace, breadcrumbs, and source/blame/commit history for the frames Sentry flagged. You \
may call `read_source_file` to inspect additional files in the repo (e.g. a caller, a shared \
util, a config file) not already provided, if that would sharpen your hypothesis. When you \
have enough evidence, stop calling tools and give your final hypotheses."""


async def run(state: BugOpsState, settings: Settings, model: BaseChatModel) -> BugOpsState:
    repo = state["repo"]
    local = local_repo.ensure_clone(
        settings.git_cache_dir, repo["owner"], repo["name"], settings.github_token.get_secret_value()
    )
    read_tool = make_read_file_tool(local, state["release_sha"])
    bound_model = model.bind_tools([read_tool])

    messages: list = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=build_context_message(state))]
    tool_calls_made: list[dict] = []
    round_no = 0

    while round_no < settings.max_investigate_rounds:
        round_no += 1
        logger.info("investigate_round_start", round=round_no, max_rounds=settings.max_investigate_rounds)
        response = await bound_model.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for call in response.tool_calls:
            tool_calls_made.append({"round": round_no, "name": call["name"], "args": call["args"]})
            result = read_tool.invoke(call["args"])
            messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

    logger.info("investigate_requesting_conclusion", rounds_used=round_no)
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
