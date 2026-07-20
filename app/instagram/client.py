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

    def send_message(self, recipient_id: str, text: str) -> dict:
        """Reply to an inbound message (e.g. a story reply) - the standard
        Send API. Unlike send_private_reply, the recipient here is the
        sender's own IGSID: they already messaged us, so no comment_id
        detour is needed to authorize the send."""
        response = httpx.post(
            f"{self._base_url}/me/messages",
            json={"recipient": {"id": recipient_id}, "message": {"text": text}},
            headers=self._headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def get_media(self, cursor: str | None = None, limit: int = 25) -> dict:
        """The dashboard's post-picker feed (app/automations/router.py) -
        no new permission needed, instagram_business_basic already covers
        this. A plain read, not routed through app/instagram/budget.py's
        180/hr send cap - that budget is about reply calls, not this."""
        params = {
            "fields": "id,caption,media_type,thumbnail_url,media_url,permalink,timestamp",
            "limit": limit,
        }
        if cursor:
            params["after"] = cursor
        response = httpx.get(f"{self._base_url}/me/media", params=params, headers=self._headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def subscribe_webhooks(self, fields: str = "comments") -> dict:
        """The step the app-level webhook URL registration (Meta App
        Dashboard, or app/instagram/router.py's GET /instagram/webhook
        verify handshake) does NOT cover: that only proves Meta can reach
        our callback URL at all. Nothing is actually sent for any specific
        connected account until THAT account is subscribed via this call
        - call once right after OAuth connect (app/instagram/router.py's
        oauth_callback), using the account's own just-obtained token."""
        response = httpx.post(
            f"{self._base_url}/me/subscribed_apps",
            params={"subscribed_fields": fields},
            headers=self._headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
