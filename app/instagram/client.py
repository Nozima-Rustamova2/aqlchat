"""Thin Instagram Graph API client, mirroring app/telegram/client.py's
shape (sync httpx, one instance per merchant credential). Only the two
calls comment-to-DM automation needs; anything else waits until a feature
needs it.

Uses the Instagram API with Instagram Login (graph.instagram.com) - no
Facebook Page in the loop. The API version is pinned in settings
(instagram_graph_api_version); bump it deliberately, changelog open.
"""

import httpx

from app.config import settings


class InstagramClient:
    def __init__(self, access_token: str):
        self._base_url = f"https://graph.instagram.com/{settings.instagram_graph_api_version}"
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def send_private_reply(self, comment_id: str, text: str) -> dict:
        """The one private DM Meta allows per comment (7-day window).
        recipient is the comment id, not a user id - that's what
        authorizes messaging someone who never DM'd us first."""
        response = httpx.post(
            f"{self._base_url}/me/messages",
            json={"recipient": {"comment_id": comment_id}, "message": {"text": text}},
            headers=self._headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def reply_to_comment(self, comment_id: str, text: str) -> dict:
        """Public reply, threaded under the customer's comment."""
        response = httpx.post(
            f"{self._base_url}/{comment_id}/replies",
            json={"message": text},
            headers=self._headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
