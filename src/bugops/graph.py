from __future__ import annotations

import functools

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, StateGraph

from bugops.clients.github_client import GitHubClient
from bugops.clients.sentry_mcp import SentryMCPClient
from bugops.config import Settings
from bugops.nodes import gather_context, ingest, investigate
from bugops.state import BugOpsState


async def _ingest_node(state: BugOpsState, *, settings: Settings, mcp: SentryMCPClient, gh: GitHubClient) -> dict:
    return await ingest.from_issue_url(
        state["issue_url"], settings, mcp, gh, project_slug_override=state.get("project_slug_override")
    )


def build_graph(
    settings: Settings,
    mcp: SentryMCPClient,
    gh: GitHubClient,
    model: BaseChatModel | None = None,
):
    """Builds and compiles the BugOps StateGraph: ingest -> gather_context -> investigate.

    `model` can be injected directly (tests do this to avoid a real API key); otherwise it's
    constructed from settings via langchain's provider-agnostic `init_chat_model`, so swapping
    LLM_PROVIDER/LLM_MODEL in .env never requires a code change here.
    """
    if model is None:
        kwargs = {"api_key": settings.llm_api_key.get_secret_value()}
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        model = init_chat_model(settings.llm_model, model_provider=settings.llm_provider, **kwargs)

    graph = StateGraph(BugOpsState)
    graph.add_node("ingest", functools.partial(_ingest_node, settings=settings, mcp=mcp, gh=gh))
    graph.add_node("gather_context", functools.partial(gather_context.run, settings=settings, mcp=mcp, gh=gh))
    graph.add_node("investigate", functools.partial(investigate.run, settings=settings, model=model))

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "gather_context")
    graph.add_edge("gather_context", "investigate")
    graph.add_edge("investigate", END)

    return graph.compile()


__all__ = ["build_graph"]
