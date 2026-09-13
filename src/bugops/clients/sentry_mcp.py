from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp import Client
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

from bugops.logging import get_logger
from bugops.models.sentry import IssueRef, SentryIssueSummary

logger = get_logger(__name__)


class FileTokenStorage:
    """Persists OAuth tokens/client registration to disk so the interactive authorize step
    (browser + paste-back redirect URL) only happens once per machine, not once per run."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict = {}
        if path.exists():
            self._data = json.loads(path.read_text())

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._data.get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._data["tokens"] = tokens.model_dump(mode="json")
        self._flush()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._data.get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._data["client_info"] = client_info.model_dump(mode="json")
        self._flush()

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data))


async def _print_redirect_url(authorization_url: str) -> None:
    print("\nBugOps needs one-time Sentry authorization. Open this URL and approve access:")
    print(f"  {authorization_url}\n")


async def _prompt_for_callback() -> AuthorizationCodeResult:
    redirect_url = input("After approving, paste the URL you were redirected to: ").strip()
    params = parse_qs(urlparse(redirect_url).query)
    return AuthorizationCodeResult(
        code=params["code"][0],
        state=params["state"][0],
        iss=params.get("iss", [None])[0],
    )


def _first_text(content: list) -> str | None:
    for block in content:
        text = getattr(block, "text", None)
        if text:
            return text
    return None


class SentryMCPClient:
    """Thin wrapper around the Sentry hosted MCP server (mcp.sentry.dev) for the tool calls
    gather_context needs: get_issue_details, get_event_stacktrace, get_issue_breadcrumbs."""

    def __init__(self, server_url: str, token_cache_path: Path, redirect_uri: str, client_name: str) -> None:
        self._server_url = server_url
        self._oauth = OAuthClientProvider(
            server_url=server_url,
            client_metadata=OAuthClientMetadata(
                client_name=client_name,
                redirect_uris=[AnyUrl(redirect_uri)],
                scope="org:read project:read event:read",
            ),
            storage=FileTokenStorage(token_cache_path),
            redirect_handler=_print_redirect_url,
            callback_handler=_prompt_for_callback,
        )

    async def _call(self, tool_name: str, arguments: dict) -> tuple[dict | None, str | None]:
        async with httpx2.AsyncClient(auth=self._oauth, timeout=30.0) as http_client:
            transport = streamable_http_client(self._server_url, http_client=http_client)
            async with Client(transport) as client:
                result = await client.call_tool(tool_name, arguments)
        if result.is_error:
            text = _first_text(result.content)
            raise RuntimeError(f"Sentry MCP tool {tool_name!r} failed: {text}")
        return result.structured_content, _first_text(result.content)

    async def get_issue_details(self, issue: IssueRef) -> SentryIssueSummary:
        structured, markdown = await self._call("get_issue_details", {"issueUrl": issue.issue_url})
        if structured is None:
            logger.warning(
                "sentry_issue_details_unstructured",
                issue=issue.short_id,
                reason="org not on Sentry's structured-output rollout; falling back to markdown",
            )
            return SentryIssueSummary(short_id=issue.short_id, raw_markdown=markdown)

        issue_data = structured.get("issue", {})
        event_data = structured.get("event", {})
        return SentryIssueSummary(
            short_id=issue_data.get("shortId", issue.short_id),
            title=issue_data.get("title"),
            culprit=issue_data.get("culprit"),
            project_slug=issue_data.get("project"),
            platform=issue_data.get("platform"),
            status=issue_data.get("status"),
            first_seen=issue_data.get("firstSeen"),
            last_seen=issue_data.get("lastSeen"),
            occurrences=issue_data.get("occurrences"),
            users_impacted=issue_data.get("usersImpacted"),
            url=issue_data.get("url"),
            event_id=event_data.get("id"),
            event_occurred_at=event_data.get("occurredAt"),
            raw_markdown=markdown,
        )

    async def get_event_stacktrace(self, issue: IssueRef, event_id: str = "latest") -> str | None:
        _, markdown = await self._call(
            "get_event_stacktrace",
            {"organizationSlug": issue.org_slug, "issueId": issue.short_id, "eventId": event_id},
        )
        return markdown

    async def get_issue_breadcrumbs(self, issue: IssueRef, event_id: str = "latest") -> str | None:
        _, markdown = await self._call(
            "get_issue_breadcrumbs",
            {"issueUrl": issue.issue_url, "eventId": event_id},
        )
        return markdown
