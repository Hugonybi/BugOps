from __future__ import annotations

import functools
from collections.abc import Callable

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, StateGraph

from bugops.clients.github_client import GitHubClient
from bugops.clients.sentry_mcp import SentryMCPClient
from bugops.clients.slack_client import SlackClient
from bugops.config import Settings
from bugops.nodes import (
    decision_gate,
    gather_context,
    generate_fix,
    ingest,
    investigate,
    notify_slack,
    open_pr,
    test_fix,
)
from bugops.sandbox.docker_runner import DockerTestRunner
from bugops.state import BugOpsState


async def _ingest_node(state: BugOpsState, *, settings: Settings, mcp: SentryMCPClient, gh: GitHubClient) -> dict:
    return await ingest.from_issue_url(
        state["issue_url"], settings, mcp, gh, project_slug_override=state.get("project_slug_override")
    )


def _route_after_test(state: BugOpsState, *, settings: Settings) -> str:
    if state.get("drop_reason"):
        return "decision_gate"
    if state.get("test_attempts") and state["test_attempts"][-1]["passed"]:
        return "decision_gate"
    return "generate_fix"


def build_graph(
    settings: Settings,
    mcp: SentryMCPClient,
    gh: GitHubClient,
    model: BaseChatModel | None = None,
    docker_runner: DockerTestRunner | None = None,
    slack_client: SlackClient | None = None,
    approve: Callable[[BugOpsState], bool] | None = None,
):
    """Builds and compiles the BugOps StateGraph:
    ingest -> gather_context -> investigate -> generate_fix -> test_fix ->
    {generate_fix | decision_gate} -> open_pr -> notify_slack -> END.

    `model`, `docker_runner`, `slack_client`, and `approve` can be injected directly (tests do
    this to avoid a real API key, a real Docker daemon, a real Slack workspace, or blocking on
    stdin); otherwise `model` is constructed from settings via langchain's provider-agnostic
    `init_chat_model`, so swapping LLM_PROVIDER/LLM_MODEL in .env never requires a code change
    here, `docker_runner` defaults to a real `DockerTestRunner`, `slack_client` defaults to a real
    `SlackClient` only if Slack notifications are enabled and a bot token is configured (otherwise
    it stays `None` and `notify_slack` skips the API call), and `approve` defaults to `open_pr`'s
    interactive `Confirm.ask` prompt.

    `open_pr` always runs — it self-guards on `route_decision != "suggest_pr"` and on
    `enable_pr_creation`, so no new conditional edge is needed; `_route_after_test` remains the
    only routing decision in the graph.
    """
    if model is None:
        kwargs = {"api_key": settings.llm_api_key.get_secret_value()}
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        model = init_chat_model(settings.llm_model, model_provider=settings.llm_provider, **kwargs)
    if docker_runner is None:
        docker_runner = DockerTestRunner(settings)
    if slack_client is None and settings.enable_slack_notify and settings.slack_bot_token:
        slack_client = SlackClient(settings.slack_bot_token.get_secret_value())
    if approve is None:
        approve = open_pr.default_approve

    graph = StateGraph(BugOpsState)
    graph.add_node("ingest", functools.partial(_ingest_node, settings=settings, mcp=mcp, gh=gh))
    graph.add_node("gather_context", functools.partial(gather_context.run, settings=settings, mcp=mcp, gh=gh))
    graph.add_node("investigate", functools.partial(investigate.run, settings=settings, model=model))
    graph.add_node("generate_fix", functools.partial(generate_fix.run, settings=settings, model=model))
    graph.add_node("test_fix", functools.partial(test_fix.run, settings=settings, docker_runner=docker_runner))
    graph.add_node("decision_gate", functools.partial(decision_gate.run, settings=settings))
    graph.add_node("open_pr", functools.partial(open_pr.run, settings=settings, gh=gh, approve=approve))
    graph.add_node("notify_slack", functools.partial(notify_slack.run, settings=settings, slack_client=slack_client))

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "gather_context")
    graph.add_edge("gather_context", "investigate")
    graph.add_edge("investigate", "generate_fix")
    graph.add_edge("generate_fix", "test_fix")
    graph.add_conditional_edges(
        "test_fix",
        functools.partial(_route_after_test, settings=settings),
        {"generate_fix": "generate_fix", "decision_gate": "decision_gate"},
    )
    graph.add_edge("decision_gate", "open_pr")
    graph.add_edge("open_pr", "notify_slack")
    graph.add_edge("notify_slack", END)

    return graph.compile()


__all__ = ["build_graph"]
