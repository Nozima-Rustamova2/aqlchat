from typing import Literal

from pydantic import BaseModel, Field

Tone = Literal["polite", "formal"]

# Design's stepper: step 5, min 1, max 200, default 20 (per-end-customer
# daily reply cap) - see design_handoff_dukan_ai_agents/README.md step 2.
_DAILY_LIMIT_MIN = 1
_DAILY_LIMIT_MAX = 200


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    tone: Tone = "polite"
    daily_limit: int = Field(default=20, ge=_DAILY_LIMIT_MIN, le=_DAILY_LIMIT_MAX)
    knowledge_text: str = ""


class UpdateAgentRequest(BaseModel):
    """Partial update - any subset of the editable fields. Telegram
    connection is deliberately NOT here; it goes through its own
    validated endpoint (POST /agents/{id}/telegram/connect) since it
    requires a live getMe check, not a blind field write."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    tone: Tone | None = None
    daily_limit: int | None = Field(default=None, ge=_DAILY_LIMIT_MIN, le=_DAILY_LIMIT_MAX)
    knowledge_text: str | None = None
    active: bool | None = None
    split_messages: bool | None = None
    operator_pause: bool | None = None
    stop_keywords: str | None = None


class TelegramConnectRequest(BaseModel):
    token: str = Field(min_length=1)


class TestChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class TestChatResponse(BaseModel):
    reply: str


class AgentOut(BaseModel):
    id: str
    name: str
    tone: Tone
    daily_limit: int
    knowledge_text: str
    active: bool
    split_messages: bool
    operator_pause: bool
    stop_keywords: str | None
    # Bot id itself isn't sensitive (mirrors Merchant.telegram_bot_id -
    # only the token is encrypted/withheld); its presence IS the
    # telegram_connected signal, see app/db/models.py's Agent docstring.
    telegram_bot_id: int | None
    telegram_connected: bool
    created_at: str
    updated_at: str
