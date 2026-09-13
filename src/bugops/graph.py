from __future__ import annotations

import functools

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, StateGraph

from bugops.clients.github_client import GitHubClient
from bugops.clients.sentry_mcp import SentryMCPClient
from bugops.config import Settings
from bugops.nodes import gather_context, generate_fix, ingest, investigate, test_fix
from bugops.sandbox.docker_runner import DockerTestRunner
from bugops.state import BugOpsState


async def _ingest_node(state: BugOpsState, *, settings: Settings, mcp: SentryMCPClient, gh: GitHubClient) -> dict:
    return await ingest.from_issue_url(
        state["issue_url"], settings, mcp, gh, project_slug_override=state.get("project_slug_override")
    )


def _route_after_test(state: BugOpsState, *, settings: Settings) -> str:
    if state.get("drop_reason"):
        return END
    if state.get("test_attempts") and state["test_attempts"][-1]["passed"]:
        return END
    return "generate_fix"


def build_graph(
    settings: Settings,
    mcp: SentryMCPClient,
    gh: GitHubClient,
    model: BaseChatModel | None = None,
    docker_runner: DockerTestRunner | None = None,
):
    """Builds and compiles the BugOps StateGraph:
    ingest -> gather_context -> investigate -> generate_fix -> test_fix -> {generate_fix | END}.

    `model` and `docker_runner` can be injected directly (tests do this to avoid a real API key
    or a real Docker daemon); otherwise `model` is constructed from settings via langchain's
    provider-agnostic `init_chat_model`, so swapping LLM_PROVIDER/LLM_MODEL in .env never
    requires a code change here, and `docker_runner` defaults to a real `DockerTestRunner`.
    """
    if model is None:
        kwargs = {"api_key": settings.llm_api_key.get_secret_value()}
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        model = init_chat_model(settings.llm_model, model_provider=settings.llm_provider, **kwargs)
    if docker_runner is None:
        docker_runner = DockerTestRunner(settings)

    graph = StateGraph(BugOpsState)
    graph.add_node("ingest", functools.partial(_ingest_node, settings=settings, mcp=mcp, gh=gh))
    graph.add_node("gather_context", functools.partial(gather_context.run, settings=settings, mcp=mcp, gh=gh))
    graph.add_node("investigate", functools.partial(investigate.run, settings=settings, model=model))
    graph.add_node("generate_fix", functools.partial(generate_fix.run, settings=settings, model=model))
    graph.add_node("test_fix", functools.partial(test_fix.run, settings=settings, docker_runner=docker_runner))

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "gather_context")
    graph.add_edge("gather_context", "investigate")
    graph.add_edge("investigate", "generate_fix")
    graph.add_edge("generate_fix", "test_fix")
    graph.add_conditional_edges(
        "test_fix", functools.partial(_route_after_test, settings=settings), {"generate_fix": "generate_fix", END: END}
    )

    return graph.compile()


__all__ = ["build_graph"]
