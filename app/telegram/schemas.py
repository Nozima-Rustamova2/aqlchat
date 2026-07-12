from pydantic import BaseModel, Field


class TelegramUser(BaseModel):
    id: int
    is_bot: bool = False
    first_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class TelegramChat(BaseModel):
    id: int
    type: str
    title: str | None = None
    username: str | None = None


class TelegramPhotoSize(BaseModel):
    file_id: str
    file_unique_id: str
    width: int
    height: int
    file_size: int | None = None


class TelegramMessage(BaseModel):
    model_config = {"populate_by_name": True}

    message_id: int
    date: int
    chat: TelegramChat
    from_: TelegramUser | None = Field(default=None, alias="from")
    text: str | None = None
    # Channel posts and photos-with-captions carry text here, not in
    # `text` - anything reading a message's textual content for those
    # update types needs this field, not `.text`.
    caption: str | None = None
    photo: list[TelegramPhotoSize] | None = None
    # A customer forwarding a channel post to the bot is the deterministic
    # forward-match signal (app/telegram/webhook.py) - Telegram deprecated
    # forward_from_chat/forward_from_message_id in Bot API 7.0 in favor of
    # the unified forward_origin, but both may still be populated
    # depending on API version, so both are kept and parsed defensively.
    forward_from_chat: TelegramChat | None = None
    forward_from_message_id: int | None = None
    forward_origin: dict | None = None
    # Used to resolve which pending_admin_replies row an admin's reply
    # targets (app/handoff/service.py) - self-referential, needs
    # model_rebuild() below since the class isn't fully defined yet at
    # the point this annotation is evaluated.
    reply_to_message: "TelegramMessage | None" = None


class TelegramCallbackQuery(BaseModel):
    model_config = {"populate_by_name": True}

    id: str
    from_: TelegramUser = Field(alias="from")
    message: TelegramMessage | None = None
    data: str | None = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None
    channel_post: TelegramMessage | None = None
    edited_channel_post: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


TelegramMessage.model_rebuild()
