from pydantic import BaseModel, Field


class TemplateOut(BaseModel):
    key: str
    name: str
    description: str
    channel: str
    keywords: list[str]
    fields: list[dict]


class InstallRequest(BaseModel):
    template_key: str
    fields: dict[str, str] = Field(default_factory=dict)
    media_ids: list[str] = Field(default_factory=list)
    public_reply_enabled: bool = True


class UpdateRequest(BaseModel):
    is_active: bool | None = None
    media_ids: list[str] | None = None
    fields: dict[str, str] | None = None


class AutomationOut(BaseModel):
    id: str
    template_key: str | None
    name: str
    channel: str
    keywords: list[str]
    is_active: bool
    media_ids: list[str]
    link: str | None
    public_reply_enabled: bool
    comments_7d: int
    dms_sent_7d: int
    created_at: str


class MediaItemOut(BaseModel):
    id: str
    caption: str | None
    media_type: str
    thumbnail_url: str | None
    media_url: str | None
    permalink: str | None
    timestamp: str | None


class MediaPageOut(BaseModel):
    items: list[MediaItemOut]
    next_cursor: str | None
