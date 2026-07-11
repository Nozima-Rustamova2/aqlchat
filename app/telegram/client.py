import json
import os

import httpx

from app.config import settings


class TelegramClient:
    def __init__(self, bot_token: str):
        self._bot_token = bot_token
        self._base_url = f"{settings.telegram_api_base}/bot{bot_token}"

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        response = httpx.post(f"{self._base_url}/sendMessage", json=payload, timeout=10)
        response.raise_for_status()
        return response.json()

    def send_photo(
        self, chat_id: int, photo: str, caption: str | None = None, reply_markup: dict | None = None
    ) -> dict:
        """`photo` is either an HTTP(S) URL / Telegram file_id (sent as a
        JSON string field, Telegram fetches it) or a local file path
        (uploaded as multipart form data) - Product.image_url is a local
        path for catalogs loaded via scripts/load_products.py until
        product photos have real public hosting, so both cases need to
        work."""
        data = {"chat_id": str(chat_id)}
        if caption is not None:
            data["caption"] = caption
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup)

        if os.path.isfile(photo):
            with open(photo, "rb") as f:
                response = httpx.post(
                    f"{self._base_url}/sendPhoto", data=data, files={"photo": f}, timeout=30
                )
        else:
            response = httpx.post(f"{self._base_url}/sendPhoto", data={**data, "photo": photo}, timeout=10)
        response.raise_for_status()
        return response.json()

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict:
        payload = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
        response = httpx.post(f"{self._base_url}/answerCallbackQuery", json=payload, timeout=10)
        response.raise_for_status()
        return response.json()

    def download_photo(self, file_id: str) -> bytes:
        """Resolves a Telegram file_id to bytes via getFile + the separate
        file-download host (a different base path than the Bot API
        itself: /file/bot<token>/<file_path>, not /bot<token>/...)."""
        get_file = httpx.get(f"{self._base_url}/getFile", params={"file_id": file_id}, timeout=10)
        get_file.raise_for_status()
        file_path = get_file.json()["result"]["file_path"]

        download_url = f"{settings.telegram_api_base}/file/bot{self._bot_token}/{file_path}"
        response = httpx.get(download_url, timeout=10)
        response.raise_for_status()
        return response.content
