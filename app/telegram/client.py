import httpx

from app.config import settings


class TelegramClient:
    def __init__(self, bot_token: str):
        self._base_url = f"{settings.telegram_api_base}/bot{bot_token}"

    def send_message(self, chat_id: int, text: str) -> dict:
        response = httpx.post(f"{self._base_url}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
        response.raise_for_status()
        return response.json()
