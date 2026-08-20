"""Merchant-facing Instagram comment-automation dashboard API - the
missing layer on top of the already-built ingress/matching/reply pipeline
(app/instagram/, app/flows/executor.py). Before this, the only way to
create a rule was scripts/load_flows (hand-written YAML) - no merchant
could do it themselves.

Templates are fill-fields-only by design (see app/flows/templates.py's
docstring): a merchant picks a preset and supplies a link (and, for
presets with a "keyword" field, a custom trigger keyword) - the trigger
keywords and reply copy always come from the preset, never free text from
the merchant. This keeps the reply surface pre-vetted (no broken {link}
placeholders, no moderation surface for arbitrary merchant-authored public
comment replies) at the cost of flexibility the MVP doesn't need yet.

"keyword_to_dm" is the one deliberate exception on the reply-copy side: it
has a "message" field, and _build_response_config uses that merchant text
verbatim as the private DM (still with {link} substitution). The public
reply stays preset-fixed even there - only the private DM, never seen
under the post, is merchant-authored, which is why the moderation-surface
concern above doesn't block it.

Auth is the real dashboard session (get_current_merchant), NOT the
webhook_slug capability-token pattern used by /instagram/connect - these
are authenticated calls from an already-logged-in merchant, same trust
level as /auth/me.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_merchant
from app.automations.schemas import (
    AutomationOut,
    InstallRequest,
    MediaItemOut,
    MediaPageOut,
    TemplateOut,
    UpdateRequest,
)
from app.db.models import CommentEvent, Flow, Merchant
from app.db.session import get_db
from app.flows.schema import InstagramCommentResponse
from app.flows.templates import TEMPLATES, templates_for_vertical
from app.instagram.client import InstagramClient
from app.nlp.transliteration import normalize
from app.redis_client import get_redis

router = APIRouter(tags=["automations"])

_MEDIA_CACHE_TTL_SECONDS = 300
_STATS_WINDOW = timedelta(days=7)
# Every channel the dashboard's unified "Avtomatlashtirish" page manages -
# comment-to-DM and story-reply-to-DM installers share one list/CRUD
# surface, distinguished by AutomationOut.channel.
_MANAGED_CHANNELS = ("instagram_comment", "instagram_story_reply")


def _lang(language: str) -> str:
    return language if language in ("uz", "ru") else "uz"


def _localized_template(key: str, template: dict, language: str) -> TemplateOut:
    return TemplateOut(
        key=key,
        name=template["name"][language],
        description=template["description"][language],
        channel=template["channel"],
        keywords=template["keywords"],
        fields=[{**f, "label": f["label"][language]} for f in template["fields"]],
    )


@router.get("/automations/templates")
def list_templates(
    lang: str = Query(default="uz"),
    merchant: Merchant = Depends(get_current_merchant),
) -> list[TemplateOut]:
    language = _lang(lang)
    presets = templates_for_vertical(merchant.vertical)
    return [_localized_template(key, template, language) for key, template in presets.items()]


def _validate_link(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 512 or not (value.startswith("http://") or value.startswith("https://")):
        raise HTTPException(status_code=400, detail="link must be an http(s) URL, max 512 chars")
    return value


def _resolve_keywords(template: dict, fields: dict[str, str]) -> list[str]:
    field_keys = {f["key"] for f in template["fields"]}
    if "keyword" in field_keys:
        custom = (fields.get("keyword") or "").strip()
        if not custom:
            raise HTTPException(status_code=400, detail="keyword is required for this template")
        # Canonicalize the same way inbound comments are before match_flow
        # compares them (app/instagram/service.py's process_comment_event)
        # - a raw Uzbek-Cyrillic keyword would otherwise never match a
        # customer's equivalent comment, which gets transliterated to
        # Latin before comparison.
        return [normalize(custom).normalized_text]
    return template["keywords"]


def _validate_message(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 700:
        raise HTTPException(status_code=400, detail="message must be 1-700 chars")
    return value


def _build_response_config(template: dict, fields: dict[str, str], media_ids: list[str], public_reply_enabled: bool) -> dict:
    for field in template["fields"]:
        if field["required"] and not (fields.get(field["key"]) or "").strip():
            raise HTTPException(status_code=400, detail=f"{field['key']} is required")
    link = _validate_link(fields["link"])
    field_keys = {f["key"] for f in template["fields"]}
    if "message" in field_keys:
        message = _validate_message(fields["message"])
        private_reply = {"uz": message, "ru": message}
    else:
        private_reply = template["private_reply"]
    # Public reply only exists as a concept on the comment channel - a
    # story-reply template's public_reply is always None (see
    # app/flows/templates.py), so this naturally no-ops there regardless
    # of the flag, but the explicit channel check is the real guard.
    wants_public_reply = public_reply_enabled and template["channel"] == "instagram_comment"
    return InstagramCommentResponse(
        link=link,
        private_reply=private_reply,
        public_reply=template["public_reply"] if wants_public_reply else None,
        media_ids=media_ids,
    ).model_dump(exclude_none=True)


def _stats_by_flow(db: Session, merchant_id, flow_ids: list[uuid.UUID]) -> dict:
    if not flow_ids:
        return {}
    since = datetime.now(timezone.utc).replace(tzinfo=None) - _STATS_WINDOW
    rows = db.execute(
        select(
            CommentEvent.matched_flow_id,
            func.count(),
            func.sum(case((CommentEvent.status == "replied", 1), else_=0)),
        )
        .where(
            CommentEvent.merchant_id == merchant_id,
            CommentEvent.matched_flow_id.in_(flow_ids),
            CommentEvent.created_at >= since,
        )
        .group_by(CommentEvent.matched_flow_id)
    ).all()
    return {row[0]: (row[1], row[2] or 0) for row in rows}


def _automation_out(flow: Flow, stats: dict, language: str) -> AutomationOut:
    comments_7d, dms_sent_7d = stats.get(flow.id, (0, 0))
    template = TEMPLATES.get(flow.template_key) if flow.template_key else None
    name = template["name"][language] if template else flow.name
    # Only surface response_config's private_reply as an editable "message"
    # for presets that actually collect one (keyword_to_dm) - other
    # presets' private_reply is fixed preset copy, not merchant text.
    has_message_field = bool(template) and any(f["key"] == "message" for f in template["fields"])
    message = flow.response_config.get("private_reply", {}).get("uz") if has_message_field else None
    return AutomationOut(
        id=str(flow.id),
        template_key=flow.template_key,
        name=name,
        channel=flow.channel,
        keywords=json.loads(flow.trigger_value),
        is_active=flow.is_active,
        media_ids=flow.response_config.get("media_ids") or [],
        link=flow.response_config.get("link"),
        message=message,
        public_reply_enabled="public_reply" in flow.response_config,
        comments_7d=comments_7d,
        dms_sent_7d=dms_sent_7d,
        created_at=flow.created_at.isoformat(),
    )


@router.get("/automations")
def list_automations(
    lang: str = Query(default="uz"),
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> list[AutomationOut]:
    language = _lang(lang)
    flows = db.scalars(
        select(Flow)
        .where(Flow.merchant_id == merchant.id, Flow.channel.in_(_MANAGED_CHANNELS))
        .order_by(Flow.created_at.desc())
    ).all()
    stats = _stats_by_flow(db, merchant.id, [f.id for f in flows])
    return [_automation_out(flow, stats, language) for flow in flows]


@router.post("/automations")
def install_automation(
    body: InstallRequest,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> AutomationOut:
    template = TEMPLATES.get(body.template_key)
    if template is None:
        raise HTTPException(status_code=404, detail="unknown template")
    if merchant.vertical is not None and template["verticals"] is not None and merchant.vertical not in template["verticals"]:
        raise HTTPException(status_code=400, detail="template not available for this vertical")

    keywords = _resolve_keywords(template, body.fields)
    response_config = _build_response_config(template, body.fields, body.media_ids, body.public_reply_enabled)

    # One installed instance per (merchant, template_key) in MVP -
    # re-installing is an edit, not a duplicate (mirrors "copy, don't
    # reference": the merchant's single installed copy just gets updated).
    existing = db.scalar(
        select(Flow).where(
            Flow.merchant_id == merchant.id,
            Flow.channel == template["channel"],
            Flow.template_key == body.template_key,
        )
    )
    if existing is not None:
        existing.trigger_value = json.dumps(keywords)
        existing.response_config = response_config
        existing.is_active = True
        db.add(existing)
        flow = existing
    else:
        flow = Flow(
            merchant_id=merchant.id,
            name=template["name"]["uz"],
            trigger_type="keyword",
            trigger_value=json.dumps(keywords),
            channel=template["channel"],
            response_config=response_config,
            is_active=True,
            template_key=body.template_key,
        )
        db.add(flow)
    db.commit()
    db.refresh(flow)
    return _automation_out(flow, {}, _lang("uz"))


def _get_owned_flow(db: Session, merchant: Merchant, automation_id: str) -> Flow:
    try:
        flow_id = uuid.UUID(automation_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    flow = db.scalar(
        select(Flow).where(Flow.id == flow_id, Flow.merchant_id == merchant.id, Flow.channel.in_(_MANAGED_CHANNELS))
    )
    if flow is None:
        raise HTTPException(status_code=404, detail="not found")
    return flow


@router.patch("/automations/{automation_id}")
def update_automation(
    automation_id: str,
    body: UpdateRequest,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> AutomationOut:
    flow = _get_owned_flow(db, merchant, automation_id)

    if body.is_active is not None:
        flow.is_active = body.is_active

    if body.media_ids is not None:
        flow.response_config = {**flow.response_config, "media_ids": body.media_ids}

    if body.fields is not None and "link" in body.fields:
        config = dict(flow.response_config)
        config["link"] = _validate_link(body.fields["link"])
        flow.response_config = config

    if body.fields is not None and "keyword" in body.fields:
        custom = body.fields["keyword"].strip()
        if not custom:
            raise HTTPException(status_code=400, detail="keyword cannot be empty")
        # Same canonicalization as install (_resolve_keywords) - without
        # this, editing an existing automation's keyword to Cyrillic-Uzbek
        # text stores it unnormalized, so it would never match an inbound
        # comment (already normalized to Latin before match_flow compares).
        flow.trigger_value = json.dumps([normalize(custom).normalized_text])

    db.add(flow)
    db.commit()
    db.refresh(flow)
    return _automation_out(flow, {}, _lang("uz"))


@router.delete("/automations/{automation_id}")
def delete_automation(
    automation_id: str,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> dict:
    flow = _get_owned_flow(db, merchant, automation_id)
    db.delete(flow)
    db.commit()
    return {"ok": True}


@router.get("/instagram/media")
def list_media(
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> MediaPageOut:
    if not merchant.instagram_access_token:
        raise HTTPException(status_code=409, detail="instagram_not_connected")

    redis_client = get_redis()
    cache_key = f"ig_media:{merchant.id}"
    if cursor is None:
        cached = redis_client.get(cache_key)
        if cached:
            return MediaPageOut.model_validate_json(cached)

    client = InstagramClient(merchant.instagram_access_token)
    try:
        payload = client.get_media(cursor=cursor)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="instagram_reconnect_required")

    items = [
        MediaItemOut(
            id=item["id"],
            caption=item.get("caption"),
            media_type=item.get("media_type", "IMAGE"),
            thumbnail_url=item.get("thumbnail_url"),
            media_url=item.get("media_url"),
            permalink=item.get("permalink"),
            timestamp=item.get("timestamp"),
        )
        for item in payload.get("data", [])
    ]
    next_cursor = payload.get("paging", {}).get("cursors", {}).get("after")
    page = MediaPageOut(items=items, next_cursor=next_cursor)

    if cursor is None:
        redis_client.set(cache_key, page.model_dump_json(), ex=_MEDIA_CACHE_TTL_SECONDS)

    return page
