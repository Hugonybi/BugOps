from __future__ import annotations

from slack_sdk import WebClient


class SlackClient:
    """Thin slack_sdk wrapper for the one call notify_slack needs: posting a message."""

    def __init__(self, token: str) -> None:
        self._client = WebClient(token=token)

    def post_message(self, channel: str, text: str) -> str | None:
        """Posts `text` to `channel`, returns the message's ts (thread anchor) or None."""
        response = self._client.chat_postMessage(channel=channel, text=text)
        return response.get("ts")
