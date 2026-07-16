"""YAML schema for merchant-defined flows (rule-based triggers, the free/instant
layer that should absorb traffic before FAQ retrieval or the intent classifier
run - see aqlchat-phase1-plan memory for the layering rationale).

Deliberately just keyword-trigger for Phase 1. Not a visual flow builder -
a human-editable YAML file per merchant. Two response shapes, discriminated
by the flow's channel: telegram rules reply with text in-thread;
instagram_comment rules (app/instagram/) send a private DM built from
`link` + `private_reply`, optionally a public comment reply, optionally
scoped to specific posts.
"""

from typing import Literal

from pydantic import BaseModel, model_validator


class FlowResponse(BaseModel):
    type: str = "text"
    text: str


class InstagramCommentResponse(BaseModel):
    link: str
    # Per-language DM text with a {link} placeholder, e.g.
    # {"uz": "Mana havola 👉 {link}", "ru": "Вот ссылка 👉 {link}"}.
    private_reply: dict[str, str]
    # Optional visible reply under the comment ("DM'ga yubordim! 📩").
    # Only sent when the comment exactly equals a keyword (see
    # app/instagram/ - substring matches DM quietly instead).
    public_reply: dict[str, str] | None = None
    # Absent/empty = rule applies to comments on ALL posts; otherwise
    # only on these IG media ids. Scoped rules beat account-wide ones
    # when both match (app/flows/executor.py).
    media_ids: list[str] = []


class FlowDefinition(BaseModel):
    name: str
    trigger_type: str = "keyword"
    channel: Literal["telegram", "instagram_comment"] = "telegram"
    keywords: list[str]
    response: FlowResponse | InstagramCommentResponse

    @model_validator(mode="after")
    def _response_shape_matches_channel(self) -> "FlowDefinition":
        expected = InstagramCommentResponse if self.channel == "instagram_comment" else FlowResponse
        if not isinstance(self.response, expected):
            raise ValueError(
                f"flow '{self.name}': channel '{self.channel}' requires a {expected.__name__}-shaped response"
            )
        return self


class FlowFile(BaseModel):
    flows: list[FlowDefinition]
